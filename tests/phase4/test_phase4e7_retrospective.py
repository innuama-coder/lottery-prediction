from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e7"


class Phase4E7RetrospectiveTests(unittest.TestCase):
    def test_split_is_fixed_and_retrospective(self) -> None:
        manifest = json.loads((BASE / "split-manifest.json").read_text())
        self.assertEqual(manifest["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertEqual(manifest["window_size"], 120)
        for game in ("ssq", "dlt"):
            self.assertEqual(len(manifest["games"][game]["holdout_row_hashes"]), 120)

    def test_outputs_and_promotion_fence(self) -> None:
        summary = json.loads((BASE / "summary.json").read_text())
        self.assertEqual(summary["p4e6_terminal_status"], "PROSPECTIVE_ONLY")
        self.assertTrue(summary["p4e6_serving_unchanged"])
        for game in ("ssq", "dlt"):
            report = json.loads((BASE / game / "report.json").read_text())
            self.assertFalse(report["promotion_eligible"])
            self.assertEqual(report["window_size"], 120)
            self.assertEqual(len((BASE / game / "top1000.jsonl").read_text().splitlines()), 1000)
            self.assertEqual(len((BASE / game / "top10.jsonl").read_text().splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
