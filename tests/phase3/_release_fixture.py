"""Shared test fixture: synthesize a minimal W07-authorized formal release.

The real formal pipeline cannot be exercised inside the unit suite because W06
qualification (2,000 replications) and the full W08 run are far too slow. To prove
the W08 resume, W10 reconstruction, W11 E2E and W13 handoff fixes end-to-end we
need a release tree whose frozen authorization layer
(``validate_authorization``) accepts the current implementation. This module
rebuilds exactly that layer (release-control, formal authorization, a hand-bound
W07 receipt, a PASS readiness receipt and a fresh implementation inventory) over
an otherwise empty release directory so the W08 ``run`` command can execute
against the real frozen data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lottery_research.phase3.serialization import canonical_sha256, sha256_file, write_new_json

PREP_ROLES = ("data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer")
FORMAL_ROLES = (
    "data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer",
    "run_operator", "independent_reviewer", "acceptance_engineer", "classification_approver", "release_controller",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _actor_assignment(path: Path, *, stage: str, roles: tuple[str, ...], parent_sha: str | None, controller_id: str, assignment_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    records_dir = path.parent / "task-records"
    records_dir.mkdir(parents=True, exist_ok=True)
    assignments = []
    by_role: dict[str, dict[str, Any]] = {}
    for role in roles:
        record = records_dir / f"{role}.json"
        record.write_text(json.dumps({"role": role, "controller_id": controller_id}, sort_keys=True), encoding="utf-8")
        row = {
            "role": role, "actor_id": f"actor-{role}", "task_id": f"task-{role}", "session_id": f"session-{role}",
            "assigned_at_utc": "2026-08-09T00:00:00Z", "assigned_by": controller_id,
            "task_record_path": f"task-records/{role}.json", "task_record_sha256": sha256_file(record),
        }
        assignments.append(row)
        by_role[role] = row
    payload = {
        "schema_version": "3.0.0", "artifact_type": "phase3_actor_assignment", "assignment_id": assignment_id,
        "assignment_stage": stage, "parent_assignment_sha256": parent_sha, "controller_id": controller_id,
        "created_at_utc": "2026-08-09T00:00:00Z", "assignments": assignments,
    }
    write_new_json(path, payload)
    return payload, by_role


def _receipt(root: Path, *, work_item: str, identity: str, owner_role: str, owner: dict[str, Any],
             actor_assignment_sha256: str, inputs: list[Path], outputs: list[Path], terminal: str) -> dict[str, Any]:
    def row(p: Path) -> dict[str, str]:
        return {"path": str(p.resolve()), "sha256": sha256_file(p)}
    return {
        "schema_version": "3.0.0", "artifact_type": "phase3_work_item_receipt", "work_item": work_item,
        "identity": identity, "actor_assignment_sha256": actor_assignment_sha256, "owner_role": owner_role,
        "owner_id": owner["actor_id"], "owner_task_id": owner["task_id"], "owner_session_id": owner["session_id"],
        "inputs": [row(p) for p in inputs], "outputs": [row(p) for p in outputs],
        "command": ["python3", "-m", "lottery_research.phase3", work_item.lower()], "started_at_utc": _utc_now(),
        "ended_at_utc": _utc_now(), "process_exit_code": 0, "status": "PASS", "terminal": terminal,
    }


def build_authorized_release(root: Path, release_dir: Path, *, readiness_identity: str = "fixture-W07", controller_id: str = "controller") -> Path:
    """Create release_dir with a frozen authorization layer the W08 run accepts."""

    release_dir.mkdir(parents=True, exist_ok=True)
    control = release_dir / "control"
    control.mkdir(parents=True, exist_ok=True)
    contracts = release_dir / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    import shutil
    for source in sorted((root / "config/phase3").glob("*.json")):
        shutil.copyfile(source, contracts / source.name)
    prep_actor_path = control / "actor-assignments-preparation.json"
    prep_payload, _ = _actor_assignment(
        prep_actor_path, stage="preparation", roles=PREP_ROLES, parent_sha=None,
        controller_id=controller_id, assignment_id=f"{release_dir.name}-prep-actors",
    )
    formal_actor_path = control / "actor-assignments-formal.json"
    formal_payload, formal_by_role = _actor_assignment(
        formal_actor_path, stage="formal_before_W07", roles=FORMAL_ROLES, parent_sha=sha256_file(prep_actor_path),
        controller_id=controller_id, assignment_id=f"{release_dir.name}-formal-actors",
    )
    formal_actor_sha = sha256_file(formal_actor_path)
    write_new_json(control / "formal-run-registry.json", {
        "schema_version": "3.0.0", "artifact_type": "phase3_formal_run_registry", "release_id": release_dir.name, "experiments": [],
    })
    write_new_json(control / "approved-workload.json", {
        "schema_version": "3.0.0", "artifact_type": "phase3_approved_workload", "release_id": release_dir.name,
        "max_attempts_per_experiment": 2, "logical_experiments": 600,
    })
    write_new_json(control / "artifact-whitelist.json", {
        "schema_version": "3.0.0", "artifact_type": "phase3_artifact_whitelist", "release_id": release_dir.name,
        "explicit_roots": ["control", "readiness", "runs", "evaluation", "replay", "review", "e2e", "reports", "manifest", "acceptance", "handoff", "handoff-validation", "work-items"],
        "commands": ["run"], "network_policy": "disabled_no_network_inputs_W08_W13",
    })
    readiness_dir = release_dir / "readiness"
    readiness_dir.mkdir(parents=True, exist_ok=True)
    readiness_path = readiness_dir / "readiness.json"
    write_new_json(readiness_path, {
        "schema_version": "3.0.0", "artifact_type": "phase3_readiness_receipt", "identity": readiness_identity,
        "release_id": release_dir.name, "status": "PASS", "terminal": "READY", "formal_run_authorized": True,
        "code_hash_match_rate": 1.0, "input_hash_match_rate": 1.0, "dependency_hash_match_rate": 1.0,
    })
    implementation_paths: list[Path] = []
    for relative in ("src/lottery_research/phase3", "scripts/phase3", "schemas/phase3", "config/phase3"):
        implementation_paths.extend(path for path in (root / relative).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    for relative in ("requirements/phase3.lock", "tasks/phase3/README.md", "docs/research/phase-3-overall-design.md", "docs/plans/phase-3-detailed-plan.md", "docs/runbooks/phase-3-historical-research-runtime.md"):
        implementation_paths.append(root / relative)
    inventory_rows = [{"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size} for path in sorted(set(implementation_paths))]
    # The fixture's release tree carries no Git metadata: independent acceptance
    # checks copy the source tree without .git, so ``git rev-parse HEAD`` is
    # unavailable. Derive a stable synthetic implementation_freeze_commit from
    # the canonical inventory hash so the frozen authorization layer is
    # reproducible without Git. (Production W07/readiness still binds formal
    # releases to the real Git commit; only this fixture is Git-free.)
    inventory_sha = canonical_sha256(inventory_rows)
    freeze_commit = hashlib.sha1(inventory_sha.encode("ascii")).hexdigest()
    write_new_json(control / "implementation-inventory.json", {
        "release_id": release_dir.name, "implementation_freeze_commit": freeze_commit,
        "files": inventory_rows, "inventory_sha256": inventory_sha,
    })
    write_new_json(control / "formal-authorization.json", {
        "schema_version": "3.0.0", "artifact_type": "phase3_formal_authorization", "release_id": release_dir.name,
        "readiness_identity": readiness_identity, "formal_run_authorized": True,
        "readiness_sha256": sha256_file(readiness_path), "implementation_freeze_commit": freeze_commit,
    })
    work_items_dir = release_dir / "work-items"
    w06_dir = work_items_dir / "W06"
    w06_dir.mkdir(parents=True, exist_ok=True)
    w06_receipt_path = w06_dir / "receipt.json"
    write_new_json(w06_receipt_path, _receipt(
        root, work_item="W06", identity=f"{release_dir.name}-fixture-W06", owner_role="independent_method_reviewer",
        owner=formal_by_role["independent_method_reviewer"], actor_assignment_sha256=formal_actor_sha,
        inputs=[prep_actor_path, formal_actor_path], outputs=[control / "approved-workload.json"], terminal="PASS",
    ))
    w07_dir = work_items_dir / "W07"
    w07_dir.mkdir(parents=True, exist_ok=True)
    w07_outputs = [control / "formal-run-registry.json", control / "approved-workload.json", control / "artifact-whitelist.json", control / "formal-authorization.json", control / "implementation-inventory.json"]
    write_new_json(w07_dir / "receipt.json", _receipt(
        root, work_item="W07", identity=f"{release_dir.name}-fixture-W07", owner_role="release_controller",
        owner=formal_by_role["release_controller"], actor_assignment_sha256=formal_actor_sha,
        inputs=[formal_actor_path, prep_actor_path, w06_receipt_path], outputs=w07_outputs, terminal="READY",
    ))
    write_new_json(control / "release-control.json", {
        "schema_version": "3.0.0", "artifact_type": "phase3_release_control", "release_id": release_dir.name,
        "prep_id": "fixture-prep", "implementation_freeze_commit": freeze_commit, "actor_assignment_sha256": formal_actor_sha,
        "input_manifest_sha256": sha256_file(root / "config/phase3/input-manifest.json"),
        "preregistration_sha256": sha256_file(root / "config/phase3/preregistration.json"),
        "model_registry_sha256": sha256_file(root / "config/phase3/model-registry.json"),
        "feature_registry_sha256": sha256_file(root / "config/phase3/feature-registry.json"),
        "created_at_utc": _utc_now(), "formal_result_count_at_creation": 0,
        "formal_network_policy": "disabled_no_network_inputs_W08_W13",
    })
    return release_dir
