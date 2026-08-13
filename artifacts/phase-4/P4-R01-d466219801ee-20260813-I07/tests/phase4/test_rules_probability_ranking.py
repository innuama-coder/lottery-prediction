from __future__ import annotations

import hashlib
import json
import math
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import lottery_system.phase4.ranking as ranking_module

from lottery_system.phase4.probability import (
    ProbabilityViolation,
    decimal_probability,
    distribution,
    estimate_ticks,
    normalization_proof,
    zone_distribution,
)
from lottery_system.phase4.ranking import (
    RankingViolation,
    probability_order_key,
    rank_bands,
    rank_histogram,
    tie_group_id,
    tie_key,
    top1000,
    top_k_coverage,
    zone_histogram,
    zone_top_rows,
)
from lottery_system.phase4.rules import RuleViolation, canonical_ticket, game_rule, normalize_ticks
from lottery_system.phase4.serialization import canonical_json_bytes, sha256_bytes


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "artifacts/phase-4-prep/p4-prep-phase4-mvp-20260813-r01-i02/work-items/T10/attempts/T10-I01/known-answers"


def _load(name: str) -> dict:
    return json.loads((ORACLE / name).read_text())


class RulesProbabilityRankingTests(unittest.TestCase):
    def test_registered_rules_and_ticket_legality(self) -> None:
        self.assertEqual(game_rule("ssq").space_size, 17_721_088)
        self.assertEqual(game_rule("dlt").space_size, 21_425_712)
        self.assertEqual(canonical_ticket("ssq", [1, 2, 3, 4, 5, 6], [16]), ((1, 2, 3, 4, 5, 6), (16,)))
        for front, back in (([1, 2, 3, 4, 5, 5], [1]), ([0, 2, 3, 4, 5, 6], [1]), ([1, 2, 3, 4, 5, 6], [17])):
            with self.assertRaises(RuleViolation):
                canonical_ticket("ssq", front, back)

    def test_small_space_vectors_match_exactly(self) -> None:
        known = _load("small-space-probability-rank.json")
        self.assertEqual((known["decimal_precision"], known["tick_bound"]), (80, 4096))
        for fixture in known["fixtures"]:
            with self.subTest(fixture=fixture["fixture_id"]):
                zone = zone_distribution(fixture["ticks"], fixture["k"])
                histogram = zone_histogram(zone.ticks, zone.k)
                rows = zone_top_rows(zone, limit=100)
                self.assertEqual([[score, count] for score, count in histogram.items()], fixture["histogram"])
                self.assertEqual(sha256_bytes(canonical_json_bytes(fixture["histogram"])), fixture["histogram_sha256"])
                self.assertEqual(rows, fixture["top_rows"])
                self.assertEqual(sha256_bytes(canonical_json_bytes(rows)), fixture["top_rows_sha256"])
                self.assertLessEqual(abs(zone.partition - Decimal(fixture["partition_dp"])), Decimal("1e-75"))

    def test_real_rule_m0_and_full_rule_candidate_vectors(self) -> None:
        m0 = _load("real-rule-m0.json")
        full = _load("full-rule-oracle.json")
        spec = json.loads((ROOT / "qualification-design/full-rule-spec-candidate.json").read_text())
        for expected in m0["games"]:
            game = expected["game"]
            rule = game_rule(game)
            model = distribution(game, [0] * rule.front_n, [0] * rule.back_n, model_contract_id="M0")
            histogram = rank_histogram(model)
            rows = top1000(model, forecast_id=f"oracle-m0-{game}-fixture")
            self.assertEqual(histogram, {0: rule.space_size})
            self.assertEqual(rows, expected["top1000"])
            self.assertEqual(sha256_bytes(canonical_json_bytes(rows)), expected["top1000_sha256"])
            proof = normalization_proof(model, histogram)
            self.assertLessEqual(Decimal(proof["absolute_residual"]), Decimal("1e-45"))
        for expected in full["results"]:
            game = expected["game"]
            rule_spec = spec["games"][game]
            raw = [0] * rule_spec["front_n"]
            for position, tick in zip(range(1, 5), spec["raw_tick_rule"]["positions_1_to_4"]):
                raw[position - 1] = tick
            for position, tick in zip(range(rule_spec["front_n"] - 3, rule_spec["front_n"] + 1), spec["raw_tick_rule"]["positions_n_minus_3_to_n"]):
                raw[position - 1] = tick
            front = normalize_ticks(raw)
            raw_back = [0] * rule_spec["back_n"]
            for position, tick in zip(range(1, 5), spec["raw_tick_rule"]["positions_1_to_4"]):
                raw_back[position - 1] = tick
            for position, tick in zip(range(rule_spec["back_n"] - 3, rule_spec["back_n"] + 1), spec["raw_tick_rule"]["positions_n_minus_3_to_n"]):
                raw_back[position - 1] = tick
            back = normalize_ticks(raw_back)
            model = distribution(game, front, back, model_contract_id=spec["spec_id"])
            histogram_rows = [[score, count] for score, count in rank_histogram(model).items()]
            rows = top1000(model)
            self.assertEqual(front, tuple(expected["front_ticks"]))
            self.assertEqual(back, tuple(expected["back_ticks"]))
            self.assertEqual(sha256_bytes(canonical_json_bytes(histogram_rows)), expected["histogram_sha256"])
            self.assertEqual(rows, expected["top1000"])
            self.assertEqual(sha256_bytes(canonical_json_bytes(rows)), expected["top1000_sha256"])
            coverage = top_k_coverage(model, rows, [10, 100, 200, 1000])
            for cell in expected["cells"]:
                self.assertLessEqual(abs(Decimal(coverage[str(cell["K"])]) - Decimal(cell["candidate_coverage"])), Decimal(cell["absolute_error_bound"]))

    def test_exact_ties_cross_cutoffs_without_approximation(self) -> None:
        guards = _load("guard-vectors.json")
        for guard in guards["cross_top_k_ties"]:
            higher_count = guard["tie_rank_lower"] - 1
            group_count = guard["tie_rank_upper"] - higher_count
            bands = rank_bands({guard["score"] + 1: higher_count, guard["score"]: group_count})
            lower, upper, _ = bands[guard["score"]]
            self.assertEqual((lower, upper), (guard["tie_rank_lower"], guard["tie_rank_upper"]))
            self.assertLessEqual(lower, guard["K"])
            self.assertGreater(upper, guard["K"])
        values = [Decimal(guards["nontransitive_approximation"][key]) for key in ("a", "b", "c")]
        self.assertNotEqual(values[0], values[1])
        self.assertNotEqual(values[1], values[2])
        self.assertEqual(probability_order_key(0), "P4Q1024-28672")
        self.assertNotEqual(tie_key("M", probability_order_key(0)), tie_key("M", probability_order_key(1)))

    def test_tick_anchor_bounds_order_and_input_permutation(self) -> None:
        self.assertEqual(normalize_ticks([7, 8, 9]), (0, 1, 2))
        for values in ([0, 4097], [0, -4097], [0, 1.5], []):
            with self.assertRaises(RuleViolation):
                normalize_ticks(values)
        with self.assertRaises(RuleViolation):
            distribution("ssq", [1] + [0] * 32, [0] * 16, model_contract_id="bad-anchor")
        zone = zone_distribution([0, 0, 1, 1, 2, 2, 3, 3, 4, 4], 3)
        baseline = zone_top_rows(zone, limit=100)
        repeated = zone_top_rows(zone_distribution(tuple(zone.ticks), 3), limit=100)
        self.assertEqual(baseline, repeated)
        self.assertEqual(probability_order_key(-28672), "P4Q1024-00000")
        self.assertEqual(probability_order_key(28672), "P4Q1024-57344")
        self.assertGreater(probability_order_key(1), probability_order_key(0))

    def test_probability_fail_closed_and_no_zero_serialization(self) -> None:
        guards = _load("guard-vectors.json")
        self.assertEqual(decimal_probability(Decimal(guards["theoretical_minimum_probability"])), guards["theoretical_minimum_serialized_50_places"])
        for value in (Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), Decimal("1e-90")):
            with self.assertRaises(ProbabilityViolation):
                decimal_probability(value)
        with self.assertRaises(RankingViolation):
            probability_order_key(28673)
        with self.assertRaises(RankingViolation):
            tie_group_id("", "0" * 64)
        with self.assertRaises(RankingViolation):
            tie_key("M0", "P4Q1024-not-a-key")
        rule = game_rule("ssq")
        m0 = distribution("ssq", [0] * rule.front_n, [0] * rule.back_n, model_contract_id="M0")
        with self.assertRaises(ProbabilityViolation):
            normalization_proof(m0, {0: 1})

    def test_estimator_closed_parameters_half_even_and_bounds(self) -> None:
        draws = [[1, 2, 3]] * 60 + [[2, 3, 4]] * 60
        ticks = estimate_ticks(draws, n=10, k=3, shrinkage=5, training_window=100, recency_half_life="none")
        self.assertEqual(ticks[0], 0)
        self.assertTrue(all(-4096 <= value <= 4096 for value in ticks))
        with self.assertRaises(ProbabilityViolation):
            estimate_ticks(draws, n=10, k=3, shrinkage=2, training_window=100, recency_half_life="none")
        with self.assertRaises(ProbabilityViolation):
            estimate_ticks(draws, n=10, k=3, shrinkage=5, training_window=25, recency_half_life="none")

    def test_complete_71_case_acceptance_matrix(self) -> None:
        observed: list[str] = []

        def reject(name: str, action: object) -> None:
            with self.subTest(case=name), self.assertRaises(RuleViolation):
                action()  # type: ignore[operator]
            observed.append(name)

        def accept(name: str, action: object) -> object:
            with self.subTest(case=name):
                value = action()  # type: ignore[operator]
            observed.append(name)
            return value

        rule = game_rule("ssq")
        model = distribution("ssq", [0] * rule.front_n, [0] * rule.back_n, model_contract_id="M0")
        forecast_id = "forecast-matrix-i02"
        rows = top1000(model, forecast_id=forecast_id)

        reject("zero", lambda: decimal_probability(Decimal(0)))
        reject("negative", lambda: decimal_probability(Decimal(-1)))
        reject("NaN", lambda: decimal_probability(Decimal("NaN")))
        reject("+Infinity", lambda: decimal_probability(Decimal("Infinity")))
        reject("-Infinity", lambda: decimal_probability(Decimal("-Infinity")))
        reject("1e-90_underflow", lambda: decimal_probability(Decimal("1e-90")))
        reject("5e-51_half_even_zero", lambda: decimal_probability(Decimal("5e-51")))
        reject("tick_above_4096", lambda: normalize_ticks([0, 4097]))
        reject("tick_below_-4096", lambda: normalize_ticks([0, -4097]))
        reject("tick_float", lambda: normalize_ticks([0, 1.0]))
        reject("tick_bool", lambda: normalize_ticks([0, True]))
        reject("tick_empty", lambda: normalize_ticks([]))
        reject("hist_k_negative", lambda: zone_histogram([0, 1], -1))
        reject("hist_k_gt_n", lambda: zone_histogram([0, 1], 3))
        reject("hist_float_tick", lambda: zone_histogram([0, 1.0], 1))
        reject("zone_distribution_k_zero", lambda: zone_distribution([0, 1], 0))
        reject("order_low", lambda: probability_order_key(-28673))
        reject("order_high", lambda: probability_order_key(28673))
        reject("order_bool", lambda: probability_order_key(True))
        reject("order_float", lambda: probability_order_key(0.0))
        reject("tie_key_malformed", lambda: tie_key("M0", "bad"))
        reject("tie_key_out_of_range", lambda: tie_key("M0", "P4Q1024-99999"))
        reject("group_empty_forecast", lambda: tie_group_id("", "0" * 64))
        reject("group_short_key", lambda: tie_group_id(forecast_id, "0" * 63))
        reject("group_long_key", lambda: tie_group_id(forecast_id, "0" * 65))
        reject("group_nonstring_key", lambda: tie_group_id(forecast_id, 0))
        reject("bands_empty", lambda: rank_bands({}))
        reject("bands_zero_count", lambda: rank_bands({0: 0}))
        reject("bands_bool_count", lambda: rank_bands({0: True}))
        reject("bands_string_score", lambda: rank_bands({"0": 1}))
        reject("top_limit_zero", lambda: ranking_module.top_zone_combinations(model.front, 0))
        reject("top_limit_negative", lambda: ranking_module.top_zone_combinations(model.front, -1))
        reject("top_limit_bool", lambda: ranking_module.top_zone_combinations(model.front, True))
        reject("top_limit_float", lambda: ranking_module.top_zone_combinations(model.front, 1.0))
        reject("top999", lambda: top1000(model, limit=999))
        reject("coverage_zero", lambda: top_k_coverage(model, rows, [0], forecast_id=forecast_id))
        reject("coverage_gt_rows", lambda: top_k_coverage(model, rows, [1001], forecast_id=forecast_id))
        reject("coverage_bool_k", lambda: top_k_coverage(model, rows, [True], forecast_id=forecast_id))
        reject("normalization_wrong_count", lambda: normalization_proof(model, {0: 1}))

        def mutated(field: str, value: object) -> list[dict[str, object]]:
            changed = [dict(row) for row in rows]
            if value is _MISSING:
                changed[0].pop(field)
            else:
                changed[0][field] = value
            return changed

        _MISSING = object()
        reject("cached_row_missing_score", lambda: top_k_coverage(model, mutated("joint_tick_score", _MISSING), [10], forecast_id=forecast_id))
        reject("ticket_duplicate", lambda: canonical_ticket("ssq", [1, 2, 3, 4, 5, 5], [1]))
        reject("ticket_unsorted", lambda: canonical_ticket("ssq", [2, 1, 3, 4, 5, 6], [1]))
        reject("ticket_low", lambda: canonical_ticket("ssq", [0, 1, 2, 3, 4, 5], [1]))
        reject("ticket_high", lambda: canonical_ticket("ssq", [28, 29, 30, 31, 32, 34], [1]))
        reject("ticket_back_high", lambda: canonical_ticket("ssq", [1, 2, 3, 4, 5, 6], [17]))
        reject("ticket_bool", lambda: canonical_ticket("ssq", [True, 2, 3, 4, 5, 6], [1]))

        accept("6e-51_smallest_roundup", lambda: decimal_probability(Decimal("6e-51")))
        accept("tick_exact_bounds", lambda: normalize_ticks([0, 4096, -4096]))
        accept("input_tuple_permutation_stable", lambda: self.assertEqual(
            zone_top_rows(zone_distribution([0, 0, 1, 1, 2, 2, 3, 3, 4, 4], 3), limit=100),
            zone_top_rows(zone_distribution((0, 0, 1, 1, 2, 2, 3, 3, 4, 4), 3), limit=100),
        ))
        accept("cross_top10_tie_exact", lambda: self.assertEqual(rank_bands({5: 4, 4: 16})[4][:2], (5, 20)))
        accept("nontransitive_values_remain_distinct", lambda: self.assertEqual(len({Decimal("1"), Decimal("1.0000000000000000000000000000000000000001"), Decimal("1.0000000000000000000000000000000000000002")}), 3))

        reject("hist_k_zero", lambda: zone_histogram([0, 1], 0))
        reject("hist_k_bool", lambda: zone_histogram([0, 1], True))
        reject("hist_empty_k_zero", lambda: zone_histogram([], 0))
        reject("tie_key_empty_model", lambda: tie_key("", probability_order_key(0)))
        reject("tie_key_nonstring_model", lambda: tie_key(7, probability_order_key(0)))
        reject("group_nonstring_forecast", lambda: tie_group_id(7, "0" * 64))
        reject("group_nonhex_key", lambda: tie_group_id(forecast_id, "g" * 64))
        reject("group_upperhex_key", lambda: tie_group_id(forecast_id, "A" * 64))
        reject("cached_row_string_joint_tick_score", lambda: top_k_coverage(model, mutated("joint_tick_score", "0"), [10], forecast_id=forecast_id))
        reject("cached_row_bool_joint_tick_score", lambda: top_k_coverage(model, mutated("joint_tick_score", True), [10], forecast_id=forecast_id))
        reject("cached_row_probability", lambda: top_k_coverage(model, mutated("probability", "0." + "1" * 50), [10], forecast_id=forecast_id))
        reject("cached_row_probability_order_key", lambda: top_k_coverage(model, mutated("probability_order_key", probability_order_key(1)), [10], forecast_id=forecast_id))
        reject("cached_row_tie_key", lambda: top_k_coverage(model, mutated("tie_key", "0" * 64), [10], forecast_id=forecast_id))
        reject("cached_row_tie_group_id", lambda: top_k_coverage(model, mutated("tie_group_id", "0" * 64), [10], forecast_id=forecast_id))
        reject("cached_row_tie_rank_lower", lambda: top_k_coverage(model, mutated("tie_rank_lower", 2), [10], forecast_id=forecast_id))
        reject("cached_row_tie_rank_upper", lambda: top_k_coverage(model, mutated("tie_rank_upper", 1), [10], forecast_id=forecast_id))
        reject("cached_row_tie_group_size", lambda: top_k_coverage(model, mutated("tie_group_size", 1), [10], forecast_id=forecast_id))
        reject("cached_row_front_ticket", lambda: top_k_coverage(model, mutated("front", [1, 2, 3, 4, 5, 5]), [10], forecast_id=forecast_id))

        def duplicate_cached_ticket() -> list[dict[str, object]]:
            changed = [dict(row) for row in rows]
            changed[1] = dict(changed[0])
            return changed

        reject("cached_row_duplicate_ticket", lambda: top_k_coverage(model, duplicate_cached_ticket(), [10], forecast_id=forecast_id))

        class _ForcedDigest:
            def hexdigest(self) -> str:
                return "0" * 64

        nonuniform = distribution("ssq", [0] + [-index for index in range(1, 33)], [0] + [-index for index in range(1, 16)], model_contract_id="collision-model")
        reject("forced_sha256_collision_distinct_order_keys", lambda: _forced_collision(nonuniform, _ForcedDigest()))
        self.assertEqual(len(observed), 71)
        self.assertEqual(len(set(observed)), 71)


def _forced_collision(model: object, digest: object) -> object:
    with mock.patch.object(ranking_module.hashlib, "sha256", return_value=digest):
        return top1000(model, forecast_id="collision-forecast")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
