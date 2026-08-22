from __future__ import annotations

import unittest

from lottery_system.phase4.bonus import DLT_FIXED_RULE, SSQ_FIXED_RULE, fixed_bonus
from lottery_system.phase4.prize_metrics import full_space_oracle, group_prize_metrics
from scripts.phase4e23_group_prize_eval.run_group_prize_eval import (
    GROUP_SIZES,
    STRATEGIES,
    build_group,
    ticket_hit_state,
)


GAME_SPEC = {
    "ssq": (33, 6, 16, 1, SSQ_FIXED_RULE),
    "dlt": (35, 5, 12, 2, DLT_FIXED_RULE),
}


class CandidateGroupTests(unittest.TestCase):
    def test_all_groups_have_exactly_k_unique_legal_tickets(self):
        for game, (front_n, front_k, back_n, back_k, _rule) in GAME_SPEC.items():
            for strategy in STRATEGIES:
                for k in GROUP_SIZES:
                    with self.subTest(game=game, strategy=strategy, k=k):
                        group = build_group(game, strategy, k)
                        self.assertEqual(len(group), k)
                        self.assertEqual(len(set(group)), k)
                        for front, back in group:
                            self.assertEqual(front, tuple(sorted(front)))
                            self.assertEqual(back, tuple(sorted(back)))
                            self.assertEqual(len(front), front_k)
                            self.assertEqual(len(back), back_k)
                            self.assertEqual(len(set(front)), front_k)
                            self.assertEqual(len(set(back)), back_k)
                            self.assertTrue(all(1 <= number <= front_n for number in front))
                            self.assertTrue(all(1 <= number <= back_n for number in back))

    def test_construction_is_deterministic(self):
        for game in GAME_SPEC:
            for strategy in STRATEGIES:
                self.assertEqual(build_group(game, strategy, 1_000), build_group(game, strategy, 1_000))


class PrizeIntegrationTests(unittest.TestCase):
    def test_single_ticket_hit_state_agrees_with_fixed_bonus(self):
        examples = {
            "ssq": (((1, 2, 3, 4, 5, 6), (1,)), {"front_numbers": [1, 2, 3, 4, 5, 6], "back_numbers": [1]}),
            "dlt": (((1, 2, 3, 4, 5), (1, 2)), {"front_numbers": [1, 2, 3, 4, 9], "back_numbers": [2, 3]}),
        }
        for game, (ticket, draw) in examples.items():
            rule = GAME_SPEC[game][4]
            state = ticket_hit_state(ticket, draw)
            direct = fixed_bonus(game, rule, *state)
            metrics = group_prize_metrics(game, rule, [state])
            self.assertEqual(metrics["prize_total_yuan"], direct["fixed_prize_yuan"])
            self.assertEqual(metrics["winning_ticket_count"], int(direct["prize_tier"] is not None))

    def test_group_prize_metrics_fields_and_integer_total(self):
        required = {
            "ticket_count", "winning_ticket_count", "prize_total_yuan",
            "average_prize_yuan", "win_rate", "prize_tier_ticket_counts",
        }
        for game, spec in GAME_SPEC.items():
            result = group_prize_metrics(game, spec[4], [(0, 0), (spec[1], spec[3])])
            self.assertTrue(required.issubset(result))
            self.assertIs(type(result["prize_total_yuan"]), int)

    def test_full_space_oracle_reproduces_frozen_values(self):
        expected = {
            "ssq": (17_721_088, 1_188_988, 15_117_950),
            "dlt": (21_425_712, 1_429_197, 18_890_405),
        }
        for game, values in expected.items():
            oracle = full_space_oracle(game)
            self.assertEqual(
                (oracle["total_ticket_count"], oracle["winning_ticket_count"], oracle["fixed_prize_total_yuan"]),
                values,
            )
            self.assertTrue(oracle["matches_frozen_oracle"])


if __name__ == "__main__":
    unittest.main()
