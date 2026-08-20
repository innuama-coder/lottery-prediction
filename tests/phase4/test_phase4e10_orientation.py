from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e10"
CANDIDATES = ("positive", "negative", "zero_uniform")


class Phase4E10OrientationTests(unittest.TestCase):
    def test_inner_selection_is_strictly_before_outer(self) -> None:
        for game in ("ssq", "dlt"):
            report = json.loads((BASE / game / "report.json").read_text())
            rows = [json.loads(line) for line in (BASE / game / "inner-rolling-report.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 120)
            self.assertTrue(report["candidate_selection_window"]["strictly_before_outer"])
            self.assertEqual(report["outer_window_draws"], 120)
            self.assertTrue(all(row["strict_lag"] for row in rows))
            self.assertTrue(all(row["maximum_training_position"] == row["target_position"] - 1 for row in rows))
            self.assertLess(rows[-1]["target_position"], rows[-1]["target_position"] + report["outer_window_draws"])

    def test_selection_rule_is_reproducible(self) -> None:
        for game in ("ssq", "dlt"):
            report = json.loads((BASE / game / "report.json").read_text())
            metrics = report["candidate_metrics"]
            selected = min(
                CANDIDATES,
                key=lambda candidate: (
                    metrics[candidate]["inner_k90"],
                    metrics[candidate]["inner_k80"],
                    metrics[candidate]["inner_k50"],
                    CANDIDATES.index(candidate),
                ),
            )
            self.assertEqual(report["selected_candidate"], selected)

    def test_outer_gate_and_serving_fence(self) -> None:
        summary = json.loads((BASE / "summary.json").read_text())
        self.assertEqual(summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertTrue(summary["p4e6_serving_unchanged"])
        self.assertEqual(summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        self.assertEqual(summary["p4e6_terminal_status"], "PROSPECTIVE_ONLY")
        for game in ("ssq", "dlt"):
            report = json.loads((BASE / game / "report.json").read_text())
            evaluation = report["outer_evaluation"]
            expected = evaluation["rate"] >= 0.8 and evaluation["wilson95"][0] >= 0.75
            self.assertEqual(evaluation["reliability_gate_pass"], expected)
            self.assertFalse(report["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()
