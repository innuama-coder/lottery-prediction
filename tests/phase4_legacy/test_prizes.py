from __future__ import annotations

import unittest

from lottery_system.phase4.prizes import (
    DLT_PRIZES,
    SSQ_PRIZES,
    PrizeRuleViolation,
    calculate_dlt_prize,
    calculate_ssq_prize,
)
from lottery_system.phase4.bonus import DLT_FIXED_RULE, SSQ_FIXED_RULE, registered_rule_version


class PrizeRuleTests(unittest.TestCase):
    def test_ssq_all_states_are_deterministic_and_first_two_are_frozen(self) -> None:
        self.assertEqual((SSQ_PRIZES[1], SSQ_PRIZES[2]), (5_000_000, 100_000))
        awards = [calculate_ssq_prize(front, blue) for front in range(7) for blue in range(2)]
        self.assertEqual(sum(a.won for a in awards), 10)
        self.assertEqual(calculate_ssq_prize(6, 1).amount_yuan, 5_000_000)
        self.assertEqual(calculate_ssq_prize(6, 0).amount_yuan, 100_000)
        self.assertEqual(calculate_ssq_prize(0, 0).amount_yuan, 0)

    def test_dlt_patterns_are_mutually_exclusive(self) -> None:
        awards = [
            calculate_dlt_prize(
                front, back, rule_version=DLT_FIXED_RULE
            )
            for front in range(6)
            for back in range(3)
        ]
        self.assertEqual(sum(a.won for a in awards), 13)
        self.assertEqual(calculate_dlt_prize(2, 2).tier, 6)
        self.assertEqual(calculate_dlt_prize(2, 2).amount_yuan, DLT_PRIZES[6])

    def test_dlt_fixed_patterns_and_fixed_top_prizes(self) -> None:
        awards = [
            calculate_dlt_prize(
                front, back, rule_version=DLT_FIXED_RULE
            )
            for front in range(6)
            for back in range(3)
        ]
        self.assertEqual(sum(a.won for a in awards), 13)
        self.assertEqual(calculate_dlt_prize(5, 2).amount_yuan, DLT_PRIZES[1])
        self.assertEqual(calculate_dlt_prize(5, 1).amount_yuan, DLT_PRIZES[2])

    def test_promotions_and_invalid_states_fail_closed(self) -> None:
        self.assertEqual(
            calculate_dlt_prize(3, 0, rule_version=DLT_FIXED_RULE).amount_yuan,
            5,
        )
        for call in (
            lambda: calculate_ssq_prize(7, 0),
            lambda: calculate_ssq_prize(6, 2),
            lambda: calculate_dlt_prize(6, 0),
        ):
            with self.assertRaises(PrizeRuleViolation):
                call()

    def test_ssq_three_red_without_blue_is_not_a_prize(self) -> None:
        self.assertFalse(calculate_ssq_prize(3, 0).won)

    def test_issue_only_validates_format_and_never_switches_rule(self) -> None:
        self.assertEqual(registered_rule_version("ssq", "2024001"), SSQ_FIXED_RULE)
        self.assertEqual(registered_rule_version("dlt", "2024001"), DLT_FIXED_RULE)
        self.assertEqual(registered_rule_version("dlt", "2026014"), DLT_FIXED_RULE)
