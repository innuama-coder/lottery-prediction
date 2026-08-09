from __future__ import annotations

import unittest
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lottery_research.phase3.prerun_contract import validate_prerun_contract
from lottery_research.phase3.serialization import sha256_file
from lottery_research.phase3.work_items import create_command_work_item_receipt, validate_work_item_receipt_file


ROOT = Path(__file__).resolve().parents[2]


class PreRunContractTests(unittest.TestCase):
    def contract_root(self, raw: str) -> Path:
        root = Path(raw)
        for name in ("artifacts", "schemas", "tasks"):
            os.symlink(ROOT / name, root / name, target_is_directory=True)
        shutil.copytree(ROOT / "config/phase3", root / "config/phase3")
        return root

    def test_frozen_baseline_is_ready_under_sequence_safe_contract(self) -> None:
        receipt = validate_prerun_contract(ROOT)

        self.assertEqual(receipt["status"], "READY")
        self.assertFalse(receipt["formal_run_authorized"])
        self.assertEqual(receipt["metrics"]["input_identity_coverage"], 1.0)
        self.assertEqual(receipt["metrics"]["outer_target_count"], 300)
        self.assertEqual(receipt["metrics"]["expanded_sequence_relation_count"], 37350)
        self.assertEqual(receipt["metrics"]["sequence_relation_coverage"], 1.0)
        self.assertEqual(receipt["hold_reasons"], [])

    def test_missing_required_forbidden_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self.contract_root(raw)
            path = root / "config/phase3/data-time-contract.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["forbidden_fields"].remove("future_draw_result")
            path.write_text(json.dumps(payload), encoding="utf-8")
            prereg_path = root / "config/phase3/preregistration.json"
            prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
            prereg["data_time_contract_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            prereg_path.write_text(json.dumps(prereg), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "forbidden field coverage"):
                validate_prerun_contract(root)

    def test_same_or_future_source_issue_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self.contract_root(raw)
            path = root / "config/phase3/availability-ledger.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["entries"][0]["source_issues"].append(payload["entries"][0]["target_issue"])
            payload["entries"][0]["source_count"] += 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            contract_path = root / "config/phase3/data-time-contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["availability_ledger_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            prereg_path = root / "config/phase3/preregistration.json"
            prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
            prereg["availability_ledger_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            prereg["data_time_contract_sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
            prereg_path.write_text(json.dumps(prereg), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source prefix mismatch|same/future issue"):
                validate_prerun_contract(root)

    def test_validator_emits_json_and_zero_exit_for_ready_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            assignments = []
            for role in ("data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer"):
                record = base / f"{role}.log"
                record.write_text(f"task record for {role}\n", encoding="utf-8")
                assignments.append({
                    "role": role, "actor_id": f"actor-{role}", "task_id": f"task-{role}",
                    "session_id": f"session-{role}", "assigned_at_utc": "2026-08-09T00:00:00Z",
                    "assigned_by": "controller", "task_record_path": record.name,
                    "task_record_sha256": sha256_file(record),
                })
            actor_path = base / "actor-assignments.json"
            actor_path.write_text(json.dumps({
                "schema_version": "3.0.0", "artifact_type": "phase3_actor_assignment",
                "assignment_id": "prep-actors-i01", "assignment_stage": "preparation_before_W01",
                "parent_assignment_sha256": None, "controller_id": "controller",
                "created_at_utc": "2026-08-09T00:00:00Z", "assignments": assignments,
            }), encoding="utf-8")
            outputs = {}
            for index, work_item in enumerate(("W01", "W02", "W03"), start=1):
                output = base / f"{work_item}.json"
                command = [
                    sys.executable, "scripts/phase3/validate_prerun_contract.py", "--check", work_item,
                    "--identity", f"prep-{work_item}", "--actor-assignments", str(actor_path), "--output", str(output),
                ]
                if index > 1:
                    command.extend(("--upstream-receipt", str(outputs[f"W{index - 1:02d}"])))
                completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["work_item"], work_item)
                outputs[work_item] = output
            self.assertEqual(json.loads(outputs["W01"].read_text())["terminal"], "W01_INPUTS_READY")
            self.assertEqual(json.loads(outputs["W02"].read_text())["terminal"], "W02_SEQUENCE_TIME_READY")
            self.assertEqual(json.loads(outputs["W03"].read_text())["terminal"], "W03_PREREGISTRATION_READY")
            command_output = base / "implementation-validation"
            command_output.mkdir()
            (command_output / "implementation-validation.json").write_text('{"status":"PASS"}\n', encoding="utf-8")
            w04 = base / "W04.json"
            create_command_work_item_receipt(
                ROOT, work_item="W04", identity="prep-W04", actor_path=actor_path,
                upstream_actor_path=actor_path, upstream_receipt=outputs["W03"], command_output=command_output,
                receipt_output=w04, command=["phase3", "validate", "--scope", "implementation"],
                started_at_utc="2026-08-09T00:00:00Z", ended_at_utc="2026-08-09T00:00:01Z",
                process_exit_code=0, status="PASS", terminal="PASS",
            )
            verified = validate_work_item_receipt_file(ROOT, w04, actor_path, "W04")
            self.assertEqual(verified["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
