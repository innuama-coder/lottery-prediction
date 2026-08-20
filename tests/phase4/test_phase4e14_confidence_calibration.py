from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e14"
SCRIPT = ROOT / "scripts/phase4e14/run_confidence_calibration.py"
SPEC = importlib.util.spec_from_file_location("phase4e14_confidence_calibration", SCRIPT)
assert SPEC and SPEC.loader
E14 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E14)


class Phase4E14UnitTests(unittest.TestCase):
    def test_pava_is_nondecreasing_and_pools_inversions(self) -> None:
        thresholds, fitted = E14.pava_fit([1, 2, 3, 4], [0.4, 0.2, 0.8, 0.6])
        self.assertEqual(thresholds, [1.0, 2.0, 3.0, 4.0])
        for observed, expected in zip(fitted, [0.3, 0.3, 0.7, 0.7]):
            self.assertAlmostEqual(observed, expected)
        self.assertTrue(all(left <= right for left, right in zip(fitted, fitted[1:])))

    def test_registered_transforms_have_expected_direction_and_claims(self) -> None:
        raw = [float(index) for index in range(E14.TRANSFORM_FIT_DRAWS)]
        outcome = [float(index % 3) / 2 for index in range(E14.TRANSFORM_FIT_DRAWS)]
        raw_model = E14.fit_transform("raw_marginal_mass", raw, outcome, "fit-hash")
        reverse = E14.fit_transform("reverse_raw_mass_control", raw, outcome, "fit-hash")
        rank = E14.fit_transform("empirical_rank_overlap_quantile", raw, outcome, "fit-hash")
        isotonic = E14.fit_transform("isotonic_expected_overlap_pava", raw, outcome, "fit-hash")
        self.assertEqual(E14.apply_transform(raw_model, 7.0), 7.0)
        self.assertEqual(E14.apply_transform(reverse, 7.0), -7.0)
        self.assertTrue(reverse["registered_control_only"])
        for model in (raw_model, reverse, rank, isotonic):
            self.assertFalse(model["uses_outer_labels"])
            self.assertIn("transform_id", model)
            self.assertNotIn("lottery probability", str(model["score_semantics"]).lower())

    def test_constant_score_association_fails_honestly(self) -> None:
        metrics = E14.fixed_size_metrics(
            [1.0] * 10, [0, 1] * 5, [str(index) for index in range(10)], 1, 1
        )
        self.assertEqual(metrics["spearman_rho_score_vs_number_hit_rate"], 0.0)
        self.assertEqual(metrics["descriptive_linear_association"]["slope"], 0.0)
        self.assertFalse(metrics["acceptance_pass"])

    def test_outer_rows_are_rejected_as_transform_selection_labels(self) -> None:
        for game in ("ssq", "dlt"):
            outer_rows = E14.raw_e13_rows(game)
            with self.assertRaisesRegex(ValueError, "FAIL_OUTER_LABEL_IN_TRANSFORM_SELECTION"):
                E14.validate_selection_rows(game, outer_rows)


@unittest.skipUnless((BASE / "summary.json").exists(), "P4E14 artifacts have not been generated")
class Phase4E14ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = json.loads((BASE / "summary.json").read_text())

    def report(self, game: str) -> dict[str, object]:
        return json.loads((BASE / game / "report.json").read_text())

    def rows(self, game: str, kind: str) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (BASE / game / f"{kind}-rolling-report.jsonl").read_text().splitlines()
        ]

    def test_retrospective_serving_fence_and_bounded_candidates(self) -> None:
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertFalse(self.summary["promotion_eligible"])
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        for game in ("ssq", "dlt"):
            report = self.report(game)
            experiment = report["transform_experiment"]
            self.assertEqual(experiment["bounded_candidates"], list(E14.CANDIDATE_ORDER))
            self.assertEqual(experiment["candidate_count"], 4)
            self.assertFalse(experiment["outer_labels_used"])
            self.assertFalse(experiment["number_ranking_or_selected_sets_changed"])
            self.assertFalse(report["promotion_eligible"])

    def test_selection_window_is_exact_strictly_pre_outer_60_60(self) -> None:
        expected_issues = {"ssq": ("2024148", "2025116"), "dlt": ("2025004", "2025123")}
        for game in ("ssq", "dlt"):
            report = self.report(game)
            window = report["candidate_selection_window"]
            inner = self.rows(game, "inner")
            self.assertEqual(len(inner), 120)
            self.assertEqual((window["first_issue"], window["last_issue"]), expected_issues[game])
            self.assertEqual(window["draws"], 120)
            self.assertEqual(window["transform_fit"]["draws"], 60)
            self.assertEqual(window["transform_holdout"]["draws"], 60)
            self.assertTrue(window["immediately_before_outer"])
            self.assertTrue(window["strictly_before_outer"])
            self.assertFalse(window["outer_labels_used_for_transform_selection"])
            self.assertEqual(
                [row["selection_subsplit"] for row in inner],
                ["transform_fit"] * 60 + ["transform_holdout"] * 60,
            )
            self.assertTrue(all(row["strict_lag"] for row in inner))
            self.assertTrue(
                all(row["maximum_training_position"] == row["target_position"] - 1 for row in inner)
            )
            self.assertLess(inner[-1]["target_position"], report["outer_window"]["first_target_position"])

    def test_frozen_outer_and_raw_phase4e13_rows_are_unchanged(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows14 = self.rows(game, "outer")
            rows13 = E14.raw_e13_rows(game)
            self.assertEqual(len(rows14), 120)
            projected = []
            for row in rows14:
                source = copy.deepcopy(row)
                source.pop("phase4e14_confidence_calibration")
                projected.append(source)
            self.assertEqual(projected, rows13)
            self.assertEqual(E14.row_identity(rows14), E14.row_identity(rows13))
            self.assertEqual(
                report["outer_window"]["identity_sha256"],
                E14.phase4e13_outer_identity_digest(rows13),
            )
            self.assertEqual(
                report["outer_window"]["identity_named_fields_sha256"],
                E14.digest(E14.row_identity(rows13)),
            )
            self.assertTrue(report["outer_window"]["identity_matches_phase4e13"])

    def test_raw_metrics_and_exact_ticket_comparison_remain_exact(self) -> None:
        for game in ("ssq", "dlt"):
            report14 = self.report(game)
            report13 = E14.raw_e13_report(game)
            self.assertEqual(report14["raw_phase4e13_metrics"]["splits"], report13["splits"])
            self.assertEqual(
                report14["raw_phase4e13_metrics"]["partial_hit_acceptance"],
                report13["partial_hit_acceptance"],
            )
            self.assertEqual(report14["full_ticket_comparison"], report13["full_ticket_comparison"])
            self.assertTrue(report14["raw_phase4e13_metrics"]["unchanged"])
            for split in ("calibration", "evaluation"):
                for zone, sizes in E14.ZONE_SIZES.items():
                    for size in sizes:
                        observed = report14["outer_splits"][split][zone]["fixed_size_association"][str(size)]
                        self.assertEqual(
                            observed["raw_phase4e13_fixed_size_association"],
                            report13["splits"][split][zone]["fixed_size_confidence_association"][str(size)],
                        )
                        self.assertEqual(
                            observed["raw_phase4e13_set_size_metrics"],
                            report13["splits"][split][zone]["set_size_metrics"][str(size)],
                        )

    def test_number_rankings_sets_strict_lag_and_transform_ids_per_row(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            selections = report["transform_experiment"]["selection_results"]
            rows14 = self.rows(game, "outer")
            rows13 = E14.raw_e13_rows(game)
            for row14, row13 in zip(rows14, rows13):
                calibration = row14["phase4e14_confidence_calibration"]
                self.assertTrue(row14["strict_lag"])
                self.assertTrue(calibration["strict_lag"])
                self.assertEqual(row14["maximum_training_position"], row14["target_position"] - 1)
                for zone, sizes in E14.ZONE_SIZES.items():
                    self.assertEqual(row14["zones"][zone]["marginal_ranking"], row13["zones"][zone]["marginal_ranking"])
                    for size in sizes:
                        self.assertEqual(
                            row14["zones"][zone]["confidence_sets"][str(size)],
                            row13["zones"][zone]["confidence_sets"][str(size)],
                        )
                        value = calibration["zones"][zone][str(size)]
                        transform_id = selections[zone][str(size)]["selected_transform_id"]
                        self.assertEqual(value["transform_id"], transform_id)
                        self.assertEqual(calibration["transform_ids"][zone][str(size)], transform_id)
                        self.assertFalse(value["score_is_true_lottery_probability"])

    def test_selection_recomputes_from_inner_holdout_only(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            inner = self.rows(game, "inner")
            recomputed = E14.fit_and_select_transforms(game, inner)
            observed = report["transform_experiment"]["selection_results"]
            self.assertEqual(recomputed, observed)
            for zone, sizes in E14.ZONE_SIZES.items():
                for size in sizes:
                    selection = observed[zone][str(size)]
                    self.assertFalse(selection["selection_uses_outer_labels"])
                    self.assertNotEqual(selection["selected_candidate"], "reverse_raw_mass_control")
                    self.assertEqual(len(selection["candidates"]), len(E14.CANDIDATE_ORDER))
                    for candidate in selection["candidates"]:
                        self.assertFalse(candidate["model"]["uses_outer_labels"])
                        if candidate["candidate"] == "reverse_raw_mass_control":
                            self.assertTrue(candidate["registered_control_only"])
                            self.assertFalse(candidate["eligible_positive_rho_and_slope"])

    def test_outer_metrics_acceptance_is_fixed_size_and_honest(self) -> None:
        expected_all = True
        for game in ("ssq", "dlt"):
            report = self.report(game)
            game_pass = True
            for zone, sizes in E14.ZONE_SIZES.items():
                zone_pass = True
                for size in sizes:
                    value = report["outer_splits"]["evaluation"][zone]["fixed_size_association"][str(size)]
                    expected = (
                        value["spearman_rho_score_vs_number_hit_rate"] > 0
                        and value["descriptive_linear_association"]["slope"] > 0
                        and value["monotonic_with_registered_tolerance"]
                    )
                    self.assertEqual(value["acceptance_pass"], expected)
                    zone_index = 0 if zone == "front" else 1
                    zone_draw_count = E14.e13.e12.e9.oracle.RULES[game][zone_index][1]
                    self.assertEqual(value["number_level_trials"], 60 * zone_draw_count)
                    low, high = value["overlap_rate_wilson95"]
                    self.assertLessEqual(low, value["overlap_rate"])
                    self.assertGreaterEqual(high, value["overlap_rate"])
                    zone_pass &= expected
                self.assertEqual(
                    report["outer_splits"]["evaluation"][zone]["all_fixed_sizes_acceptance_pass"],
                    zone_pass,
                )
                self.assertFalse(report["outer_splits"]["evaluation"][zone]["pooled_set_size_acceptance_used"])
                game_pass &= zone_pass
            self.assertEqual(report["acceptance"]["accepted"], game_pass)
            self.assertFalse(report["acceptance"]["pooled_set_sizes_used"])
            expected_all &= game_pass
        self.assertEqual(self.summary["accepted_all_games"], expected_all)

    def test_lineage_hashes_cover_source_oracle_e13_and_generated_rows(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            lineage = report["lineage"]
            for path_key, hash_key in (
                ("source_data_path", "source_data_sha256"),
                ("registered_p4e2_oracle_path", "registered_p4e2_oracle_sha256"),
                ("phase4e13_script_path", "phase4e13_script_sha256"),
            ):
                path = ROOT / lineage[path_key]
                self.assertEqual(lineage[hash_key], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(
                lineage["phase4e13_report_sha256"],
                hashlib.sha256((ROOT / f"artifacts/phase4e13/{game}/report.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                lineage["phase4e13_outer_rows_sha256"],
                hashlib.sha256((ROOT / f"artifacts/phase4e13/{game}/outer-rolling-report.jsonl").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                lineage["phase4e14_inner_rows_sha256"],
                hashlib.sha256((BASE / game / "inner-rolling-report.jsonl").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                lineage["phase4e14_outer_rows_sha256"],
                hashlib.sha256((BASE / game / "outer-rolling-report.jsonl").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                lineage["phase4e13_outer_identity_sha256"],
                lineage["phase4e14_outer_identity_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
