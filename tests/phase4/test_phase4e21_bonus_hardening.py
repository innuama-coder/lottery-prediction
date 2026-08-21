import itertools
import sys
import unittest

sys.path.insert(0, "scripts/phase4e17")
from run_per_number_feature_model import ticket_prize

from lottery_system.phase4.bonus import (
    DLT_NEW_FIXED_PRIZES,
    DLT_NEW_RULE,
    DLT_NEW_TIER_STATES,
    SSQ_FIXED_PRIZES,
    SSQ_NEW_RULE,
    SSQ_OLD_RULE,
    SSQ_TIER_STATES,
    fixed_bonus,
)


class Phase4E21BonusHardeningTests(unittest.TestCase):
    SSQ_EXPECTED = {
        (6, 1): 1,
        (6, 0): 2,
        (5, 1): 3,
        (5, 0): 4,
        (4, 1): 4,
        (4, 0): 5,
        (3, 1): 5,
        (3, 0): 6,
        (2, 1): 6,
        (1, 1): 6,
        (0, 1): 6,
    }
    DLT_NEW_EXPECTED = {
        (5, 2): 1,
        (5, 1): 2,
        (5, 0): 3,
        (4, 2): 3,
        (4, 1): 4,
        (3, 2): 4,
        (4, 0): 5,
        (3, 1): 5,
        (2, 2): 5,
        (3, 0): 6,
        (2, 1): 6,
        (1, 2): 6,
        (0, 2): 6,
        (2, 0): 7,
        (1, 1): 7,
        (0, 1): 7,
    }

    def assert_exhaustive(
        self,
        game,
        rule_version,
        front_range,
        back_range,
        expected,
        prizes,
        tables,
    ):
        flattened = list(itertools.chain.from_iterable(tables.values()))
        self.assertEqual(len(flattened), len(set(flattened)), "tier states overlap")
        for front_hits in front_range:
            for back_hits in back_range:
                state = (front_hits, back_hits)
                result = fixed_bonus(game, rule_version, front_hits, back_hits)
                tier = expected.get(state)
                self.assertEqual(result["prize_tier"], tier, state)
                self.assertEqual(result["fixed_prize_yuan"], prizes[tier] if tier else 0, state)
                self.assertIs(type(result["fixed_prize_yuan"]), int)
                self.assertFalse(result["is_floating_prize"])

    def test_exhaustive_ssq_7_by_2_old_and_new(self):
        for version in (SSQ_OLD_RULE, SSQ_NEW_RULE):
            with self.subTest(version=version):
                self.assert_exhaustive(
                    "ssq",
                    version,
                    range(7),
                    range(2),
                    self.SSQ_EXPECTED,
                    SSQ_FIXED_PRIZES,
                    SSQ_TIER_STATES,
                )

    def test_exhaustive_dlt_6_by_3_new_rule(self):
        self.assertEqual(set(DLT_NEW_TIER_STATES), set(range(1, 8)))
        self.assert_exhaustive(
            "dlt",
            DLT_NEW_RULE,
            range(6),
            range(3),
            self.DLT_NEW_EXPECTED,
            DLT_NEW_FIXED_PRIZES,
            DLT_NEW_TIER_STATES,
        )

    def test_first_and_second_prizes_are_exact_fixed_integers(self):
        for game, versions, first_state, second_state in (
            ("ssq", (SSQ_OLD_RULE, SSQ_NEW_RULE), (6, 1), (6, 0)),
            ("dlt", (DLT_NEW_RULE,), (5, 2), (5, 1)),
        ):
            for version in versions:
                with self.subTest(game=game, version=version):
                    first = fixed_bonus(game, version, *first_state)
                    second = fixed_bonus(game, version, *second_state)
                    self.assertEqual(first["fixed_prize_yuan"], 5_000_000)
                    self.assertEqual(second["fixed_prize_yuan"], 100_000)
                    self.assertIs(type(first["fixed_prize_yuan"]), int)
                    self.assertIs(type(second["fixed_prize_yuan"]), int)

    def test_issue_and_special_payout_metadata_are_invariant(self):
        mutations = (
            {},
            {"issue": "1900001"},
            {"issue": "2099999", "special_draw": True},
            {"first_prize_amount": 99_999_999, "second_prize_amount": 1},
            {"fuyun_prize": 8_888_888, "promotion_ids": ["PROMO"]},
            {"floating_prize": "NaN", "issue_specific_branch": {"tier": 1}},
        )
        cases = (
            ("ssq", SSQ_NEW_RULE, 6, 1),
            ("ssq", SSQ_OLD_RULE, 3, 0),
            ("dlt", DLT_NEW_RULE, 2, 2),
            ("dlt", DLT_NEW_RULE, 0, 2),
        )
        for game, version, front_hits, back_hits in cases:
            expected = fixed_bonus(game, version, front_hits, back_hits)
            for metadata in mutations:
                with self.subTest(game=game, version=version, metadata=metadata):
                    self.assertEqual(
                        fixed_bonus(game, version, front_hits, back_hits, **metadata),
                        expected,
                    )

    def test_wrapper_uses_explicit_registered_version_not_mutated_issue(self):
        expected = ticket_prize(
            "dlt", "2025001", 0, 2, prize_rule_version=DLT_NEW_RULE,
            special_payout=1_000_000,
        )
        mutated = ticket_prize(
            "dlt", "2026999", 0, 2, prize_rule_version=DLT_NEW_RULE,
            special_payout=0, promotion="different",
        )
        self.assertEqual(mutated, expected)
        self.assertEqual(expected, {"prize_tier": 6, "fixed_prize_yuan": 15, "is_floating_prize": False})

    def test_ssq_and_dlt_rule_isolation(self):
        ssq_before = fixed_bonus("ssq", SSQ_NEW_RULE, 3, 0)
        dlt_before = fixed_bonus("dlt", DLT_NEW_RULE, 3, 0)
        ssq_after = fixed_bonus(
            "ssq", SSQ_NEW_RULE, 3, 0,
            dlt_rule_version=DLT_NEW_RULE, dlt_special_payout=999_999,
        )
        dlt_after = fixed_bonus(
            "dlt", DLT_NEW_RULE, 3, 0,
            ssq_rule_version=SSQ_OLD_RULE, fuyun_prize=999_999,
        )
        self.assertEqual(ssq_before, ssq_after)
        self.assertEqual(dlt_before, dlt_after)
        self.assertNotEqual(ssq_before, dlt_before)

    def test_unregistered_rule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unregistered prize rule"):
            fixed_bonus("dlt", "DLT_PROMO_ISSUE_BRANCH", 5, 2)

    def test_out_of_range_hit_counts_are_rejected(self):
        for args in (
            ("ssq", SSQ_NEW_RULE, -1, 0),
            ("ssq", SSQ_NEW_RULE, 7, 0),
            ("ssq", SSQ_NEW_RULE, 6, 2),
            ("dlt", DLT_NEW_RULE, -1, 0),
            ("dlt", DLT_NEW_RULE, 6, 0),
            ("dlt", DLT_NEW_RULE, 5, 3),
        ):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, "outside"):
                    fixed_bonus(*args)

    def test_non_integer_hit_counts_are_rejected(self):
        for args in (
            ("ssq", SSQ_NEW_RULE, True, 0),
            ("dlt", DLT_NEW_RULE, 2.0, 1),
        ):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, "integers"):
                    fixed_bonus(*args)


if __name__ == "__main__":
    unittest.main()
