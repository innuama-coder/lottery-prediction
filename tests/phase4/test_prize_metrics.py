from __future__ import annotations

import unittest

from lottery_system.phase4.bonus import (
    BONUS_CONTRACT_FINGERPRINT,
    DLT_FIXED_RULE,
    SSQ_FIXED_RULE,
    fixed_bonus,
)
from lottery_system.phase4.prize_metrics import (
    FROZEN_ORACLE,
    PrizeMetricsViolation,
    full_space_oracle,
    group_prize_metrics,
)

# Frozen contract fingerprint recorded in the bonus-hardening delivery.
CONTRACT_FINGERPRINT = "0c57745377d7821a26fb3b8bd954d0010c1c4410c51643e6a5650e53aabad2d1"


class GroupPrizeMetricsTests(unittest.TestCase):
    def test_single_ticket_first_and_second_prizes_ssq(self):
        self.assertEqual(
            group_prize_metrics("ssq", SSQ_FIXED_RULE, [(6, 1)]),
            {
                "game": "ssq",
                "rule_version": SSQ_FIXED_RULE,
                "ticket_count": 1,
                "winning_ticket_count": 1,
                "prize_total_yuan": 5_000_000,
                "average_prize_yuan": 5_000_000.0,
                "win_rate": 1.0,
                "prize_tier_ticket_counts": {"1": 1},
                "bonus_contract_fingerprint": BONUS_CONTRACT_FINGERPRINT,
            },
        )
        self.assertEqual(
            group_prize_metrics("ssq", SSQ_FIXED_RULE, [(6, 0)])["prize_total_yuan"],
            100_000,
        )

    def test_dlt_single_ticket_tiers(self):
        self.assertEqual(
            group_prize_metrics("dlt", DLT_FIXED_RULE, [(5, 2)])["prize_total_yuan"],
            5_000_000,
        )
        self.assertEqual(
            group_prize_metrics("dlt", DLT_FIXED_RULE, [(0, 2)])["prize_total_yuan"],
            5,
        )

    def test_non_winning_state_contributes_zero(self):
        result = group_prize_metrics("ssq", SSQ_FIXED_RULE, [(3, 0)])
        self.assertEqual(result["prize_total_yuan"], 0)
        self.assertEqual(result["winning_ticket_count"], 0)
        self.assertEqual(result["win_rate"], 0.0)
        self.assertEqual(result["prize_tier_ticket_counts"], {})

    def test_partition_aggregates_tier_counts_and_win_rate(self):
        # Two winners (tier 6 at 5 yuan each) and one non-winner.
        result = group_prize_metrics("ssq", SSQ_FIXED_RULE, [(2, 1), (1, 1), (3, 0)])
        self.assertEqual(result["ticket_count"], 3)
        self.assertEqual(result["winning_ticket_count"], 2)
        self.assertEqual(result["prize_total_yuan"], 10)
        self.assertEqual(result["average_prize_yuan"], 10 / 3)
        self.assertEqual(result["win_rate"], 2 / 3)
        self.assertEqual(result["prize_tier_ticket_counts"], {"6": 2})

    def test_delegates_to_bonus_for_every_ticket(self):
        states = [(6, 1), (5, 1), (0, 1), (3, 0)]
        expected_total = sum(
            int(fixed_bonus("ssq", SSQ_FIXED_RULE, f, b)["fixed_prize_yuan"])
            for f, b in states
        )
        self.assertEqual(
            group_prize_metrics("ssq", SSQ_FIXED_RULE, states)["prize_total_yuan"],
            expected_total,
        )


class FullSpaceOracleTests(unittest.TestCase):
    def test_ssq_matches_frozen_oracle(self):
        oracle = full_space_oracle("ssq")
        self.assertTrue(oracle["matches_frozen_oracle"])
        self.assertEqual(oracle["total_ticket_count"], FROZEN_ORACLE["ssq"]["ticket_count"])
        self.assertEqual(oracle["winning_ticket_count"], FROZEN_ORACLE["ssq"]["winning_ticket_count"])
        self.assertEqual(oracle["fixed_prize_total_yuan"], FROZEN_ORACLE["ssq"]["prize_total_yuan"])
        self.assertEqual(oracle["total_ticket_count"], 17_721_088)
        self.assertEqual(oracle["winning_ticket_count"], 1_188_988)
        self.assertEqual(oracle["fixed_prize_total_yuan"], 15_117_950)

    def test_dlt_matches_frozen_oracle(self):
        oracle = full_space_oracle("dlt")
        self.assertTrue(oracle["matches_frozen_oracle"])
        self.assertEqual(oracle["total_ticket_count"], 21_425_712)
        self.assertEqual(oracle["winning_ticket_count"], 1_429_197)
        self.assertEqual(oracle["fixed_prize_total_yuan"], 18_890_405)

    def test_tier_winning_counts_sum_to_winning_total(self):
        for game in ("ssq", "dlt"):
            oracle = full_space_oracle(game)
            self.assertEqual(
                sum(oracle["tier_winning_counts"].values()),
                oracle["winning_ticket_count"],
            )


class IsolationAndFailClosedTests(unittest.TestCase):
    def test_game_isolation_rejects_cross_game_rule(self):
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("ssq", DLT_FIXED_RULE, [(6, 1)])
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("dlt", SSQ_FIXED_RULE, [(5, 2)])

    def test_unknown_rule_version_fails_closed(self):
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("dlt", "DLT_PRIZE_2019_9TIER", [(5, 2)])
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("dlt", "DLT_PRIZE_2026_7TIER", [(5, 2)])

    def test_unknown_game_fails_closed(self):
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("ssq8", SSQ_FIXED_RULE, [(0, 0)])
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("k3", DLT_FIXED_RULE, [(0, 0)])
        with self.assertRaises(PrizeMetricsViolation):
            full_space_oracle("k3")
        with self.assertRaises(PrizeMetricsViolation):
            full_space_oracle("foobar")

    def test_empty_partition_fails_closed(self):
        with self.assertRaises(PrizeMetricsViolation):
            group_prize_metrics("ssq", SSQ_FIXED_RULE, [])

    def test_malformed_hit_state_fails_closed(self):
        for bad in [(6,), (6, 1, 2), "61", (6, "1"), (None, 1), 6]:
            with self.assertRaises(PrizeMetricsViolation, msg=repr(bad)):
                group_prize_metrics("ssq", SSQ_FIXED_RULE, [bad])

    def test_out_of_bounds_hits_fail_closed_via_bonus(self):
        with self.assertRaises(ValueError):
            group_prize_metrics("ssq", SSQ_FIXED_RULE, [(7, 0)])
        with self.assertRaises(ValueError):
            group_prize_metrics("ssq", SSQ_FIXED_RULE, [(6, 2)])
        with self.assertRaises(ValueError):
            group_prize_metrics("dlt", DLT_FIXED_RULE, [(6, 2)])
        with self.assertRaises(ValueError):
            group_prize_metrics("dlt", DLT_FIXED_RULE, [(-1, 0)])

    def test_dlt_has_single_fixed_rule_only(self):
        # No "new rule / old rule" switch: DLT evaluates one fixed policy and
        # its full-space totals differ from SSQ (game isolation).
        self.assertNotEqual(FROZEN_ORACLE["ssq"], FROZEN_ORACLE["dlt"])
        self.assertNotEqual(full_space_oracle("ssq")["total_ticket_count"], full_space_oracle("dlt")["total_ticket_count"])


class ContractFingerprintTests(unittest.TestCase):
    def test_fingerprint_matches_frozen_contract(self):
        self.assertEqual(BONUS_CONTRACT_FINGERPRINT, CONTRACT_FINGERPRINT)
        for game in ("ssq", "dlt"):
            self.assertEqual(
                group_prize_metrics(game, _rule(game), [_any_state(game)])["bonus_contract_fingerprint"],
                CONTRACT_FINGERPRINT,
            )
            self.assertEqual(full_space_oracle(game)["bonus_contract_fingerprint"], CONTRACT_FINGERPRINT)


def _rule(game: str) -> str:
    return SSQ_FIXED_RULE if game == "ssq" else DLT_FIXED_RULE


def _any_state(game: str) -> tuple[int, int]:
    return (6, 1) if game == "ssq" else (5, 2)


if __name__ == "__main__":
    unittest.main()
