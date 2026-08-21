from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e17"
SCRIPT = ROOT / "scripts/phase4e17/run_per_number_feature_model.py"
SPEC = importlib.util.spec_from_file_location("phase4e17_feature_model_artifacts", SCRIPT)
assert SPEC and SPEC.loader
E17 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E17)


@unittest.skipUnless((BASE / "summary.json").exists(), "P4E17 artifacts have not been generated")
class Phase4E17ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = json.loads((BASE / "summary.json").read_text())

    def report(self, game: str) -> dict[str, object]:
        return json.loads((BASE / game / "report.json").read_text())

    def rows(self, phase: str, game: str, kind: str) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (ROOT / f"artifacts/{phase}/{game}/{kind}-rolling-report.jsonl")
            .read_text()
            .splitlines()
        ]

    def test_retrospective_serving_fence_and_candidate_registry(self) -> None:
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertFalse(self.summary["promotion_eligible"])
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        self.assertFalse(self.summary["scores_are_true_lottery_probabilities"])
        self.assertEqual(self.summary["candidate_order"], list(E17.CANDIDATE_ORDER))
        for game in ("ssq", "dlt"):
            report = self.report(game)
            experiment = report["alternative_feature_model_experiment"]
            self.assertEqual(experiment["candidate_count"], 4)
            self.assertEqual(experiment["candidate_order"], list(E17.CANDIDATE_ORDER))
            self.assertFalse(experiment["configuration_grid_search_performed"])
            self.assertFalse(experiment["selection_uses_outer_labels"])
            self.assertIn("not true lottery probabilities", report["diagnostic_claim"])

    def test_selection_uses_exact_e16_240_identity_and_four_blocks(self) -> None:
        inner17 = self.rows("phase4e17", "dlt", "inner")
        inner16 = self.rows("phase4e16", "dlt", "inner")
        report = self.report("dlt")
        window = report["candidate_selection_window"]
        self.assertEqual(len(inner17), 240)
        self.assertEqual(E17.row_identity(inner17), E17.row_identity(inner16))
        self.assertEqual(window["identity_sha256"], E17.digest(E17.row_identity(inner17)))
        self.assertEqual([block["draws"] for block in window["blocks"]], [60] * 4)
        self.assertFalse(window["outer_labels_used_for_candidate_selection"])
        self.assertTrue(window["all_candidate_fits_strict_prefix"])
        self.assertTrue(
            window["all_maximum_feature_source_positions_equal_target_minus_one"]
        )
        outer_start = self.report("dlt")["outer_window"]["first_target_position"]
        self.assertEqual(inner17[0]["target_position"], outer_start - 240)
        self.assertEqual(inner17[-1]["target_position"], outer_start - 1)

    def test_stability_selection_recomputes_without_outer_rows(self) -> None:
        inner = self.rows("phase4e17", "dlt", "inner")
        recorded = self.report("dlt")["alternative_feature_model_experiment"][
            "selection_result"
        ]
        self.assertEqual(recorded, E17.select_dlt_front_candidate(inner))
        for candidate in recorded["candidates"]:
            positives = sum(
                bool(block["positive_rho_and_slope"]) for block in candidate["blocks"]
            )
            self.assertEqual(candidate["positive_block_count"], positives)
            self.assertEqual(candidate["stable_eligible"], positives >= 3)
        self.assertFalse(recorded["selection_uses_outer_labels"])

    def test_outer_is_exact_e16_embedding_and_identity(self) -> None:
        for game in ("ssq", "dlt"):
            rows17 = self.rows("phase4e17", game, "outer")
            rows16 = self.rows("phase4e16", game, "outer")
            self.assertEqual(len(rows17), 120)
            projected = []
            for row in rows17:
                source = copy.deepcopy(row)
                source.pop("phase4e17_per_number_feature_model")
                projected.append(source)
            self.assertEqual(projected, rows16)
            self.assertEqual(E17.row_identity(rows17), E17.row_identity(rows16))
            report = self.report(game)
            self.assertTrue(
                report["outer_window"]["identity_matches_exact_phase4e13_e14_e15_e16"]
            )
            self.assertEqual(
                report["outer_window"]["identity_sha256"],
                E17.outer_identity_digest(rows16),
            )

    def test_only_dlt_front_changes_decision_and_other_zones_inherit_e16(self) -> None:
        for game in ("ssq", "dlt"):
            rows = self.rows("phase4e17", game, "outer")
            for row in rows:
                phase17 = row["phase4e17_per_number_feature_model"]
                self.assertFalse(phase17["outer_label_used_for_candidate_selection"])
                self.assertTrue(phase17["selection_completed_before_outer_rows_loaded"])
                for zone in ("front", "back"):
                    value = phase17["zones"][zone]
                    if game == "dlt" and zone == "front":
                        self.assertEqual(
                            value["decision_origin"],
                            "phase4e17_dlt_front_pre_outer_stability_selection",
                        )
                    else:
                        self.assertEqual(
                            value["decision_origin"], "phase4e16_inherited_unchanged"
                        )
                        inherited = row["phase4e16_stable_orientation"]["zones"][zone]
                        self.assertEqual(value["selected_candidate"], inherited["selected_candidate"])
                        self.assertEqual(
                            value["selected_orientation_ranking"],
                            inherited["selected_orientation_ranking"],
                        )

    def test_individual_number_association_and_fixed_size_coverage_are_reported(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            for split, expected_draws in (
                ("calibration", 60),
                ("evaluation", 60),
                ("all_120_descriptive", 120),
            ):
                for zone, sizes in E17.ZONE_SIZES.items():
                    value = report["outer_splits"][split][zone]
                    individual = value["individual_number_association"]
                    self.assertIn("spearman_rho", individual)
                    self.assertIn("slope", individual["descriptive_linear_association"])
                    pool = 35 if game == "dlt" and zone == "front" else len(
                        value["per_canonical_number_association"]
                    )
                    self.assertEqual(len(value["per_canonical_number_association"]), pool)
                    self.assertTrue(
                        all(
                            "spearman_rho" in row and "descriptive_slope" in row
                            for row in value["per_canonical_number_association"]
                        )
                    )
                    for size in sizes:
                        fixed = value["fixed_size_set_metrics"][str(size)]
                        self.assertEqual(fixed["group_count"], expected_draws)
                        self.assertEqual(len(fixed["groups"]), expected_draws)
                        self.assertIn("best_single_group_hit_rate", fixed)
                        self.assertTrue(
                            all(
                                group["hit_rate"] == group["hit_count"] / group["number_count"]
                                for group in fixed["groups"]
                            )
                        )
                        self.assertEqual(
                            fixed["best_single_group_hit_rate"],
                            max(group["hit_rate"] for group in fixed["groups"]),
                        )

    def test_exact_ticket_gates_hashes_and_strict_lag_are_unchanged(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            report16 = E17.e16_report(game)
            self.assertEqual(report["full_ticket_comparison"], report16["full_ticket_comparison"])
            self.assertTrue(report["acceptance"]["exact_ticket_gates_unchanged"])
            self.assertTrue(
                report["full_ticket_comparison_unchanged_from_phase4e13_e14_e15_e16"]
            )
            lineage = report["lineage"]
            for key in (
                "phase4e3_model",
                "phase4e3_dlt_selection_receipt",
            ):
                path = ROOT / lineage[f"{key}_path"]
                self.assertEqual(
                    lineage[f"{key}_sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
                )
            # E17 is an append-only historical artifact.  Phase4E21 is allowed
            # to harden the live bonus function without rewriting this receipt.
            self.assertEqual(
                lineage["phase4e17_script_sha256"],
                "27ac17cb67921a9c4125bcfb1751acad541ba83f22e9f60d5057bdf22e608fd9",
            )
            e21 = ROOT / "artifacts/phase4e21_bonus_hardening/old-new-report-hashes.json"
            self.assertTrue(e21.exists())
            comparisons = json.loads(e21.read_text())["reports"]
            comparison = next(row for row in comparisons if row["report"] == f"phase4e17_{game}")
            self.assertEqual(comparison["old_sha256"], hashlib.sha256((BASE / game / "report.json").read_bytes()).hexdigest())
            self.assertNotEqual(comparison["old_sha256"], comparison["new_sha256"])
            self.assertEqual(
                lineage["phase4e17_candidate_registry_sha256"],
                E17.digest(E17.candidate_registry()),
            )
            self.assertEqual(len(lineage["phase4e17_block_identity_sha256"]), 4)
            strict_lag = report["strict_lag"]
            self.assertTrue(strict_lag["target_t_uses_through_t_minus_1_only"])
            self.assertTrue(strict_lag["candidate_models_receive_only_draws_before_target_t"])
            self.assertTrue(strict_lag["all_selection_rows_strict_lag"])
            self.assertTrue(strict_lag["all_outer_rows_strict_lag"])
            self.assertTrue(
                strict_lag["all_maximum_feature_source_positions_equal_target_minus_one"]
            )
            self.assertFalse(strict_lag["outer_labels_used_for_candidate_selection"])


if __name__ == "__main__":
    unittest.main()
