from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e13"
SCRIPT = ROOT / "scripts/phase4e13/run_partial_hit_evaluation.py"
SPEC = importlib.util.spec_from_file_location("phase4e13_partial_hits", SCRIPT)
assert SPEC and SPEC.loader
E13 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E13)


class Phase4E13UnitTests(unittest.TestCase):
    def test_exact_marginals_sum_combination_mass(self) -> None:
        combos = np.asarray([[1, 2], [1, 3], [2, 3]], dtype=np.int16)
        probabilities = np.asarray([0.2, 0.3, 0.5])
        marginal = E13.marginal_number_probabilities(combos, probabilities, 3)
        np.testing.assert_allclose(marginal, [0.5, 0.7, 0.8], rtol=0, atol=1e-15)
        self.assertAlmostEqual(float(marginal.sum()), 2.0)

    def test_score_normalization_and_canonical_number_tie_break(self) -> None:
        probabilities = E13.normalize_combination_scores(np.asarray([1001.0, 1001.0, 999.0]))
        self.assertAlmostEqual(float(probabilities.sum()), 1.0)
        self.assertEqual(E13.canonical_marginal_ranking([0.4, 0.6, 0.6, 0.1]), [2, 3, 1, 4])

    def test_confidence_set_is_partial_not_full_ticket_gate(self) -> None:
        marginal = np.asarray([0.9, 0.8, 0.2, 0.1])
        result = E13.confidence_set(marginal, [1, 2, 3, 4], [2, 4], 2)
        self.assertEqual(result["overlap_count"], 1)
        self.assertEqual(result["number_hit_rate"], 0.5)
        self.assertTrue(result["any_number_hit"])
        self.assertFalse(result["exact_all_zone_numbers_hit"])
        self.assertAlmostEqual(result["confidence_mass"], 0.85)

    def test_spearman_uses_average_tie_ranks(self) -> None:
        self.assertGreater(E13.spearman([1, 2, 3], [0, 1, 1]), 0)
        self.assertAlmostEqual(E13.spearman([1, 2, 3], [3, 2, 1]), -1.0)


@unittest.skipUnless((BASE / "summary.json").exists(), "P4E13 artifacts have not been generated")
class Phase4E13ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = json.loads((BASE / "summary.json").read_text())

    def report(self, game: str) -> dict[str, object]:
        return json.loads((BASE / game / "report.json").read_text())

    def rows(self, game: str) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (BASE / game / "outer-rolling-report.jsonl").read_text().splitlines()
        ]

    def test_retrospective_serving_and_probability_fences(self) -> None:
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertFalse(self.summary["promotion_eligible"])
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        for game in ("ssq", "dlt"):
            report = self.report(game)
            self.assertFalse(report["probability_contract"]["true_lottery_probability_claim"])
            self.assertFalse(report["probability_contract"]["guaranteed_winnings_claim"])
            self.assertFalse(report["promotion_eligible"])

    def test_frozen_outer_identity_selection_fence_and_strict_lag(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game)
            rows12 = [
                json.loads(line)
                for line in (ROOT / f"artifacts/phase4e12/{game}/outer-rolling-report.jsonl").read_text().splitlines()
            ]
            rows11 = [
                json.loads(line)
                for line in (ROOT / f"artifacts/phase4e11/{game}/outer-rolling-report.jsonl").read_text().splitlines()
            ]
            identity = [(row["issue"], row["target_position"]) for row in rows]
            self.assertEqual(len(rows), 120)
            self.assertEqual(identity, [(row["issue"], row["target_position"]) for row in rows12])
            self.assertEqual(identity, [(row["issue"], row["target_position"]) for row in rows11])
            self.assertTrue(report["outer_window"]["identity_matches_phase4e11"])
            self.assertTrue(report["outer_window"]["identity_matches_phase4e12"])
            self.assertTrue(report["selection_fence"]["selection_draws_immediately_before_outer"])
            self.assertFalse(report["selection_fence"]["outer_labels_used_for_selection"])
            self.assertEqual(report["selection_fence"]["selection_window"]["draws"], 120)
            self.assertTrue(all(row["strict_lag"] for row in rows))
            self.assertTrue(
                all(row["maximum_training_position"] == row["target_position"] - 1 for row in rows)
            )

    def test_marginals_ranking_sets_and_per_draw_hit_indicators(self) -> None:
        for game in ("ssq", "dlt"):
            for row in self.rows(game):
                for zone, sizes in (("front", E13.FRONT_SET_SIZES), ("back", E13.BACK_SET_SIZES)):
                    artifact = row["zones"][zone]
                    marginal = [value["inclusion_mass"] for value in artifact["marginal_probabilities"]]
                    self.assertTrue(math.isclose(sum(marginal), artifact["zone_draw_count"], abs_tol=1e-10))
                    self.assertTrue(math.isclose(artifact["combination_probability_sum"], 1.0, abs_tol=1e-12))
                    self.assertEqual(artifact["marginal_ranking"], E13.canonical_marginal_ranking(marginal))
                    previous: set[int] = set()
                    actual = set(artifact["actual_numbers"])
                    for size in sizes:
                        result = artifact["confidence_sets"][str(size)]
                        selected = set(result["selected_numbers"])
                        self.assertEqual(len(selected), size)
                        self.assertTrue(previous.issubset(selected))
                        self.assertEqual(result["overlap_count"], len(actual & selected))
                        self.assertEqual(result["any_number_hit"], bool(actual & selected))
                        self.assertEqual(result["exact_all_zone_numbers_hit"], actual.issubset(selected))
                        expected_confidence = math.fsum(marginal[number - 1] for number in selected) / len(actual)
                        self.assertAlmostEqual(result["confidence_mass"], expected_confidence)
                        previous = selected

    def test_split_metrics_and_wilson_intervals_are_exact(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game)
            for split, subset in (("calibration", rows[:60]), ("evaluation", rows[60:])):
                for zone, sizes in (("front", E13.FRONT_SET_SIZES), ("back", E13.BACK_SET_SIZES)):
                    for size in sizes:
                        expected = E13.summarize_set(subset, zone, size)
                        observed = report["splits"][split][zone]["set_size_metrics"][str(size)]
                        self.assertEqual(observed, expected)

    def test_confidence_acceptance_is_separate_by_split_and_zone(self) -> None:
        overall = True
        for game in ("ssq", "dlt"):
            report = self.report(game)
            for split in ("calibration", "evaluation"):
                for zone in ("front", "back"):
                    association = report["splits"][split][zone]["confidence_association"]
                    expected = (
                        association["monotonic_with_registered_tolerance"]
                        and association["spearman_rho_confidence_vs_number_hit_rate"] > 0
                        and association["linear_calibration_association"]["slope"] > 0
                        and all(
                            value["positive_association"]
                            for value in report["splits"][split][zone]["fixed_size_confidence_association"].values()
                        )
                    )
                    pooled_expected = (
                        association["monotonic_with_registered_tolerance"]
                        and association["spearman_rho_confidence_vs_number_hit_rate"] > 0
                        and association["linear_calibration_association"]["slope"] > 0
                    )
                    self.assertEqual(association["acceptance_pass"], pooled_expected)
                    self.assertEqual(report["partial_hit_acceptance"]["split_zone_pass"][split][zone], expected)
                    overall &= expected
            self.assertEqual(report["partial_hit_acceptance"]["accepted"], all(
                report["partial_hit_acceptance"]["split_zone_pass"][split][zone]
                for split in ("calibration", "evaluation") for zone in ("front", "back")
            ))
        self.assertEqual(self.summary["partial_hit_ladder_accepted_all_games"], overall)

    def test_full_ticket_comparison_is_byte_lineage_equivalent_and_gates_unchanged(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            report12 = json.loads((ROOT / f"artifacts/phase4e12/{game}/report.json").read_text())
            comparison = report["full_ticket_comparison"]
            self.assertEqual(comparison["first_ranked_space"], report12["first_ranked_space"])
            self.assertEqual(comparison["compression_evaluation"], report12["compression_evaluation"])
            self.assertEqual(comparison["compression_acceptance"], report12["compression_acceptance"])
            self.assertFalse(comparison["gates_changed"])

    def test_source_model_and_config_hashes(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            lineage = report["lineage"]
            source = ROOT / lineage["source_data_path"]
            self.assertEqual(lineage["source_data_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            oracle = ROOT / lineage["registered_p4e2_oracle_path"]
            self.assertEqual(lineage["registered_p4e2_oracle_sha256"], hashlib.sha256(oracle.read_bytes()).hexdigest())
            self.assertEqual(lineage["model_configuration_sha256"], E13.digest(E13.model_configuration(game)))
            self.assertEqual(lineage["experiment_config_sha256"], E13.digest(report["experiment_config"]))


if __name__ == "__main__":
    unittest.main()
