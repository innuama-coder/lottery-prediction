from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/phase4e17/run_per_number_feature_model.py"
SPEC = importlib.util.spec_from_file_location("phase4e17_feature_model", SCRIPT)
assert SPEC and SPEC.loader
E17 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E17)


class Phase4E17SelectionTests(unittest.TestCase):
    def synthetic_rows(self) -> list[dict[str, object]]:
        rows = []
        for target in range(E17.SELECTION_DRAWS):
            candidates = {}
            for candidate in E17.CANDIDATE_ORDER:
                candidates[candidate] = {
                    "number_observations": [
                        {"number": 1, "candidate_score": 1.0, "binary_hit": 1},
                        {"number": 2, "candidate_score": 0.0, "binary_hit": 0},
                    ]
                }
            rows.append(
                {
                    "issue": str(target),
                    "target_position": target,
                    "candidates": candidates,
                }
            )
        return rows

    def test_registry_is_bounded_and_partitions_all_available_features(self) -> None:
        E17.validate_registry()
        self.assertEqual(len(E17.CANDIDATE_ORDER), 4)
        self.assertEqual(E17.CANDIDATE_ORDER[:2], E17.e16.CANDIDATE_ORDER)
        features = [
            feature
            for candidate in E17.candidate_registry()
            for feature in candidate["feature_ids"]
        ]
        self.assertCountEqual(features, E17.AVAILABLE_FEATURE_IDS)
        self.assertEqual(len(features), len(set(features)))
        self.assertEqual(len(E17.MODEL_CANDIDATES), 2)

    def test_selection_orders_eligible_candidates_by_median_rho_then_order(self) -> None:
        rows = self.synthetic_rows()
        calls = []
        rho_by_candidate = {
            E17.CANDIDATE_ORDER[0]: [0.1, 0.1, 0.1, 0.1],
            E17.CANDIDATE_ORDER[1]: [0.4, 0.4, 0.4, -0.2],
            E17.CANDIDATE_ORDER[2]: [0.2, -0.2, -0.2, -0.2],
            E17.CANDIDATE_ORDER[3]: [-0.1, -0.1, -0.1, -0.1],
        }
        for candidate in E17.CANDIDATE_ORDER:
            for rho in rho_by_candidate[candidate]:
                calls.append(
                    {
                        "spearman_rho": rho,
                        "descriptive_linear_association": {"slope": rho},
                        "positive_association": rho > 0,
                    }
                )
        with mock.patch.object(E17.e16.e15, "association_metrics", side_effect=calls):
            selected = E17.select_dlt_front_candidate(rows)
        self.assertTrue(selected["stable"])
        self.assertFalse(selected["fallback_used"])
        # Candidate 1 has four positive blocks, but candidate 2 wins because the
        # registered rule ranks eligible candidates by median rho, not block count.
        self.assertEqual(selected["selected_candidate"], E17.CANDIDATE_ORDER[1])
        self.assertEqual(selected["candidates"][0]["positive_block_count"], 4)
        self.assertEqual(selected["candidates"][1]["positive_block_count"], 3)

    def test_deterministic_order_breaks_equal_median_rho(self) -> None:
        rows = self.synthetic_rows()
        selected = E17.select_dlt_front_candidate(rows)
        self.assertTrue(selected["stable"])
        self.assertEqual(selected["selected_candidate"], E17.CANDIDATE_ORDER[0])

    def test_walk_forward_fit_has_strict_prefix_and_two_label_purge(self) -> None:
        data = E17.e16.e13.load(E17.TARGET_GAME)
        target = len(data) - E17.OUTER_DRAWS - E17.SELECTION_DRAWS
        spec = E17.SPEC_BY_ID[E17.MODEL_CANDIDATES[0]]
        output = E17.walk_forward_model_output(E17.TARGET_GAME, data, target, spec)
        audit = output["walk_forward_fit"]
        self.assertEqual(audit["strict_prefix_draws"], target)
        self.assertEqual(audit["maximum_feature_source_position"], target - 1)
        self.assertEqual(audit["max_training_label_position"], target - 3)
        self.assertFalse(audit["target_or_future_label_available_to_fit"])
        self.assertEqual(audit["fit_training_input_sha256"], audit["strict_prefix_sha256"])
        self.assertEqual(
            audit["distribution_context_input_sha256"], audit["strict_prefix_sha256"]
        )
        self.assertEqual(len(output["scores"]), 35)
        self.assertTrue(math.isclose(math.fsum(output["scores"]), 5.0, abs_tol=1e-10))


if __name__ == "__main__":
    unittest.main()
