import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "phase4e11" / "summary.json"


@unittest.skipUnless(ARTIFACT.exists(), "P4E11 artifacts are generated on the VPS")
class Phase4E11MaskSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = json.loads(ARTIFACT.read_text())

    def test_status_and_serving_contract(self):
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_terminal_status"], "PROSPECTIVE_ONLY")
        for report in self.summary["games"].values():
            self.assertFalse(report["promotion_eligible"])

    def test_strict_lag_and_nested_space_metrics(self):
        for report in self.summary["games"].values():
            self.assertEqual(report["candidate_selection_window"]["draws"], 120)
            self.assertTrue(report["candidate_selection_window"]["strictly_before_outer"])
            compression = report["compression"]
            previous = 0
            for k in (1000, 2000, 5000, 10000, 50000, 100000):
                current = compression[str(k)]["hits"]
                self.assertGreaterEqual(current, previous)
                previous = current


if __name__ == "__main__":
    unittest.main()
