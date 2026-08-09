from __future__ import annotations

import itertools
import math
import unittest

from lottery_research.phase3.evaluation import (
    inclusion_brier,
    evaluate_rolling_subsets,
    joint_log_score,
    rolling_folds,
    select_shrinkage,
    summarize_skill,
)
from lottery_research.phase3.probability import FixedCardinalityDistribution, joint_distribution


class ProbabilityContractTests(unittest.TestCase):
    def test_m0_known_answer_and_m1_zero_parameter_equivalence(self) -> None:
        m0 = FixedCardinalityDistribution.uniform(5, 2)
        m1 = FixedCardinalityDistribution.from_theta([0.0] * 5, 2)

        expected = 1.0 / math.comb(5, 2)
        for combination in itertools.combinations(range(1, 6), 2):
            self.assertEqual(m0.probability(combination), expected)
            self.assertEqual(m1.probability(combination), m0.probability(combination))
        self.assertAlmostEqual(m1.normalization_audit(), 1.0, places=14)

    def test_weighted_small_world_matches_direct_enumeration(self) -> None:
        theta = [-0.4, 0.0, 0.2, 0.7]
        model = FixedCardinalityDistribution.from_theta(theta, 2)
        combinations = list(itertools.combinations(range(1, 5), 2))
        raw = {item: math.exp(sum(theta[index - 1] for index in item)) for item in combinations}
        normalizer = sum(raw.values())

        for item in combinations:
            self.assertAlmostEqual(model.probability(item), raw[item] / normalizer, places=14)
        self.assertEqual(model.probability((1, 1)), 0.0)
        self.assertEqual(model.probability((0, 2)), 0.0)

    def test_partitioned_joint_probability_is_normalized_and_top_k_is_diagnostic(self) -> None:
        front = FixedCardinalityDistribution.from_theta([0.0, 0.3, -0.2, 0.1], 2)
        back = FixedCardinalityDistribution.uniform(3, 1)
        model = joint_distribution(front, back)

        self.assertAlmostEqual(model.normalization_audit(), 1.0, places=14)
        top = model.top_k(5)
        self.assertEqual(len(top), 5)
        self.assertEqual(len({(row["front"], row["back"]) for row in top}), 5)
        self.assertTrue(all(row["probability"] >= 0.0 for row in top))

    def test_real_game_spaces_have_dp_normalization_proof(self) -> None:
        ssq_front = FixedCardinalityDistribution.uniform(33, 6)
        ssq_back = FixedCardinalityDistribution.uniform(16, 1)
        dlt_front = FixedCardinalityDistribution.uniform(35, 5)
        dlt_back = FixedCardinalityDistribution.uniform(12, 2)
        self.assertEqual(ssq_front.combination_count * ssq_back.combination_count, 17_721_088)
        self.assertEqual(dlt_front.combination_count * dlt_back.combination_count, 21_425_712)
        for model in (ssq_front, ssq_back, dlt_front, dlt_back):
            self.assertEqual(model.normalization_dp_audit(), 1.0)

    def test_illegal_theta_and_probability_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FixedCardinalityDistribution.from_theta([0.0, float("nan")], 1)
        with self.assertRaises(ValueError):
            FixedCardinalityDistribution.from_weights([1.0, -1.0], 1)
        with self.assertRaises(ValueError):
            FixedCardinalityDistribution.uniform(2, 3)


class EvaluationContractTests(unittest.TestCase):
    def test_rolling_folds_never_include_outer_target(self) -> None:
        folds = rolling_folds(list(range(10)), minimum_training=4, inner_folds=2)
        self.assertEqual([fold.target for fold in folds], list(range(4, 10)))
        for fold in folds:
            self.assertNotIn(fold.target, fold.training)
            self.assertTrue(all(value < fold.target for value in fold.training))
            self.assertTrue(all(value < fold.target for inner in fold.inner for value in inner.training))
            self.assertTrue(all(inner.target < fold.target for inner in fold.inner))

    def test_metrics_known_answers(self) -> None:
        self.assertAlmostEqual(joint_log_score(0.25), math.log(4.0))
        self.assertAlmostEqual(inclusion_brier([0.5, 0.5], {1}), 0.25)
        summary = summarize_skill([0.0, 0.1, -0.1])
        self.assertAlmostEqual(summary["mean"], 0.0)
        self.assertEqual(summary["count"], 3)

    def test_rolling_evaluator_scores_each_target_once(self) -> None:
        draws = [(1, 2), (1, 3), (2, 3), (1, 4), (2, 4), (1, 2), (1, 3)]
        rows = evaluate_rolling_subsets(draws, size=4, cardinality=2, minimum_training=4, inner_folds=2, shrinkage=5.0)
        self.assertEqual([row["target_index"] for row in rows], [4, 5, 6])
        self.assertEqual(len({row["target_index"] for row in rows}), len(rows))
        self.assertTrue(all(row["target_index"] not in row["training_indices"] for row in rows))

    def test_frozen_m1_inner_window_selects_from_registered_grid(self) -> None:
        draws = [tuple(((index + offset) % 6) + 1 for offset in (0, 2)) for index in range(55)]
        folds = rolling_folds(list(range(len(draws))), minimum_training=50, inner_folds=20)
        self.assertEqual(len(folds[0].inner), 20)
        self.assertEqual(len(folds[0].inner[0].training), 30)
        selected = select_shrinkage(draws, folds[0], size=6, cardinality=2)
        self.assertIn(selected, (1.0, 5.0, 20.0, 100.0))


if __name__ == "__main__":
    unittest.main()
