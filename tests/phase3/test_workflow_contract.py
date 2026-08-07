from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.workflow import (
    final_validate,
    qualify,
    readiness,
    replay,
    verify_e2e,
)


ROOT = Path(__file__).resolve().parents[2]


class WorkflowContractTests(unittest.TestCase):
    def test_synthetic_qualification_replay_and_e2e_are_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            qualification = qualify(ROOT, base / "qualification-i01", "qualification-i01")
            replay_receipt = replay(ROOT, base / "replay-i01", "replay-i01", base / "qualification-i01")
            e2e = verify_e2e(ROOT, base / "e2e-i01", "e2e-i01")

            self.assertEqual(qualification["status"], "PASS")
            self.assertTrue(qualification["non_formal_synthetic_only"])
            self.assertEqual(replay_receipt["status"], "PASS")
            self.assertEqual(e2e["status"], "PASS")
            self.assertEqual(e2e["required_case_coverage"], 1.0)

    def test_readiness_and_final_validator_fail_closed_on_pit_hold(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            readiness_receipt = readiness(ROOT, base / "readiness-i01", "readiness-i01")
            final = final_validate(ROOT, base / "final-i01", "final-i01")

            self.assertEqual(readiness_receipt["terminal"], "HOLD_PENDING_PIT_EVIDENCE")
            self.assertFalse(readiness_receipt["formal_run_authorized"])
            self.assertIn("dirty", readiness_receipt["task"])
            self.assertEqual(final["terminal"], "HOLD_PENDING_PIT_EVIDENCE")
            self.assertEqual(final["formal_result_count"], 0)

    def test_run_command_refuses_formal_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lottery_research.phase3",
                    "run",
                    "--identity",
                    "formal-refusal-i01",
                    "--output",
                    str(Path(raw) / "formal-refusal-i01"),
                ],
                cwd=ROOT,
                env={"PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 20)
            self.assertEqual(json.loads(completed.stdout)["terminal"], "HOLD_PENDING_PIT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
