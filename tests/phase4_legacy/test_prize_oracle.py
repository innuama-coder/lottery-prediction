from __future__ import annotations

import math
import unittest

from lottery_system.phase4.bonus import (
    DLT_FIXED_RULE,
    SSQ_FIXED_RULE,
    fixed_bonus,
)


class IndependentPrizeOracleTests(unittest.TestCase):
    SSQ_ORACLE = {
        (6, 1): 1,
        (6, 0): 2,
        (5, 1): 3,
        (5, 0): 4,
        (4, 1): 4,
        (4, 0): 5,
        (3, 1): 5,
        (2, 1): 6,
        (1, 1): 6,
        (0, 1): 6,
    }
    DLT_ORACLE = {
        (5, 2): 1,
        (5, 1): 2,
        (5, 0): 3,
        (4, 2): 3,
        (4, 1): 4,
        (4, 0): 5,
        (3, 2): 5,
        (3, 1): 6,
        (2, 2): 6,
        (3, 0): 7,
        (2, 1): 7,
        (1, 2): 7,
        (0, 2): 7,
    }

    def test_fixed_state_tables_match_independent_oracle(self) -> None:
        for front in range(7):
            for back in range(2):
                self.assertEqual(
                    fixed_bonus("ssq", SSQ_FIXED_RULE, front, back)["prize_tier"],
                    self.SSQ_ORACLE.get((front, back)),
                )
        for front in range(6):
            for back in range(3):
                self.assertEqual(
                    fixed_bonus("dlt", DLT_FIXED_RULE, front, back)["prize_tier"],
                    self.DLT_ORACLE.get((front, back)),
                )

    def test_fixed_full_space_winning_probability_and_average_are_exact(self) -> None:
        cases = (
            ("ssq", SSQ_FIXED_RULE, 33, 6, 16, 1, 1_188_988, 15_117_950),
            ("dlt", DLT_FIXED_RULE, 35, 5, 12, 2, 1_429_197, 18_890_405),
        )
        for game, version, front_n, front_k, back_n, back_k, expected_winners, expected_payout in cases:
            total_tickets = math.comb(front_n, front_k) * math.comb(back_n, back_k)
            winners = 0
            payout = 0
            for front_hits in range(front_k + 1):
                for back_hits in range(back_k + 1):
                    ways = (
                        math.comb(front_k, front_hits)
                        * math.comb(front_n - front_k, front_k - front_hits)
                        * math.comb(back_k, back_hits)
                        * math.comb(back_n - back_k, back_k - back_hits)
                    )
                    amount = int(
                        fixed_bonus(game, version, front_hits, back_hits)[
                            "fixed_prize_yuan"
                        ]
                    )
                    winners += ways * (amount > 0)
                    payout += ways * amount
            self.assertEqual(total_tickets, 17_721_088 if game == "ssq" else 21_425_712)
            self.assertEqual(winners, expected_winners)
            self.assertEqual(payout, expected_payout)


if __name__ == "__main__":
    unittest.main()
