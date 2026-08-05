from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lottery_research.phase2.schema import load_json, validate_payload

from tests.phase2_e2e.run_phase2_e2e import (
    CONTRACT_REL,
    SOURCE_ROOT,
    Phase2E2ERunner,
    run_interruption_protocol_probe,
)


class Phase2E2ERunnerTests(unittest.TestCase):
    def test_isolated_root_excludes_formal_results_and_preserves_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = Phase2E2ERunner(SOURCE_ROOT, Path(raw))
            root = runner.prepare_isolated_root("E2E-P2-02-input-tamper")
            self.assertEqual((root / CONTRACT_REL).read_bytes(), (SOURCE_ROOT / CONTRACT_REL).read_bytes())
            self.assertFalse((root / "artifacts/phase-2/gates/g0-g1.json").exists())
            self.assertFalse((root / "artifacts/phase-2/results/historical-audit.json").exists())
            self.assertFalse((root / "artifacts/phase-2/results/power-envelope.json").exists())

    def test_real_validate_input_faults_emit_schema_valid_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = Phase2E2ERunner(SOURCE_ROOT, Path(raw))
            receipts = runner.run_light_validation_faults()
            self.assertEqual(len(receipts), 4)
            outcomes = {}
            for path in receipts:
                receipt = load_json(path)
                validate_payload("e2e_receipt", receipt)
                outcomes[receipt["case_id"]] = receipt["observed"]
                self.assertEqual(receipt["status"], "PASS")
                self.assertTrue(receipt["run_identities"])
                assertion_ids = {row["id"] for row in receipt["assertions"]}
                self.assertIn("observed-exit-code", assertion_ids)
                self.assertIn("observed-terminal", assertion_ids)
            self.assertEqual(outcomes["E2E-P2-02-input-tamper"]["exit_code"], 5)
            for case_id in (
                "E2E-P2-03-observation-count-inflation",
                "E2E-P2-04-rule-segment-mixing",
                "E2E-P2-05-point-in-time-leakage",
            ):
                self.assertEqual(outcomes[case_id], {"exit_code": 2, "terminal": "REJECTED"})

    def test_all_seven_preregistration_tampers_fail_before_replay(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = Phase2E2ERunner(SOURCE_ROOT, Path(raw))
            receipt_path = runner.run_preregistration_tamper()
            receipt = load_json(receipt_path)
            validate_payload("e2e_receipt", receipt)
            self.assertEqual(receipt["observed"], {"exit_code": 5, "terminal": "EVIDENCE_MISMATCH"})
            self.assertEqual(len(receipt["run_identities"]), 7)
            self.assertIn(
                {"id": "tamper-scope-coverage", "status": "PASS", "expected": 7, "observed": 7},
                receipt["assertions"],
            )

    def test_registry_rejects_duplicate_aggregate_case(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            runner = Phase2E2ERunner(SOURCE_ROOT, base)
            receipt_path = runner.run_validate_fault(
                "E2E-P2-02-input-tamper",
                lambda payload: payload["upstream"]["draws"].update(sha256="0" * 64),
            )
            duplicates = [receipt_path] * 10
            with self.assertRaisesRegex(ValueError, "duplicate aggregate"):
                runner.build_registry(duplicates, base / "registry.json")

    def test_receipt_internal_identity_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            runner = Phase2E2ERunner(SOURCE_ROOT, base)
            receipt_path = runner.run_validate_fault(
                "E2E-P2-02-input-tamper",
                lambda payload: payload["upstream"]["draws"].update(sha256="0" * 64),
            )
            receipt = load_json(receipt_path)
            receipt["run_identities"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                runner.verify_receipt_evidence(receipt)

    def test_heavy_receipt_consumer_rejects_nonpass_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            runner = Phase2E2ERunner(SOURCE_ROOT, base)
            root = runner.prepare_isolated_root("E2E-P2-07-uniform-calibration")
            result_path = root / "result.json"
            payload = {
                "schema_version": "1.0.0",
                "artifact_type": "phase2_run_result",
                "run_id": "not-pass",
                "command": "power",
                "terminal": "HOLD",
                "exit_code": 20,
                "started_at_utc": "2026-08-05T00:00:00Z",
                "finished_at_utc": "2026-08-05T00:00:01Z",
                "request_identity": {"path": "request.json", "sha256": "a" * 64},
                "input_identities": [],
                "output_identities": [],
                "metrics": {},
                "errors": ["controlled test fixture"],
            }
            from lottery_research.phase2.serialization import canonical_json_bytes

            result_path.write_bytes(canonical_json_bytes(payload))
            with self.assertRaisesRegex(AssertionError, "shared run is not PASS"):
                runner.consume_shared_pass_runs(
                    "E2E-P2-07-uniform-calibration",
                    root,
                    [result_path],
                    assertions=[],
                    input_paths=[root / CONTRACT_REL],
                    output_paths=[result_path],
                )

    def test_interruption_probe_cannot_be_used_as_e2e10_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = run_interruption_protocol_probe(Path(raw))
            self.assertNotEqual(result["first_native_return_code"], 0)
            self.assertIsNone(result["first_normal_cli_terminal"])
            self.assertTrue(result["checkpoint_exists"])
            self.assertEqual(result["resume_return_code"], 0)
            self.assertFalse(result["eligible_for_e2e10_receipt"])


if __name__ == "__main__":
    unittest.main()
