from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lottery_research.phase3.serialization import canonical_json_bytes, sha256_file, write_new_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--prep-root", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--assignment", action="append", required=True, help="role|actor_id|task_id|session_id|source_task_record")
    args = parser.parse_args()
    root, release, prep = args.project_root.resolve(), args.release_root.resolve(), args.prep_root.resolve()
    release.mkdir(parents=True, exist_ok=False)
    control = release / "control"
    records = control / "task-records"
    records.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assignments = []
    for raw in args.assignment:
        role, actor_id, task_id, session_id, record_source = raw.split("|", 4)
        source = Path(record_source).resolve()
        target = records / f"{role}.json"
        shutil.copyfile(source, target)
        assignments.append({
            "role": role, "actor_id": actor_id, "task_id": task_id, "session_id": session_id,
            "assigned_at_utc": now, "assigned_by": args.controller_id,
            "task_record_path": f"task-records/{role}.json", "task_record_sha256": sha256_file(target),
        })
    release_controllers = [row for row in assignments if row["role"] == "release_controller"]
    if len(release_controllers) != 1:
        raise ValueError("formal assignment must contain exactly one release_controller")
    release_controller = release_controllers[0]
    parent = prep / "control/actor-assignments-preparation.json"
    actor_payload = {
        "schema_version": "3.0.0", "artifact_type": "phase3_actor_assignment",
        "assignment_id": f"{release.name}-formal-actors", "assignment_stage": "formal_before_W07",
        "parent_assignment_sha256": sha256_file(parent), "controller_id": args.controller_id,
        "created_at_utc": now, "assignments": assignments,
    }
    actor_path = control / "actor-assignments-formal.json"
    write_new_json(actor_path, actor_payload)
    contracts = release / "contracts"
    contracts.mkdir()
    for source in sorted((root / "config/phase3").glob("*.json")):
        shutil.copyfile(source, contracts / source.name)
    prep_evidence = release / "preparation-evidence"
    prep_evidence.mkdir()
    for relative in ("work-items", "benchmark", "wheelhouse-manifest.json", "offline-rebuild-receipt.json"):
        source = prep / relative
        target = prep_evidence / relative
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copyfile(source, target)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    release_control = {
        "schema_version": "3.0.0", "artifact_type": "phase3_release_control", "release_id": release.name,
        "prep_id": prep.name, "implementation_freeze_commit": commit,
        "actor_assignment_sha256": sha256_file(actor_path),
        "input_manifest_sha256": sha256_file(root / "config/phase3/input-manifest.json"),
        "preregistration_sha256": sha256_file(root / "config/phase3/preregistration.json"),
        "model_registry_sha256": sha256_file(root / "config/phase3/model-registry.json"),
        "feature_registry_sha256": sha256_file(root / "config/phase3/feature-registry.json"),
        "created_at_utc": now, "formal_result_count_at_creation": 0,
        "formal_network_policy": "disabled_no_network_inputs_W08_W13",
        "task_id": release_controller["task_id"], "worktree": root.as_posix(),
        "branch": branch,
    }
    write_new_json(control / "release-control.json", release_control)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
