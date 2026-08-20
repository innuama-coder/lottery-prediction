from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e16"
SCRIPT = ROOT / "scripts/phase4e16/run_stable_orientation.py"
SPEC = importlib.util.spec_from_file_location("phase4e16_stable_orientation", SCRIPT)
assert SPEC and SPEC.loader
E16 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E16)


class Phase4E16UnitTests(unittest.TestCase):
    @staticmethod
    def synthetic_rows(block_directions: list[str]) -> list[dict[str, object]]:
        rows = []
        for block, direction in enumerate(block_directions):
            for offset in range(E16.SELECTION_BLOCK_DRAWS):
                target = block * E16.SELECTION_BLOCK_DRAWS + offset
                marginal = [
                    {"number": 1, "inclusion_mass": 0.1},
                    {"number": 2, "inclusion_mass": 0.9},
                ]
                if direction == "raw":
                    actual = [2]
                elif direction == "reverse":
                    actual = [1]
                else:
                    actual = [2 if offset % 2 == 0 else 1]
                zone = {
                    "actual_numbers": actual,
                    "marginal_probabilities": marginal,
                }
                rows.append(
                    {
                        "issue": str(target),
                        "target_position": target,
                        "zones": {"front": zone, "back": zone},
                    }
                )
        return rows

    def test_three_of_four_positive_blocks_selects_stable_orientation(self) -> None:
        rows = self.synthetic_rows(["raw", "raw", "reverse", "raw"])
        selected = E16.select_orientations("ssq", rows)
        for zone in ("front", "back"):
            self.assertTrue(selected[zone]["stable"])
            self.assertFalse(selected[zone]["fallback_used"])
            self.assertEqual(selected[zone]["selected_candidate"], "raw_descending_marginal_mass")
            raw = selected[zone]["candidates"][0]
            reverse = selected[zone]["candidates"][1]
            self.assertEqual(raw["positive_block_count"], 3)
            self.assertTrue(raw["stable_eligible"])
            self.assertEqual(reverse["positive_block_count"], 1)
            self.assertFalse(reverse["stable_eligible"])
            self.assertEqual([value["draws"] for value in raw["blocks"]], [60] * 4)

    def test_no_three_of_four_candidate_uses_registered_e15_fallback(self) -> None:
        rows = self.synthetic_rows(["raw", "reverse", "flat", "flat"])
        selected = E16.select_orientations("dlt", rows)
        report15 = E16.raw_report(E16.E15, "dlt")
        fallback = report15["orientation_experiment"]["selection_results"]
        for zone in ("front", "back"):
            self.assertFalse(selected[zone]["stable"])
            self.assertTrue(selected[zone]["fallback_used"])
            self.assertEqual(selected[zone]["selection_status"], "unstable_phase4e15_fallback")
            self.assertEqual(
                selected[zone]["selected_candidate"], fallback[zone]["selected_candidate"]
            )
            self.assertEqual(
                selected[zone]["registered_phase4e15_fallback_orientation_id"],
                fallback[zone]["selected_orientation_id"],
            )

    def test_selection_window_is_exact_n_minus_360_to_n_minus_120(self) -> None:
        for game in ("ssq", "dlt"):
            data = E16.e13.load(game)
            targets = E16.selection_targets(game)
            self.assertEqual(len(targets), 240)
            self.assertEqual(targets, list(range(len(data) - 360, len(data) - 120)))


@unittest.skipUnless((BASE / "summary.json").exists(), "P4E16 artifacts have not been generated")
class Phase4E16ArtifactTests(unittest.TestCase):
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

    def test_retrospective_serving_fence_and_bounded_candidates(self) -> None:
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertFalse(self.summary["promotion_eligible"])
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        for game in ("ssq", "dlt"):
            report = self.report(game)
            experiment = report["stable_orientation_experiment"]
            self.assertEqual(experiment["bounded_candidates"], list(E16.CANDIDATE_ORDER))
            self.assertEqual(experiment["candidate_count"], 2)
            self.assertFalse(experiment["selection_uses_outer_labels"])
            self.assertFalse(report["promotion_eligible"])
            self.assertIn("not true lottery probabilities", report["diagnostic_claim"])

    def test_selection_window_has_four_exact_pre_outer_blocks(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            inner = self.rows("phase4e16", game, "inner")
            window = report["candidate_selection_window"]
            outer_start = report["outer_window"]["first_target_position"]
            self.assertEqual(len(inner), 240)
            self.assertEqual(window["draws"], 240)
            self.assertEqual(inner[0]["target_position"], outer_start - 240)
            self.assertEqual(inner[-1]["target_position"], outer_start - 1)
            self.assertEqual(len(window["blocks"]), 4)
            self.assertEqual([block["draws"] for block in window["blocks"]], [60] * 4)
            self.assertEqual(
                [row["selection_block_index"] for row in inner],
                [1] * 60 + [2] * 60 + [3] * 60 + [4] * 60,
            )
            for index, block in enumerate(window["blocks"]):
                rows = inner[index * 60 : (index + 1) * 60]
                self.assertEqual(block["identity_sha256"], E16.digest(E16.row_identity(rows)))
                self.assertFalse(block["outer_labels_used"])
            self.assertTrue(all(row["strict_lag"] for row in inner))
            self.assertTrue(
                all(row["maximum_training_position"] == row["target_position"] - 1 for row in inner)
            )

    def test_last_120_recomputed_rows_match_e14_and_e15_rank_set_identity(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            inner16 = self.rows("phase4e16", game, "inner")[-120:]
            inner15 = self.rows("phase4e15", game, "inner")
            inner14 = self.rows("phase4e14", game, "inner")
            overlap = report["candidate_selection_window"]["phase4e14_phase4e15_overlap"]
            self.assertEqual(E16.row_identity(inner16), E16.row_identity(inner14))
            self.assertEqual(E16.row_identity(inner16), E16.row_identity(inner15))
            self.assertTrue(overlap["rank_and_set_identity_verified"])
            self.assertTrue(overlap["full_phase4e15_zone_identity_verified"])
            for row16, row14, row15 in zip(inner16, inner14, inner15):
                self.assertTrue(row16["phase4e14_phase4e15_overlap_identity_verified"])
                self.assertEqual(row16["zones"], row15["zones"])
                for zone in ("front", "back"):
                    self.assertEqual(
                        row16["zones"][zone]["marginal_ranking"],
                        row14["zones"][zone]["marginal_ranking"],
                    )
                    self.assertEqual(
                        row16["zones"][zone]["confidence_sets"],
                        row14["zones"][zone]["confidence_sets"],
                    )

    def test_selection_recomputes_from_four_blocks_without_outer_labels(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            inner = self.rows("phase4e16", game, "inner")
            observed = report["stable_orientation_experiment"]["selection_results"]
            self.assertEqual(observed, E16.select_orientations(game, inner))
            for zone in ("front", "back"):
                selection = observed[zone]
                self.assertFalse(selection["selection_uses_outer_labels"])
                self.assertEqual(len(selection["candidates"]), 2)
                for candidate in selection["candidates"]:
                    count = sum(
                        bool(block["positive_rho_and_slope"])
                        for block in candidate["blocks"]
                    )
                    self.assertEqual(candidate["positive_block_count"], count)
                    self.assertEqual(candidate["stable_eligible"], count >= 3)
                if not selection["stable"]:
                    report15 = E16.raw_report(E16.E15, game)
                    fallback = report15["orientation_experiment"]["selection_results"][zone]
                    self.assertTrue(selection["fallback_used"])
                    self.assertEqual(selection["selected_candidate"], fallback["selected_candidate"])

    def test_frozen_outer_embeds_e15_and_retains_e14_e13_identity(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows16 = self.rows("phase4e16", game, "outer")
            rows15 = self.rows("phase4e15", game, "outer")
            self.assertEqual(len(rows16), 120)
            projected = []
            for row in rows16:
                source = copy.deepcopy(row)
                source.pop("phase4e16_stable_orientation")
                projected.append(source)
            self.assertEqual(projected, rows15)
            self.assertEqual(E16.row_identity(rows16), E16.row_identity(rows15))
            self.assertTrue(report["outer_window"]["identity_matches_phase4e13_e14_e15"])
            self.assertTrue(
                report["outer_window"]["phase4e14_phase4e15_rank_and_set_identity_verified"]
            )

    def test_outer_ranking_overlap_wilson_and_per_number_association(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows("phase4e16", game, "outer")
            selections = report["stable_orientation_experiment"]["selection_results"]
            for row in rows:
                phase16 = row["phase4e16_stable_orientation"]
                self.assertFalse(phase16["outer_label_used_for_orientation_selection"])
                for zone, sizes in E16.ZONE_SIZES.items():
                    candidate = selections[zone]["selected_candidate"]
                    expected = E16.e15.oriented_ranking(
                        row["zones"][zone]["marginal_probabilities"], candidate
                    )
                    zone16 = phase16["zones"][zone]
                    self.assertEqual(zone16["selected_orientation_ranking"], expected)
                    actual = set(row["zones"][zone]["actual_numbers"])
                    for size in sizes:
                        value = zone16["confidence_sets"][str(size)]
                        self.assertEqual(value["ranked_numbers"], expected[:size])
                        self.assertEqual(value["overlap_count"], len(actual.intersection(expected[:size])))
            for split in ("calibration", "evaluation", "all_120_descriptive"):
                expected_draws = 120 if split == "all_120_descriptive" else 60
                for zone, sizes in E16.ZONE_SIZES.items():
                    metrics = report["outer_splits"][split][zone]
                    pool = rows[0]["zones"][zone]["number_pool_size"]
                    per_number = metrics["per_canonical_number_association"]
                    self.assertEqual(len(per_number), pool)
                    self.assertTrue(
                        all("spearman_rho" in value and "descriptive_slope" in value for value in per_number)
                    )
                    for size in sizes:
                        value = metrics["fixed_size_set_metrics"][str(size)]
                        self.assertEqual(value["predicted_number_trials"], expected_draws * size)
                        low, high = value["predicted_number_hit_rate_wilson95"]
                        self.assertLessEqual(low, value["predicted_number_hit_rate"])
                        self.assertGreaterEqual(high, value["predicted_number_hit_rate"])

    def test_acceptance_is_only_zone_level_individual_number_association(self) -> None:
        failures = []
        for game in ("ssq", "dlt"):
            report = self.report(game)
            for zone in ("front", "back"):
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
                    failures.append({"game": game, "zone": zone})
        self.assertEqual(self.summary["failed_game_zones"], failures)
        self.assertEqual(self.summary["accepted_all_games_zones"], not failures)
        self.assertEqual(
            self.summary["expansion_stopped_if_dlt_front_failed"],
            {"game": "dlt", "zone": "front"} in failures,
        )

    def test_exact_ticket_lineage_and_strict_lag_are_complete(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            report15 = E16.raw_report(E16.E15, game)
            self.assertEqual(report["full_ticket_comparison"], report15["full_ticket_comparison"])
            self.assertTrue(report["full_ticket_comparison_unchanged_from_phase4e13_e14_e15"])
            lineage = report["lineage"]
            for phase in (13, 14, 15):
                prefix = f"phase4e{phase}"
                script = ROOT / lineage[f"{prefix}_script_path"]
                self.assertEqual(
                    lineage[f"{prefix}_script_sha256"],
                    hashlib.sha256(script.read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    lineage[f"{prefix}_outer_identity_sha256"],
                    report["outer_window"]["identity_sha256"],
                )
            source = ROOT / lineage["source_data_path"]
            self.assertEqual(
                lineage["source_data_sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
            )
            self.assertEqual(
                lineage["phase4e16_selection_identity_sha256"],
                report["candidate_selection_window"]["identity_sha256"],
            )
            self.assertEqual(len(lineage["phase4e16_block_identity_sha256"]), 4)
            strict_lag = report["strict_lag"]
            self.assertTrue(strict_lag["target_t_uses_through_t_minus_1_only"])
            self.assertTrue(strict_lag["all_selection_rows_strict_lag"])
            self.assertTrue(strict_lag["all_outer_rows_strict_lag"])
            self.assertTrue(strict_lag["all_maximum_training_positions_equal_target_minus_one"])
            self.assertFalse(strict_lag["outer_labels_used_for_orientation_selection"])


if __name__ == "__main__":
    unittest.main()
