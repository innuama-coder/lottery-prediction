from __future__ import annotations

import copy
import itertools
import math
import unittest
from pathlib import Path

from lottery_system.phase4e4.data import load_jsonl, make_draw
from lottery_system.phase4e4.model import (
    FAMILIES,
    _set_feature_matrix,
    _top_independent_product,
    build_context,
    configurations,
    distribution,
    fit_model,
    score_model,
    top_tickets,
    zcolumns,
)


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "artifacts/phase-4e4/data-20260819/selection-prefix"


class Phase4E4ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draws = load_jsonl(DATA / "dlt.jsonl", "dlt")
        cls.cutoff = len(cls.draws) - 56

    def test_normalization_statistics_are_exactly_prefix_local(self) -> None:
        columns = [[1.0, 2.0, 3.0, 4.0], [7.0, 7.0, 7.0, 7.0]]
        normalized, specs = zcolumns(columns)
        self.assertAlmostEqual(math.fsum(normalized[0]), 0.0, places=15)
        self.assertAlmostEqual(math.fsum(value * value for value in normalized[0]) / 4, 1.0, places=15)
        self.assertEqual(normalized[1], [0.0] * 4)
        self.assertEqual(specs[0]["mean"], 2.5)
        self.assertEqual(specs[1], {"mean": 7.0, "scale": 1e-12})

        prefix = self.draws[: self.cutoff]
        context = build_context("dlt", prefix, 0, "E401_MULTISCALE_REGIME")
        self.assertEqual(context["prefix_count"], self.cutoff)
        self.assertEqual(context["maximum_source_issue"], prefix[-1].issue)
        for column in zip(*context["number_features"]):
            self.assertAlmostEqual(math.fsum(column), 0.0, places=10)
            self.assertAlmostEqual(math.fsum(value * value for value in column) / len(column), 1.0, places=10)

    def test_future_and_target_mutation_cannot_change_fit_or_prediction(self) -> None:
        config = configurations("E401_MULTISCALE_REGIME")[0]
        original = fit_model("dlt", self.draws, self.cutoff, "E401_MULTISCALE_REGIME", config)
        mutated = list(self.draws)
        for position in (self.cutoff, self.cutoff + 1, len(mutated) - 1):
            row = mutated[position]
            mutated[position] = make_draw(
                "dlt", row.issue, row.draw_date,
                tuple(range(1, 6)), (1, 2), "mutation-test",
            )
        replay = fit_model("dlt", mutated, self.cutoff, "E401_MULTISCALE_REGIME", config)
        self.assertEqual(original, replay)
        for left, right in zip(original["zones"], replay["zones"]):
            self.assertEqual(distribution(left), distribution(right))
            self.assertLess(int(left["maximum_training_label_position"]), self.cutoff)
            self.assertLess(left["context"]["prefix_count"], self.cutoff)

    def test_every_registered_candidate_has_nonconstant_features(self) -> None:
        prefix = self.draws[: self.cutoff]
        for family in FAMILIES:
            config = configurations(family)[0]
            for zone in (0, 1):
                context = build_context("dlt", prefix, zone, family, config)
                if "number_features" in context:
                    columns = list(zip(*context["number_features"]))
                else:
                    matrix = _set_feature_matrix(context)
                    columns = [matrix[:, index] for index in range(matrix.shape[1])]
                self.assertTrue(columns, family)
                self.assertTrue(any(float(max(values) - min(values)) > 1e-12 for values in columns), (family, zone))

    def test_all_candidates_are_exact_fixed_cardinality_distributions(self) -> None:
        for family in FAMILIES:
            model = fit_model("dlt", self.draws, self.cutoff, family, configurations(family)[0])
            scored = score_model(model, self.draws[self.cutoff + 8])
            self.assertLessEqual(abs(float(scored["normalization_mass"]) - 1.0), 1e-12, family)
            self.assertGreater(scored["joint_probability"], 0.0)
            for zone, dist in enumerate(scored["distributions"]):
                self.assertLessEqual(abs(float(dist["normalization_mass"]) - 1.0), 1e-12, (family, zone))
                self.assertAlmostEqual(math.fsum(dist["inclusion_probabilities"]), dist["k"], places=10)
                self.assertEqual(dist["n"], len(dist["inclusion_probabilities"]))

    def test_fit_and_replay_are_byte_for_byte_deterministic_in_memory(self) -> None:
        for family in FAMILIES:
            config = configurations(family)[-1]
            first = fit_model("dlt", self.draws, self.cutoff, family, copy.deepcopy(config))
            second = fit_model("dlt", self.draws, self.cutoff, family, copy.deepcopy(config))
            self.assertEqual(first, second, family)
            self.assertEqual(score_model(first, self.draws[self.cutoff + 8]), score_model(second, self.draws[self.cutoff + 8]), family)

    def test_heap_topk_matches_exhaustive_product_and_all_model_scores(self) -> None:
        front = [(0.7, (1,)), (0.2, (2,)), (0.1, (3,))]
        back = [(0.6, (1,)), (0.25, (2,)), (0.15, (3,))]
        exhaustive = sorted(
            ((p * q, left, right) for (p, left), (q, right) in itertools.product(front, back)),
            key=lambda row: (-row[0], row[1], row[2]),
        )[:7]
        self.assertEqual(_top_independent_product(front, back, 7), exhaustive)

        for family in FAMILIES:
            model = fit_model("dlt", self.draws, self.cutoff, family, configurations(family)[0])
            rows, summary = top_tickets(model, 10)
            self.assertEqual([row["rank"] for row in rows], list(range(1, 11)))
            self.assertEqual(rows, sorted(rows, key=lambda row: (-row["joint_probability"], row["front"], row["back"])))
            for row in rows:
                draw = make_draw("dlt", "2099999", "2099-01-01", row["front"], row["back"], "top-k-replay")
                self.assertAlmostEqual(row["joint_probability"], score_model(model, draw)["joint_probability"], places=20, msg=family)
            self.assertLessEqual(abs(summary["probability_normalization_mass"] - 1.0), 1e-12)


if __name__ == "__main__":
    unittest.main()
