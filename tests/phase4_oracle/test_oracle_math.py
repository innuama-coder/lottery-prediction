from __future__ import annotations

import itertools
import json
import sys
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "phase4_independent"
sys.path.insert(0, str(SCRIPTS))

from oracle_math import (  # noqa: E402
    combinations_with_scores,
    effect_ticks,
    full_rule_ticks,
    guard_vectors,
    joint_histogram,
    normalize_ticks,
    order_key,
    partition_direct,
    partition_dp,
    m0_real_rule_oracle,
    rank_bounds,
    top_joint_rows,
    zone_histogram_direct,
    zone_histogram_dp,
)
from oracle_metrics import build_metric_vectors  # noqa: E402
from oracle_validation import (  # noqa: E402
    validate_full_rule_spec,
    validate_m0_results,
    validate_metric_vectors_independent,
    validate_probability_contract,
)


class OracleMathTests(unittest.TestCase):
    def test_normalization_and_frozen_effect_bounds(self) -> None:
        self.assertEqual(normalize_ticks([7, 8, 6]), (0, 1, -1))
        self.assertEqual(effect_ticks(2048)[0], 0)
        self.assertEqual(min(effect_ticks(2048)), -4096)
        self.assertEqual(max(effect_ticks(2048, sign=-1)), 4096)
        with self.assertRaises(ValueError):
            normalize_ticks([0, 4097])

    def test_direct_and_dp_histograms_match(self) -> None:
        ticks = (0, 1, 2, 4, 8, 16)
        self.assertEqual(zone_histogram_direct(6, 3, ticks), zone_histogram_dp(6, 3, ticks))
        direct = partition_direct(combinations_with_scores(6, 3, ticks))
        dynamic = partition_dp(ticks, 3)
        self.assertLess(abs(direct - dynamic), Decimal("1e-75"))

    def test_m0_is_one_full_space_tie(self) -> None:
        histogram = zone_histogram_direct(10, 3, (0,) * 10)
        self.assertEqual(histogram, {0: 120})
        lower, upper, midrank = rank_bounds(histogram, 0)
        self.assertEqual((lower, upper, midrank), (1, 120, Decimal("60.5")))

    def test_heap_top_matches_direct_product_sort(self) -> None:
        front = sorted(combinations_with_scores(7, 3, (0, 3, 1, 4, 2, -1, 0)), key=lambda row: (-row[0], row[1]))
        back = sorted(combinations_with_scores(5, 2, (0, 2, -2, 1, 0)), key=lambda row: (-row[0], row[1]))
        expected = sorted(
            [(a_score + b_score, a, b) for a_score, a in front for b_score, b in back],
            key=lambda row: (-row[0], row[1] + row[2]),
        )[:100]
        self.assertEqual(top_joint_rows(front, back, limit=100), expected)

    def test_order_key_uses_frozen_width_and_domain(self) -> None:
        self.assertEqual(order_key(-28672), "P4Q1024-00000")
        self.assertEqual(order_key(0), "P4Q1024-28672")
        self.assertEqual(order_key(28672), "P4Q1024-57344")

    def test_full_rule_tick_spec(self) -> None:
        self.assertEqual(full_rule_ticks(8), (0, -8, -16, -24, -40, -48, -56, -64))
        ticks = full_rule_ticks(16)
        self.assertEqual((min(ticks), max(ticks), ticks[4]), (-64, 0, -32))

    def test_metric_vectors_cover_boundary_and_insufficient_state(self) -> None:
        vectors = build_metric_vectors()
        self.assertEqual(len(vectors["per_forecast"]), 30)
        self.assertEqual(vectors["window_30"]["observation_count"], 30)
        self.assertEqual(vectors["insufficient_observation_vector"]["observation_count"], 29)
        self.assertFalse(vectors["insufficient_observation_vector"]["numeric_metrics_present"])
        self.assertEqual(sum(row["count"] for row in vectors["window_30"]["reliability_bins"]), 270)
        residuals = validate_metric_vectors_independent(vectors)
        self.assertLessEqual(Decimal(residuals["maximum_probability_absolute_error"]), Decimal("1e-40"))
        self.assertLessEqual(Decimal(residuals["maximum_log_absolute_error"]), Decimal("1e-40"))

    def test_real_rule_m0_decimal80_normalization(self) -> None:
        contract = json.loads((Path(__file__).resolve().parents[2] / "config/phase4/probability-ranking-contract.json").read_text())
        bundle = {"games": [m0_real_rule_oracle(game, contract["games"][game], contract["top_k"]) for game in ("ssq", "dlt")]}
        residuals = validate_m0_results(bundle)
        self.assertTrue(all(Decimal(value) <= Decimal("1e-45") for value in residuals.values()))

    def test_contract_and_full_rule_deletion_mutations_fail_closed(self) -> None:
        root = Path(__file__).resolve().parents[2]
        probability = json.loads((root / "config/phase4/probability-ranking-contract.json").read_text())
        full_spec = json.loads((root / "qualification-design/full-rule-spec-candidate.json").read_text())
        for key in ("normalization_tolerance", "forecast_size", "top_k", "games"):
            mutation = deepcopy(probability)
            del mutation[key]
            with self.subTest(probability_deleted=key), self.assertRaises(ValueError):
                validate_probability_contract(mutation)
        for key in ("games", "top_k", "absolute_error_bound", "decimal_precision", "scale"):
            mutation = deepcopy(full_spec)
            del mutation[key]
            with self.subTest(full_rule_deleted=key), self.assertRaises(ValueError):
                validate_full_rule_spec(mutation)
        mutation = deepcopy(full_spec)
        mutation["games"]["ssq"]["space_size"] -= 1
        with self.assertRaises(ValueError):
            validate_full_rule_spec(mutation)

    def test_guard_vectors_are_stable_positive_and_cross_a_cutoff(self) -> None:
        vectors = guard_vectors()
        self.assertTrue(vectors["theoretical_minimum_gt_1e_32"])
        self.assertNotEqual(vectors["theoretical_minimum_serialized_50_places"], "0." + "0" * 50)
        self.assertTrue(vectors["input_permutation"]["stable"])
        self.assertTrue(any(row["tie_crosses_cutoff"] for row in vectors["cross_top_k_ties"]))


if __name__ == "__main__":
    unittest.main()
