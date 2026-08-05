"""Create and verify the immutable B-before-C Phase-0 repair snapshot.

The create operation is deliberately one-shot: existing archive bytes are never
overwritten.  Subsequent invocations verify the archived files, manifest
sidecar, and pending repair draft without consulting mutable post-repair files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phase0lib import canonical_sha256, load_json


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts/phase-0"
BATCH_ID = "p0-20260801-c-pre"
REPAIR_LAYER_ID = "p0-20260801-c"
ARCHIVE = ARTIFACTS / "batches" / BATCH_ID
SNAPSHOT_MANIFEST = ARCHIVE / "snapshot-manifest.json"
SNAPSHOT_SIDECAR = ARCHIVE / "snapshot-manifest.json.sha256"
REPAIR_DRAFT = ARTIFACTS / "repair-manifest-p0-20260801-c-draft.json"

EXPECTED_SCHEMA_COUNT = 21
EXPECTED_TOOL_COUNT = 9

FIXED_PATHS = (
    "docs/roadmap/phase-0-acceptance-contract.json",
    "docs/roadmap/phase-0-data-feasibility-plan.md",
    "artifacts/phase-0/batch-migration-p0-20260801-b.json",
    "artifacts/phase-0/scope-freeze.json",
    "artifacts/phase-0/observation-plan.json",
    "artifacts/phase-0/reviewer-assignment.json",
    "artifacts/phase-0/verification-command.json",
    "artifacts/phase-0/verification-command.json.sha256",
    "artifacts/phase-0/source-catalog.json",
    "artifacts/phase-0/field-contract.json",
    "artifacts/phase-0/rule-bundles.json",
    "artifacts/phase-0/clock-check-p0-04.json",
    "artifacts/phase-0/environment-lock.json",
    "artifacts/phase-0/p0-04-evidence-migration-p0-20260801-b.json",
    "artifacts/phase-0/evidence-manifest.jsonl",
    "artifacts/phase-0/p0-05-work-plan.json",
    "artifacts/phase-0/coverage-report.json",
    "artifacts/phase-0/reconciliation.jsonl",
    "artifacts/phase-0/p0-06-runtime-plan.json",
    "artifacts/phase-0/p0-06-runtime-plan.json.sha256",
    "artifacts/phase-0/p0-06-scheduler-install-audit.json",
    "artifacts/phase-0/soak-run-log.jsonl",
)

PROTECTED_PATHS = (
    "artifacts/phase-0/scope-freeze.json",
    "artifacts/phase-0/observation-plan.json",
    "artifacts/phase-0/reviewer-assignment.json",
    "artifacts/phase-0/p0-06-runtime-plan.json",
    "artifacts/phase-0/p0-06-runtime-plan.json.sha256",
    "artifacts/phase-0/p0-06-scheduler-install-audit.json",
    "artifacts/phase-0/soak-run-log.jsonl",
)

ROOT_CAUSES = (
    {
        "id": "P0-07-01",
        "summary": "The verifier did not require cutoff passage and exact 24-of-24 soak reconciliation before closeout.",
    },
    {
        "id": "P0-07-02",
        "summary": "The frozen full command validated pre-existing outputs instead of rebuilding derived outputs from a clean directory.",
    },
    {
        "id": "P0-07-03",
        "summary": "Replay, handoff, attestation, and final-gate dependencies were cyclic or chronologically ambiguous.",
    },
    {
        "id": "P0-07-04",
        "summary": "Terminal schemas and verifier checks did not bind declared hashes, evidence references, replay results, and attestations to actual artifacts.",
    },
    {
        "id": "P0-07-05",
        "summary": "Project aggregation existed, but per-game outcomes were not mechanically derived from per-game gate evidence.",
    },
)

ALLOWED_CHANGE_CLASSES = (
    "P0-07 closeout and clean-replay implementation files",
    "terminal-artifact and auxiliary closeout schema strengthening",
    "full-verifier semantic checks and failure-injection tests",
    "verification-command schema/tool inventory hashes and self sidecar",
    "new repair-lineage, closeout, replay, handoff, attestation, and acceptance artifacts produced after the repair gate",
)

FORBIDDEN_CHANGE_CLASSES = (
    "scope-freeze.json bytes or semantics",
    "observation-plan.json bytes, 24 request IDs, trigger times, cutoff, retry/rate/resource budgets, or clock tolerance",
    "reviewer-assignment.json bytes, reviewer identity, or role separation",
    "acceptance-contract hard gates, fail conditions, per-game outcome definitions, evaluation order, or project decision rules",
    "p0-06-runtime-plan.json or sidecar bytes, request mappings, network authorization, scheduler task, or installed trigger definition",
    "existing raw payload, evidence manifest, parsed record, normalized record, coverage, reconciliation, or soak bytes",
    "backfilling a missed scheduled request or representing post-cutoff collection as on-time",
)


class ArchiveError(RuntimeError):
    """Raised when the snapshot or its lineage is incomplete or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def repo_relative(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def load_command(repo_root: Path) -> dict[str, Any]:
    return load_json(repo_root / "artifacts/phase-0/verification-command.json")


def source_inventory(repo_root: Path) -> list[str]:
    command = load_command(repo_root)
    schemas = [item["path"] for item in command["schema_hashes"]]
    tools = [item["path"] for item in command["verifier_file_hashes"]]
    if len(schemas) != EXPECTED_SCHEMA_COUNT or len(set(schemas)) != EXPECTED_SCHEMA_COUNT:
        raise ArchiveError(f"expected {EXPECTED_SCHEMA_COUNT} unique frozen schemas, got {len(schemas)}/{len(set(schemas))}")
    if len(tools) != EXPECTED_TOOL_COUNT or len(set(tools)) != EXPECTED_TOOL_COUNT:
        raise ArchiveError(f"expected {EXPECTED_TOOL_COUNT} unique frozen tools, got {len(tools)}/{len(set(tools))}")

    paths = set(FIXED_PATHS) | set(schemas) | set(tools)
    for directory in ("raw", "parsed", "normalized"):
        base = repo_root / "artifacts/phase-0" / directory
        if not base.is_dir():
            raise ArchiveError(f"required evidence directory is missing: {base}")
        files = [path for path in base.rglob("*") if path.is_file()]
        if not files:
            raise ArchiveError(f"required evidence directory is empty: {base}")
        paths.update(repo_relative(path, repo_root) for path in files)

    missing = [relative for relative in sorted(paths) if not (repo_root / relative).is_file()]
    if missing:
        raise ArchiveError(f"snapshot source files are missing: {missing}")
    if (repo_root / "artifacts/phase-0/soak-run-log.jsonl").stat().st_size != 0:
        raise ArchiveError("C-pre snapshot requires the P0-06 soak log to remain empty")
    return sorted(paths)


def file_record(path: Path, relative: str) -> dict[str, Any]:
    return {"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)}


def decision_surface(repo_root: Path) -> dict[str, Any]:
    artifacts = repo_root / "artifacts/phase-0"
    contract = load_json(repo_root / "docs/roadmap/phase-0-acceptance-contract.json")
    scope = load_json(artifacts / "scope-freeze.json")
    observation = load_json(artifacts / "observation-plan.json")
    reviewer = load_json(artifacts / "reviewer-assignment.json")
    runtime = load_json(artifacts / "p0-06-runtime-plan.json")
    return {
        "contract_version": contract["version"],
        "hard_gates": contract["hard_gates"],
        "decision_logic": contract["decision_logic"],
        "scope_games": scope["games"],
        "corroboration_sample": scope["corroboration_sample"],
        "status_machine": scope["status_machine"],
        "acceptance_cutoff_utc": observation["acceptance_cutoff_utc"],
        "clock": observation["clock"],
        "request_schedule": observation["request_schedule"],
        "retry_policy": observation["retry_policy"],
        "budgets": observation["budgets"],
        "observation_games": observation["games"],
        "reviewers": reviewer["reviewers"],
        "role_separation": reviewer["role_separation"],
        "independence_declaration": reviewer["independence_declaration"],
        "runtime_requests": runtime["requests"],
        "runtime_scheduler": runtime["scheduler"],
        "runtime_network_authorization": runtime["network_authorization"],
    }


def build_snapshot_manifest(repo_root: Path, recorded_at_utc: str) -> dict[str, Any]:
    relative_paths = source_inventory(repo_root)
    records = [file_record(repo_root / relative, relative) for relative in relative_paths]
    parent_b = "artifacts/phase-0/batch-migration-p0-20260801-b.json"
    ancestor_a = "artifacts/phase-0/batches/p0-20260801-a/snapshot-manifest.json"
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase0_before_repair_snapshot_manifest",
        "batch_id": BATCH_ID,
        "repair_target_id": REPAIR_LAYER_ID,
        "recorded_at_utc": recorded_at_utc,
        "snapshot_semantics": "exact repository-relative file bytes before any C repair-layer implementation change",
        "parent_lineage": {
            "parent_repair_layer_id": "p0-20260801-b",
            "parent_manifest_path": parent_b,
            "parent_manifest_sha256": sha256_file(repo_root / parent_b),
            "ancestor_batch_id": "p0-20260801-a",
            "ancestor_snapshot_manifest_path": ancestor_a,
            "ancestor_snapshot_manifest_sha256": sha256_file(repo_root / ancestor_a),
        },
        "inventory_contract": {
            "schema_count": EXPECTED_SCHEMA_COUNT,
            "frozen_tool_count": EXPECTED_TOOL_COUNT,
            "includes_acceptance_contract_and_plan": True,
            "includes_p0_04_p0_05_evidence_chain": True,
            "includes_runtime_plan_and_scheduler_audit": True,
            "soak_log_expected_bytes": 0,
        },
        "decision_surface_sha256": canonical_sha256(decision_surface(repo_root)),
        "file_count": len(records),
        "files": records,
    }


def manifest_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = manifest["files"]
    index = {item["path"]: item for item in records}
    if len(index) != len(records):
        raise ArchiveError("snapshot manifest contains duplicate paths")
    return index


def verify_snapshot_tree(snapshot: dict[str, Any], archive_root: Path) -> dict[str, dict[str, Any]]:
    records = manifest_index(snapshot)
    if snapshot.get("file_count") != len(records):
        raise ArchiveError("snapshot file_count mismatch")
    for relative, record in records.items():
        archived = archive_root / relative
        if not archived.is_file():
            raise ArchiveError(f"archived file is missing: {relative}")
        if file_record(archived, relative) != record:
            raise ArchiveError(f"archived file differs from manifest: {relative}")
    return records


def build_repair_draft(snapshot: dict[str, Any], snapshot_sha256: str) -> dict[str, Any]:
    index = manifest_index(snapshot)
    before_hashes = {path: index[path]["sha256"] for path in sorted(index)}
    return {
        "schema_version": "1.0.0-draft",
        "artifact_type": "phase0_contract_repair_manifest_draft",
        "repair_layer_id": REPAIR_LAYER_ID,
        "status": "pending_after_implementation_tests_and_independent_review",
        "parent_lineage": {
            **snapshot["parent_lineage"],
            "before_snapshot_batch_id": BATCH_ID,
            "before_snapshot_manifest_path": f"artifacts/phase-0/batches/{BATCH_ID}/snapshot-manifest.json",
            "before_snapshot_manifest_sha256": snapshot_sha256,
        },
        "root_causes": list(ROOT_CAUSES),
        "allowed_change_classes": list(ALLOWED_CHANGE_CLASSES),
        "forbidden_change_classes": list(FORBIDDEN_CHANGE_CLASSES),
        "before": {
            "status": "captured",
            "recorded_at_utc": snapshot["recorded_at_utc"],
            "decision_surface_sha256": snapshot["decision_surface_sha256"],
            "protected_file_hashes": {path: before_hashes[path] for path in PROTECTED_PATHS},
            "all_archived_file_hashes": before_hashes,
            "schema_count": snapshot["inventory_contract"]["schema_count"],
            "frozen_tool_count": snapshot["inventory_contract"]["frozen_tool_count"],
            "soak_log_bytes": index["artifacts/phase-0/soak-run-log.jsonl"]["size"],
        },
        "after": {
            "status": "pending",
            "snapshot_manifest_path": None,
            "snapshot_manifest_sha256": None,
            "decision_surface_sha256": None,
            "changed_files": None,
        },
        "tests": {"status": "pending", "command": None, "exit_code": None, "results": None},
        "reviewer": {
            "status": "pending",
            "reviewer_id": "/root/phase0_reviewer",
            "attestation_path": None,
            "conclusion": None,
        },
    }


def copy_once(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or source.read_bytes() != destination.read_bytes():
            raise ArchiveError(f"refusing to overwrite conflicting archive file: {destination}")
        return
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)


def write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ArchiveError(f"refusing to overwrite conflicting immutable file: {path}")
        return
    with path.open("xb") as handle:
        handle.write(payload)


def create_archive(repo_root: Path = ROOT, recorded_at_utc: str | None = None) -> dict[str, Any]:
    if SNAPSHOT_MANIFEST.exists() or SNAPSHOT_SIDECAR.exists() or REPAIR_DRAFT.exists():
        verify_archive(repo_root, check_current=True)
        return load_json(SNAPSHOT_MANIFEST)
    recorded = recorded_at_utc or datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    snapshot = build_snapshot_manifest(repo_root, recorded)
    for record in snapshot["files"]:
        relative = record["path"]
        copy_once(repo_root / relative, ARCHIVE / relative)
    manifest_payload = json_bytes(snapshot)
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    write_once(SNAPSHOT_MANIFEST, manifest_payload)
    write_once(SNAPSHOT_SIDECAR, (manifest_hash + "\n").encode("ascii"))
    write_once(REPAIR_DRAFT, json_bytes(build_repair_draft(snapshot, manifest_hash)))
    verify_archive(repo_root, check_current=True)
    return snapshot


def verify_archive(repo_root: Path = ROOT, *, check_current: bool = False) -> dict[str, Any]:
    if not SNAPSHOT_MANIFEST.is_file() or not SNAPSHOT_SIDECAR.is_file() or not REPAIR_DRAFT.is_file():
        raise ArchiveError("C-pre snapshot manifest, sidecar, or repair draft is missing")
    expected_sidecar = sha256_file(SNAPSHOT_MANIFEST)
    sidecar_text = SNAPSHOT_SIDECAR.read_text(encoding="ascii").strip()
    if sidecar_text != expected_sidecar:
        raise ArchiveError("snapshot manifest self-hash sidecar mismatch")
    snapshot = load_json(SNAPSHOT_MANIFEST)
    if snapshot.get("batch_id") != BATCH_ID or snapshot.get("repair_target_id") != REPAIR_LAYER_ID:
        raise ArchiveError("snapshot batch identity mismatch")
    records = verify_snapshot_tree(snapshot, ARCHIVE)
    for relative, record in records.items():
        if check_current:
            current = repo_root / relative
            if not current.is_file() or file_record(current, relative) != record:
                raise ArchiveError(f"current before-repair file differs from snapshot: {relative}")
    draft = load_json(REPAIR_DRAFT)
    if draft.get("status") != "pending_after_implementation_tests_and_independent_review":
        raise ArchiveError("repair draft is not explicitly pending")
    if draft.get("after", {}).get("status") != "pending" or draft.get("tests", {}).get("status") != "pending" or draft.get("reviewer", {}).get("status") != "pending":
        raise ArchiveError("repair draft after/tests/reviewer states must remain pending")
    if draft["parent_lineage"]["before_snapshot_manifest_sha256"] != expected_sidecar:
        raise ArchiveError("repair draft does not bind the snapshot manifest")
    archived_hashes = {path: record["sha256"] for path, record in sorted(records.items())}
    if draft["before"]["all_archived_file_hashes"] != archived_hashes:
        raise ArchiveError("repair draft before hashes differ from the snapshot")
    if draft["before"]["soak_log_bytes"] != 0:
        raise ArchiveError("repair draft does not prove an empty pre-repair soak log")
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true", help="create the one-shot archive and pending repair draft")
    parser.add_argument("--check-current", action="store_true", help="also compare current files to the before snapshot")
    parser.add_argument("--recorded-at-utc", help="fixed RFC3339 UTC timestamp for deterministic fixture creation")
    args = parser.parse_args(argv)
    try:
        snapshot = create_archive(ROOT, args.recorded_at_utc) if args.create else verify_archive(ROOT, check_current=args.check_current)
        print(json.dumps({
            "status": "PASS",
            "action": "create" if args.create else "verify",
            "batch_id": snapshot["batch_id"],
            "file_count": snapshot["file_count"],
            "manifest_sha256": sha256_file(SNAPSHOT_MANIFEST),
            "decision_surface_sha256": snapshot["decision_surface_sha256"],
            "network_used": False,
        }, separators=(",", ":")))
        return 0
    except (ArchiveError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
