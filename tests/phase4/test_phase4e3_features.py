from __future__ import annotations

import copy
import math
import unittest
from pathlib import Path

from lottery_system.phase4.real_common import Draw, RULES, digest
from lottery_system.phase4.real_model import load_draws
from lottery_system.phase4e3.model import (
    FEATURE_FAMILIES,
    build_context,
    elementary,
    fit_zone,
    inclusion_probabilities,
    score_zone_observation,
    subset_probability,
    top_zone,
    zone_distribution,
)
from scripts.phase4e3.run_report import bootstrap, holm_table
from scripts.phase4e3.run_delivery import top_product


ROOT = Path(__file__).resolve().parents[2]
PHASE1 = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"


class Phase4E3FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draws = {game: load_draws(PHASE1, game) for game in ("ssq", "dlt")}

    def test_all_number_features_are_finite_centered_and_strict_prefix(self) -> None:
        for game in ("ssq", "dlt"):
            for zone in (0, 1):
                context = build_context(game, self.draws[game][:120], zone)
                self.assertEqual(context["cutoff_position"], 119)
                self.assertEqual(context["max_source_position"], 119)
                self.assertEqual(context["input_prefix_sha256"], digest([draw.fact_hash for draw in self.draws[game][:120]]))
                for values in context["feature_values"].values():
                    self.assertTrue(all(math.isfinite(value) for value in values))
                    self.assertAlmostEqual(math.fsum(values), 0.0, places=10)

    def test_future_and_target_mutation_cannot_change_features(self) -> None:
        draws = list(self.draws["dlt"])
        original = build_context("dlt", draws[:120], 0)
        for position in (120, 150, 199):
            draw = draws[position]
            draws[position] = Draw(draw.issue, tuple(reversed(draw.front)), tuple(reversed(draw.back)), "f" * 64)
        mutated = build_context("dlt", draws[:120], 0)
        self.assertEqual(original, mutated)

    def test_fixed_cardinality_normalizer_and_marginals_known_answer(self) -> None:
        weights = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(elementary(weights, 2), 35.0)
        marginals = inclusion_probabilities(weights, 2)
        self.assertAlmostEqual(sum(marginals), 2.0, places=14)
        self.assertTrue(all(0 < value < 1 for value in marginals))

    def test_fit_replay_is_deterministic_and_purged(self) -> None:
        draws = self.draws["ssq"]
        arguments = dict(game="ssq", draws=draws, cutoff=128, zone=0,
                         feature_ids=FEATURE_FAMILIES["C01_SURPRISE_REGIME"],
                         history=80, l2=12.0, temperature=0.5)
        first = fit_zone(**arguments)
        second = fit_zone(**arguments)
        self.assertEqual(first, second)
        self.assertEqual(first["max_training_label_position"], 125)
        self.assertEqual(digest(first), digest(second))

    def test_probability_and_score_replay_are_deterministic(self) -> None:
        draws = self.draws["dlt"]
        fitted = fit_zone("dlt", draws, 128, 0, FEATURE_FAMILIES["C03_TRANSITION"],
                          history=80, l2=12.0, temperature=0.5)
        first = zone_distribution("dlt", draws[:130], 0, fitted)
        second = zone_distribution("dlt", draws[:130], 0, copy.deepcopy(fitted))
        self.assertEqual(first["weights"], second["weights"])
        self.assertEqual(first["normalizer"], second["normalizer"])
        self.assertEqual(score_zone_observation(draws[130].front, first), score_zone_observation(draws[130].front, second))
        self.assertAlmostEqual(sum(first["inclusion_probabilities"]), RULES["dlt"][0][1], places=12)

    def test_frozen_bootstrap_and_six_family_holm_are_deterministic(self) -> None:
        values = [-0.2, -0.1, 0.05, -0.3] * 6
        first = bootstrap(values, 20260820)
        self.assertEqual(first, bootstrap(values, 20260820))
        selection = {"eligible_for_report_only": ["C03_TRANSITION"]}
        table = holm_table(selection, "C03_TRANSITION", 0.004)
        transition = next(row for row in table if row["candidate"] == "C03_TRANSITION")
        self.assertEqual(transition["holm_rank"], 1)
        self.assertAlmostEqual(transition["holm_adjusted_p"], 0.024)
        self.assertEqual(sum(row["report_only_evaluated"] for row in table), 1)

    def test_streaming_zone_and_product_top_k_match_complete_sort(self) -> None:
        distribution = {"n": 7, "k": 2, "weights": [1.0, 1.3, 0.7, 2.0, 0.9, 1.7, 1.1]}
        distribution["normalizer"] = elementary(distribution["weights"], distribution["k"])
        expected = [
            (subset_probability(combo, distribution), combo)
            for combo in __import__("itertools").combinations(range(1, 8), 2)
        ]
        expected.sort(key=lambda row: row[1])
        expected.sort(key=lambda row: row[0], reverse=True)
        self.assertEqual(top_zone(distribution, 8), expected[:8])
        front, back = expected[:8], [(0.6, (1,)), (0.4, (2,))]
        product = [(left * right, a, b) for left, a in front for right, b in back]
        product.sort(key=lambda row: (row[1], row[2]))
        product.sort(key=lambda row: row[0], reverse=True)
        self.assertEqual(top_product(front, back, 10), product[:10])


if __name__ == "__main__":
    unittest.main()
