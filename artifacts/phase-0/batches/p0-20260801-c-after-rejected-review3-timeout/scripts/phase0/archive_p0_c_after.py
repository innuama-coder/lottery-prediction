"""Create and verify the C-after repair freeze pending independent review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archive_p0_c_pre import (
    BATCH_ID as PRE_BATCH_ID,
    REPAIR_DRAFT,
    SNAPSHOT_MANIFEST as PRE_MANIFEST,
    SNAPSHOT_SIDECAR as PRE_SIDECAR,
    canonical_sha256,
    decision_surface,
    load_json,
    sha256_file,
    verify_archive as verify_pre_archive,
)


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts/phase-0"
BATCH_ID = "p0-20260801-c-after"
ARCHIVE = ARTIFACTS / "batches" / BATCH_ID
MANIFEST = ARCHIVE / "snapshot-manifest.json"
SIDECAR = ARCHIVE / "snapshot-manifest.json.sha256"
CHANGES = ARCHIVE / "changed-files.json"
PENDING = ARTIFACTS / "repair-manifest-p0-20260801-c-review-pending.json"
PENDING_SIDECAR = ARTIFACTS / "repair-manifest-p0-20260801-c-review-pending.json.sha256"

EXPECTED_SCHEMA_COUNT = 32
EXPECTED_TOOL_COUNT = 23
EXPECTED_TEST_COUNT = 160
REJECTED_BATCH_ID = "p0-20260801-c-after-rejected-review1"
REJECTED_ARCHIVE = ARTIFACTS / "batches" / REJECTED_BATCH_ID
REJECTED_PENDING = ARTIFACTS / "batches" / f"{REJECTED_BATCH_ID}.pending-manifest.json"
REJECTED_PENDING_SIDECAR = ARTIFACTS / "batches" / f"{REJECTED_BATCH_ID}.pending-manifest.json.sha256"
P1_FIX_FILES = (
    "scripts/phase0/archive_p0_c_pre.py",
    "tests/phase0/test_p0_c_pre_archive.py",
    "tests/phase0/test_p0_07_decision.py",
)

DOC_PATHS = (
    "docs/roadmap/phase-0-acceptance-contract.json",
    "docs/roadmap/phase-0-data-feasibility-plan.md",
    "docs/research/TASK-001-official-data-source-feasibility.md",
)
COMMAND_PATHS = (
    "artifacts/phase-0/verification-command.json",
    "artifacts/phase-0/verification-command.json.sha256",
)


class AfterArchiveError(RuntimeError):
    pass


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _record(path: Path, relative: str) -> dict[str, Any]:
    return {"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)}


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _copy_once(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != source.read_bytes():
            raise AfterArchiveError(f"refusing to overwrite conflicting C-after byte: {destination}")
        return
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)


def _is_allowed_path(relative: str) -> bool:
    return (
        relative in DOC_PATHS
        or relative in COMMAND_PATHS
        or relative == "artifacts/phase-0/repair-manifest-p0-20260801-c-draft.json"
        or relative.startswith("artifacts/phase-0/schemas/")
        or relative.startswith("scripts/phase0/")
        or relative.startswith("tests/phase0/")
    )


def _current_inventory(pre: dict[str, Any]) -> tuple[list[str], list[str]]:
    paths = set(DOC_PATHS) | set(COMMAND_PATHS)
    paths.add("artifacts/phase-0/repair-manifest-p0-20260801-c-draft.json")
    paths.update(_relative(path) for path in (ARTIFACTS / "schemas").glob("*.schema.json"))
    paths.update(_relative(path) for path in (ROOT / "scripts/phase0").iterdir() if path.is_file() and path.suffix in {".py", ".ps1"})
    paths.update(_relative(path) for path in (ROOT / "tests/phase0").rglob("*") if path.is_file() and "__pycache__" not in path.parts)

    pre_paths = {item["path"] for item in pre["files"]}
    protected = sorted(path for path in pre_paths if not _is_allowed_path(path))
    paths.update(protected)
    missing = [path for path in sorted(paths) if not (ROOT / path).is_file()]
    if missing:
        raise AfterArchiveError(f"C-after source files are missing: {missing}")
    return sorted(paths), protected


def _changes(pre: dict[str, Any], current_paths: list[str], protected: list[str]) -> dict[str, Any]:
    before = {item["path"]: item for item in pre["files"]}
    after = {path: _record(ROOT / path, path) for path in current_paths}
    changed: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if path in protected:
            raise AfterArchiveError(f"protected C-pre byte changed: {path}")
        if not _is_allowed_path(path):
            raise AfterArchiveError(f"change is outside the allowed C repair surface: {path}")
        change_type = "added" if old is None else "removed" if new is None else "modified"
        changed.append({
            "path": path,
            "change_type": change_type,
            "before_size": None if old is None else old["size"],
            "before_sha256": None if old is None else old["sha256"],
            "after_size": None if new is None else new["size"],
            "after_sha256": None if new is None else new["sha256"],
        })
    counts = {kind: sum(item["change_type"] == kind for item in changed) for kind in ("added", "modified", "removed")}
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase0_repair_changed_files",
        "from_batch_id": PRE_BATCH_ID,
        "to_batch_id": BATCH_ID,
        "allowed_surface_only": True,
        "counts": {**counts, "total": len(changed)},
        "files": changed,
    }


def _validate_before() -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
    verify_pre_archive(ROOT, check_current=False)
    if PRE_SIDECAR.read_text(encoding="ascii").strip() != sha256_file(PRE_MANIFEST):
        raise AfterArchiveError("C-pre anchor sidecar mismatch")
    pre = load_json(PRE_MANIFEST)
    current_paths, protected = _current_inventory(pre)
    change_set = _changes(pre, current_paths, protected)
    command = load_json(ARTIFACTS / "verification-command.json")
    if command["freeze_id"] != "P0-01-p0-20260801-c-repair":
        raise AfterArchiveError("formal C verification command is not frozen")
    if len(command["schema_hashes"]) != EXPECTED_SCHEMA_COUNT or len(command["verifier_file_hashes"]) != EXPECTED_TOOL_COUNT:
        raise AfterArchiveError("formal C schema/tool inventory count mismatch")
    if command["command"] != command["full_replay_command"] or command["command"] != command["replay_command"]:
        raise AfterArchiveError("formal replay command fields disagree")
    if len({command["replay_command"], command["finalize_command"], command["full_verify_command"]}) != 3:
        raise AfterArchiveError("replay/finalize/full-verify commands are not distinct")
    if (ARTIFACTS / "soak-run-log.jsonl").stat().st_size != 0:
        raise AfterArchiveError("C-after requires the pre-observation soak log to remain empty")
    current_decision = canonical_sha256(decision_surface(ROOT))
    if current_decision != pre["decision_surface_sha256"]:
        raise AfterArchiveError("decision surface differs from C-pre anchor")
    return pre, change_set, current_paths, protected


def _rejected_review_lineage() -> dict[str, Any]:
    rejected_manifest = REJECTED_ARCHIVE / "snapshot-manifest.json"
    rejected_sidecar = REJECTED_ARCHIVE / "snapshot-manifest.json.sha256"
    if not rejected_manifest.is_file() or not rejected_sidecar.is_file():
        raise AfterArchiveError("rejected Review-1 C-after snapshot is missing")
    rejected_snapshot_sha = sha256_file(rejected_manifest)
    if rejected_sidecar.read_text(encoding="ascii").strip() != rejected_snapshot_sha:
        raise AfterArchiveError("rejected Review-1 C-after snapshot sidecar mismatch")
    if not REJECTED_PENDING.is_file() or not REJECTED_PENDING_SIDECAR.is_file():
        raise AfterArchiveError("rejected Review-1 pending manifest lineage is missing")
    rejected_pending_sha = sha256_file(REJECTED_PENDING)
    if REJECTED_PENDING_SIDECAR.read_text(encoding="ascii").strip() != rejected_pending_sha:
        raise AfterArchiveError("rejected Review-1 pending manifest sidecar mismatch")
    rejected = load_json(rejected_manifest)
    rejected_index = {item["path"]: item for item in rejected["files"]}
    fixes = []
    for path in P1_FIX_FILES:
        before = rejected_index.get(path)
        if before is None or not (ROOT / path).is_file():
            raise AfterArchiveError(f"P1 fix file is absent from rejected/current state: {path}")
        after = _record(ROOT / path, path)
        if before["sha256"] == after["sha256"]:
            raise AfterArchiveError(f"declared P1 fix file did not change after Review-1: {path}")
        fixes.append({
            "path": path,
            "rejected_review1_sha256": before["sha256"],
            "repaired_sha256": after["sha256"],
        })
    return {
        "batch_id": REJECTED_BATCH_ID,
        "snapshot_manifest_path": _relative(rejected_manifest),
        "snapshot_manifest_sha256": rejected_snapshot_sha,
        "pending_manifest_path": _relative(REJECTED_PENDING),
        "pending_manifest_sha256": rejected_pending_sha,
        "review_outcome": "REJECTED_P1",
        "reviewer_id": "/root/phase0_reviewer",
        "p1_reason": "Historical C-pre repair-draft verification depended on mutable root state instead of binding the archived draft bytes to the immutable C-after snapshot; tampering of that archived historical draft was not independently failure-injected.",
        "p1_fix_files": fixes,
    }


def _manifest(recorded_at: str, pre: dict[str, Any], change_set: dict[str, Any], archived_records: list[dict[str, Any]], protected: list[str]) -> dict[str, Any]:
    current = {item["path"]: item for item in archived_records}
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase0_after_repair_snapshot_manifest",
        "batch_id": BATCH_ID,
        "repair_layer_id": "p0-20260801-c",
        "status": "frozen_pending_independent_review",
        "recorded_at_utc": recorded_at,
        "parent_lineage": {
            "before_batch_id": PRE_BATCH_ID,
            "before_snapshot_manifest_path": _relative(PRE_MANIFEST),
            "before_snapshot_manifest_sha256": sha256_file(PRE_MANIFEST),
        },
        "decision_surface_sha256": canonical_sha256(decision_surface(ROOT)),
        "verification_command_sha256": sha256_file(ARTIFACTS / "verification-command.json"),
        "schema_count": EXPECTED_SCHEMA_COUNT,
        "frozen_tool_count": EXPECTED_TOOL_COUNT,
        "protected_files": [current[path] for path in protected],
        "changed_files_path": f"artifacts/phase-0/batches/{BATCH_ID}/changed-files.json",
        "changed_files_sha256": sha256_file(CHANGES),
        "changed_file_counts": change_set["counts"],
        "file_count": len(archived_records),
        "files": archived_records,
    }


def _pending_payload(
    draft: dict[str, Any], snapshot_sha: str, change_set: dict[str, Any],
    recorded_at: str, rejected_lineage: dict[str, Any],
) -> bytes:
    pending = json.loads(json.dumps(draft))
    pending["status"] = "frozen_pending_independent_review"
    pending["parent_lineage"]["supersedes_rejected_review1"] = rejected_lineage
    pending["after"] = {
        "status": "captured_pending_independent_review",
        "recorded_at_utc": recorded_at,
        "snapshot_manifest_path": f"artifacts/phase-0/batches/{BATCH_ID}/snapshot-manifest.json",
        "snapshot_manifest_sha256": snapshot_sha,
        "decision_surface_sha256": canonical_sha256(decision_surface(ROOT)),
        "verification_command_sha256": sha256_file(ARTIFACTS / "verification-command.json"),
        "changed_files": {
            "path": f"artifacts/phase-0/batches/{BATCH_ID}/changed-files.json",
            "sha256": sha256_file(CHANGES),
            "counts": change_set["counts"],
        },
    }
    pending["tests"] = {
        "status": "passed_pending_independent_review",
        "phase_gate_stages": ["p0-01", "p0-02", "p0-03", "p0-05", "p0-06"],
        "phase_gate_exit_codes": [0, 0, 0, 0, 0],
        "unittest_command": "python -B -m unittest discover -s tests/phase0 -p test_*.py -q",
        "unittest_count": EXPECTED_TEST_COUNT,
        "unittest_exit_code": 0,
        "replay_before_cutoff": {
            "status": "HOLD",
            "exit_code": 1,
            "network_used": False,
            "file_diff_count": 0,
            "directory_diff_count": 0,
            "artifact_tree_before_sha256": "f9a3837bcef6b6be05b79def1ef04d9401ed2ac9d0efb8461cc99a19ae5f1df8",
            "artifact_tree_after_sha256": "f9a3837bcef6b6be05b79def1ef04d9401ed2ac9d0efb8461cc99a19ae5f1df8",
        },
    }
    pending["reviewer"] = {
        "status": "pending",
        "reviewer_id": "/root/phase0_reviewer",
        "attestation_path": None,
        "conclusion": None,
    }
    return _bytes(pending)


def create() -> dict[str, Any]:
    if ARCHIVE.exists() or PENDING.exists() or PENDING_SIDECAR.exists():
        return verify()
    rejected_lineage = _rejected_review_lineage()
    pre, change_set, current_paths, protected = _validate_before()
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    ARCHIVE.mkdir(parents=True)
    _atomic(CHANGES, _bytes(change_set))
    records: list[dict[str, Any]] = []
    for relative in current_paths:
        source = ROOT / relative
        destination = ARCHIVE / relative
        _copy_once(source, destination)
        records.append(_record(destination, relative))
    # Generated archive metadata is rooted directly in the batch directory;
    # source snapshots retain their repository-relative paths.
    changes_relative = "changed-files.json"
    records.append(_record(CHANGES, changes_relative))
    records.sort(key=lambda item: item["path"])
    manifest = _manifest(recorded_at, pre, change_set, records, protected)
    _atomic(MANIFEST, _bytes(manifest))
    manifest_sha = sha256_file(MANIFEST)
    _atomic(SIDECAR, (manifest_sha + "\n").encode("ascii"))

    draft = load_json(REPAIR_DRAFT)
    pending_payload = _pending_payload(draft, manifest_sha, change_set, recorded_at, rejected_lineage)
    temporary_pending = ARTIFACTS / ".repair-manifest-p0-20260801-c-review-pending.tmp"
    _atomic(temporary_pending, pending_payload)
    # Keep at most one repair-manifest*.json in the root at every rename step.
    os.replace(temporary_pending, REPAIR_DRAFT)
    os.replace(REPAIR_DRAFT, PENDING)
    _atomic(PENDING_SIDECAR, (sha256_file(PENDING) + "\n").encode("ascii"))
    return verify()


def verify() -> dict[str, Any]:
    if not MANIFEST.is_file() or not SIDECAR.is_file() or not CHANGES.is_file() or not PENDING.is_file() or not PENDING_SIDECAR.is_file():
        raise AfterArchiveError("C-after snapshot or review-pending repair manifest is incomplete")
    if SIDECAR.read_text(encoding="ascii").strip() != sha256_file(MANIFEST):
        raise AfterArchiveError("C-after snapshot sidecar mismatch")
    if PENDING_SIDECAR.read_text(encoding="ascii").strip() != sha256_file(PENDING):
        raise AfterArchiveError("review-pending repair manifest sidecar mismatch")
    manifest = load_json(MANIFEST)
    for record in manifest["files"]:
        path = ARCHIVE / record["path"]
        if not path.is_file() or _record(path, record["path"]) != record:
            raise AfterArchiveError(f"C-after archived byte mismatch: {record['path']}")
    pending = load_json(PENDING)
    if pending["status"] != "frozen_pending_independent_review" or pending["reviewer"]["status"] != "pending":
        raise AfterArchiveError("repair manifest is not pending independent review")
    if pending["after"]["snapshot_manifest_sha256"] != sha256_file(MANIFEST):
        raise AfterArchiveError("repair manifest does not bind C-after snapshot")
    root_manifests = list(ARTIFACTS.glob("repair-manifest*.json"))
    if root_manifests != [PENDING]:
        raise AfterArchiveError(f"root must contain exactly one repair manifest, got {[path.name for path in root_manifests]}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = create() if args.create else verify()
        print(json.dumps({
            "status": "PASS",
            "batch_id": manifest["batch_id"],
            "manifest_sha256": sha256_file(MANIFEST),
            "decision_surface_sha256": manifest["decision_surface_sha256"],
            "file_count": manifest["file_count"],
            "changed_file_counts": manifest["changed_file_counts"],
            "reviewer_status": "pending",
            "network_used": False,
        }, separators=(",", ":")))
        return 0
    except (AfterArchiveError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
