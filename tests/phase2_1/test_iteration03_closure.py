from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lottery_research.phase2_1 import BASELINE_SHA, RELEASE_ID
from lottery_research.phase2_1.cli import execute_external_commands, write_command_receipt
from lottery_research.phase2_1.schema import SCHEMAS, validate
from lottery_research.phase2_1.serialization import canonical_json_bytes, identity
from lottery_research.phase2_1.workflow import (
    _formal_output_snapshot,
    _validate_staging_power_baseline,
    _verify_formal_output_contract,
    project_root,
    run_e2e,
)


ROOT = project_root()


def verification_fixture() -> dict[str, object]:
    return {
        "schema_version": "2.1.0",
        "artifact_type": "phase2_1_verification_receipt",
        "release_id": RELEASE_ID,
        "operation": "fixture",
        "status": "FAIL",
        "terminal": "EVIDENCE_MISMATCH",
        "exit_code": 5,
        "started_at_utc": "2026-08-05T00:00:00Z",
        "finished_at_utc": "2026-08-05T00:00:01Z",
        "error": "expected",
    }


class Iteration03ClosureTests(unittest.TestCase):
    def test_every_core_and_receipt_artifact_has_a_dedicated_schema(self) -> None:
        expected = {
            "readiness", "gate", "qualification", "historical_audit", "power", "replay", "review",
            "e2e_registry", "acceptance", "verification_receipt", "command_receipt",
            "external_command_receipt", "run_log_summary", "evidence_manifest", "negative_suite",
        }
        self.assertTrue(expected.issubset(SCHEMAS))

    def test_verification_receipt_rejects_zero_exit_failure_terminal(self) -> None:
        receipt = verification_fixture()
        receipt["exit_code"] = 0
        with self.assertRaises(ValueError):
            validate("verification_receipt", receipt)

    def test_verification_receipt_rejects_nonzero_exit_pass_terminal(self) -> None:
        receipt = verification_fixture()
        receipt.update(status="PASS", terminal="PASS", exit_code=3, error=None)
        with self.assertRaises(ValueError):
            validate("verification_receipt", receipt)

    def test_external_receipts_preserve_exit_codes_two_through_five(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw)
            (destination / "logs").mkdir()
            (destination / "contracts").mkdir()
            (destination / "contracts/acceptance-contract.json").write_bytes(
                (ROOT / "docs/roadmap/phase-2.1-acceptance-contract.json").read_bytes()
            )
            completed = [SimpleNamespace(returncode=code, stdout=b"", stderr=b"expected") for code in (2, 3, 4, 5)]
            completed.append(SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
            with patch("lottery_research.phase2_1.cli.subprocess.run", side_effect=completed):
                receipts = execute_external_commands(ROOT, destination)
            self.assertEqual([row["exit_code"] for row in receipts], [2, 3, 4, 5, 0])
            self.assertTrue(all(row["status"] == row["terminal"] == "FAIL" for row in receipts[:4]))

    def test_formal_command_receipt_preserves_failure_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw)
            (destination / "logs").mkdir()
            stdout = b'{"terminal":"FAIL"}'
            receipt = write_command_receipt(
                destination,
                command="gates",
                argv=["gates"],
                started_at_utc="2026-08-05T00:00:00Z",
                terminal="FAIL",
                exit_code=2,
                stdout=stdout,
                stderr=b"expected",
                working_directory=raw,
            )
            self.assertEqual(receipt["exit_code"], 2)
            self.assertEqual(receipt["stdout_sha256"], hashlib.sha256(stdout).hexdigest())

    def test_readiness_allowlist_rejects_late_result_log_and_evidence(self) -> None:
        for relative in ("results/stale-power.json", "logs/late.log", "reviews/late-evidence.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as raw:
                destination = Path(raw)
                (destination / "inputs").mkdir()
                (destination / "inputs/frozen.txt").write_text("frozen\n", encoding="utf-8")
                readiness = {"formal_output_snapshot": _formal_output_snapshot(destination)}
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("late\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unregistered post-readiness"):
                    _verify_formal_output_contract(destination, readiness, require_complete=False)

    def test_replay_engine_source_does_not_import_power_workflow(self) -> None:
        source = (ROOT / "src/lottery_research/phase2_1/independent_replay.py").read_text(encoding="utf-8")
        self.assertNotIn("from .workflow", source)
        self.assertNotIn("_power_grid(", source)

    def test_frozen_staging_baseline_reuses_exact_power_and_rejects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw)
            (destination / "readiness").mkdir()
            (destination / "results").mkdir()
            (destination / "logs").mkdir()
            input_identity = {"release_id": RELEASE_ID}
            readiness = {
                "input_identity": input_identity,
                "source_manifest": {"sha256": "1" * 64},
                "frozen_input_identities": [],
            }
            (destination / "readiness/readiness.json").write_bytes(canonical_json_bytes(readiness))
            power_path = destination / "results/power.json"
            power_path.write_bytes(canonical_json_bytes({"grid": "frozen"}))
            receipt_path = destination / "logs/07-power.json"
            receipt = {
                "schema_version": "2.1.0", "artifact_type": "phase2_1_command_record", "release_id": RELEASE_ID,
                "command": "power", "argv": ["power"], "status": "PASS", "terminal": "PASS", "exit_code": 0,
                "started_at_utc": "2026-08-05T00:00:00Z", "finished_at_utc": "2026-08-05T00:00:01Z",
                "working_directory": raw, "executed": True, "network_access": False,
                "stdout_summary": "PASS", "stderr_summary": "", "stdout_sha256": "2" * 64, "stderr_sha256": "3" * 64,
                "input_identity": {
                    "release_id": RELEASE_ID, "baseline_sha": BASELINE_SHA,
                    "phase1_frozen": [], "phase2_frozen": [], "task_inputs": {}, "task_input_aggregate_sha256": "0" * 64,
                },
            }
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            baseline = {
                "profile": "fixture", "release_id": RELEASE_ID, "staging_bundle": destination.as_posix(),
                "created_at_utc": "2026-08-05T00:00:01Z", "input_identity": input_identity,
                "source_manifest_sha256": "1" * 64, "power_identity": identity(destination, power_path),
                "power_normalized_sha256": "4" * 64,
                "power_command_receipt_identity": identity(destination, receipt_path), "frozen_input_identities": [],
            }
            _validate_staging_power_baseline(ROOT, destination, baseline, {"normalized_sha256": "4" * 64})
            power_path.write_bytes(canonical_json_bytes({"grid": "tampered"}))
            with self.assertRaisesRegex(ValueError, "power differs"):
                _validate_staging_power_baseline(ROOT, destination, baseline, {"normalized_sha256": "4" * 64})

    def test_registry_schemas_require_per_case_execution_observability(self) -> None:
        e2e_schema = (ROOT / "schemas/phase2_1/e2e-registry.schema.json").read_text(encoding="utf-8")
        negative_schema = (ROOT / "schemas/phase2_1/negative-suite.schema.json").read_text(encoding="utf-8")
        for field in ("input_bundle", "command", "exit_code", "duration_seconds", "terminal"):
            self.assertIn(field, e2e_schema)
            self.assertIn(field, negative_schema)

    def test_completed_e2e_rerun_uses_new_staging_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            destination = root / "bundle"
            (destination / "e2e").mkdir(parents=True)
            marker = destination / "e2e/registry.json"
            marker.write_text("old evidence\n", encoding="utf-8")
            staging = root / "staging" / RELEASE_ID
            with patch("lottery_research.phase2_1.workflow._run_e2e_in_place", return_value={"status": "PASS"}) as runner:
                result = run_e2e(destination, root=root, staging_bundle=staging)
            self.assertEqual(marker.read_text(encoding="utf-8"), "old evidence\n")
            self.assertEqual(result["_staging_bundle"], staging.as_posix())
            self.assertFalse((staging / "e2e/registry.json").exists())
            runner.assert_called_once_with(staging, root=root)


if __name__ == "__main__":
    unittest.main()
