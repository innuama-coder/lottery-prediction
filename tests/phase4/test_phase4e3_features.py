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
    zone_distribution,
)


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


if __name__ == "__main__":
    unittest.main()
