from __future__ import annotations

import math
import unittest
from decimal import Decimal
from pathlib import Path

from lottery_system.phase4.real_model import (
    FEATURE_GROUPS, FEATURE_IDS, elementary, feature_snapshot_rows, load_draws,
    subset_probability, top_tickets, train,
)


ROOT = Path(__file__).resolve().parents[2]
PHASE1 = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"


class RealModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draws = {game: load_draws(PHASE1, game) for game in ("ssq", "dlt")}
        cls.models = {game: train(game, cls.draws[game]) for game in ("ssq", "dlt")}

    def test_real_history_train_is_non_m0_and_fold_isolated(self) -> None:
        for game in ("ssq", "dlt"):
            model = self.models[game]
            self.assertEqual(model["family"], "P4E2-R")
            self.assertFalse(set(model["selection_indices"]) & set(model["report_only_indices"]))
            self.assertEqual(set(model["feature_ids"]), set(FEATURE_IDS))
            self.assertEqual(set(model["feature_groups_consumed"]), set(FEATURE_GROUPS.values()))
            for group in set(FEATURE_GROUPS.values()):
                self.assertTrue(any(abs(zone["coefficients"][feature]) > 0 for zone in model["zones"] for feature in FEATURE_IDS if FEATURE_GROUPS[feature] == group))
            self.assertTrue(all(zone["minimum_probability"] > 0 and zone["normalization_mass"] == 1 for zone in model["zones"]))
            self.assertEqual(model["estimator"], "one_step_exact_uniform_gradient_l2_conditional_log_likelihood_v1")
            self.assertTrue(all(row["brier_formula"].startswith("1-2*p_observed+") for row in model["report_only_metrics"]))

    def test_snapshot_is_strict_prefix_and_complete(self) -> None:
        for game in ("ssq", "dlt"):
            rows = feature_snapshot_rows(game, self.draws[game], len(self.draws[game]))
            self.assertTrue(rows)
            self.assertTrue(all(row["max_source_position"] < row["target_position"] for row in rows))
            observed = set().union(*(row["feature_values"] for row in rows))
            self.assertEqual(observed, set(FEATURE_IDS))
            with self.assertRaisesRegex(ValueError, "FAIL_LEAKAGE"):
                feature_snapshot_rows(game, self.draws[game], len(self.draws[game]) - 1)

    def test_elementary_normalization_small_known_answer(self) -> None:
        zone = {"weights": [1.0, 2.0, 3.0, 4.0], "normalizer": elementary([1.0, 2.0, 3.0, 4.0], 2)}
        total = sum(subset_probability(combo, zone) for combo in ((1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)))
        self.assertTrue(math.isclose(total, 1.0, rel_tol=1e-14))

    def test_top1000_probability_primary(self) -> None:
        model = self.models["dlt"]
        rows = top_tickets(model)
        self.assertEqual(len(rows), 1000)
        self.assertEqual(len({(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in rows}), 1000)
        probabilities = [float(row["joint_probability"]) for row in rows]
        self.assertTrue(all(left >= right > 0 for left, right in zip(probabilities, probabilities[1:])))
        self.assertGreater(len(set(probabilities)), 1)
        exact_probabilities = [Decimal(row["joint_probability"]) for row in rows]
        self.assertTrue(all(left >= right > 0 for left, right in zip(exact_probabilities, exact_probabilities[1:])))
        required = {"tie_group_id", "tie_group_size", "tie_rank_lower", "tie_rank_upper", "tie_midrank", "tie_key"}
        self.assertTrue(all(required <= row.keys() for row in rows))
        for row in rows:
            self.assertEqual(Decimal(row["tie_midrank"]), (Decimal(row["tie_rank_lower"]) + Decimal(row["tie_rank_upper"])) / 2)
            self.assertEqual(row["probability_representation"], "P4-LOGSUMEXP-ENUM-1")

    def test_formal_rejections(self) -> None:
        model = self.models["dlt"]
        with self.assertRaisesRegex(ValueError, "HOLD_NON_PRODUCT"):
            top_tickets({**model, "family": "M0"})
        with self.assertRaisesRegex(ValueError, "top_k=1000"):
            top_tickets(model, 100)


if __name__ == "__main__":
    unittest.main()
