from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e9"
SPACES = (1000, 2000, 5000, 10000, 50000, 100000)


class Phase4E9NestedSpacesTests(unittest.TestCase):
    def rows(self, game: str) -> list[dict[str, object]]:
        return [json.loads(line) for line in (BASE / game / "rolling-report.jsonl").read_text().splitlines()]

    def report(self, game: str) -> dict[str, object]:
        return json.loads((BASE / game / "report.json").read_text())

    def test_walk_forward_rank_contract(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game)
            self.assertEqual(len(rows), 120)
            self.assertEqual(report["ranking_contract"], "joint_stable_score_key_desc_tie_canonical_ticket_asc_v1")
            self.assertEqual(report["score_order_key_id"], "P4S10HE1")
            for row in rows:
                self.assertTrue(row["strict_lag"])
                self.assertEqual(row["maximum_training_position"], row["target_position"] - 1)
                self.assertLessEqual(1, row["tie_rank_lower"])
                self.assertLessEqual(row["tie_rank_lower"], row["canonical_rank"])
                self.assertLessEqual(row["canonical_rank"], row["tie_rank_upper"])
                self.assertLessEqual(row["tie_rank_upper"], report["full_space_size"])

    def test_fixed_spaces_are_nested_and_monotonic(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game)
            self.assertEqual(report["nested_spaces"], list(SPACES))
            self.assertTrue(report["nested_coverage_monotonic"])
            for row in rows:
                observed = [bool(row["covered"][str(k)]) for k in SPACES]
                self.assertEqual(observed, sorted(observed))
                for k, covered in zip(SPACES, observed):
                    self.assertEqual(covered, row["canonical_rank"] <= k)
            hits = [report["fixed_space_coverage"][str(k)]["all_120"]["hits"] for k in SPACES]
            self.assertEqual(hits, sorted(hits))

    def test_calibration_and_evaluation_are_separate(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game)
            calibration_ranks = sorted(row["canonical_rank"] for row in rows[:60])
            selected = report["calibrated_spaces"]["0.9"]["selected_k_from_calibration_only"]
            self.assertEqual(selected, calibration_ranks[54])
            self.assertEqual(report["calibrated_spaces"]["0.9"]["calibration"]["draws"], 60)
            self.assertEqual(report["calibrated_spaces"]["0.9"]["evaluation"]["draws"], 60)
            acceptance = report["first_reliable_space"]["acceptance"]
            expected = (
                acceptance["actual_evaluation_rate"] >= acceptance["minimum_evaluation_rate"]
                and acceptance["actual_wilson95_lower"] >= acceptance["minimum_wilson95_lower"]
            )
            self.assertEqual(acceptance["pass"], expected)

    def test_serving_and_probability_claim_fences(self) -> None:
        summary = json.loads((BASE / "summary.json").read_text())
        self.assertEqual(summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertTrue(summary["p4e6_serving_unchanged"])
        self.assertEqual(summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        self.assertEqual(summary["p4e6_terminal_status"], "PROSPECTIVE_ONLY")
        for game in ("ssq", "dlt"):
            report = self.report(game)
            self.assertFalse(report["promotion_eligible"])
            self.assertEqual(report["probability_spread_adjustment"], "none")
            self.assertIn("no ticket-level probability guarantee", report["probability_claim"])


if __name__ == "__main__":
    unittest.main()
