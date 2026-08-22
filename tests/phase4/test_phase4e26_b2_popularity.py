from __future__ import annotations

import unittest

from lottery_system.phase4.parimutuel import DLT_PRIZE_PARIMUTUEL_v1, expected_ticket_value
from lottery_system.phase4.popularity import number_popularity_weight, ticket_popularity_weight


class PopularityProxyTests(unittest.TestCase):
    def test_number_boundary_and_neutral_back(self):
        self.assertAlmostEqual(number_popularity_weight(31, "front", birthday_bias=0.2), 1.2)
        self.assertAlmostEqual(number_popularity_weight(32, "front", birthday_bias=0.2), 0.8)
        self.assertEqual(number_popularity_weight(1, "back", birthday_bias=0.9), 1.0)
        self.assertEqual(number_popularity_weight(16, "back"), 1.0)

    def test_ticket_direction_and_bias_monotonicity(self):
        birthday = (1, 2, 3, 4, 5)
        anti = (31, 32, 33, 34, 35)
        back = (1, 2)
        self.assertGreater(ticket_popularity_weight(birthday, back), 1.0)
        self.assertLess(ticket_popularity_weight(anti, back), 1.0)
        self.assertGreater(
            ticket_popularity_weight(birthday, back, birthday_bias=0.3),
            ticket_popularity_weight(birthday, back, birthday_bias=0.1),
        )
        self.assertLess(
            ticket_popularity_weight(anti, back, birthday_bias=0.3),
            ticket_popularity_weight(anti, back, birthday_bias=0.1),
        )

    def test_invalid_ticket_and_number_inputs(self):
        for args in ((0, "front"), (36, "front"), (17, "back"), (1, "middle")):
            with self.assertRaises(ValueError):
                number_popularity_weight(*args)
        with self.assertRaises(ValueError):
            ticket_popularity_weight((2, 1, 3, 4, 5), (1, 2))
        with self.assertRaises(ValueError):
            ticket_popularity_weight((1, 2, 3, 4, 36), (1, 2))

    def test_ev_decreases_with_popularity(self):
        inputs = dict(tier1_pool=100_000_000, tier2_pool=20_000_000, total_bets=50_000_000)
        cold = expected_ticket_value("dlt", DLT_PRIZE_PARIMUTUEL_v1, popularity_weight=0.5, **inputs)["total_ev"]
        hot = expected_ticket_value("dlt", DLT_PRIZE_PARIMUTUEL_v1, popularity_weight=1.5, **inputs)["total_ev"]
        self.assertGreater(cold, hot)


if __name__ == "__main__":
    unittest.main()
