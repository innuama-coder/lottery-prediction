from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.phase1.e2e07_case import execute_fault_matrix, wait_for_ready_then_kill


REPO = Path(__file__).resolve().parents[2]


class E2EFaultCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.outcomes = {item["fault"]: item for item in execute_fault_matrix()}

    def test_snapshot_parser_and_quality_faults_are_exit2_after_valid_resign(self) -> None:
        for fault in ("source_conflict", "truncated_html", "wrong_encoding"):
            outcome = self.outcomes[fault]
            self.assertEqual((outcome["actual_exit"], outcome["expected_exit"]), (2, 2), outcome)
            self.assertTrue(outcome["request_terminal_closed"], outcome)
            self.assertFalse(outcome["release_created"], outcome)

    def test_unresigned_raw_tamper_is_exit5(self) -> None:
        outcome = self.outcomes["raw_hash_mismatch"]
        self.assertEqual((outcome["actual_exit"], outcome["terminal_count"]), (5, 0), outcome)
        self.assertTrue(outcome["request_terminal_closed"], outcome)

    def test_invalid_config_is_exit4_without_run(self) -> None:
        outcome = self.outcomes["invalid_configuration"]
        self.assertEqual((outcome["actual_exit"], outcome["terminal_count"]), (4, 0), outcome)
        self.assertFalse(outcome["release_created"], outcome)

    def test_controlled_live_network_failure_is_exit3(self) -> None:
        outcome = self.outcomes["network_failure"]
        self.assertEqual(outcome["actual_exit"], 3, outcome)
        self.assertTrue(outcome["request_terminal_closed"], outcome)
        self.assertEqual(outcome["pointer_before"], outcome["pointer_after"], outcome)

    def test_real_publish_lock_and_cas_barrier_are_exit6(self) -> None:
        for fault in ("publish_lock", "compare_and_swap"):
            outcome = self.outcomes[fault]
            self.assertEqual(outcome["actual_exit"], 6, outcome)
            self.assertEqual(outcome["pointer_before"], outcome["pointer_after"], outcome)
            self.assertEqual(outcome["release_names_before"], outcome["release_names_after"], outcome)
            self.assertFalse(outcome["release_created"], outcome)

        # A pointer change in one structured fact must make the runner contract fail.
        sys.path.insert(0, str(REPO / "tests/phase1"))
        import run_acceptance as acceptance
        contract_path = REPO / "docs/roadmap/phase-1-acceptance-contract.json"
        contract = acceptance.load_contract(contract_path)
        attacked = [copy.deepcopy(item) for item in self.outcomes.values()]
        attacked[0]["pointer_after"] = b"attacker-pointer"
        with patch("e2e07_case.execute_fault_matrix", return_value=attacked):
            code, report = acceptance._fault_case_report(contract, contract_path)
        self.assertEqual((code, report["status"]), (acceptance.FAIL, "FAIL"))
        pointer_assertion = next(item for item in report["assertions"] if item["id"] == "current_pointer_unchanged=true")
        self.assertEqual((pointer_assertion["expected"], pointer_assertion["actual"]), (True, False))

    def test_forced_termination_recovers_once_and_is_idempotent(self) -> None:
        outcome = self.outcomes["forced_process_termination"]
        self.assertTrue(outcome["recovered"] and outcome["recovery_idempotent"], outcome)
        self.assertEqual(outcome["pointer_before"], outcome["pointer_after"], outcome)
        self.assertFalse(outcome["release_created"], outcome)

        # The shared process monitor must fail closed and reap every child.
        cases = (
            ("timeout", "import time; time.sleep(60)", "timed out", 0.2),
            (
                "stderr", "import sys,time; print('early-error', file=sys.stderr, flush=True); time.sleep(60)",
                "stderr before READY", 5.0,
            ),
            ("eof", "pass", "EOF before READY", 1.0),
        )
        environment = dict(os.environ)
        for kind, source, message, timeout_seconds in cases:
            with self.subTest(monitor_failure=kind):
                process = subprocess.Popen(
                    [sys.executable, "-u", "-c", source], cwd=REPO, env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                if kind == "eof":
                    process.wait(timeout=10)
                    self.assertIsNotNone(process.poll())
                with self.assertRaisesRegex(AssertionError, message):
                    wait_for_ready_then_kill(process, timeout_seconds=timeout_seconds)
                self.assertIsNotNone(process.poll())
                self.assertTrue(process.stdout is not None and process.stdout.closed)
                self.assertTrue(process.stderr is not None and process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
