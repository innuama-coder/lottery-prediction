from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e15"
SCRIPT = ROOT / "scripts/phase4e15/run_number_orientation.py"
SPEC = importlib.util.spec_from_file_location("phase4e15_number_orientation", SCRIPT)
assert SPEC and SPEC.loader
E15 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E15)


class Phase4E15UnitTests(unittest.TestCase):
    def test_orientations_and_canonical_ties(self) -> None:
        marginal = [
            {"number": 1, "inclusion_mass": 0.2},
            {"number": 2, "inclusion_mass": 0.4},
            {"number": 3, "inclusion_mass": 0.2},
            {"number": 4, "inclusion_mass": 0.1},
        ]
        self.assertEqual(
            E15.oriented_ranking(marginal, "raw_descending_marginal_mass"), [2, 1, 3, 4]
        )
        self.assertEqual(
            E15.oriented_ranking(marginal, "reverse_ascending_marginal_mass_control"),
            [4, 1, 3, 2],
        )
        self.assertEqual(E15.orientation_score("raw_descending_marginal_mass", 0.2), 0.2)
        self.assertEqual(
            E15.orientation_score("reverse_ascending_marginal_mass_control", 0.2), -0.2
        )

    def test_constant_scores_fail_positive_association_honestly(self) -> None:
        observations = [
            {
                "issue": str(index),
                "number": index + 1,
                "orientation_score": 1.0,
                "binary_hit": index % 2,
            }
            for index in range(10)
        ]
        metrics = E15.association_metrics(observations, "orientation_score", "binary_hit")
        self.assertEqual(metrics["spearman_rho"], 0.0)
        self.assertEqual(metrics["descriptive_linear_association"]["slope"], 0.0)
        self.assertFalse(metrics["positive_association"])

    def test_orientation_selection_uses_last_inner_60_and_can_choose_control(self) -> None:
        rows = []
        for draw in range(E15.SELECTION_DRAWS):
            # The measure half favors raw, while the selection half favors reverse.
            # Choosing reverse therefore proves the first half is not used to select.
            marginal = [
                {"number": 1, "inclusion_mass": 0.1 + draw / 10000},
                {"number": 2, "inclusion_mass": 0.9 - draw / 10000},
            ]
            zone = {
                "actual_numbers": [2 if draw < E15.ORIENTATION_MEASURE_DRAWS else 1],
                "marginal_probabilities": marginal,
            }
            rows.append({"issue": str(draw), "target_position": draw, "zones": {"front": zone, "back": zone}})
        selected = E15.select_orientations(rows)
        for zone in ("front", "back"):
            self.assertEqual(
                selected[zone]["selected_candidate"],
                "reverse_ascending_marginal_mass_control",
            )
            self.assertTrue(selected[zone]["selected_registered_control"])
            self.assertFalse(selected[zone]["selection_uses_outer_labels"])


@unittest.skipUnless((BASE / "summary.json").exists(), "P4E15 artifacts have not been generated")
class Phase4E15ArtifactTests(unittest.TestCase):
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

    def e14_rows(self, game: str, kind: str) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (ROOT / f"artifacts/phase4e14/{game}/{kind}-rolling-report.jsonl")
            .read_text()
            .splitlines()
        ]

    def test_retrospective_serving_fence_and_two_bounded_orientations(self) -> None:
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertFalse(self.summary["promotion_eligible"])
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        for game in ("ssq", "dlt"):
            report = self.report(game)
            experiment = report["orientation_experiment"]
            self.assertEqual(experiment["bounded_candidates"], list(E15.CANDIDATE_ORDER))
            self.assertEqual(experiment["candidate_count"], 2)
            self.assertFalse(experiment["selection_uses_outer_labels"])
            self.assertTrue(experiment["canonical_ascending_number_tie_break"])
            self.assertFalse(report["promotion_eligible"])
            self.assertIn("not true lottery probabilities", report["diagnostic_claim"])

    def test_selection_is_e14_inner_identity_with_exact_60_60_fence(self) -> None:
        expected_issues = {"ssq": ("2024148", "2025116"), "dlt": ("2025004", "2025123")}
        for game in ("ssq", "dlt"):
            report = self.report(game)
            window = report["orientation_selection_window"]
            inner = self.rows(game, "inner")
            inner14 = self.e14_rows(game, "inner")
            self.assertEqual(len(inner), 120)
            self.assertEqual((window["first_issue"], window["last_issue"]), expected_issues[game])
            self.assertEqual(window["identity_sha256"], E15.digest(E15.row_identity(inner14)))
            self.assertEqual(window["orientation_fit_measure"]["draws"], 60)
            self.assertEqual(window["orientation_selection"]["draws"], 60)
            self.assertEqual(
                [row["selection_subsplit"] for row in inner],
                ["orientation_fit_measure"] * 60 + ["orientation_selection"] * 60,
            )
            self.assertEqual(E15.row_identity(inner), E15.row_identity(inner14))
            self.assertTrue(all(row["phase4e14_inner_row_identity_verified"] for row in inner))
            self.assertTrue(all(row["strict_lag"] for row in inner))
            self.assertTrue(
                all(row["maximum_training_position"] == row["target_position"] - 1 for row in inner)
            )
            self.assertLess(inner[-1]["target_position"], report["outer_window"]["first_target_position"])

    def test_selection_recomputes_without_outer_labels(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            inner = self.rows(game, "inner")
            observed = report["orientation_experiment"]["selection_results"]
            recomputed = E15.select_orientations(inner)
            self.assertEqual(observed, recomputed)
            for zone in ("front", "back"):
                self.assertFalse(observed[zone]["selection_uses_outer_labels"])
                self.assertEqual(len(observed[zone]["candidates"]), 2)
                self.assertEqual(
                    [candidate["registered_control"] for candidate in observed[zone]["candidates"]],
                    [False, True],
                )

    def test_frozen_e14_outer_is_unchanged_and_matches_e13(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows15 = self.rows(game, "outer")
            rows14 = self.e14_rows(game, "outer")
            self.assertEqual(len(rows15), 120)
            projected = []
            for row in rows15:
                source = copy.deepcopy(row)
                source.pop("phase4e15_number_orientation")
                projected.append(source)
            self.assertEqual(projected, rows14)
            self.assertEqual(E15.row_identity(rows15), E15.row_identity(rows14))
            self.assertTrue(report["outer_window"]["identity_matches_phase4e13"])
            self.assertTrue(report["outer_window"]["identity_matches_phase4e14"])
            self.assertEqual(report["outer_window"]["identity_sha256"], E15.e13_identity_digest(rows14))

    def test_outer_ranking_sets_and_canonical_ties_match_selected_orientation(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game, "outer")
            selections = report["orientation_experiment"]["selection_results"]
            for row in rows:
                self.assertTrue(row["strict_lag"])
                self.assertEqual(row["maximum_training_position"], row["target_position"] - 1)
                phase15 = row["phase4e15_number_orientation"]
                self.assertTrue(phase15["strict_lag"])
                self.assertFalse(phase15["outer_label_used_for_orientation_selection"])
                for zone, sizes in E15.ZONE_SIZES.items():
                    selected = selections[zone]["selected_candidate"]
                    zone15 = phase15["zones"][zone]
                    expected = E15.oriented_ranking(row["zones"][zone]["marginal_probabilities"], selected)
                    self.assertEqual(zone15["selected_orientation_ranking"], expected)
                    actual = set(row["zones"][zone]["actual_numbers"])
                    for size in sizes:
                        value = zone15["confidence_sets"][str(size)]
                        self.assertEqual(value["ranked_numbers"], expected[:size])
                        self.assertEqual(value["selected_numbers"], sorted(expected[:size]))
                        self.assertEqual(value["overlap_count"], len(actual.intersection(expected[:size])))
                        self.assertFalse(value["score_is_true_lottery_probability"])

    def test_evaluation_acceptance_and_fixed_size_wilson_reports_are_honest(self) -> None:
        accepted_all = True
        expected_failures = []
        for game in ("ssq", "dlt"):
            report = self.report(game)
            game_pass = True
            for zone, sizes in E15.ZONE_SIZES.items():
                metrics = report["outer_splits"]["evaluation"][zone]
                individual = metrics["individual_number_association"]
                expected = (
                    individual["spearman_rho"] > 0
                    and individual["descriptive_linear_association"]["slope"] > 0
                )
                self.assertEqual(metrics["acceptance_pass"], expected)
                self.assertEqual(report["acceptance"]["evaluation_zone_pass"][zone], expected)
                self.assertFalse(metrics["acceptance_uses_fixed_set_or_pooled_set_association"])
                if not expected:
                    expected_failures.append({"game": game, "zone": zone})
                game_pass &= expected
                for size in sizes:
                    value = metrics["fixed_size_set_metrics"][str(size)]
                    self.assertEqual(value["predicted_number_trials"], 60 * size)
                    self.assertEqual(
                        value["actual_number_coverage_trials"],
                        60 * int(self.rows(game, "outer")[0]["zones"][zone]["zone_draw_count"]),
                    )
                    for rate_key, interval_key in (
                        ("predicted_number_hit_rate", "predicted_number_hit_rate_wilson95"),
                        ("actual_number_coverage_rate", "actual_number_coverage_rate_wilson95"),
                    ):
                        low, high = value[interval_key]
                        self.assertLessEqual(low, value[rate_key])
                        self.assertGreaterEqual(high, value[rate_key])
            self.assertEqual(report["acceptance"]["accepted"], game_pass)
            self.assertEqual(
                report["acceptance"]["failed_zones"],
                [zone for zone, passed in report["acceptance"]["evaluation_zone_pass"].items() if not passed],
            )
            accepted_all &= game_pass
        self.assertEqual(self.summary["failed_game_zones"], expected_failures)
        self.assertEqual(self.summary["accepted_all_games_zones"], accepted_all)

    def test_exact_ticket_and_lineage_hashes_are_unchanged_and_complete(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            report14 = json.loads((ROOT / f"artifacts/phase4e14/{game}/report.json").read_text())
            self.assertEqual(report["full_ticket_comparison"], report14["full_ticket_comparison"])
            self.assertTrue(report["full_ticket_comparison_unchanged_from_phase4e13"])
            lineage = report["lineage"]
            for path_key, hash_key in (
                ("source_data_path", "source_data_sha256"),
                ("registered_p4e2_oracle_path", "registered_p4e2_oracle_sha256"),
                ("phase4e13_script_path", "phase4e13_script_sha256"),
                ("phase4e14_script_path", "phase4e14_script_sha256"),
            ):
                path = ROOT / lineage[path_key]
                self.assertEqual(lineage[hash_key], hashlib.sha256(path.read_bytes()).hexdigest())
            for phase, kind in (("phase4e14", "inner"), ("phase4e14", "outer"), ("phase4e15", "inner"), ("phase4e15", "outer")):
                path = ROOT / f"artifacts/{phase}/{game}/{kind}-rolling-report.jsonl"
                self.assertEqual(
                    lineage[f"{phase}_{kind}_rows_sha256"],
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            self.assertEqual(
                lineage["phase4e13_outer_identity_sha256"],
                lineage["phase4e14_outer_identity_sha256"],
            )
            self.assertEqual(
                lineage["phase4e14_outer_identity_sha256"],
                lineage["phase4e15_outer_identity_sha256"],
            )
            strict_lag = report["strict_lag"]
            self.assertTrue(strict_lag["target_t_uses_through_t_minus_1_only"])
            self.assertTrue(strict_lag["all_selection_rows_strict_lag"])
            self.assertTrue(strict_lag["all_outer_rows_strict_lag"])
            self.assertTrue(strict_lag["all_maximum_training_positions_equal_target_minus_one"])
            self.assertFalse(strict_lag["outer_labels_used_for_orientation_selection"])


if __name__ == "__main__":
    unittest.main()
