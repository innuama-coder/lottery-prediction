"""Read-only G2 release and provenance verification."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from lottery_data.artifacts import load_json
from lottery_data.models import ContractViolation, validate_live_event_stream, validate_object
from lottery_data.serialization import canonical_jsonl_bytes, sha256_bytes, sha256_file
from lottery_data.steps.live_policy import LIVE_POLICY_SHA256, LIVE_POLICY_V13_SHA256


class VerifyContractError(RuntimeError):
    """A release, manifest, identity, path, or non-raw digest is invalid (exit 4)."""


class RawHashMismatchError(VerifyContractError):
    """Persisted raw bytes do not match their frozen digest (exit 5)."""


_RELEASE_FILES = frozenset({
    "draws.jsonl", "observations.jsonl", "manifest.json", "quality-report.json", "hashes.json",
})
_RELEASE_HASHED_FILES = _RELEASE_FILES - {"hashes.json"}
_RUN_TOP_LEVEL_FILES = frozenset({
    "run-manifest.json",
    "events.jsonl",
    "observations.jsonl",
    "reconciliation.jsonl",
    "candidate-draws.jsonl",
    "quality-report.json",
    "run-result.json",
})
_LEGACY_BOOTSTRAP_CONFIG_REFS = frozenset({
    "config/phase1/collection-policy.json",
    "config/phase1/source-catalog.json",
})
_CURRENT_BOOTSTRAP_CONFIG_HASHES = {
    "config/collection-policy.json": "79c2f55d93d3458602122f51148c96073f3dc4a809f95bc3cc7041e0a983e760",
    "config/source-catalog.json": "b0a30f0a6c90744043cb74ed504db161b3456c9607618a522237cf28487b36fa",
}
_CURRENT_SNAPSHOT_SEED_HASHES = {
    "draws": "cde7c6f2491ec03ecf0dce451bfc60894cbd69c0cfd1e904c52a3193ce4b95dd",
    "observations": "cb80d27a685decf62574214c2343dbf126f1b91174c2ab23fda0b35e63096029",
}
_LEGACY_LIVE_POLICY_SHA256 = "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1"
_LIVE_POLICY_BY_RUN_SCHEMA = {
    "1.1.0": _LEGACY_LIVE_POLICY_SHA256,
    "1.2.0": LIVE_POLICY_SHA256,
    "1.3.0": LIVE_POLICY_V13_SHA256,
}


def _live_manifest_profile(manifest: dict[str, Any]) -> tuple[str, str]:
    """Return the explicit live run/policy profile; never infer it from request fields."""
    version = manifest.get("run_schema_version")
    expected_policy_sha = _LIVE_POLICY_BY_RUN_SCHEMA.get(version)
    if expected_policy_sha is None:
        raise VerifyContractError(f"unsupported live run schema version: {version!r}")
    try:
        schema = {"1.1.0": "RunManifestV1.1", "1.2.0": "RunManifestV1.2", "1.3.0": "RunManifestV1.3"}[version]
        validate_object(schema, manifest)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    configs = manifest.get("config_files")
    if configs != [{"ref": "config/live-source-policy.json", "sha256": expected_policy_sha}]:
        raise VerifyContractError("live manifest policy identity differs from its frozen run profile")
    return version, expected_policy_sha


class _StableReader:
    def __init__(self) -> None:
        self._states: dict[Path, tuple[int, int, int | None]] = {}

    @staticmethod
    def _state(path: Path) -> tuple[int, int, int | None]:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", None)

    def bytes(self, path: Path) -> bytes:
        if not path.is_file() or path.is_symlink():
            raise VerifyContractError(f"required regular file is absent or symlinked: {path}")
        before = self._state(path)
        payload = path.read_bytes()
        after = self._state(path)
        if before != after or len(payload) != before[0]:
            raise VerifyContractError(f"file changed while being read: {path}")
        self._states[path] = after
        return payload

    def finish(self) -> None:
        changed = [str(path) for path, state in self._states.items() if not path.is_file() or self._state(path) != state]
        if changed:
            raise VerifyContractError(f"files changed during verification: {sorted(changed)}")


def _sha(payload: bytes) -> str:
    return sha256_bytes(payload)


def _json(reader: _StableReader, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(reader.bytes(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifyContractError(f"invalid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise VerifyContractError(f"JSON object required: {path}")
    return value


def _jsonl(reader: _StableReader, path: Path) -> tuple[list[dict[str, Any]], bytes]:
    payload = reader.bytes(path)
    try:
        lines = payload.decode("utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifyContractError(f"invalid UTF-8 JSONL: {path}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise VerifyContractError(f"JSONL objects required: {path}")
    return rows, payload


def _contained(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or "\\" in relative or ".." in pure.parts:
        raise VerifyContractError(f"unsafe relative path: {relative!r}")
    root_resolved = root.resolve(strict=True)
    candidate = root.joinpath(*pure.parts)
    current = root_resolved
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise VerifyContractError(f"symlink is forbidden in evidence path: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise VerifyContractError(f"path escapes or is absent: {relative}") from exc
    return candidate


def _directory_files(root: Path) -> set[str]:
    if not root.is_dir() or root.is_symlink():
        raise VerifyContractError(f"required directory is absent or symlinked: {root}")
    result: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise VerifyContractError(f"symlink is forbidden: {path}")
        if path.is_file():
            result.add(path.relative_to(root).as_posix())
    return result


def _validate_hash_manifest(
    reader: _StableReader,
    manifest_path: Path,
    content_root: Path,
    expected_paths: set[str],
    *,
    paths_relative_to: Path | None = None,
    raw_path_prefix: str | None = None,
) -> dict[str, str]:
    value = _json(reader, manifest_path)
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise VerifyContractError(f"hash manifest entries must be a list: {manifest_path}")
    index: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise VerifyContractError(f"invalid hash manifest entry: {manifest_path}")
        relative = entry["path"]
        if relative in index:
            raise VerifyContractError(f"duplicate hash path: {relative}")
        if paths_relative_to is None:
            disk = _contained(content_root, relative)
        else:
            disk = _contained(paths_relative_to, relative)
        payload = reader.bytes(disk)
        actual = _sha(payload)
        if entry.get("size_bytes") != len(payload) or entry.get("sha256") != actual:
            error = RawHashMismatchError if raw_path_prefix and relative.startswith(raw_path_prefix) else VerifyContractError
            raise error(f"managed file size/hash mismatch: {relative}")
        index[relative] = actual
    if set(index) != expected_paths:
        raise VerifyContractError(
            f"managed hash path set mismatch: expected={sorted(expected_paths)}, actual={sorted(index)}",
        )
    return index


def _canonical_jsonl(payload: bytes, rows: list[dict[str, Any]], sort_keys: tuple[str, ...], label: str) -> str:
    expected = canonical_jsonl_bytes(rows, sort_keys=sort_keys)
    if payload != expected:
        raise VerifyContractError(f"{label} is not frozen canonical sorted JSONL")
    return _sha(payload)


def _snapshot_root_from_lineage(root: Path, release_id: str) -> Path:
    """Resolve the immutable bootstrap snapshot through declared predecessors."""
    visited: set[str] = set()
    current = release_id
    while current not in visited:
        visited.add(current)
        release_manifest = load_json(root / "releases" / current / "manifest.json")
        run_manifest = load_json(root / "runs" / release_manifest["input_run_id"] / "run-manifest.json")
        if run_manifest.get("mode") == "bootstrap":
            return Path(run_manifest["bootstrap_snapshot"]["snapshot_root"])
        previous = release_manifest.get("previous_release_id")
        if not isinstance(previous, str):
            break
        current = previous
    raise VerifyContractError("snapshot dynamic lineage does not terminate at a bootstrap snapshot")


def _dynamic_raw_ref_matches_profile(
    raw_ref: str, *, source_id: str, game: str, raw_sha256: str | None, snapshot: bool,
) -> bool:
    """Disjoint raw namespaces: snapshot page refs never satisfy the live profile and vice versa."""
    parts = PurePosixPath(raw_ref).parts
    if snapshot:
        page = parts[3] if len(parts) == 4 else ""
        return (
            len(parts) == 4 and parts[:3] == ("raw", source_id, game)
            and len(page) == 13 and page.startswith("page-") and page[5:8].isdigit() and page.endswith(".html")
        )
    return (
        isinstance(raw_sha256, str) and len(parts) == 5
        and parts[:4] == ("raw", source_id, game, "sha256")
        and parts[4] == raw_sha256 + ".raw"
    )


def _verify_baseline_release(
    artifacts_root: Path,
    release_id: str,
    snapshot_root_override: Path | None = None,
) -> dict[str, Any]:
    """Verify the formal and published copies plus the complete Phase 0 evidence chain."""
    reader = _StableReader()
    root = Path(artifacts_root)
    if not release_id or "/" in release_id or "\\" in release_id or release_id in {".", ".."}:
        raise VerifyContractError("unsafe release_id")
    formal = root / release_id
    published = root / "releases" / release_id
    for directory in (formal, published):
        files = _directory_files(directory)
        if files != _RELEASE_FILES:
            raise VerifyContractError(
                f"release file set mismatch at {directory}: expected={sorted(_RELEASE_FILES)}, actual={sorted(files)}",
            )
    for name in sorted(_RELEASE_FILES):
        if reader.bytes(formal / name) != reader.bytes(published / name):
            raise VerifyContractError(f"formal/published release bytes differ: {name}")

    manifest = _json(reader, formal / "manifest.json")
    try:
        validate_object("DatasetRelease", manifest)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    if manifest["release_id"] != release_id or manifest["status"] != "published":
        raise VerifyContractError("release identity/status mismatch")
    release_hashes = _validate_hash_manifest(
        reader, formal / "hashes.json", formal, set(_RELEASE_HASHED_FILES),
    )

    draws, draws_bytes = _jsonl(reader, formal / "draws.jsonl")
    observations, observations_bytes = _jsonl(reader, formal / "observations.jsonl")
    draws_sha = _canonical_jsonl(draws_bytes, draws, ("game", "issue_id", "revision_id"), "draws")
    observations_sha = _canonical_jsonl(
        observations_bytes, observations,
        ("game", "issue_id", "publisher_id", "source_id", "observation_id"),
        "release observations",
    )
    is_snapshot_seed = {
        "draws": draws_sha, "observations": observations_sha,
    } == _CURRENT_SNAPSHOT_SEED_HASHES
    expected_draw_count = 399 if is_snapshot_seed else 400
    expected_observation_count = 798 if is_snapshot_seed else 800
    expected_game_counts = {"dlt": 200, "ssq": 199} if is_snapshot_seed else {"dlt": 200, "ssq": 200}
    if draws_sha != manifest["records_sha256"] or observations_sha != manifest["observations_sha256"]:
        raise VerifyContractError("release content hashes do not match DatasetRelease")
    if len(draws) != expected_draw_count or len(observations) != expected_observation_count:
        raise VerifyContractError(f"frozen release counts mismatch: draws={len(draws)}, observations={len(observations)}")
    game_counts = dict(Counter(item.get("game") for item in draws))
    if game_counts != expected_game_counts or manifest["record_count_by_game"] != expected_game_counts:
        raise VerifyContractError(f"frozen game counts mismatch: {game_counts}")
    try:
        for row in draws:
            validate_object("DrawRecord", row)
        for row in observations:
            validate_object("SourceObservation", row)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    draw_keys = [(row["game"], row["issue_id"]) for row in draws]
    observation_ids = [row["observation_id"] for row in observations]
    if len(set(draw_keys)) != expected_draw_count or len(set(observation_ids)) != expected_observation_count:
        raise VerifyContractError("release identity keys are not unique")

    run_id = manifest["input_run_id"]
    if not isinstance(run_id, str) or "/" in run_id or "\\" in run_id:
        raise VerifyContractError("unsafe input_run_id")
    run_root = root / "runs" / run_id
    run_manifest_bytes = reader.bytes(run_root / "run-manifest.json")
    if _sha(run_manifest_bytes) != manifest["input_manifest_sha256"]:
        raise VerifyContractError("input run manifest hash mismatch")
    run_manifest = _json(reader, run_root / "run-manifest.json")
    run_result = _json(reader, run_root / "run-result.json")
    try:
        validate_object("RunManifest", run_manifest)
        validate_object("RunResult", run_result)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    if run_manifest["run_id"] != run_id or run_result["run_id"] != run_id:
        raise VerifyContractError("run identity mismatch")
    expected_refs = {
        "manifest_ref": f"runs/{run_id}/run-manifest.json",
        "events_ref": f"runs/{run_id}/events.jsonl",
        "quality_report_ref": f"runs/{run_id}/quality-report.json",
    }
    if any(run_result.get(field) != expected for field, expected in expected_refs.items()):
        raise VerifyContractError(f"run managed refs mismatch: expected={expected_refs}")
    if (
        run_result.get("mode") != "bootstrap"
        or run_result.get("status") != "published"
        or run_result.get("release_id") != release_id
    ):
        raise VerifyContractError("RunResult is not the matching published bootstrap result")

    config_files = run_manifest.get("config_files")
    if not isinstance(config_files, list) or len(config_files) != 2:
        raise VerifyContractError("bootstrap config_files must contain exactly two entries")
    config_hashes: dict[str, str] = {}
    for config in config_files:
        if not isinstance(config, dict) or not isinstance(config.get("ref"), str):
            raise VerifyContractError("bootstrap config_files entry is invalid")
        ref = config["ref"]
        if ref in config_hashes:
            raise VerifyContractError(f"duplicate bootstrap config ref: {ref}")
        config_hashes[ref] = config.get("sha256")
    config_refs = frozenset(config_hashes)
    if config_refs == _LEGACY_BOOTSTRAP_CONFIG_REFS:
        current_config_refs: set[str] = set()
        include_quality_result_hash = False
    elif config_refs == frozenset(_CURRENT_BOOTSTRAP_CONFIG_HASHES):
        if config_hashes != _CURRENT_BOOTSTRAP_CONFIG_HASHES:
            raise VerifyContractError("current bootstrap config declarations do not match frozen SHA-256 values")
        current_config_refs = set(config_refs)
        include_quality_result_hash = True
        for ref, expected_sha256 in _CURRENT_BOOTSTRAP_CONFIG_HASHES.items():
            config_path = _contained(run_root, ref)
            if _sha(reader.bytes(config_path)) != expected_sha256:
                raise VerifyContractError(f"current bootstrap config bytes do not match frozen SHA-256: {ref}")
    else:
        raise VerifyContractError(
            f"unknown or mixed bootstrap config ref profile: {sorted(config_refs)}",
        )
    if is_snapshot_seed and not include_quality_result_hash:
        raise VerifyContractError("the frozen snapshot seed is valid only under the current six-hash bootstrap profile")

    raw_refs = [request.get("input_ref") for request in run_manifest["request_plan"]]
    if len(raw_refs) != 30 or any(not isinstance(raw_ref, str) for raw_ref in raw_refs) or len(set(raw_refs)) != 30:
        raise VerifyContractError("run request plan must contain 30 unique raw refs")
    for raw_ref in raw_refs:
        pure = PurePosixPath(raw_ref)
        if pure.is_absolute() or "\\" in raw_ref or ".." in pure.parts or not raw_ref.startswith("raw/"):
            raise VerifyContractError(f"unsafe run raw_ref: {raw_ref!r}")
    expected_run_files = set(_RUN_TOP_LEVEL_FILES) | {"hashes.json"} | set(raw_refs) | current_config_refs
    actual_run_files = _directory_files(run_root)
    if actual_run_files != expected_run_files:
        raise VerifyContractError(
            f"run managed file set mismatch: expected={sorted(expected_run_files)}, actual={sorted(actual_run_files)}",
        )
    expected_run_hash_paths = {
        f"runs/{run_id}/{relative}"
        for relative in (_RUN_TOP_LEVEL_FILES | set(raw_refs) | current_config_refs)
    }
    _validate_hash_manifest(
        reader,
        run_root / "hashes.json",
        run_root,
        expected_run_hash_paths,
        paths_relative_to=root,
        raw_path_prefix=f"runs/{run_id}/raw/",
    )

    events, events_bytes = _jsonl(reader, run_root / "events.jsonl")
    if len(events) != 63 or [event.get("sequence") for event in events] != list(range(1, 64)):
        raise VerifyContractError("published bootstrap requires exactly 63 events with sequence 1..63")
    try:
        for event in events:
            validate_object("RunEvent", event)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    event_types = [event["event_type"] for event in events]
    if any(event["run_id"] != run_id for event in events):
        raise VerifyContractError("RunEvent run_id does not match release input run")
    if event_types[:2] != ["run_planned", "run_started"] or event_types[-1] != "run_published":
        raise VerifyContractError("published run event boundaries are invalid")
    terminal_types = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    if [event["event_type"] for event in events if event["event_type"] in terminal_types] != ["run_published"]:
        raise VerifyContractError("published run requires one final run_published terminal event")
    plan_by_request = {request["request_id"]: request for request in run_manifest["request_plan"]}
    request_events: dict[str, list[dict[str, Any]]] = {request_id: [] for request_id in plan_by_request}
    for event in events[2:-1]:
        request_id = event.get("request_id")
        if request_id not in request_events or event["event_type"] not in {
            "request_started", "request_succeeded", "request_failed",
        }:
            raise VerifyContractError(f"unexpected non-terminal event: {event['event_type']}")
        planned = plan_by_request[request_id]
        if event["source_id"] != planned["source_id"] or event["game"] != planned["game"] or event["attempt"] != 1:
            raise VerifyContractError(f"request event/plan identity mismatch: {request_id}")
        request_events[request_id].append(event)
    for request_id, history in request_events.items():
        if [event["event_type"] for event in history] != ["request_started", "request_succeeded"]:
            raise VerifyContractError(f"published request lifecycle mismatch: {request_id}")
        if history[1].get("artifact_ref") != plan_by_request[request_id]["input_ref"]:
            raise VerifyContractError(f"request success artifact_ref mismatch: {request_id}")

    run_observations, run_observations_bytes = _jsonl(reader, run_root / "observations.jsonl")
    reconciliation, reconciliation_bytes = _jsonl(reader, run_root / "reconciliation.jsonl")
    candidates, candidates_bytes = _jsonl(reader, run_root / "candidate-draws.jsonl")
    if len(run_observations) != 1042 or len(reconciliation) != 400:
        raise VerifyContractError(
            f"run counts mismatch: observations={len(run_observations)}, reconciliation={len(reconciliation)}",
        )
    run_observations_sha = _canonical_jsonl(
        run_observations_bytes, run_observations,
        ("game", "issue_id", "publisher_id", "source_id", "observation_id"), "run observations",
    )
    try:
        for row in run_observations:
            validate_object("SourceObservation", row)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    reconciliation_sha = _canonical_jsonl(
        reconciliation_bytes, reconciliation, ("game", "issue_id"), "reconciliation",
    )
    candidate_sha = _canonical_jsonl(
        candidates_bytes, candidates, ("game", "issue_id", "revision_id"), "candidate draws",
    )
    if candidates_bytes != draws_bytes:
        raise VerifyContractError("candidate and published draws are not byte-identical")
    expected_run_result_hashes = {
        "run_manifest": _sha(run_manifest_bytes),
        "events": _sha(events_bytes),
        "observations": run_observations_sha,
        "candidate_draws": candidate_sha,
        "reconciliation": reconciliation_sha,
    }
    if include_quality_result_hash:
        expected_run_result_hashes["quality_report"] = _sha(reader.bytes(run_root / "quality-report.json"))
    if run_result.get("deterministic_artifact_hashes") != dict(sorted(expected_run_result_hashes.items())):
        raise VerifyContractError("RunResult deterministic artifact hashes mismatch")
    run_observation_index = {row["observation_id"]: row for row in run_observations}
    run_observation_keys = {(row["source_id"], row["game"], row["issue_id"]) for row in run_observations}
    if (
        len(run_observation_index) != 1042
        or len(run_observation_keys) != 1042
        or any(run_observation_index.get(row["observation_id"]) != row for row in observations)
    ):
        raise VerifyContractError("release observations are not an exact subset of run observations")

    quality_bytes = reader.bytes(formal / "quality-report.json")
    run_quality_bytes = reader.bytes(run_root / "quality-report.json")
    if quality_bytes != run_quality_bytes:
        raise VerifyContractError("run/release quality report bytes differ")
    quality = _json(reader, formal / "quality-report.json")
    output_hashes = quality.get("deterministic", {}).get("output_hashes", {})
    expected_outputs = {
        "draws": draws_sha,
        "run_observations": run_observations_sha,
        "release_observations": observations_sha,
        "reconciliation": reconciliation_sha,
    }
    counts = quality.get("deterministic", {}).get("counts", {})
    if (
        quality.get("run_id") != run_id
        or quality.get("decision") != "PASS"
        or output_hashes != dict(sorted(expected_outputs.items()))
    ):
        raise VerifyContractError("quality decision/output hashes mismatch")
    if counts.get("parsed_observations") != 1042 or counts.get("selected_observations") != expected_observation_count:
        raise VerifyContractError("quality observation counts mismatch")

    observation_index = {row["observation_id"]: row for row in observations}
    linked_ids: list[str] = []
    for draw in draws:
        for link in draw["evidence_links"]:
            linked_ids.append(link["observation_id"])
            observation = observation_index.get(link["observation_id"])
            if observation is None or any(
                link[field] != observation[field]
                for field in ("publisher_id", "source_id", "raw_ref", "raw_sha256")
            ) or draw["core_fact_sha256"] != observation["core_fact_sha256"]:
                raise VerifyContractError(f"draw evidence join mismatch: {draw['game']}/{draw['issue_id']}")
    if len(linked_ids) != expected_observation_count or len(set(linked_ids)) != expected_observation_count or set(linked_ids) != set(observation_ids):
        raise VerifyContractError("draw evidence links do not cover release observations exactly")

    bootstrap = run_manifest["bootstrap_snapshot"]
    snapshot_root = Path(snapshot_root_override) if snapshot_root_override is not None else Path(bootstrap["snapshot_root"])
    if not snapshot_root.is_dir() or snapshot_root.is_symlink():
        raise VerifyContractError("bootstrap snapshot root is absent or symlinked")
    artifact_hashes_path = snapshot_root / "artifact-hashes.json"
    artifact_hashes_bytes = reader.bytes(artifact_hashes_path)
    if _sha(artifact_hashes_bytes) != bootstrap["artifact_hashes_sha256"]:
        raise VerifyContractError("Phase0 artifact-hashes digest mismatch")
    artifact_hashes = json.loads(artifact_hashes_bytes)
    if not isinstance(artifact_hashes, dict):
        raise VerifyContractError("Phase0 artifact-hashes must be an object")
    for relative, expected_hash in artifact_hashes.items():
        path = _contained(snapshot_root, relative)
        if _sha(reader.bytes(path)) != expected_hash:
            raise VerifyContractError(f"Phase0 managed artifact mismatch: {relative}")
    for required in ("capture-manifest.jsonl", "consensus/canonical-records.jsonl"):
        if required not in artifact_hashes:
            raise VerifyContractError(f"Phase0 artifact-hashes missing {required}")

    capture, _ = _jsonl(reader, snapshot_root / "capture-manifest.jsonl")
    canonical, _ = _jsonl(reader, snapshot_root / "consensus" / "canonical-records.jsonl")
    if len(capture) != 30 or len(canonical) != 400:
        raise VerifyContractError(f"Phase0 frozen counts mismatch: capture={len(capture)}, canonical={len(canonical)}")
    plan_index = {row.get("input_ref"): row for row in run_manifest["request_plan"]}
    capture_index = {row.get("raw_ref"): row for row in capture}
    if len(plan_index) != 30 or len(capture_index) != 30 or set(plan_index) != set(capture_index):
        raise VerifyContractError("request plan/capture manifest path set mismatch")
    for raw_ref, captured in capture_index.items():
        planned = plan_index[raw_ref]
        if planned["request_id"] != captured["request_id"] or any(
            planned[field] != captured[field] for field in ("source_id", "game", "url")
        ):
            raise VerifyContractError(f"request plan/capture join mismatch: {raw_ref}")
        run_raw = _contained(run_root, raw_ref)
        snapshot_raw = _contained(snapshot_root, raw_ref)
        expected_raw_hash = captured["raw_sha256"]
        if _sha(reader.bytes(run_raw)) != expected_raw_hash or _sha(reader.bytes(snapshot_raw)) != expected_raw_hash:
            raise RawHashMismatchError(f"raw hash mismatch: {raw_ref}")
        if reader.bytes(run_raw) != reader.bytes(snapshot_raw):
            raise RawHashMismatchError(f"run/snapshot raw bytes differ: {raw_ref}")
    for observation in run_observations:
        captured = capture_index.get(observation["raw_ref"])
        if captured is None or observation["raw_sha256"] != captured["raw_sha256"]:
            raise VerifyContractError(f"observation/capture join mismatch: {observation['observation_id']}")

    canonical_index = {(row["game"], row["issue_id"]): row for row in canonical}
    if len(canonical_index) != 400:
        raise VerifyContractError("Phase0 canonical game/issue keys are not unique")
    for draw in draws:
        phase0 = canonical_index.get((draw["game"], draw["issue_id"]))
        if phase0 is None or any((
            phase0.get("draw_date") != draw["draw_date_local"],
            phase0.get("front_numbers") != draw["front_numbers"],
            phase0.get("back_numbers") != draw["back_numbers"],
            phase0.get("core_fact_sha256") != draw["core_fact_sha256"],
        )):
            raise VerifyContractError(f"Phase0 canonical mismatch: {draw['game']}/{draw['issue_id']}")

    reader.finish()
    return {
        "status": "PASS",
        "release_id": release_id,
        "expected": {
            "draws": expected_draw_count, "release_observations": expected_observation_count, "run_observations": 1042,
            "records_by_game": expected_game_counts, "requests": 30,
        },
        "actual": {
            "draws": len(draws), "release_observations": len(observations),
            "run_observations": len(run_observations), "records_by_game": manifest["record_count_by_game"],
            "requests": len(capture),
        },
        "hashes": {
            **expected_outputs,
            "candidate_draws": candidate_sha,
            "release_hash_manifest_entries": release_hashes,
            "phase0_artifact_hashes": bootstrap["artifact_hashes_sha256"],
        },
        "checks": {
            "formal_published_byte_identity": True,
            "exact_release_hash_inventory": True,
            "exact_run_hash_inventory_including_raw": True,
            "schema_ids_and_evidence": True,
            "phase0_capture_raw_canonical_chain": True,
            "path_containment_and_no_symlinks": True,
            "stable_reads": True,
        },
    }


def _verify_dynamic_release(
    artifacts_root: Path, release_id: str, *, seen: set[str] | None = None,
) -> dict[str, Any]:
    """Rebuild a dynamic release from disk facts instead of trusting resigned hashes."""
    visited = set() if seen is None else seen
    if release_id in visited:
        raise VerifyContractError(f"dynamic previous-release cycle: {release_id}")
    visited.add(release_id)
    reader = _StableReader()
    root = Path(artifacts_root).resolve()
    formal, published = root / release_id, root / "releases" / release_id
    for directory in (formal, published):
        if _directory_files(directory) != _RELEASE_FILES:
            raise VerifyContractError(f"dynamic release file set mismatch: {directory}")
    for name in _RELEASE_FILES:
        if reader.bytes(formal / name) != reader.bytes(published / name):
            raise VerifyContractError(f"formal/published release bytes differ: {name}")
    manifest = _json(reader, formal / "manifest.json")
    try:
        validate_object("DatasetRelease", manifest)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    if manifest.get("release_id") != release_id or manifest.get("status") != "published":
        raise VerifyContractError("dynamic release identity/status mismatch")
    _validate_hash_manifest(reader, formal / "hashes.json", formal, set(_RELEASE_HASHED_FILES))
    draws, draw_bytes = _jsonl(reader, formal / "draws.jsonl")
    observations, observation_bytes = _jsonl(reader, formal / "observations.jsonl")
    draw_sha = _canonical_jsonl(draw_bytes, draws, ("game", "issue_id", "revision_id"), "draws")
    observation_sha = _canonical_jsonl(
        observation_bytes, observations,
        ("game", "issue_id", "publisher_id", "source_id", "observation_id"), "release observations",
    )
    try:
        for row in draws:
            validate_object("DrawRecord", row)
        for row in observations:
            validate_object("SourceObservation", row)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    counts = Counter(row["game"] for row in draws)
    if (
        manifest["records_sha256"] != draw_sha
        or manifest["observations_sha256"] != observation_sha
        or manifest["record_count_by_game"] != {"ssq": counts["ssq"], "dlt": counts["dlt"]}
        or manifest["observation_count"] != len(observations)
        or len(observations) != 2 * len(draws)
    ):
        raise VerifyContractError("dynamic release hashes/counts do not close")
    draw_index = {(row["game"], row["issue_id"]): row for row in draws}
    if len(draw_index) != len(draws):
        raise VerifyContractError("dynamic draw game/issue identities are not unique")
    observation_index = {row["observation_id"]: row for row in observations}
    if len(observation_index) != len(observations):
        raise VerifyContractError("dynamic observation identities are not unique")
    linked_observations: set[str] = set()
    for draw in draws:
        if len(draw["evidence_links"]) != 2:
            raise VerifyContractError("dynamic draw does not have exactly two evidence links")
        for link in draw["evidence_links"]:
            observation = observation_index.get(link["observation_id"])
            if observation is None or any(
                link[field] != observation[field]
                for field in ("source_id", "publisher_id", "raw_ref", "raw_sha256")
            ) or observation["core_fact_sha256"] != draw["core_fact_sha256"]:
                raise VerifyContractError(f"dynamic evidence join mismatch: {draw['game']}/{draw['issue_id']}")
            if (observation["game"], observation["issue_id"]) != (draw["game"], draw["issue_id"]):
                raise VerifyContractError(f"dynamic evidence key mismatch: {draw['game']}/{draw['issue_id']}")
            linked_observations.add(observation["observation_id"])
    if linked_observations != set(observation_index):
        raise VerifyContractError("dynamic draw evidence does not exactly cover selected observations")
    run_id = manifest["input_run_id"]
    run_root = root / "runs" / run_id
    run_manifest = _json(reader, run_root / "run-manifest.json")
    run_result = _json(reader, run_root / "run-result.json")
    if run_manifest.get("source_mode") not in {"snapshot", "live"}:
        raise VerifyContractError("dynamic source_mode must be exactly snapshot or live")
    snapshot_dynamic = run_manifest.get("source_mode") == "snapshot"
    try:
        if snapshot_dynamic:
            validate_object("RunManifest", run_manifest)
        else:
            _live_manifest_profile(run_manifest)
        validate_object("RunResult", run_result)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    if (
        (snapshot_dynamic and run_manifest.get("run_schema_version") != "1.0.0")
        or run_manifest.get("mode") != "incremental" or run_manifest.get("run_id") != run_id
        or run_result.get("status") != "published" or run_result.get("release_id") != release_id
        or _sha(reader.bytes(run_root / "run-manifest.json")) != manifest["input_manifest_sha256"]
        or run_result.get("manifest_ref") != f"runs/{run_id}/run-manifest.json"
        or run_result.get("events_ref") != f"runs/{run_id}/events.jsonl"
        or run_result.get("quality_report_ref") != f"runs/{run_id}/quality-report.json"
        or run_result.get("error_refs") != []
    ):
        raise VerifyContractError("dynamic input run identity/result mismatch")

    run_observations, run_observation_bytes = _jsonl(reader, run_root / "observations.jsonl")
    candidates, candidate_bytes = _jsonl(reader, run_root / "candidate-draws.jsonl")
    reconciliation, reconciliation_bytes = _jsonl(reader, run_root / "reconciliation.jsonl")
    events, event_bytes = _jsonl(reader, run_root / "events.jsonl")
    run_observation_sha = _canonical_jsonl(
        run_observation_bytes, run_observations,
        ("game", "issue_id", "publisher_id", "source_id", "observation_id"), "dynamic run observations",
    )
    candidate_sha = _canonical_jsonl(
        candidate_bytes, candidates, ("game", "issue_id", "revision_id"), "dynamic candidate draws",
    )
    reconciliation_sha = _canonical_jsonl(
        reconciliation_bytes, reconciliation, ("game", "issue_id"), "dynamic reconciliation",
    )
    try:
        for row in run_observations:
            validate_object("SourceObservation", row)
        for row in candidates:
            validate_object("DrawRecord", row)
        if not snapshot_dynamic:
            validate_live_event_stream(run_manifest, events, run_result)
    except ContractViolation as exc:
        raise VerifyContractError(str(exc)) from exc
    if candidate_bytes != draw_bytes:
        raise VerifyContractError("dynamic candidate draws and release draws are not byte-identical")

    if snapshot_dynamic:
        for request in run_manifest["request_plan"]:
            if request.get("method") != "SNAPSHOT" or not _dynamic_raw_ref_matches_profile(
                request.get("input_ref", ""), source_id=request.get("source_id"),
                game=request.get("game"), raw_sha256=None, snapshot=True,
            ):
                raise VerifyContractError("snapshot dynamic request plan is not page-based SNAPSHOT input")
        if (
            len(events) != 2 * len(run_manifest["request_plan"]) + 3
            or [item.get("sequence") for item in events] != list(range(1, len(events) + 1))
            or [item.get("event_type") for item in events[:2]] != ["run_planned", "run_started"]
            or events[-1].get("event_type") != "run_published"
        ):
            raise VerifyContractError("snapshot dynamic event boundaries are invalid")
        try:
            for event in events:
                validate_object("RunEvent", event)
        except ContractViolation as exc:
            raise VerifyContractError(str(exc)) from exc
        histories: dict[str, list[dict[str, Any]]] = {
            item["request_id"]: [] for item in run_manifest["request_plan"]
        }
        for event in events[2:-1]:
            request_id = event.get("request_id")
            if request_id not in histories:
                raise VerifyContractError("snapshot dynamic event names an unplanned request")
            histories[request_id].append(event)
        for request in run_manifest["request_plan"]:
            history = histories[request["request_id"]]
            if (
                [item.get("event_type") for item in history] != ["request_started", "request_succeeded"]
                or any(item.get("run_id") != run_id for item in history)
                or any(item.get("source_id") != request["source_id"] or item.get("game") != request["game"] for item in history)
                or history[1].get("artifact_ref") != request["input_ref"]
            ):
                raise VerifyContractError(f"snapshot dynamic request lifecycle mismatch: {request['request_id']}")
    effective_requests = {item["request_id"]: item for item in run_manifest["request_plan"]}
    for item in events:
        if item.get("event_type") == "request_discovered":
            effective_requests[item["request_id"]] = item
    succeeded_by_request = {
        item["request_id"]: item for item in events if item.get("event_type") == "request_succeeded"
    }
    for request_id, succeeded in succeeded_by_request.items():
        request = effective_requests.get(request_id)
        pure = PurePosixPath(succeeded["artifact_ref"])
        valid_ref = request is not None and _dynamic_raw_ref_matches_profile(
            succeeded["artifact_ref"], source_id=request.get("source_id"), game=request.get("game"),
            raw_sha256=pure.parts[4][:-4] if len(pure.parts) == 5 and pure.parts[4].endswith(".raw") else None,
            snapshot=snapshot_dynamic,
        )
        if request is None or not valid_ref or (snapshot_dynamic and succeeded["artifact_ref"] != request.get("input_ref")):
            raise VerifyContractError("dynamic request raw evidence identity is invalid")
    for observation in run_observations:
        matching = [
            request for request in effective_requests.values()
            if request.get("source_id") == observation["source_id"] and request.get("game") == observation["game"]
            and succeeded_by_request.get(request["request_id"], {}).get("artifact_ref") == observation["raw_ref"]
        ]
        if len(matching) != 1:
            raise VerifyContractError("dynamic observation does not map to exactly one effective request")
        request = matching[0]
        succeeded = succeeded_by_request.get(request["request_id"])
        if (
            succeeded is None or succeeded.get("artifact_ref") != observation["raw_ref"]
            or request.get("publisher_id") != observation["publisher_id"]
            or request.get("url") != observation["source_url"]
            or (not snapshot_dynamic and request.get("parser_id") != observation["parser_id"])
            or (not snapshot_dynamic and request.get("parser_version") != observation["parser_version"])
        ):
            raise VerifyContractError("dynamic observation provenance differs from request/event evidence")

    run_observation_index = {row["observation_id"]: row for row in run_observations}
    if len(run_observation_index) != len(run_observations):
        raise VerifyContractError("dynamic run observation identities are not unique")
    reconciliation_index: dict[tuple[str, str], dict[str, Any]] = {}
    expected_reconciliation_fields = {
        "game", "issue_id", "decision", "selected_observation_ids", "agreeing_observation_ids",
        "dissenting_observation_ids", "core_fact_sha256", "reason_code",
    }
    run_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in run_observations:
        run_by_key.setdefault((row["game"], row["issue_id"]), []).append(row)
    for row in reconciliation:
        if set(row) != expected_reconciliation_fields:
            raise VerifyContractError(
                f"dynamic reconciliation fields differ from the executable profile: {sorted(row)}"
            )
        key = (row.get("game"), row.get("issue_id"))
        if key in reconciliation_index or key not in run_by_key:
            raise VerifyContractError(f"dynamic reconciliation key is duplicate or unobserved: {key}")
        observed = run_by_key[key]
        if snapshot_dynamic:
            selected_ids = set(row.get("selected_observation_ids", []))
            observed = [item for item in observed if item["observation_id"] in selected_ids]
        observed_ids = sorted(item["observation_id"] for item in observed)
        cores = {item["core_fact_sha256"] for item in observed}
        if (
            row.get("decision") != "verified" or len(observed) != 2 or len(cores) != 1
            or sorted(row.get("selected_observation_ids", [])) != observed_ids
            or sorted(row.get("agreeing_observation_ids", [])) != observed_ids
            or row.get("dissenting_observation_ids") != []
            or row.get("core_fact_sha256") != next(iter(cores))
            or row.get("reason_code") is not None
        ):
            raise VerifyContractError(f"dynamic reconciliation does not rebuild from observations: {key}")
        reconciliation_index[key] = row
    config_refs: set[str] = set()
    expected_run_hash_paths = {
        f"runs/{run_id}/{relative}" for relative in _RUN_TOP_LEVEL_FILES
    }
    for config in run_manifest.get("config_files", []):
        ref = config.get("ref")
        if not isinstance(ref, str) or ref in config_refs:
            raise VerifyContractError("dynamic config refs are invalid or duplicate")
        config_path = _contained(run_root, ref)
        actual_config_sha = _sha(reader.bytes(config_path))
        if actual_config_sha != config.get("sha256"):
            raise VerifyContractError(f"dynamic config hash mismatch: {ref}")
        config_refs.add(ref)
        expected_run_hash_paths.add(f"runs/{run_id}/{ref}")
    if snapshot_dynamic and {
        ref: _sha(reader.bytes(_contained(run_root, ref))) for ref in config_refs
    } != _CURRENT_BOOTSTRAP_CONFIG_HASHES:
        raise VerifyContractError("snapshot dynamic config profile differs from the frozen current profile")

    event_raw_refs = {
        row["artifact_ref"] for row in events
        if row.get("event_type") == "request_succeeded" and isinstance(row.get("artifact_ref"), str)
    }
    raw_refs = {row["raw_ref"] for row in (*run_observations, *observations)} | event_raw_refs
    for row in run_observations:
        raw_ref = row["raw_ref"]
        pure = PurePosixPath(raw_ref)
        parts = pure.parts
        if not _dynamic_raw_ref_matches_profile(
            raw_ref, source_id=row["source_id"], game=row["game"],
            raw_sha256=row["raw_sha256"], snapshot=snapshot_dynamic,
        ):
            # The live content-addressed profile is raw/source/game/sha256/<digest>.raw.
            raise VerifyContractError(f"dynamic run observation raw_ref is not content-addressed: {raw_ref}")
    for raw_ref in raw_refs:
        raw_path = _contained(run_root, raw_ref)
        referencing = [row for row in (*run_observations, *observations) if row["raw_ref"] == raw_ref]
        expected_digests = {row["raw_sha256"] for row in referencing}
        if raw_ref in event_raw_refs and not snapshot_dynamic:
            pure = PurePosixPath(raw_ref)
            if len(pure.parts) != 5 or pure.parts[3] != "sha256" or not pure.parts[4].endswith(".raw"):
                raise VerifyContractError(f"dynamic request raw_ref is not content-addressed: {raw_ref}")
            expected_digests.add(pure.parts[4][:-4])
        actual_digest = _sha(reader.bytes(raw_path))
        if len(expected_digests) != 1 or actual_digest not in expected_digests:
            raise RawHashMismatchError(f"dynamic raw hash mismatch: {raw_ref}")
        expected_run_hash_paths.add(f"runs/{run_id}/{raw_ref}")

    actual_run_files = _directory_files(run_root)
    expected_run_files = {
        *(path.removeprefix(f"runs/{run_id}/") for path in expected_run_hash_paths), "hashes.json",
    }
    if actual_run_files != expected_run_files:
        raise VerifyContractError(
            f"dynamic run file set mismatch: expected={sorted(expected_run_files)}, actual={sorted(actual_run_files)}"
        )
    _validate_hash_manifest(
        reader, run_root / "hashes.json", run_root, expected_run_hash_paths,
        paths_relative_to=root, raw_path_prefix=f"runs/{run_id}/raw/",
    )

    quality_bytes = reader.bytes(run_root / "quality-report.json")
    quality = _json(reader, run_root / "quality-report.json")
    if quality.get("decision") != "PASS" or reader.bytes(formal / "quality-report.json") != quality_bytes:
        raise VerifyContractError("dynamic quality evidence mismatch")

    previous_release_id = manifest.get("previous_release_id")
    if (
        not isinstance(previous_release_id, str)
        or run_manifest.get("previous_release_id") != previous_release_id
        or run_manifest.get("incremental_watermark", {}).get("current_release_id") != previous_release_id
    ):
        raise VerifyContractError("dynamic previous release identity is missing or inconsistent")
    _verify_release_profile(root, previous_release_id, None, visited)
    previous_formal = root / previous_release_id
    previous_published = root / "releases" / previous_release_id
    if reader.bytes(previous_formal / "manifest.json") != reader.bytes(previous_published / "manifest.json"):
        raise VerifyContractError("dynamic previous release projection differs")
    previous_manifest = _json(reader, previous_published / "manifest.json")
    previous_draws, previous_draw_bytes = _jsonl(reader, previous_published / "draws.jsonl")
    _canonical_jsonl(previous_draw_bytes, previous_draws, ("game", "issue_id", "revision_id"), "previous draws")
    previous_index = {(row["game"], row["issue_id"]): row for row in previous_draws}
    if len(previous_index) != len(previous_draws) or not set(previous_index).issubset(draw_index):
        raise VerifyContractError("dynamic current release removes or duplicates previous draw keys")
    if previous_manifest.get("release_id") != previous_release_id:
        raise VerifyContractError("dynamic previous release manifest identity mismatch")
    if not snapshot_dynamic:
        recheck_limit = run_manifest.get("incremental_watermark", {}).get("recheck_published_issues")
        if isinstance(recheck_limit, bool) or not isinstance(recheck_limit, int) or recheck_limit <= 0:
            raise VerifyContractError("dynamic live recheck limit is invalid")
        observed_keys = set(run_by_key)
        previous_keys = set(previous_index)
        all_observed_keys = previous_keys | observed_keys
        recent_existing: set[tuple[str, str]] = set()
        for game in sorted({key[0] for key in all_observed_keys}):
            window = sorted(
                (key for key in all_observed_keys if key[0] == game),
                key=lambda key: key[1],
            )[-recheck_limit:]
            recent_existing.update(key for key in window if key in previous_keys)
        expected_reconciliation_keys = (
            observed_keys - previous_keys
        ) | (
            observed_keys & recent_existing
        )
        if set(reconciliation_index) != expected_reconciliation_keys:
            raise VerifyContractError(
                "dynamic live reconciliation does not exactly cover new issues and the recheck window"
            )

    snapshot_input_hashes: dict[str, str] | None = None
    if snapshot_dynamic:
        snapshot_root = _snapshot_root_from_lineage(root, previous_release_id)
        if not snapshot_root.is_dir() or snapshot_root.is_symlink():
            raise VerifyContractError("snapshot dynamic frozen input root is unavailable")
        capture, capture_bytes = _jsonl(reader, snapshot_root / "capture-manifest.jsonl")
        capture_by_ref = {item.get("raw_ref"): item for item in capture}
        if len(capture_by_ref) != 30 or set(capture_by_ref) != event_raw_refs:
            raise VerifyContractError("snapshot dynamic request raw set differs from frozen capture")
        for raw_ref, captured in capture_by_ref.items():
            if (
                _sha(reader.bytes(_contained(run_root, raw_ref))) != captured.get("raw_sha256")
                or reader.bytes(_contained(run_root, raw_ref)) != reader.bytes(_contained(snapshot_root, raw_ref))
            ):
                raise RawHashMismatchError(f"snapshot dynamic raw differs from frozen capture: {raw_ref}")
        snapshot_input_hashes = {
            "artifact_hashes": _sha(reader.bytes(snapshot_root / "artifact-hashes.json")),
            "canonical": _sha(reader.bytes(snapshot_root / "consensus" / "canonical-records.jsonl")),
            "capture_manifest": _sha(capture_bytes),
            "request_events": _sha(reader.bytes(snapshot_root / "request-events.jsonl")),
            "source_catalog": _CURRENT_BOOTSTRAP_CONFIG_HASHES["config/source-catalog.json"],
            "collection_policy": _CURRENT_BOOTSTRAP_CONFIG_HASHES["config/collection-policy.json"],
            "run_manifest": _sha(reader.bytes(run_root / "run-manifest.json")),
            "current_release_manifest": _sha(reader.bytes(previous_published / "manifest.json")),
        }

    changes = {"added": 0, "revised": 0, "unchanged": 0, "conflict": 0, "unresolved": 0}
    for key, current in draw_index.items():
        previous = previous_index.get(key)
        observed = key in reconciliation_index
        if previous is None:
            if not observed or current.get("supersedes_revision_id") is not None:
                raise VerifyContractError(f"dynamic added draw has a false predecessor: {key}")
            changes["added"] += 1
        elif current["core_fact_sha256"] == previous["core_fact_sha256"]:
            if current != previous:
                raise VerifyContractError(f"dynamic unchanged draw/evidence bytes changed: {key}")
            if observed:
                changes["unchanged"] += 1
        else:
            if not observed or current.get("supersedes_revision_id") != previous.get("revision_id"):
                raise VerifyContractError(f"dynamic revised draw does not supersede its direct predecessor: {key}")
            changes["revised"] += 1

    if snapshot_dynamic:
        assert snapshot_input_hashes is not None
        expected_input_hashes = snapshot_input_hashes
    else:
        expected_input_hashes = {
            "current_release_manifest": _sha(reader.bytes(previous_published / "manifest.json")),
            "live_source_policy": next(iter(
                config["sha256"] for config in run_manifest["config_files"]
                if config["ref"].endswith("live-source-policy.json")
            ), None),
            "run_manifest": _sha(reader.bytes(run_root / "run-manifest.json")),
        }
    expected_output_hashes = {
        "draws": draw_sha, "release_observations": observation_sha,
        "run_observations": run_observation_sha, "reconciliation": reconciliation_sha,
    }
    deterministic = quality.get("deterministic", {})
    recheck_complete = sum(key in previous_index for key in reconciliation_index)
    expected_counts = (
        {
            "draws": len(draws), "parsed_observations": len(run_observations),
            "selected_observations": len(observations), "ssq": counts["ssq"], "dlt": counts["dlt"],
            "invalid": 0, "missing": 0, "duplicate": 0, "conflict": 0, "manual_core_edit": 0,
        }
        if snapshot_dynamic else {
            "draws": len(draws), "release_observations": len(observations),
            "run_observations": len(run_observations), **changes,
            "recheck_attempted": recheck_complete, "recheck_complete": recheck_complete,
            "recheck_deferred": 0,
        }
    )
    if (
        deterministic.get("input_hashes") != dict(sorted(expected_input_hashes.items()))
        or deterministic.get("output_hashes") != dict(sorted(expected_output_hashes.items()))
        or deterministic.get("counts") != expected_counts
        or deterministic.get("blocking_reason_codes") != []
        or quality.get("run_id") != run_id
    ):
        raise VerifyContractError(
            "dynamic quality inputs/outputs/counts do not rebuild from disk: "
            f"expected_counts={expected_counts}, actual_counts={deterministic.get('counts')}"
        )

    result_hashes = run_result.get("deterministic_artifact_hashes")
    expected_result_hashes = {
        "events": _sha(event_bytes),
        "observations": run_observation_sha,
        "reconciliation": reconciliation_sha,
        "candidate_draws": candidate_sha,
        "quality_report": _sha(quality_bytes),
        "run_manifest": _sha(reader.bytes(run_root / "run-manifest.json")),
    }
    expected_observation_stats = {
        "parsed": len(run_observations), "valid": len(observations) if snapshot_dynamic else len(run_observations),
        "invalid": 0, "missing": 0, "duplicate": 0, "conflict": 0,
    }
    expected_candidate_stats = {
        "observed": len(reconciliation), "eligible": len(candidates), "unresolved": changes["unresolved"],
    }
    expected_change_stats = {
        "added": changes["added"], "revised": changes["revised"], "unchanged": changes["unchanged"],
        "conflict": 0, "invalid": 0, "duplicate": 0, "manual_core_edit": 0,
    }
    if (
        result_hashes != expected_result_hashes
        or run_result.get("observation_stats") != expected_observation_stats
        or run_result.get("candidate_stats") != expected_candidate_stats
        or run_result.get("change_stats") != expected_change_stats
    ):
        raise VerifyContractError("dynamic RunResult hashes/statistics do not rebuild from disk")
    reader.finish()
    return {
        "status": "PASS", "release_id": release_id, "profile": "incremental-dynamic",
        "actual": {"draws": len(draws), "release_observations": len(observations), "records_by_game": manifest["record_count_by_game"]},
        "hashes": {"draws": draw_sha, "release_observations": observation_sha},
        "checks": {
            "dynamic_counts": True, "event_stream": True, "evidence_and_raw_closure": True,
            "exact_run_inventory": True, "quality_and_result_hashes": True, "previous_revision_chain": True,
        },
    }


def _verify_release_profile(
    root: Path, release_id: str, snapshot_root_override: Path | None, seen: set[str],
) -> dict[str, Any]:
    try:
        release_manifest = load_json(root / "releases" / release_id / "manifest.json")
        run_manifest = load_json(root / "runs" / release_manifest["input_run_id"] / "run-manifest.json")
    except Exception as exc:
        raise VerifyContractError("release/input run manifest is unavailable") from exc
    if run_manifest.get("mode") == "bootstrap":
        return _verify_baseline_release(root, release_id, snapshot_root_override)
    return _verify_dynamic_release(root, release_id, seen=seen)


def verify_release(
    artifacts_root: Path, release_id: str, snapshot_root_override: Path | None = None,
) -> dict[str, Any]:
    """Select the frozen bootstrap oracle or the count-independent incremental profile."""
    root = Path(artifacts_root)
    return _verify_release_profile(root, release_id, snapshot_root_override, set())
