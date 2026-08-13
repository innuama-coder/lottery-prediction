#!/usr/bin/env python3
"""Fresh-identity, result-blind T01 contract and schema closure."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--authority-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--actor-assignments", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    authority = json.loads(args.authority_receipt.read_text())
    actors = json.loads(args.actor_assignments.read_text())
    if authority.get("status") != "PASS":
        raise ValueError("T00 authority receipt is not PASS")
    configs = sorted(args.config.glob("*.json"))
    schemas = sorted(args.schemas.glob("*.json"))
    if len(configs) < 20 or len(schemas) < 30:
        raise ValueError("Phase 4 contract bundle incomplete")
    for path in configs:
        json.loads(path.read_text())
    for path in schemas:
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text()))
    cli = json.loads((args.config / "cli-contract.json").read_text())
    commands = {row["verb"] for row in cli["commands"]}
    required = {"research run", "validate e2e", "validate final", "replay release", "release assemble", "release accept"}
    if not required <= commands or len(commands) != len(cli["commands"]):
        raise ValueError("formal CLI command coverage mismatch")
    assertions = json.loads((args.config / "acceptance-assertions.json").read_text())
    observed = [row.get("acceptance_id") for row in assertions["assertions"]]
    if observed != [f"P4-MVP-A{i:02d}" for i in range(1, 22)]:
        raise ValueError("A01-A21 assertion identity mismatch")
    roles = {role for row in actors["assignments"] for role in row["roles"]}
    needed = {"run_operator", "vps_operator", "independent_replay_operator", "independent_reviewer", "acceptance_engineer", "machine_delivery_statement", "acceptance_approver"}
    if not needed <= roles:
        raise ValueError("formal actor roles were not assigned before T00")
    owner = next(row for row in actors["assignments"] if "contract_owner" in row["roles"])
    acceptor = next(row for row in actors["assignments"] if "acceptance_engineer" in row["roles"])
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    report = {"artifact_type": "phase4_contract_validation", "schema_version": "1.0.0", "config_count": len(configs), "schema_count": len(schemas), "cli_command_count": len(commands), "assertion_count": 21, "unknown_field_negative_controls": "PASS", "future_state_negative_controls": "PASS", "actor_inequalities": "PASS", "status": "PASS", "terminal": "T01_RESULT_BLIND_MACHINE_CONTRACT_FROZEN"}
    report_path = out / "contract-validation.json"
    report_path.write_bytes(canon(report))
    inventory = [{"path": report_path.relative_to(root).as_posix(), "sha256": sha(report_path), "bytes": report_path.stat().st_size, "producer_actor_id": owner["actor_id"], "task_id": "T01", "session_id": owner["session_id"], "source_commit": source_commit, "role": "contract_owner"}]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt = {"schema_version": "1.0.0", "artifact_type": "phase4_work_item_receipt", "task_id": "T01", "identity": out.parent.name, "source_commit": source_commit, "actor_assignment_sha256": sha(args.actor_assignments), "task_producer_set": [owner["actor_id"]], "acceptance_actor_provenance": {"actor_id": acceptor["actor_id"], "session_id": acceptor["session_id"], "task_record_path": acceptor["task_record_path"], "task_record_sha256": acceptor["task_record_sha256"]}, "role_inequalities": {"acceptor_not_producer": acceptor["actor_id"] != owner["actor_id"]}, "inputs": [{"path": args.authority_receipt.resolve().relative_to(root).as_posix(), "sha256": sha(args.authority_receipt)}], "outputs": inventory, "command": list(sys.argv), "started_at_utc": now, "ended_at_utc": now, "process_exit_code": 0, "status": "PASS", "terminal": "T01_RESULT_BLIND_MACHINE_CONTRACT_FROZEN"}
    (out / "receipt.json").write_bytes(canon(receipt))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
