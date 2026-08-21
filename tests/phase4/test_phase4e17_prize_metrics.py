import sys
import unittest

sys.path.insert(0, "scripts/phase4e17")
from run_per_number_feature_model import (
    TICKET_PARTITION_SIZES,
    ranked_ticket_partition_prize_metrics,
    ticket_group_prize_metrics,
    ticket_prize,
)


class Phase4E17PrizeMetricTests(unittest.TestCase):
    @staticmethod
    def row():
        return {
            "game": "dlt",
            "issue": "2026087",
            "zones": {
                "front": {"actual_numbers": [1, 2, 3, 4, 5]},
                "back": {"actual_numbers": [1, 2]},
            },
            "phase4e17_per_number_feature_model": {"zones": {
                "front": {"confidence_sets": {"5": {"selected_numbers": [1, 2, 3, 4, 6]}}},
                "back": {"confidence_sets": {
                    "1": {"selected_numbers": [1]},
                    "2": {"selected_numbers": [1, 2]},
                }},
            }},
        }

    def test_dlt_2026_fixed_and_floating_tiers(self):
        first = ticket_prize("dlt", "2026087", 5, 2)
        self.assertEqual(first["prize_tier"], 1)
        self.assertEqual(first["fixed_prize_yuan"], 5000000.0)
        self.assertFalse(first["is_floating_prize"])

        third = ticket_prize("dlt", "2026087", 4, 2)
        self.assertEqual(third["prize_tier"], 3)
        self.assertEqual(third["fixed_prize_yuan"], 6666.0)

    def test_dlt_2026_promotion_is_explicit(self):
        prize = ticket_prize("dlt", "2026050", 3, 0)
        self.assertEqual(prize["prize_tier"], 6)
        self.assertEqual(prize["fixed_prize_yuan"], 22.5)

    def test_ssq_second_prize_is_known_fixed_amount(self):
        prize = ticket_prize("ssq", "2026014", 6, 0)
        self.assertEqual(prize["prize_tier"], 2)
        self.assertEqual(prize["fixed_prize_yuan"], 100000.0)
        self.assertFalse(prize["is_floating_prize"])

    def test_group_average_is_total_divided_by_complete_ticket_count(self):
        metric = ticket_group_prize_metrics([self.row()], 5, 2)
        group = metric["groups"][0]
        self.assertEqual(group["ticket_count"], 1)
        self.assertEqual(group["known_prize_total_yuan"], 6666.0)
        self.assertEqual(group["average_known_prize_yuan"], 6666.0)

    def test_incomplete_back_group_is_not_a_legal_ticket_group(self):
        group = ticket_group_prize_metrics([self.row()], 5, 1)["groups"][0]
        self.assertEqual(group["ticket_count"], 0)
        self.assertFalse(group["valid_complete_ticket_group"])
        self.assertIsNone(group["average_known_prize_yuan"])

    def test_ranked_ticket_partitions_use_total_prize_divided_by_n(self):
        row = self.row()
        row["phase4e17_per_number_feature_model"]["zones"]["front"]["number_observations"] = [
            {"number": n, "candidate_score": float(36 - n)} for n in range(1, 36)
        ]
        row["phase4e17_per_number_feature_model"]["zones"]["back"]["number_observations"] = [
            {"number": n, "candidate_score": float(13 - n)} for n in range(1, 13)
        ]
        metric = ranked_ticket_partition_prize_metrics(row, "dlt", (1000, 5000))
        self.assertEqual(tuple(metric["partitions"]), (1000, 5000))
        for value in metric["partitions"].values():
            self.assertEqual(
                value["average_prize_yuan"],
                value["known_prize_total_yuan"] / value["partition_size"],
            )


if __name__ == "__main__":
    unittest.main()
