from __future__ import annotations

import unittest

from lottery_system.phase4.bonus import DLT_FIXED_RULE, SSQ_FIXED_RULE, fixed_bonus
from lottery_system.phase4.parimutuel import (
    DLT_PRIZE_PARIMUTUEL_v1,
    SSQ_PRIZE_PARIMUTUEL_v1,
    expected_ticket_value,
    parimutuel_bonus,
)


class ParimutuelBonusTests(unittest.TestCase):
    def payout(self, game, rule, state, **overrides):
        inputs = dict(tier1_pool=10_000_000, tier1_winners=3, tier2_pool=1_000_001, tier2_winners=3)
        inputs.update(overrides)
        return parimutuel_bonus(game, rule, *state, **inputs)

    def test_low_tiers_reuse_frozen_values(self):
        self.assertEqual(self.payout("ssq", SSQ_PRIZE_PARIMUTUEL_v1, (5, 1))["prize_yuan"], 3_000)
        self.assertEqual(self.payout("dlt", DLT_PRIZE_PARIMUTUEL_v1, (0, 2))["prize_yuan"], 5)
        self.assertFalse(self.payout("ssq", SSQ_PRIZE_PARIMUTUEL_v1, (5, 1))["is_parimutuel"])

    def test_top_tiers_floor_pool_split(self):
        first = self.payout("ssq", SSQ_PRIZE_PARIMUTUEL_v1, (6, 1))
        second = self.payout("dlt", DLT_PRIZE_PARIMUTUEL_v1, (5, 1))
        self.assertEqual(first["prize_yuan"], 3_333_333)
        self.assertEqual(second["prize_yuan"], 333_333)
        self.assertTrue(first["is_parimutuel"])
        self.assertTrue(second["is_parimutuel"])

    def test_nonwinner_and_invalid_inputs(self):
        self.assertEqual(self.payout("ssq", SSQ_PRIZE_PARIMUTUEL_v1, (3, 0))["prize_yuan"], 0)
        with self.assertRaises(ValueError):
            self.payout("ssq", SSQ_PRIZE_PARIMUTUEL_v1, (6, 1), tier1_winners=0)
        with self.assertRaises(ValueError):
            self.payout("bad", SSQ_PRIZE_PARIMUTUEL_v1, (0, 0))
        with self.assertRaises(ValueError):
            self.payout("ssq", DLT_PRIZE_PARIMUTUEL_v1, (6, 1))

    def test_frozen_top_tiers_are_unchanged(self):
        self.assertEqual(fixed_bonus("ssq", SSQ_FIXED_RULE, 6, 1)["fixed_prize_yuan"], 5_000_000)
        self.assertEqual(fixed_bonus("ssq", SSQ_FIXED_RULE, 6, 0)["fixed_prize_yuan"], 100_000)
        self.assertEqual(fixed_bonus("dlt", DLT_FIXED_RULE, 5, 2)["fixed_prize_yuan"], 5_000_000)
        self.assertEqual(fixed_bonus("dlt", DLT_FIXED_RULE, 5, 1)["fixed_prize_yuan"], 100_000)


class ExpectedValueTests(unittest.TestCase):
    def ev(self, **overrides):
        inputs = dict(tier1_pool=100_000_000, tier2_pool=20_000_000, total_bets=50_000_000)
        inputs.update(overrides)
        return expected_ticket_value("ssq", SSQ_PRIZE_PARIMUTUEL_v1, **inputs)

    def test_popularity_is_monotonic(self):
        cold = self.ev(popularity_weight=0.5)["total_ev"]
        uniform = self.ev(popularity_weight=1.0)["total_ev"]
        hot = self.ev(popularity_weight=2.0)["total_ev"]
        self.assertGreater(cold, uniform)
        self.assertGreater(uniform, hot)

    def test_pool_is_monotonic_and_components_sum(self):
        base = self.ev()
        larger = self.ev(tier1_pool=200_000_000)
        self.assertGreater(larger["total_ev"], base["total_ev"])
        self.assertEqual(base["total_ev"], base["low_ev"] + base["ev1"] + base["ev2"])


if __name__ == "__main__":
    unittest.main()
