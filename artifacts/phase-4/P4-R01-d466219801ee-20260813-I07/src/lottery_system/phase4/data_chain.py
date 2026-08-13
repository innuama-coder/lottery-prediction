from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .identity import content_id, validate_stable_id, verify_content_id
from .ledger import AppendOnlyLedger
from .serialization import canonical_sha256, load_json, sha256_file
from .storage import (
    AdvisoryFileLock,
    IdentityReuseError,
    LockUnavailable,
    atomic_replace_json,
    ensure_directory,
    fsync_directory,
    publish_directory_once,
    resolve_inside,
    write_once_bytes,
    write_once_json,
)


GENESIS_HASHES = {
    "base_phase1_release_id": "baseline-v1",
    "base_phase1_manifest_sha256": "0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1",
    "base_phase1_records_sha256": "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1",
    "base_phase1_observations_sha256": "dc974863c845da1e895ecf623bc6e878ba6aa6710c902357bce68ad5e661966e",
}


class DataChainMismatch(ValueError):
    exit_code = 20


class StaleDataChainHead(DataChainMismatch, LockUnavailable):
    exit_code = 30
    terminal = "STALE_DATA_CHAIN_HEAD"


def _validate_genesis(project_root: Path, genesis: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "artifact_type", "base_phase1_release_id",
        "base_phase1_manifest_path", "base_phase1_manifest_sha256",
        "base_phase1_records_path", "base_phase1_records_sha256",
        "base_phase1_observations_path", "base_phase1_observations_sha256",
        "previous_phase4_release_id", "provenance",
    }
    if set(genesis) != required or genesis["schema_version"] != "1.0.0" or genesis["artifact_type"] != "phase4_genesis":
        raise DataChainMismatch("Phase 4 genesis shape mismatch")
    if genesis["previous_phase4_release_id"] is not None:
        raise DataChainMismatch("Phase 4 genesis must not have a previous Phase 4 release")
    for key, expected in GENESIS_HASHES.items():
        if genesis[key] != expected:
            raise DataChainMismatch(f"Phase 4 genesis mismatch: {key}")
    files = {
        "manifest": (genesis["base_phase1_manifest_path"], genesis["base_phase1_manifest_sha256"]),
        "draws": (genesis["base_phase1_records_path"], genesis["base_phase1_records_sha256"]),
        "observations": (genesis["base_phase1_observations_path"], genesis["base_phase1_observations_sha256"]),
    }
    checked: dict[str, Any] = {}
    for role, (relative, expected) in files.items():
        source = (project_root.resolve() / relative).resolve()
        try:
            source.relative_to((project_root.resolve() / "artifacts/phase-1").resolve())
        except ValueError as exc:
            raise DataChainMismatch("genesis source escapes the protected Phase 1 tree") from exc
        if not source.is_file() or source.stat().st_size == 0 or sha256_file(source) != expected:
            raise DataChainMismatch(f"Phase 1 genesis content mismatch: {role}")
        checked[role] = {"source": source, "sha256": expected, "bytes": source.stat().st_size}
    return checked


def _content_manifest(
    checked: Mapping[str, Mapping[str, Any]],
    result_revision_ids: Iterable[str],
) -> dict[str, Any]:
    entries = []
    for role, filename in (("manifest", "manifest.json"), ("draws", "draws.jsonl"), ("observations", "observations.jsonl")):
        entries.append({
            "path": f"baseline/{filename}",
            "role": f"phase1_genesis_{role}",
            "sha256": checked[role]["sha256"],
            "bytes": checked[role]["bytes"],
        })
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_data_release_content_manifest",
        "files": entries,
        "result_revision_ids": list(result_revision_ids),
    }


def _release_body(
    *,
    previous_phase4_release_id: str | None,
    result_revision_ids: list[str],
    manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_data_release",
        **GENESIS_HASHES,
        "previous_phase4_release_id": previous_phase4_release_id,
        "result_revision_ids": result_revision_ids,
        "manifest_sha256": manifest_sha256,
    }


def derive_data_release_id(
    *,
    previous_phase4_release_id: str | None,
    result_revision_ids: Iterable[str],
    manifest_sha256: str,
) -> str:
    revisions = sorted(result_revision_ids)
    for revision in revisions:
        validate_stable_id(revision, "result revision identity")
    body = _release_body(
        previous_phase4_release_id=previous_phase4_release_id,
        result_revision_ids=revisions,
        manifest_sha256=manifest_sha256,
    )
    return content_id("data-release", body)


def _stored_genesis(runtime_root: Path) -> dict[str, Any]:
    path = resolve_inside(runtime_root, "control/genesis.json")
    if not path.is_file():
        raise DataChainMismatch("Phase 4 runtime genesis has not been created")
    return load_json(path, reject_floats=True)


def _release_path(runtime_root: Path, release_id: str) -> Path:
    validate_stable_id(release_id, "data release identity")
    return resolve_inside(runtime_root, f"data-releases/{release_id}")


def load_data_release(runtime_root: Path, release_id: str) -> dict[str, Any]:
    root = _release_path(runtime_root, release_id)
    body_path = root / "data-release.json"
    manifest_path = root / "content-manifest.json"
    if not body_path.is_file() or not manifest_path.is_file():
        raise DataChainMismatch("data release is incomplete")
    body = load_json(body_path, reject_floats=True)
    if body.get("data_release_id") != release_id:
        raise DataChainMismatch("data release path/identity mismatch")
    verify_content_id(release_id, "data-release", body, excluded_fields=("data_release_id",))
    if body["manifest_sha256"] != sha256_file(manifest_path):
        raise DataChainMismatch("data release content manifest mismatch")
    manifest = load_json(manifest_path, reject_floats=True)
    if manifest.get("result_revision_ids") != body["result_revision_ids"]:
        raise DataChainMismatch("data release revision manifest mismatch")
    for row in manifest.get("files", []):
        path = resolve_inside(root, row["path"])
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
            raise DataChainMismatch(f"data release file mismatch: {row['path']}")
    return body


def _write_release_staging(
    runtime_root: Path,
    release: Mapping[str, Any],
    content_manifest: Mapping[str, Any],
    checked: Mapping[str, Mapping[str, Any]],
) -> Path:
    staging = resolve_inside(runtime_root, f".staging-data-release-{release['data_release_id']}-{os.getpid()}")
    if staging.exists():
        raise IdentityReuseError(staging)
    ensure_directory(staging / "baseline")
    for role, filename in (("manifest", "manifest.json"), ("draws", "draws.jsonl"), ("observations", "observations.jsonl")):
        write_once_bytes(staging / "baseline" / filename, checked[role]["source"].read_bytes())
    write_once_json(staging / "content-manifest.json", dict(content_manifest))
    write_once_json(staging / "data-release.json", dict(release))
    fsync_directory(staging)
    return staging


def _current_view(runtime_root: Path) -> dict[str, Any] | None:
    path = resolve_inside(runtime_root, "data-releases/current-view.json")
    return load_json(path, reject_floats=True) if path.is_file() else None


def _synchronize_current_view(
    runtime_root: Path,
    ledger: AppendOnlyLedger,
) -> tuple[dict[str, Any] | None, str | None]:
    validation = ledger.validate()
    head_sha256 = validation["head_sha256"]
    if validation["event_count"] == 0:
        if _current_view(runtime_root) is not None:
            raise DataChainMismatch("data current view exists without a ledger event")
        return None, None
    ledger_view = load_json(ledger.current_view_path, reject_floats=True)
    candidates = [
        (row["ordinal"], object_id, row)
        for object_id, row in ledger_view["objects"].items()
        if row["event_type"] == "data_release_published"
    ]
    if not candidates:
        raise DataChainMismatch("data-chain ledger has no published release")
    ordinal, release_id, _ = max(candidates)
    if ordinal != validation["event_count"]:
        raise DataChainMismatch("final data-chain event is not a release publication")
    release = load_data_release(runtime_root, release_id)
    destination = _release_path(runtime_root, release_id)
    expected = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_data_chain_current_view",
        "data_release_id": release_id,
        "data_release_sha256": sha256_file(destination / "data-release.json"),
        "ledger_head_sha256": head_sha256,
    }
    observed = _current_view(runtime_root)
    if observed != expected:
        atomic_replace_json(resolve_inside(runtime_root, "data-releases/current-view.json"), expected)
    return expected, head_sha256


def _publish_or_resume(
    runtime_root: Path,
    release: dict[str, Any],
    manifest: dict[str, Any],
    checked: Mapping[str, Mapping[str, Any]],
    *,
    clock: str,
    producer_provenance: Mapping[str, Any],
    expected_head_sha256: str | None,
) -> dict[str, Any]:
    ledger = AppendOnlyLedger(runtime_root, "data-chain")
    observed_head = ledger.read_head()
    observed_head_sha256 = None if observed_head is None else observed_head.event_sha256
    if observed_head_sha256 != expected_head_sha256:
        raise StaleDataChainHead(
            f"expected data-chain head {expected_head_sha256}, found {observed_head_sha256}"
        )
    destination = _release_path(runtime_root, release["data_release_id"])
    if destination.exists():
        if load_data_release(runtime_root, release["data_release_id"]) != release:
            raise DataChainMismatch("reused data release identity has different content")
    else:
        staging = _write_release_staging(runtime_root, release, manifest, checked)
        publish_directory_once(staging, destination)
    validation = ledger.validate()
    existing = None
    if validation["event_count"]:
        view = load_json(ledger.current_view_path, reject_floats=True)
        existing = view["objects"].get(release["data_release_id"])
    if existing is None:
        appended = ledger.append_event(
            object_id=release["data_release_id"],
            event_type="data_release_published",
            event_at_utc=clock,
            payload={
                "data_release_id": release["data_release_id"],
                "data_release_sha256": sha256_file(destination / "data-release.json"),
                "previous_phase4_release_id": release["previous_phase4_release_id"],
            },
            producer_provenance=producer_provenance,
            expected_head_sha256=expected_head_sha256,
        )
        ledger_head = appended["event_sha256"]
        current = {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_data_chain_current_view",
            "data_release_id": release["data_release_id"],
            "data_release_sha256": sha256_file(destination / "data-release.json"),
            "ledger_head_sha256": ledger_head,
        }
        atomic_replace_json(resolve_inside(runtime_root, "data-releases/current-view.json"), current)
    else:
        current, ledger_head = _synchronize_current_view(runtime_root, ledger)
        if current is None or ledger_head is None:
            raise DataChainMismatch("published release is absent from the data-chain head")
    return {"status": "PASS", "release": release, "current_view": current, "idempotent_resume": existing is not None}


def create_genesis(
    project_root: Path,
    runtime_root: Path,
    genesis_path: Path,
    *,
    clock: str,
    producer_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = runtime_root.resolve()
    ensure_directory(runtime)
    genesis = load_json(genesis_path, reject_floats=True)
    checked = _validate_genesis(project_root, genesis)
    with AdvisoryFileLock(resolve_inside(runtime, ".data-chain.lock")):
        stored_path = resolve_inside(runtime, "control/genesis.json")
        if stored_path.exists():
            if load_json(stored_path, reject_floats=True) != genesis:
                raise DataChainMismatch("runtime genesis was already bound to different content")
        else:
            write_once_json(stored_path, genesis)
        manifest = _content_manifest(checked, [])
        body = _release_body(
            previous_phase4_release_id=None,
            result_revision_ids=[],
            manifest_sha256=canonical_sha256(manifest),
        )
        body["data_release_id"] = content_id("data-release", body)
        ledger = AppendOnlyLedger(runtime, "data-chain")
        current, head_sha256 = _synchronize_current_view(runtime, ledger)
        if current is not None:
            ledger_view = load_json(ledger.current_view_path, reject_floats=True)
            if body["data_release_id"] not in ledger_view["objects"]:
                raise DataChainMismatch("cannot append genesis after the data chain has advanced")
        return _publish_or_resume(
            runtime,
            body,
            manifest,
            checked,
            clock=clock,
            producer_provenance=producer_provenance,
            expected_head_sha256=head_sha256,
        )


def append_data_release(
    project_root: Path,
    runtime_root: Path,
    *,
    data_release_id: str,
    previous_phase4_release_id: str,
    result_revision_ids: Iterable[str],
    clock: str,
    producer_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = runtime_root.resolve()
    with AdvisoryFileLock(resolve_inside(runtime, ".data-chain.lock")):
        genesis = _stored_genesis(runtime)
        checked = _validate_genesis(project_root, genesis)
        previous = load_data_release(runtime, previous_phase4_release_id)
        revisions = sorted(result_revision_ids)
        if not revisions or len(revisions) != len(set(revisions)):
            raise DataChainMismatch("successor release requires a non-empty unique result revision batch")
        manifest = _content_manifest(checked, revisions)
        body = _release_body(
            previous_phase4_release_id=previous["data_release_id"],
            result_revision_ids=revisions,
            manifest_sha256=canonical_sha256(manifest),
        )
        body["data_release_id"] = content_id("data-release", body)
        if data_release_id != body["data_release_id"]:
            raise DataChainMismatch("supplied successor data release identity is not content-derived")
        ledger = AppendOnlyLedger(runtime, "data-chain")
        current, head_sha256 = _synchronize_current_view(runtime, ledger)
        current_id = None if current is None else current["data_release_id"]
        if current_id not in {previous_phase4_release_id, data_release_id}:
            raise StaleDataChainHead(
                f"successor expected current release {previous_phase4_release_id}, found {current_id}"
            )
        return _publish_or_resume(
            runtime,
            body,
            manifest,
            checked,
            clock=clock,
            producer_provenance=producer_provenance,
            expected_head_sha256=head_sha256,
        )


def proposed_data_release_id(
    project_root: Path,
    runtime_root: Path,
    *,
    previous_phase4_release_id: str,
    result_revision_ids: Iterable[str],
) -> str:
    genesis = _stored_genesis(runtime_root)
    checked = _validate_genesis(project_root, genesis)
    revisions = sorted(result_revision_ids)
    manifest = _content_manifest(checked, revisions)
    return derive_data_release_id(
        previous_phase4_release_id=previous_phase4_release_id,
        result_revision_ids=revisions,
        manifest_sha256=canonical_sha256(manifest),
    )


def current_data_release(runtime_root: Path, data_release_id: str) -> dict[str, Any]:
    body = load_data_release(runtime_root, data_release_id)
    current = _current_view(runtime_root)
    return {
        "status": "PASS",
        "requested_release": body,
        "is_current": current is not None and current["data_release_id"] == data_release_id,
        "current_view": current,
    }
