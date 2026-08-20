from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e8"


class Phase4E8IterationTests(unittest.TestCase):
    def test_outer_window_and_inner_selection_are_recorded(self) -> None:
        summary = json.loads((BASE / "summary.json").read_text())
        self.assertEqual(summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertEqual(summary["p4e6_terminal_status"], "PROSPECTIVE_ONLY")
        for game in ("ssq", "dlt"):
            report = json.loads((BASE / game / "report.json").read_text())
            self.assertEqual(report["window_size"], 120)
            self.assertEqual(report["selected"]["inner_rows"], 240)
            self.assertEqual(len(report["candidates"]), 9)
            self.assertFalse(report["promotion_eligible"])

    def test_top_outputs_and_serving_fence(self) -> None:
        for game in ("ssq", "dlt"):
            report = json.loads((BASE / game / "report.json").read_text())
            self.assertTrue(report["p4e6_serving_unchanged"])
            self.assertEqual(len((BASE / game / "top1000.jsonl").read_text().splitlines()), 1000)
            self.assertEqual(len((BASE / game / "top10.jsonl").read_text().splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
