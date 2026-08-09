from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument("--stage", required=True, choices=("preparation_before_W01", "formal_before_W07"))
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--parent-assignment", type=Path)
    parser.add_argument("--assignment", action="append", required=True, help="role|actor_id|task_id|session_id[|source_task_record]")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows = []
    for raw in args.assignment:
        parts = raw.split("|", 4)
        role, actor_id, task_id, session_id = parts[:4]
        record = {
            "schema_version": "1.0.0", "record_type": "phase3_actor_task_record", "role": role,
            "actor_id": actor_id, "task_id": task_id, "session_id": session_id,
            "parent_task_id": "phase3-implementation-20260809-r01",
            "declaration": "This stable actor/task/session record is bound before the assigned Phase 3 work item and is not a role-name-only assertion.",
        }
        record_path = args.output.parent / "task-records" / f"{role}.json"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        if record_path.exists():
            raise FileExistsError(record_path)
        if len(parts) == 5:
            shutil.copyfile(Path(parts[4]).resolve(), record_path)
        else:
            record_path.write_bytes(canonical(record))
        rows.append({
            "role": role, "actor_id": actor_id, "task_id": task_id, "session_id": session_id,
            "assigned_at_utc": now, "assigned_by": args.controller_id,
            "task_record_path": f"task-records/{role}.json",
            "task_record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
        })
    parent_sha = hashlib.sha256(args.parent_assignment.read_bytes()).hexdigest() if args.parent_assignment else None
    payload = {
        "schema_version": "3.0.0", "artifact_type": "phase3_actor_assignment",
        "assignment_id": args.assignment_id, "assignment_stage": args.stage,
        "parent_assignment_sha256": parent_sha, "controller_id": args.controller_id,
        "created_at_utc": now, "assignments": rows,
    }
    args.output.write_bytes(canonical(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
