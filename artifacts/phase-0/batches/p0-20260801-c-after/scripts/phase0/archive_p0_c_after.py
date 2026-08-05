"""Create and verify the C-after repair freeze pending independent review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
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
from validate_p0_c_freeze import (
    RECORD as FREEZE_VALIDATION,
    SIDECAR as FREEZE_VALIDATION_SIDECAR,
    load_and_validate as load_and_validate_freeze,
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
REVIEW2_INCIDENT = ARTIFACTS / "batches/p0-20260801-c-after-review2-contamination/incident.json"
REJECTED_REVIEW3_BATCH_ID = "p0-20260801-c-after-rejected-review3-timeout"
REJECTED_REVIEW3_ARCHIVE = ARTIFACTS / "batches" / REJECTED_REVIEW3_BATCH_ID
REJECTED_REVIEW3_PENDING = ARTIFACTS / "batches" / f"{REJECTED_REVIEW3_BATCH_ID}.pending-manifest.json"
REJECTED_REVIEW3_PENDING_SIDECAR = ARTIFACTS / "batches" / f"{REJECTED_REVIEW3_BATCH_ID}.pending-manifest.json.sha256"
REJECTED_REVIEW3_SNAPSHOT_SHA256 = "932df819b68189b6324fd53513b3deab8b50df2b548fb0d4eb1e92517f67f178"
REVIEW3_FIX_FILE = "tests/phase0/test_p0_06.py"
SUPERSEDED_VALIDATION_BATCH_ID = "p0-20260801-c-after-superseded-hardcoded-validation"
SUPERSEDED_VALIDATION_ARCHIVE = ARTIFACTS / "batches" / SUPERSEDED_VALIDATION_BATCH_ID
SUPERSEDED_VALIDATION_PENDING = ARTIFACTS / "batches" / f"{SUPERSEDED_VALIDATION_BATCH_ID}.pending-manifest.json"
SUPERSEDED_VALIDATION_PENDING_SIDECAR = ARTIFACTS / "batches" / f"{SUPERSEDED_VALIDATION_BATCH_ID}.pending-manifest.json.sha256"
SUPERSEDED_VALIDATION_INCIDENT = ARTIFACTS / "batches" / f"{SUPERSEDED_VALIDATION_BATCH_ID}.incident.json"
SUPERSEDED_VALIDATION_SNAPSHOT_SHA256 = "b73fe82cfa4dae0e36c49c904fac49a26568fc5a87656faec009d3d7ee0886e1"
SUPERSEDED_VALIDATION_PENDING_SHA256 = "745993ff7f0121e6b8c82167ab9eafa7b3ebd74cbf45fc2f81c50d01d83c3aeb"
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
VALIDATION_PATHS = (
    "artifacts/phase-0/freeze-validation-p0-20260801-c.json",
    "artifacts/phase-0/freeze-validation-p0-20260801-c.json.sha256",
)
SUPERSESSION_INCIDENT_PATH = "artifacts/phase-0/batches/p0-20260801-c-after-superseded-hardcoded-validation.incident.json"


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
        or relative in VALIDATION_PATHS
        or relative == SUPERSESSION_INCIDENT_PATH
        or relative == "artifacts/phase-0/repair-manifest-p0-20260801-c-draft.json"
        or relative.startswith("artifacts/phase-0/schemas/")
        or relative.startswith("scripts/phase0/")
        or relative.startswith("tests/phase0/")
    )


def _current_inventory(pre: dict[str, Any]) -> tuple[list[str], list[str]]:
    paths = set(DOC_PATHS) | set(COMMAND_PATHS) | set(VALIDATION_PATHS) | {SUPERSESSION_INCIDENT_PATH}
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


def _review2_contamination_lineage() -> dict[str, Any]:
    if not REVIEW2_INCIDENT.is_file():
        raise AfterArchiveError("Review-2 contamination incident is missing")
    incident = load_json(REVIEW2_INCIDENT)
    if (
        incident.get("incident_id") != "p0-20260801-c-after-review2-contamination"
        or incident.get("review_outcome") != "INVALIDATED_NO_PASS_ARTIFACT"
        or incident.get("scheduled_task_mutated") is not False
    ):
        raise AfterArchiveError("Review-2 contamination incident semantics mismatch")
    affected = ROOT / incident["affected_path"]
    if not affected.is_file() or sha256_file(affected) != incident["restored_sha256"]:
        raise AfterArchiveError("Review-2 contaminated protected artifact was not restored")
    polluted = ROOT / incident["polluted_copy_path"]
    if not polluted.is_file() or sha256_file(polluted) != incident["polluted_sha256"]:
        raise AfterArchiveError("Review-2 polluted evidence copy is missing or changed")
    return {
        "incident_id": incident["incident_id"],
        "incident_path": _relative(REVIEW2_INCIDENT),
        "incident_sha256": sha256_file(REVIEW2_INCIDENT),
        "reviewer_id": incident["reviewer_id"],
        "review_outcome": incident["review_outcome"],
        "affected_path": incident["affected_path"],
        "polluted_sha256": incident["polluted_sha256"],
        "restored_sha256": incident["restored_sha256"],
        "scheduled_task_mutated": False,
    }


def _rejected_review3_timeout_lineage() -> dict[str, Any]:
    rejected_manifest = REJECTED_REVIEW3_ARCHIVE / "snapshot-manifest.json"
    rejected_sidecar = REJECTED_REVIEW3_ARCHIVE / "snapshot-manifest.json.sha256"
    if not rejected_manifest.is_file() or not rejected_sidecar.is_file():
        raise AfterArchiveError("rejected Reviewer3 timeout C-after snapshot is missing")
    rejected_snapshot_sha = sha256_file(rejected_manifest)
    if rejected_snapshot_sha != REJECTED_REVIEW3_SNAPSHOT_SHA256:
        raise AfterArchiveError("rejected Reviewer3 timeout snapshot identity mismatch")
    if rejected_sidecar.read_text(encoding="ascii").strip() != rejected_snapshot_sha:
        raise AfterArchiveError("rejected Reviewer3 timeout snapshot sidecar mismatch")
    if not REJECTED_REVIEW3_PENDING.is_file() or not REJECTED_REVIEW3_PENDING_SIDECAR.is_file():
        raise AfterArchiveError("rejected Reviewer3 timeout pending manifest lineage is missing")
    rejected_pending_sha = sha256_file(REJECTED_REVIEW3_PENDING)
    if REJECTED_REVIEW3_PENDING_SIDECAR.read_text(encoding="ascii").strip() != rejected_pending_sha:
        raise AfterArchiveError("rejected Reviewer3 timeout pending manifest sidecar mismatch")

    rejected = load_json(rejected_manifest)
    rejected_index = {item["path"]: item for item in rejected["files"]}
    before = rejected_index.get(REVIEW3_FIX_FILE)
    after_path = ROOT / REVIEW3_FIX_FILE
    if before is None or not after_path.is_file():
        raise AfterArchiveError("Reviewer3 timeout fix file is missing from rejected/current state")
    after = _record(after_path, REVIEW3_FIX_FILE)
    if before["sha256"] == after["sha256"]:
        raise AfterArchiveError("Reviewer3 timeout fix file did not change")
    if before["sha256"] != "cb7e1aa3c8f332ba48833016fd7d9e2207e93855b87647d3e8807ae4008d00d5":
        raise AfterArchiveError("Reviewer3 timeout fix before-hash mismatch")
    if after["sha256"] != "3cad38de71acdfa8636125f4bacfbe5837928344828b5c7b8a160fbee341d6f3":
        raise AfterArchiveError("Reviewer3 timeout fix after-hash mismatch")

    return {
        "batch_id": REJECTED_REVIEW3_BATCH_ID,
        "snapshot_manifest_path": _relative(rejected_manifest),
        "snapshot_manifest_sha256": rejected_snapshot_sha,
        "pending_manifest_path": _relative(REJECTED_REVIEW3_PENDING),
        "pending_manifest_sha256": rejected_pending_sha,
        "review_outcome": "REJECTED_TEST_FLAKE",
        "reviewer_id": "/root/phase0_reviewer3",
        "reason": "The full 160-test review run passed 159 tests but one Windows Task Scheduler verification subprocess exceeded its 30-second test-harness timeout; the production verifier, frozen command, scheduled task, protected artifacts, and decision surface did not change.",
        "observed_runs": {
            "initial_full_suite": {"passed": 159, "timeouts": 1},
            "focused_pre_fix_seconds": 29.989,
            "test_timeout_seconds": {"before": 30, "after": 60},
            "focused_post_fix_seconds": [24.073, 43.376],
            "p0_06_tests_after_fix": {"passed": 32, "timeouts": 0},
            "full_suite_after_fix": {"passed": 160, "timeouts": 0},
        },
        "p1_fix_files": [{
            "path": REVIEW3_FIX_FILE,
            "rejected_review3_sha256": before["sha256"],
            "repaired_sha256": after["sha256"],
        }],
    }


def _superseded_hardcoded_validation_lineage() -> dict[str, Any]:
    snapshot = SUPERSEDED_VALIDATION_ARCHIVE / "snapshot-manifest.json"
    snapshot_sidecar = SUPERSEDED_VALIDATION_ARCHIVE / "snapshot-manifest.json.sha256"
    required = (
        snapshot, snapshot_sidecar, SUPERSEDED_VALIDATION_PENDING,
        SUPERSEDED_VALIDATION_PENDING_SIDECAR, SUPERSEDED_VALIDATION_INCIDENT,
    )
    if not all(path.is_file() for path in required):
        raise AfterArchiveError("superseded hardcoded-validation lineage is incomplete")
    snapshot_sha = sha256_file(snapshot)
    pending_sha = sha256_file(SUPERSEDED_VALIDATION_PENDING)
    if snapshot_sha != SUPERSEDED_VALIDATION_SNAPSHOT_SHA256:
        raise AfterArchiveError("superseded hardcoded-validation snapshot identity mismatch")
    if snapshot_sidecar.read_text(encoding="ascii").strip() != snapshot_sha:
        raise AfterArchiveError("superseded hardcoded-validation snapshot sidecar mismatch")
    if pending_sha != SUPERSEDED_VALIDATION_PENDING_SHA256:
        raise AfterArchiveError("superseded hardcoded-validation pending identity mismatch")
    if SUPERSEDED_VALIDATION_PENDING_SIDECAR.read_text(encoding="ascii").strip() != pending_sha:
        raise AfterArchiveError("superseded hardcoded-validation pending sidecar mismatch")
    incident = load_json(SUPERSEDED_VALIDATION_INCIDENT)
    expected = {
        "incident_id": SUPERSEDED_VALIDATION_BATCH_ID,
        "review_outcome": "SUPERSEDED_PRE_REVIEW_NO_PASS_ARTIFACT",
        "reviewer_pass_artifact_created": False,
        "preserved_snapshot_manifest_sha256": snapshot_sha,
        "preserved_pending_manifest_sha256": pending_sha,
        "scheduled_task_mutated": False,
        "protected_runtime_mutated": False,
        "decision_surface_mutated": False,
    }
    for key, value in expected.items():
        if incident.get(key) != value:
            raise AfterArchiveError(f"superseded hardcoded-validation incident mismatch: {key}")
    old_manifest = load_json(snapshot)
    old_index = {item["path"]: item for item in old_manifest["files"]}
    old_generator = old_index.get("scripts/phase0/archive_p0_c_after.py")
    if old_generator is None or old_generator["sha256"] != incident["affected_generator"]["preserved_snapshot_sha256"]:
        raise AfterArchiveError("superseded hardcoded generator byte is not bound by its snapshot")
    return {
        "batch_id": SUPERSEDED_VALIDATION_BATCH_ID,
        "snapshot_manifest_path": _relative(snapshot),
        "snapshot_manifest_sha256": snapshot_sha,
        "pending_manifest_path": _relative(SUPERSEDED_VALIDATION_PENDING),
        "pending_manifest_sha256": pending_sha,
        "incident_path": _relative(SUPERSEDED_VALIDATION_INCIDENT),
        "incident_sha256": sha256_file(SUPERSEDED_VALIDATION_INCIDENT),
        "review_outcome": incident["review_outcome"],
        "reviewer_pass_artifact_created": False,
        "root_cause": incident["root_cause"],
        "corrective_action": incident["corrective_action"],
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


def _pending_tests_from_validation(validation: dict[str, Any]) -> dict[str, Any]:
    validation_sha = sha256_file(FREEZE_VALIDATION)
    processes = validation["processes"]
    phase_processes = [item for item in processes if item["role"].startswith("phase_gate_")]
    unittest_process = next(item for item in processes if item["role"] == "full_unittest_discover")
    replay_process = next(item for item in processes if item["role"] == "pre_cutoff_replay_launcher")
    tree = validation["operational_tree"]
    return {
        "status": "passed_pending_independent_review",
        "evidence_semantics": validation["evidence_semantics"],
        "validation_record_path": _relative(FREEZE_VALIDATION),
        "validation_record_sha256": validation_sha,
        "validation_sidecar_path": _relative(FREEZE_VALIDATION_SIDECAR),
        "validation_sidecar_sha256": sha256_file(FREEZE_VALIDATION_SIDECAR),
        "phase_gate_stages": [item["stage"] for item in validation["phase_gates"]],
        "phase_gate_exit_codes": [item["exit_code"] for item in phase_processes],
        "phase_gate_process_refs": [f"{_relative(FREEZE_VALIDATION)}#/processes/{processes.index(item)}" for item in phase_processes],
        "unittest_argv": unittest_process["argv"],
        "unittest_command": subprocess.list2cmdline(unittest_process["argv"]),
        "unittest_count": validation["full_unittest"]["observed_test_count"],
        "unittest_exit_code": unittest_process["exit_code"],
        "unittest_process_ref": f"{_relative(FREEZE_VALIDATION)}#/processes/{processes.index(unittest_process)}",
        "replay_before_cutoff": {
            "status": validation["pre_cutoff_replay"]["status"],
            "exit_code": replay_process["exit_code"],
            "network_used": validation["pre_cutoff_replay"]["network_used"],
            "process_ref": f"{_relative(FREEZE_VALIDATION)}#/processes/{processes.index(replay_process)}",
            "file_diff_count": tree["file_diff_count"],
            "directory_diff_count": tree["directory_diff_count"],
            "artifact_tree_before_sha256": tree["before"]["root_sha256"],
            "artifact_tree_after_sha256": tree["after"]["root_sha256"],
        },
        "scheduler_live_state": validation["scheduler_live_state"],
        "protected_state_unchanged": validation["protected_state"]["unchanged"],
    }


def _validate_validation_chronology(validation: dict[str, Any], snapshot_recorded_at: str) -> None:
    completed = datetime.fromisoformat(validation["completed_at_utc"].replace("Z", "+00:00"))
    recorded = datetime.fromisoformat(snapshot_recorded_at.replace("Z", "+00:00"))
    runtime = load_json(ARTIFACTS / "p0-06-runtime-plan.json")
    cutoff = datetime.fromisoformat(runtime["acceptance_cutoff_utc"].replace("Z", "+00:00"))
    if completed.tzinfo is None or not (completed <= recorded < cutoff):
        raise AfterArchiveError("freeze validation / C-after snapshot chronology must be completed <= recorded < cutoff")


def _pending_payload(
    draft: dict[str, Any], snapshot_sha: str, change_set: dict[str, Any],
    recorded_at: str, rejected_lineage: dict[str, Any],
    review2_incident: dict[str, Any], rejected_review3: dict[str, Any],
    superseded_validation: dict[str, Any],
) -> bytes:
    validation = load_and_validate_freeze(
        FREEZE_VALIDATION, FREEZE_VALIDATION_SIDECAR, compare_current=True,
    )
    validation_sha = sha256_file(FREEZE_VALIDATION)
    if FREEZE_VALIDATION_SIDECAR.read_text(encoding="ascii").strip() != validation_sha:
        raise AfterArchiveError("freeze validation sidecar changed after strict validation")
    _validate_validation_chronology(validation, recorded_at)
    pending = json.loads(json.dumps(draft))
    pending["status"] = "frozen_pending_independent_review"
    pending["parent_lineage"]["supersedes_rejected_review1"] = rejected_lineage
    pending["parent_lineage"]["invalidated_review2_contamination"] = review2_incident
    pending["parent_lineage"]["supersedes_rejected_review3_timeout"] = rejected_review3
    pending["parent_lineage"]["supersedes_hardcoded_validation_freeze"] = superseded_validation
    if not any(item.get("id") == "P0-C-FREEZE-01" for item in pending["root_causes"]):
        pending["root_causes"].append({
            "id": "P0-C-FREEZE-01",
            "summary": "The C-after generator hardcoded observed validation outcomes instead of consuming an independently recorded execution observation.",
        })
    validation_change = "C-after actual-observation validation record, strict governance validator, mechanical pending projection, and immutable supersession lineage"
    if validation_change not in pending["allowed_change_classes"]:
        pending["allowed_change_classes"].append(validation_change)
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
    pending["tests"] = _pending_tests_from_validation(validation)
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
    # No archive or repair-manifest byte is written until the actual-observation
    # record and its commit marker have passed the strict semantic validator.
    validation = load_and_validate_freeze(
        FREEZE_VALIDATION, FREEZE_VALIDATION_SIDECAR, compare_current=True,
    )
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    _validate_validation_chronology(validation, recorded_at)
    rejected_lineage = _rejected_review_lineage()
    review2_incident = _review2_contamination_lineage()
    rejected_review3 = _rejected_review3_timeout_lineage()
    superseded_validation = _superseded_hardcoded_validation_lineage()
    archived_draft = REJECTED_REVIEW3_ARCHIVE / _relative(REPAIR_DRAFT)
    if not archived_draft.is_file():
        raise AfterArchiveError("Reviewer3 rejected snapshot does not contain the historical repair draft")
    _copy_once(archived_draft, REPAIR_DRAFT)
    pre, change_set, current_paths, protected = _validate_before()
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
    pending_payload = _pending_payload(
        draft, manifest_sha, change_set, recorded_at,
        rejected_lineage, review2_incident, rejected_review3, superseded_validation,
    )
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
    manifest_index = {record["path"]: record for record in manifest["files"]}
    if len(manifest_index) != len(manifest["files"]):
        raise AfterArchiveError("C-after snapshot contains duplicate file paths")
    for record in manifest["files"]:
        path = ARCHIVE / record["path"]
        if not path.is_file() or _record(path, record["path"]) != record:
            raise AfterArchiveError(f"C-after archived byte mismatch: {record['path']}")
    pending = load_json(PENDING)
    if pending["status"] != "frozen_pending_independent_review" or pending["reviewer"]["status"] != "pending":
        raise AfterArchiveError("repair manifest is not pending independent review")
    if pending["after"]["snapshot_manifest_sha256"] != sha256_file(MANIFEST):
        raise AfterArchiveError("repair manifest does not bind C-after snapshot")
    if not FREEZE_VALIDATION.is_file() or not FREEZE_VALIDATION_SIDECAR.is_file():
        raise AfterArchiveError("freeze validation record and sidecar are mandatory")
    validation = load_and_validate_freeze(
        FREEZE_VALIDATION, FREEZE_VALIDATION_SIDECAR, compare_current=True,
    )
    for relative, path in zip(VALIDATION_PATHS, (FREEZE_VALIDATION, FREEZE_VALIDATION_SIDECAR), strict=True):
        archived_record = manifest_index.get(relative)
        if archived_record is None:
            raise AfterArchiveError(f"C-after snapshot does not bind freeze validation byte: {relative}")
        if _record(path, relative) != archived_record or (ARCHIVE / relative).read_bytes() != path.read_bytes():
            raise AfterArchiveError(f"current/archived freeze validation byte mismatch: {relative}")
    _validate_validation_chronology(validation, manifest["recorded_at_utc"])
    if pending.get("tests") != _pending_tests_from_validation(validation):
        raise AfterArchiveError("pending tests/evidence subtree is not a mechanical deep projection of freeze validation")
    if pending.get("after", {}).get("recorded_at_utc") != manifest["recorded_at_utc"]:
        raise AfterArchiveError("pending/snapshot recorded_at chronology differs")
    lineage = pending["parent_lineage"]
    if lineage.get("supersedes_rejected_review3_timeout", {}).get("review_outcome") != "REJECTED_TEST_FLAKE":
        raise AfterArchiveError("repair manifest does not bind Reviewer3 timeout rejection")
    if lineage.get("invalidated_review2_contamination", {}).get("review_outcome") != "INVALIDATED_NO_PASS_ARTIFACT":
        raise AfterArchiveError("repair manifest does not retain Review-2 contamination lineage")
    superseded = lineage.get("supersedes_hardcoded_validation_freeze", {})
    if superseded.get("review_outcome") != "SUPERSEDED_PRE_REVIEW_NO_PASS_ARTIFACT" or superseded.get("reviewer_pass_artifact_created") is not False:
        raise AfterArchiveError("repair manifest does not retain hardcoded-validation supersession lineage")
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
