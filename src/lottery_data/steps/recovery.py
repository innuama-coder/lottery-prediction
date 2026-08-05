"""Idempotent recovery for stale publication journals."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from lottery_data.artifacts import atomic_write_bytes, atomic_write_json, load_json, validate_stable_id, write_once_json
from lottery_data.models import validate_live_event_stream, validate_object
from lottery_data.serialization import canonical_json_bytes, make_event_id, sha256_file
from lottery_data.steps.events import EventLog
from lottery_data.steps.publication_journal import PublicationJournal, decode_pointer, tree_sha256
from lottery_data.steps.locking import LockUnavailable, RunLock
from lottery_data.steps.publish import PublishLock
from lottery_data.steps.report import RunCounters, build_run_result
from lottery_data.steps.live_policy import LIVE_POLICY_SHA256, LIVE_POLICY_V13_SHA256


class RecoveryConflict(RuntimeError):
    exit_code = 6


class RecoveryInvariantError(RuntimeError):
    """Recovery could not produce its own closed interrupted evidence pair."""


# Journal state is evidence of ordering, never proof that the adjacent disk
# side effect happened.  Recovery therefore applies this disk-truth matrix to
# every non-COMPLETED journal, including a journal one write behind/ahead:
#
# pointer identity | release/projection identity | terminal event + result | action
# third-party      | any                         | any                     | conflict
# original/missing | owned or missing            | any                     | rollback
# committed        | both owned                  | matching published pair | roll-forward
# committed        | otherwise                   | otherwise               | rollback
RECOVERY_DECISION_MATRIX = "pointer-bytes + publication-tree-hashes + published-pair"
RUN_TERMINALS = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
DETERMINISTIC_PATHS = {
    "run_manifest": "run-manifest.json",
    "events": "events.jsonl",
    "quality_report": "quality-report.json",
    "observations": "observations.jsonl",
    "candidate_draws": "candidate-draws.jsonl",
    "reconciliation": "reconciliation.jsonl",
}
_LEGACY_LIVE_POLICY_SHA256 = "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1"
_LIVE_POLICY_BY_RUN_SCHEMA = {
    "1.1.0": _LEGACY_LIVE_POLICY_SHA256,
    "1.2.0": LIVE_POLICY_SHA256,
    "1.3.0": LIVE_POLICY_V13_SHA256,
}


def _manifest_profile(manifest: dict[str, Any]) -> str:
    """Validate and classify a run without interpreting one profile as another."""
    version = manifest.get("run_schema_version")
    source_mode = manifest.get("source_mode")
    if source_mode == "snapshot" and version == "1.0.0":
        validate_object("RunManifest", manifest)
        return "snapshot-v1"
    if source_mode != "live" or version not in _LIVE_POLICY_BY_RUN_SCHEMA:
        raise ValueError(f"unsupported or mixed recovery run profile: {source_mode!r}/{version!r}")
    schema = {"1.1.0": "RunManifestV1.1", "1.2.0": "RunManifestV1.2", "1.3.0": "RunManifestV1.3"}[version]
    validate_object(schema, manifest)
    expected = _LIVE_POLICY_BY_RUN_SCHEMA[version]
    if manifest.get("config_files") != [{"ref": "config/live-source-policy.json", "sha256": expected}]:
        raise ValueError("recovery live manifest policy identity differs from its frozen profile")
    return {"1.1.0": "live-v11", "1.2.0": "live-v12", "1.3.0": "live-v13"}[version]


def _canonical_result_hashes(run_root: Path) -> dict[str, str]:
    return {
        key: sha256_file(run_root / relative)
        for key, relative in DETERMINISTIC_PATHS.items()
        if (run_root / relative).is_file()
    }


@dataclass(frozen=True)
class RecoveryReport:
    recovered_run_ids: tuple[str, ...]
    rolled_forward_run_ids: tuple[str, ...]
    quarantined_paths: tuple[str, ...]


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _quarantine(artifacts_root: Path, run_id: str, relative: str) -> str | None:
    source = (artifacts_root / relative).resolve()
    source.relative_to(artifacts_root.resolve())
    destination = artifacts_root / "runs" / run_id / "recovery" / "quarantine" / relative
    if not source.exists():
        return destination.relative_to(artifacts_root).as_posix() if destination.exists() else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RecoveryConflict(f"third-party quarantine identity at: {relative}")
    os.replace(source, destination)
    return destination.relative_to(artifacts_root).as_posix()


def _owned_tree(artifacts_root: Path, relative: str, expected: str) -> bool:
    path = artifacts_root / relative
    if not path.exists():
        return False
    if not path.is_dir() or tree_sha256(path) != expected:
        raise RecoveryConflict(f"third-party publication tree identity at: {relative}")
    return True


def _expected_run_refs(run_id: str) -> dict[str, str]:
    return {
        "manifest_ref": f"runs/{run_id}/run-manifest.json",
        "events_ref": f"runs/{run_id}/events.jsonl",
        "quality_report_ref": f"runs/{run_id}/quality-report.json",
    }


def _safe_artifact_ref(artifacts_root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or "\\" in relative or ".." in pure.parts:
        raise ValueError(f"unsafe artifact ref: {relative!r}")
    path = (artifacts_root / Path(*pure.parts)).resolve()
    path.relative_to(artifacts_root.resolve())
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"referenced artifact is absent: {relative}")
    return path


def _validated_hash_index(artifacts_root: Path, run_root: Path) -> dict[str, str]:
    value = load_json(run_root / "hashes.json")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("run hash manifest has no entries")
    index: dict[str, str] = {}
    run_prefix = run_root.relative_to(artifacts_root).as_posix() + "/"
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("invalid run hash entry")
        relative = entry["path"]
        if relative in index or not relative.startswith(run_prefix):
            raise ValueError("duplicate run hash entry")
        path = _safe_artifact_ref(artifacts_root, relative)
        digest = sha256_file(path)
        if entry.get("size_bytes") != path.stat().st_size or entry.get("sha256") != digest:
            raise ValueError("run hash entry differs from disk")
        index[relative] = digest
    return index


def _managed_run_files(run_root: Path) -> set[Path]:
    """Files that define a terminal run; recovery evidence is deliberately separate."""
    files: set[Path] = set()
    for path in run_root.rglob("*"):
        if not path.is_file() or path.name == "hashes.json":
            continue
        relative = path.relative_to(run_root)
        if relative.parts[:1] == ("recovery",) and relative.as_posix() != "recovery/process-interrupted.json":
            continue
        files.add(path)
    return files


def _write_recovery_hashes(artifacts_root: Path, run_root: Path, clock: Callable[[], str]) -> None:
    entries = []
    for path in sorted(_managed_run_files(run_root), key=lambda item: item.as_posix()):
        entries.append({
            "path": path.relative_to(artifacts_root).as_posix(),
            "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
            "role": "recovery" if "recovery" in path.relative_to(run_root).parts else "artifact",
        })
    atomic_write_json(run_root / "hashes.json", {
        "hash_manifest_schema_version": "1.0.0", "hash_profile": "sha256-file-manifest-v1",
        "generated_at_utc": clock(), "entries": entries,
    })


def _exact_hash_closure(artifacts_root: Path, run_root: Path) -> bool:
    try:
        index = _validated_hash_index(artifacts_root, run_root)
        actual = {path.relative_to(artifacts_root).as_posix() for path in _managed_run_files(run_root)}
        return set(index) == actual
    except Exception:
        return False


def _deterministic_hashes_match(
    artifacts_root: Path, run_root: Path, run_id: str, result: dict[str, Any],
) -> bool:
    hashes = result.get("deterministic_artifact_hashes")
    expected_keys = {
        key for key, relative in DETERMINISTIC_PATHS.items() if (run_root / relative).is_file()
    }
    # Legacy completed runs carried a canonical subset; new writers emit every
    # existing canonical item. Recovery accepts that frozen subset but never
    # interprets managed path refs as RunResult keys.
    if (
        not isinstance(hashes, dict)
        or not {"run_manifest", "events"}.issubset(hashes)
        or not set(hashes).issubset(expected_keys)
    ):
        return False
    try:
        for key, expected in hashes.items():
            path = run_root / DETERMINISTIC_PATHS[key]
            if not path.is_file() or sha256_file(path) != expected:
                return False
    except Exception:
        return False
    return True


def _validated_event_state_machine(
    run_root: Path, run_id: str, manifest: dict[str, Any], terminal_type: str,
    *, release_id: str | None = None,
) -> list[dict[str, Any]] | None:
    try:
        rows = _events(run_root / "events.jsonl")
        if not rows or [row.get("sequence") for row in rows] != list(range(1, len(rows) + 1)):
            return None
        for row in rows:
            validate_object("RunEvent", row)
            if row.get("run_id") != run_id:
                return None
        if [row.get("event_type") for row in rows[:2]] != ["run_planned", "run_started"]:
            return None
        terminals = [row for row in rows if row.get("event_type") in RUN_TERMINALS]
        if len(terminals) != 1 or terminals[0] is not rows[-1] or terminals[0].get("event_type") != terminal_type:
            return None
        if terminal_type == "run_published":
            if terminals[0].get("artifact_ref") != f"releases/{release_id}/manifest.json":
                return None
        else:
            expected_error = f"runs/{run_id}/recovery/process-interrupted.json"
            if terminals[0].get("error_code") != "PROCESS_INTERRUPTED" or terminals[0].get("error_detail_ref") != expected_error:
                return None
        plan = {request["request_id"]: request for request in manifest.get("request_plan", [])}
        histories: dict[str, list[dict[str, Any]]] = {request_id: [] for request_id in plan}
        for row in rows[2:-1]:
            event_type = row.get("event_type")
            request_id = row.get("request_id")
            if event_type not in {"request_started", "request_succeeded", "request_failed"} or request_id not in plan:
                return None
            planned = plan[request_id]
            if (
                row.get("attempt") != 1
                or row.get("source_id") != planned.get("source_id")
                or row.get("game") != planned.get("game")
            ):
                return None
            histories[request_id].append(row)
        for request_id, history in histories.items():
            types = [row["event_type"] for row in history]
            if terminal_type == "run_published":
                if types != ["request_started", "request_succeeded"]:
                    return None
                if history[-1].get("artifact_ref") != plan[request_id].get("input_ref"):
                    return None
            elif types not in ([], ["request_started", "request_succeeded"], ["request_started", "request_failed"]):
                return None
            elif types == ["request_started", "request_succeeded"] and history[-1].get("artifact_ref") != plan[request_id].get("input_ref"):
                return None
            elif types == ["request_started", "request_failed"] and (
                history[-1].get("error_code") != "PROCESS_INTERRUPTED"
                or history[-1].get("error_detail_ref") != f"runs/{run_id}/recovery/process-interrupted.json"
            ):
                return None
        return rows
    except Exception:
        return None


def _result_identity_matches(
    artifacts_root: Path, run_root: Path, run_id: str, result: dict[str, Any],
    *, status: str, release_id: str | None,
) -> bool:
    try:
        validate_object("RunResult", result)
    except Exception:
        return False
    if (
        result.get("run_id") != run_id
        or result.get("status") != status
        or result.get("release_id") != release_id
        or any(result.get(field) != expected for field, expected in _expected_run_refs(run_id).items())
    ):
        return False
    if status == "published" and result.get("exit_code") != 0:
        return False
    if status == "interrupted" and result.get("exit_code") not in {None, 10}:
        return False
    return _deterministic_hashes_match(artifacts_root, run_root, run_id, result) and _exact_hash_closure(artifacts_root, run_root)


def _request_stats_match(result: dict[str, Any], manifest: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    stats = result.get("request_stats")
    if not isinstance(stats, dict):
        return False
    started = sum(row.get("event_type") == "request_started" for row in rows)
    succeeded = sum(row.get("event_type") == "request_succeeded" for row in rows)
    failed = sum(row.get("event_type") == "request_failed" for row in rows)
    planned = len(manifest.get("request_plan", [])) + sum(
        row.get("event_type") == "request_discovered" for row in rows
    )
    return stats == {
        "planned": planned, "started": started, "succeeded": succeeded,
        "failed": failed, "not_started": planned - started,
    }


def _terminal_run_is_complete(artifacts_root: Path, run_root: Path, run_id: str) -> bool:
    """A terminal event alone is not completion: result and exact hashes must agree."""
    try:
        manifest = load_json(run_root / "run-manifest.json")
        result = load_json(run_root / "run-result.json")
        rows = _events(run_root / "events.jsonl")
        if not rows:
            return False
        terminal = rows[-1].get("event_type")
        status_by_terminal = {
            "run_published": "published", "run_no_change": "no_change",
            "run_rejected": "rejected", "run_interrupted": "interrupted",
        }
        status = status_by_terminal.get(terminal)
        if status is None or sum(row.get("event_type") in RUN_TERMINALS for row in rows) != 1:
            return False
        if manifest.get("run_id") != run_id or result.get("mode") != manifest.get("mode"):
            return False
        profile = _manifest_profile(manifest)
        if profile in {"live-v11", "live-v12", "live-v13"}:
            validate_live_event_stream(manifest, rows, result)
        else:
            for sequence, row in enumerate(rows, 1):
                validate_object("RunEvent", row)
                if row.get("run_id") != run_id or row.get("sequence") != sequence:
                    return False
        return _result_identity_matches(
            artifacts_root, run_root, run_id, result, status=status,
            release_id=result.get("release_id"),
        )
    except Exception:
        return False


def _publication_is_complete(
    artifacts_root: Path, run_root: Path, run_id: str, release_id: str, release_relative: str,
) -> bool:
    try:
        manifest = load_json(run_root / "run-manifest.json")
        profile = _manifest_profile(manifest)
        if manifest.get("run_id") != run_id or Path(manifest.get("artifacts_root", "")).resolve() != artifacts_root:
            return False
        result = load_json(run_root / "run-result.json")
        if profile in {"live-v11", "live-v12", "live-v13"}:
            rows = _events(run_root / "events.jsonl")
            validate_live_event_stream(manifest, rows, result)
            if not rows or rows[-1].get("event_type") != "run_published" or rows[-1].get("artifact_ref") != f"releases/{release_id}/manifest.json":
                return False
            raw_refs = {
                row["artifact_ref"] for row in rows
                if row.get("event_type") == "request_succeeded" and isinstance(row.get("artifact_ref"), str)
            }
            if len(raw_refs) != (5 if profile == "live-v11" else 4):
                return False
        else:
            rows = _validated_event_state_machine(run_root, run_id, manifest, "run_published", release_id=release_id)
            if rows is None:
                return False
            raw_refs = {request["input_ref"] for request in manifest.get("request_plan", [])}
        if not _result_identity_matches(
            artifacts_root, run_root, run_id, result, status="published", release_id=release_id,
        ) or result.get("mode") != manifest.get("mode") or result.get("error_refs") or not _request_stats_match(result, manifest, rows):
            return False
        release_manifest_path = artifacts_root / release_relative / "manifest.json"
        release_manifest = load_json(release_manifest_path)
        validate_object("DatasetRelease", release_manifest)
        if (
            release_manifest.get("release_id") != release_id
            or release_manifest.get("input_run_id") != run_id
            or release_manifest.get("input_manifest_sha256") != sha256_file(run_root / "run-manifest.json")
            or release_manifest.get("quality_report_ref") != f"releases/{release_id}/quality-report.json"
            or (artifacts_root / release_relative / "quality-report.json").read_bytes()
            != (run_root / "quality-report.json").read_bytes()
        ):
            return False
        if profile in {"live-v11", "live-v12", "live-v13"}:
            release_observations = _events(artifacts_root / release_relative / "observations.jsonl")
            raw_refs.update(
                row.get("raw_ref") for row in release_observations if isinstance(row.get("raw_ref"), str)
            )
        hash_index = _validated_hash_index(artifacts_root, run_root)
        required_refs = set(_expected_run_refs(run_id).values()) | {
            f"runs/{run_id}/run-result.json",
            *(f"runs/{run_id}/{raw_ref}" for raw_ref in raw_refs),
        }
        if not required_refs.issubset(hash_index):
            return False
        return all(hash_index[relative] == sha256_file(_safe_artifact_ref(artifacts_root, relative)) for relative in required_refs)
    except Exception:
        return False


def _interrupted_pair_is_complete(artifacts_root: Path, run_root: Path, run_id: str) -> bool:
    try:
        manifest = load_json(run_root / "run-manifest.json")
        profile = _manifest_profile(manifest)
        if manifest.get("run_id") != run_id or Path(manifest.get("artifacts_root", "")).resolve() != artifacts_root:
            return False
        rows = _validated_event_state_machine(run_root, run_id, manifest, "run_interrupted")
        if profile in {"live-v11", "live-v12", "live-v13"}:
            rows = _events(run_root / "events.jsonl")
        if rows is None:
            return False
        result = load_json(run_root / "run-result.json")
        if profile in {"live-v11", "live-v12", "live-v13"}:
            validate_live_event_stream(manifest, rows, result)
        expected_error = f"runs/{run_id}/recovery/process-interrupted.json"
        return (
            _result_identity_matches(
                artifacts_root, run_root, run_id, result, status="interrupted", release_id=None,
            )
            and result.get("mode") == manifest.get("mode")
            and result.get("error_refs") == [expected_error]
            and result.get("deterministic_artifact_hashes", {}).get("quality_report")
            == sha256_file(run_root / "quality-report.json")
            and _request_stats_match(result, manifest, rows)
            and _safe_artifact_ref(artifacts_root, expected_error).is_file()
        )
    except Exception:
        return False


def _interrupted_event_result_match_without_hashes(
    artifacts_root: Path, run_root: Path, run_id: str,
) -> bool:
    """Recognize the durable prefix immediately before hashes.json is committed."""
    try:
        manifest = load_json(run_root / "run-manifest.json")
        rows = _events(run_root / "events.jsonl")
        result = load_json(run_root / "run-result.json")
        expected_error = f"runs/{run_id}/recovery/process-interrupted.json"
        if not rows or rows[-1].get("event_type") != "run_interrupted":
            return False
        profile = _manifest_profile(manifest)
        if profile in {"live-v11", "live-v12", "live-v13"}:
            validate_live_event_stream(manifest, rows, result)
        elif _validated_event_state_machine(run_root, run_id, manifest, "run_interrupted") is None:
            return False
        validate_object("RunResult", result)
        return (
            result.get("run_id") == run_id and result.get("status") == "interrupted"
            and result.get("release_id") is None and result.get("mode") == manifest.get("mode")
            and result.get("error_refs") == [expected_error]
            and all(result.get(field) == value for field, value in _expected_run_refs(run_id).items())
            and _deterministic_hashes_match(artifacts_root, run_root, run_id, result)
            and _request_stats_match(result, manifest, rows)
        )
    except Exception:
        return False


def _rewrite_without_run_terminal(run_root: Path, run_id: str) -> bool:
    path = run_root / "events.jsonl"
    rows = _events(path)
    # Rebuild the terminal after unfinished requests have been closed, so an
    # interrupted terminal can never precede the request_failed events it owns.
    removable = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    if not any(row.get("event_type") in removable for row in rows):
        return False
    evidence = run_root / "recovery" / "events-before-interruption.jsonl"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    if not evidence.exists():
        write_once_json(evidence.with_suffix(".json"), {"sha256": sha256_file(path)})
        atomic_write_bytes(evidence, path.read_bytes())
    kept = [row for row in rows if row.get("event_type") not in removable]
    profile = _manifest_profile(load_json(run_root / "run-manifest.json"))
    schema_name = {
        "snapshot-v1": "RunEvent", "live-v11": "RunEventV1.1", "live-v12": "RunEventV1.2",
        "live-v13": "RunEventV1.3",
    }[profile]
    payload = bytearray()
    for sequence, row in enumerate(kept, 1):
        row["run_id"] = run_id
        row["sequence"] = sequence
        row["event_id"] = make_event_id(run_id, sequence, row["event_type"], row.get("request_id"), row.get("attempt"))
        validate_object(schema_name, row)
        payload.extend(canonical_json_bytes(row))
    atomic_write_bytes(path, bytes(payload))
    return True


def _write_interrupted_result(run_root: Path, run_id: str, clock: Callable[[], str]) -> None:
    result_path = run_root / "run-result.json"
    if result_path.is_file():
        destination = run_root / "recovery" / "quarantine" / "run-result-before-interruption.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RecoveryConflict("third-party recovered result identity")
        os.replace(result_path, destination)
    manifest = load_json(run_root / "run-manifest.json")
    rows = _events(run_root / "events.jsonl")
    plan = manifest.get("request_plan", [])
    keys_started = {(row.get("request_id"), row.get("attempt")) for row in rows if row.get("event_type") == "request_started"}
    keys_succeeded = {(row.get("request_id"), row.get("attempt")) for row in rows if row.get("event_type") == "request_succeeded"}
    keys_failed = {(row.get("request_id"), row.get("attempt")) for row in rows if row.get("event_type") == "request_failed"}
    discovered = sum(row.get("event_type") == "request_discovered" for row in rows)
    counters = RunCounters(
        planned=max(len(plan) + discovered, len(keys_started)), started=len(keys_started),
        succeeded=len(keys_succeeded), failed=len(keys_failed),
    )
    error_ref = f"runs/{run_id}/recovery/process-interrupted.json"
    counters.artifact_hashes = _canonical_result_hashes(run_root)
    started_at = manifest.get("started_at_utc")
    if not isinstance(started_at, str):
        started_at = rows[0]["occurred_at_utc"] if rows else clock()
    result = build_run_result(
        run_id=run_id, mode=manifest.get("mode", "bootstrap"), status="interrupted", exit_code=None,
        started_at_utc=started_at, completed_at_utc=clock(),
        release_id=None, manifest_ref=f"runs/{run_id}/run-manifest.json",
        events_ref=f"runs/{run_id}/events.jsonl", quality_report_ref=f"runs/{run_id}/quality-report.json",
        error_refs=[error_ref], counters=counters,
    )
    write_once_json(result_path, result)


def _complete_interrupted_run(
    artifacts_root: Path, run_root: Path, run_id: str, clock: Callable[[], str],
) -> None:
    # This is the recovery transaction's own commit check.  If a previous
    # recovery crashed after writing the complete pair, preserve both files
    # byte-for-byte and only let the caller advance the journal.
    if _interrupted_pair_is_complete(artifacts_root, run_root, run_id):
        return
    if _interrupted_event_result_match_without_hashes(artifacts_root, run_root, run_id):
        _write_recovery_hashes(artifacts_root, run_root, clock)
        if _interrupted_pair_is_complete(artifacts_root, run_root, run_id):
            return
        raise RecoveryInvariantError("recovery hash commit did not close interrupted run")
    if (run_root / "hashes.json").is_file():
        destination = run_root / "recovery" / "quarantine" / "hashes-before-interruption.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RecoveryConflict("third-party recovered hash identity")
        os.replace(run_root / "hashes.json", destination)
    path = run_root / "events.jsonl"
    _rewrite_without_run_terminal(run_root, run_id)
    rows = _events(path)
    terminal_keys = {
        (row.get("request_id"), row.get("attempt")) for row in rows
        if row.get("event_type") in {"request_succeeded", "request_failed"}
    }
    started: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("request_id"), row.get("attempt"))
        if row.get("event_type") == "request_started" and isinstance(key[0], str) and isinstance(key[1], int):
            started.setdefault(key, row)
    error_path = run_root / "recovery" / "process-interrupted.json"
    atomic_write_json(error_path, {"error_code": "PROCESS_INTERRUPTED", "message": "recovered stale publication journal"})
    quality_path = run_root / "quality-report.json"
    if not quality_path.is_file():
        atomic_write_json(quality_path, {
            "quality_schema_version": "1.0.0", "run_id": run_id, "decision": "FAIL",
            "deterministic": {"counts": {}, "checks": [], "input_hashes": {}, "output_hashes": {},
                              "blocking_reason_codes": ["PROCESS_INTERRUPTED"]},
            "generated_at_utc": clock(),
        })
    error_ref = f"runs/{run_id}/recovery/process-interrupted.json"
    log = EventLog.open_existing(path, run_id, clock)
    for (request_id, attempt), row in started.items():
        if (request_id, attempt) not in terminal_keys:
            log.append(
                "request_failed", request_id=request_id, attempt=attempt, source_id=row.get("source_id"),
                game=row.get("game"), error_code="PROCESS_INTERRUPTED", error_detail_ref=error_ref,
            )
    current = _events(path)
    terminals = [row for row in current if row.get("event_type") in {"run_published", "run_no_change", "run_rejected", "run_interrupted"}]
    if not terminals:
        log.append("run_interrupted", error_code="PROCESS_INTERRUPTED", error_detail_ref=error_ref)
    elif len(terminals) != 1 or terminals[0].get("event_type") != "run_interrupted":
        raise RecoveryConflict("third-party run terminal identity")
    _write_interrupted_result(run_root, run_id, clock)
    _write_recovery_hashes(artifacts_root, run_root, clock)
    if not _interrupted_pair_is_complete(artifacts_root, run_root, run_id):
        raise RecoveryInvariantError("recovery did not produce a closed interrupted event/result pair")


def recover_stale_publications(artifacts_root: Path, *, clock: Callable[[], str]) -> RecoveryReport:
    """Resolve stale transactions from disk truth under the publication OS lock.

    Matrix: an original pointer rolls back any owned side effects; a committed
    pointer rolls forward only with both owned trees and a schema-valid matching
    published event/result, otherwise it rolls back. Any third-party pointer or
    tree identity exits 6.
    """

    recovered: list[str] = []
    rolled_forward: list[str] = []
    quarantined: list[str] = []
    artifacts_root = artifacts_root.resolve()
    with PublishLock(artifacts_root / ".publish.lock", "recovery"):
        journal_root = artifacts_root / ".publication-journals"
        for path in sorted(journal_root.glob("*.json")) if journal_root.is_dir() else ():
            journal = PublicationJournal(path)
            value = journal.read()
            if value["state"] == "COMPLETED":
                continue
            run_id = value["run_id"]
            run_root = artifacts_root / "runs" / run_id
            pointer_path = artifacts_root / "current-release.json"
            actual = pointer_path.read_bytes() if pointer_path.is_file() else None
            original = decode_pointer(value["original_pointer_b64"])
            committed = decode_pointer(value["committed_pointer_b64"])
            if actual not in {original, committed}:
                raise RecoveryConflict(f"third-party pointer identity for run {run_id}")
            release_owned = _owned_tree(artifacts_root, value["release_path"], value["release_tree_sha256"])
            projection_owned = _owned_tree(artifacts_root, value["projection_path"], value["projection_tree_sha256"])

            if (
                actual == committed
                and release_owned
                and projection_owned
                and value["release_tree_sha256"] == value["projection_tree_sha256"]
                and _publication_is_complete(
                    artifacts_root, run_root, run_id, value["release_id"], value["release_path"],
                )
            ):
                temp_quarantine = [
                    item for item in (_quarantine(artifacts_root, run_id, relative) for relative in value["temporary_paths"])
                    if item
                ]
                journal.complete_recovery(updated_at_utc=clock(), quarantined=temp_quarantine, status="rolled_forward")
                rolled_forward.append(run_id)
                quarantined.extend(temp_quarantine)
                continue

            if actual == committed:
                if original is None:
                    pointer_path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(pointer_path, original)
            paths = list(value["temporary_paths"])
            if release_owned:
                paths.append(value["release_path"])
            if projection_owned:
                paths.append(value["projection_path"])
            this_quarantine = [
                item for item in (_quarantine(artifacts_root, run_id, relative) for relative in paths) if item
            ]
            _complete_interrupted_run(artifacts_root, run_root, run_id, clock)
            journal.complete_recovery(updated_at_utc=clock(), quarantined=this_quarantine, status="interrupted")
            recovered.append(run_id)
            quarantined.extend(this_quarantine)
    return RecoveryReport(tuple(recovered), tuple(rolled_forward), tuple(quarantined))


def recover_stale_runs(artifacts_root: Path, *, clock: Callable[[], str]) -> tuple[str, ...]:
    """Interrupt abandoned non-publication runs without touching active run owners."""
    root = Path(artifacts_root).resolve()
    runs = root / "runs"
    recovered: list[str] = []
    if not runs.is_dir():
        return ()
    for run_root in sorted((path for path in runs.iterdir() if path.is_dir()), key=lambda path: path.name):
        try:
            validate_stable_id(run_root.name, "run-id")
        except ValueError:
            continue
        lock = RunLock(root, run_root.name)
        try:
            lock.acquire()
        except LockUnavailable:
            continue
        try:
            manifest_path, events_path = run_root / "run-manifest.json", run_root / "events.jsonl"
            # A crash before the first durable event has no schema-valid run to
            # close. Move it aside atomically so the same stable id can retry.
            if not manifest_path.is_file() or not events_path.is_file() or events_path.stat().st_size == 0:
                quarantine = root / ".run-recovery" / run_root.name
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                if quarantine.exists():
                    raise RecoveryConflict(f"third-party init quarantine identity for run {run_root.name}")
                os.replace(run_root, quarantine)
                recovered.append(run_root.name)
                continue
            rows = _events(events_path)
            if any(row.get("event_type") in RUN_TERMINALS for row in rows) and _terminal_run_is_complete(root, run_root, run_root.name):
                continue
            _complete_interrupted_run(root, run_root, run_root.name, clock)
            recovered.append(run_root.name)
        finally:
            lock.release()
    return tuple(recovered)
