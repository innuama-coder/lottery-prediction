import itertools
import sys
import unittest

sys.path.insert(0, "scripts/phase4e17")
from run_per_number_feature_model import ticket_prize

from lottery_system.phase4.bonus import (
    BONUS_CONTRACT_FINGERPRINT,
    DLT_FIXED_PRIZES,
    DLT_FIXED_RULE,
    DLT_TIER_STATES,
    SSQ_FIXED_PRIZES,
    SSQ_FIXED_RULE,
    SSQ_TIER_STATES,
    fixed_bonus,
    registered_rule_version,
)


class Phase4E21BonusHardeningTests(unittest.TestCase):
    SSQ_EXPECTED = {(6, 1): 1, (6, 0): 2, (5, 1): 3, (5, 0): 4, (4, 1): 4, (4, 0): 5, (3, 1): 5, (2, 1): 6, (1, 1): 6, (0, 1): 6}
    DLT_EXPECTED = {(5, 2): 1, (5, 1): 2, (5, 0): 3, (4, 2): 3, (4, 1): 4, (4, 0): 5, (3, 2): 5, (3, 1): 6, (2, 2): 6, (3, 0): 7, (2, 1): 7, (1, 2): 7, (0, 2): 7}

    def assert_exhaustive(self, game, version, front_range, back_range, expected, prizes, tables):
        flattened = list(itertools.chain.from_iterable(tables.values()))
        self.assertEqual(len(flattened), len(set(flattened)))
        for front_hits in front_range:
            for back_hits in back_range:
                result = fixed_bonus(game, version, front_hits, back_hits)
                tier = expected.get((front_hits, back_hits))
                self.assertEqual(result["prize_tier"], tier)
                self.assertEqual(result["fixed_prize_yuan"], prizes[tier] if tier else 0)
                self.assertIs(type(result["fixed_prize_yuan"]), int)
                self.assertFalse(result["is_floating_prize"])

    def test_exhaustive_ssq(self):
        self.assert_exhaustive("ssq", SSQ_FIXED_RULE, range(7), range(2), self.SSQ_EXPECTED, SSQ_FIXED_PRIZES, SSQ_TIER_STATES)

    def test_exhaustive_dlt(self):
        self.assertEqual(set(DLT_TIER_STATES), set(range(1, 8)))
        self.assert_exhaustive("dlt", DLT_FIXED_RULE, range(6), range(3), self.DLT_EXPECTED, DLT_FIXED_PRIZES, DLT_TIER_STATES)

    def test_first_and_second_prizes_are_frozen(self):
        for game, version, first_state, second_state in (("ssq", SSQ_FIXED_RULE, (6, 1), (6, 0)), ("dlt", DLT_FIXED_RULE, (5, 2), (5, 1))):
            self.assertEqual(fixed_bonus(game, version, *first_state)["fixed_prize_yuan"], 5_000_000)
            self.assertEqual(fixed_bonus(game, version, *second_state)["fixed_prize_yuan"], 100_000)

    def test_metadata_cannot_change_bonus(self):
        for game, version, front_hits, back_hits in (("ssq", SSQ_FIXED_RULE, 6, 1), ("ssq", SSQ_FIXED_RULE, 3, 0), ("dlt", DLT_FIXED_RULE, 2, 2), ("dlt", DLT_FIXED_RULE, 0, 2)):
            expected = fixed_bonus(game, version, front_hits, back_hits)
            mutated = fixed_bonus(game, version, front_hits, back_hits, special_payout=999999, fuyun_prize=888888, promotion_ids=["X"])
            self.assertEqual(mutated, expected)

    def test_wrapper_uses_single_registered_policy(self):
        expected = ticket_prize("dlt", "2025001", 0, 2, prize_rule_version=DLT_FIXED_RULE, special_payout=1_000_000)
        mutated = ticket_prize("dlt", "2026999", 0, 2, prize_rule_version=DLT_FIXED_RULE, special_payout=0)
        self.assertEqual(mutated, expected)
        self.assertEqual(expected, {"prize_tier": 7, "fixed_prize_yuan": 5, "is_floating_prize": False})

    def test_rule_isolation(self):
        self.assertEqual(fixed_bonus("ssq", SSQ_FIXED_RULE, 3, 0), fixed_bonus("ssq", SSQ_FIXED_RULE, 3, 0, dlt_rule_version=DLT_FIXED_RULE))
        self.assertEqual(fixed_bonus("dlt", DLT_FIXED_RULE, 3, 0), fixed_bonus("dlt", DLT_FIXED_RULE, 3, 0, ssq_rule_version=SSQ_FIXED_RULE))

    def test_unregistered_rule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unregistered prize rule"):
            fixed_bonus("dlt", "DLT_PROMO_ISSUE_BRANCH", 5, 2)

    def test_ssq_three_red_without_blue_is_not_a_prize(self):
        self.assertIsNone(fixed_bonus("ssq", SSQ_FIXED_RULE, 3, 0)["prize_tier"])

    def test_dlt_low_tier_states_match_oracle(self):
        expected = {(4, 1): 4, (4, 0): 5, (3, 2): 5, (3, 1): 6, (2, 2): 6, (3, 0): 7, (2, 1): 7, (1, 2): 7, (0, 2): 7, (2, 0): None, (1, 1): None, (0, 1): None}
        for state, tier in expected.items():
            self.assertEqual(fixed_bonus("dlt", DLT_FIXED_RULE, *state)["prize_tier"], tier)

    def test_invalid_hit_counts_are_rejected(self):
        for args in (("ssq", SSQ_FIXED_RULE, -1, 0), ("ssq", SSQ_FIXED_RULE, 7, 0), ("ssq", SSQ_FIXED_RULE, 6, 2), ("dlt", DLT_FIXED_RULE, -1, 0), ("dlt", DLT_FIXED_RULE, 6, 0), ("dlt", DLT_FIXED_RULE, 5, 3)):
            with self.assertRaisesRegex(ValueError, "outside"):
                fixed_bonus(*args)
        for args in (("ssq", SSQ_FIXED_RULE, True, 0), ("dlt", DLT_FIXED_RULE, 2.0, 1)):
            with self.assertRaisesRegex(ValueError, "integers"):
                fixed_bonus(*args)

    def test_issue_is_validated_but_never_selects_dlt_rule(self):
        for issue in ("2025001", "2026013", "2026014", "2099999"):
            self.assertEqual(registered_rule_version("dlt", issue), DLT_FIXED_RULE)
        for issue in (None, 2026014, "", "abc", "20261", "2026000", "1999999", "2100001"):
            with self.assertRaisesRegex(ValueError, "valid YYYYNNN"):
                registered_rule_version("dlt", issue)

    def test_bonus_contract_fingerprint_is_stable_and_nonempty(self):
        self.assertRegex(BONUS_CONTRACT_FINGERPRINT, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
