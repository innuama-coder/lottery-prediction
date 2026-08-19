from __future__ import annotations

import copy
import unittest

from lottery_system.phase4e6.consensus import (
    build_lagged_feature_rows,
    consensus_issue,
    normalize_observation,
    normalize_probabilities,
    require_strict_prior,
)


def observation(source: str, group: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "game": "dlt", "issue": "2024006", "draw_date": "2024-01-13",
        "front": [8, 13, 16, 17, 33], "back": [2, 10],
        "sales": 317_993_801, "jackpot": 881_907_619,
        "first_prize_count": 6, "first_prize_amount": 10_000_000,
        "second_prize_count": 90, "second_prize_amount": 140_208,
        "source_id": source, "capture_group": group, "accessible": True,
        "lineage": source, "suspected_common_upstream": None,
        "raw_receipt": f"raw/{source}/receipt.json", "raw_body_sha256": "a" * 64,
    }
    row.update(changes)
    return row


class Phase4E6ConsensusTests(unittest.TestCase):
    def test_exact_and_tolerant_consensus(self) -> None:
        result = consensus_issue([observation("official", "official"), observation("archive", "archive", jackpot=881_907_619.5)])
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["independent_source_count"], 2)
        self.assertAlmostEqual(result["jackpot"], 881_907_619.25)

    def test_conflict_is_quarantined(self) -> None:
        result = consensus_issue([observation("official", "official"), observation("archive", "archive", front=[1, 2, 3, 4, 5])])
        self.assertTrue(result["conflict"]); self.assertTrue(result["quarantined"])
        self.assertIn("front", result["conflict_fields"])

    def test_missing_operational_field_is_quarantined(self) -> None:
        result = consensus_issue([observation("official", "official"), observation("archive", "archive", sales=None)])
        self.assertTrue(result["quarantined"]); self.assertIn("sales", result["missing_fields"])

    def test_same_capture_group_does_not_satisfy_independence(self) -> None:
        result = consensus_issue([observation("one", "shared"), observation("two", "shared")])
        self.assertTrue(result["quarantined"]); self.assertEqual(result["independent_source_count"], 1)

    def test_normalization_rejects_invalid_balls(self) -> None:
        with self.assertRaises(ValueError):
            normalize_observation(observation("x", "x", front=[1, 1, 2, 3, 4]))

    def test_current_and_future_rows_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "current/future"):
            require_strict_prior(4, 4)
        with self.assertRaisesRegex(ValueError, "current/future"):
            require_strict_prior(4, 5)
        require_strict_prior(4, 3)

    def test_future_mutation_cannot_change_prior_features(self) -> None:
        draws = [
            {"game": "dlt", "issue": f"202400{index}", "draw_date": f"2024-01-{index:02d}"}
            for index in range(1, 7)
        ]
        accepted = consensus_issue([observation("o", "o"), observation("a", "a")])
        accepted["issue"] = "2024003"
        rows = build_lagged_feature_rows(draws, {"2024003": accepted})
        mutated = copy.deepcopy(accepted); mutated["sales"] = 10**30
        changed = build_lagged_feature_rows(draws, {"2024003": mutated})
        self.assertEqual(rows[:3], changed[:3])
        self.assertNotEqual(rows[4:], changed[4:])

    def test_current_row_is_never_consumed_and_missingness_is_explicit(self) -> None:
        draws = [{"game": "dlt", "issue": "2024006", "draw_date": "2024-01-13"}]
        accepted = consensus_issue([observation("o", "o"), observation("a", "a")])
        row = build_lagged_feature_rows(draws, {"2024006": accepted})[0]
        self.assertIsNone(row["maximum_metadata_issue"])
        self.assertTrue(all(value is None for key, value in row["values"].items() if key != "rollover"))

    def test_deterministic_replay_and_probability_normalization(self) -> None:
        first = consensus_issue([observation("o", "o"), observation("a", "a")])
        second = consensus_issue([observation("a", "a"), observation("o", "o")])
        self.assertEqual(first, second)
        normalized = normalize_probabilities([1.0, 2.0, 3.0])
        self.assertAlmostEqual(sum(normalized), 1.0)
        with self.assertRaises(ValueError):
            normalize_probabilities([1.0, -1.0])


if __name__ == "__main__":
    unittest.main()
