from __future__ import annotations

import importlib.util
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e12"
SCRIPT = ROOT / "scripts/phase4e12/run_compression_iteration.py"
SPEC = importlib.util.spec_from_file_location("phase4e12_compression", SCRIPT)
assert SPEC and SPEC.loader
E12 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E12)


class Phase4E12UnitTests(unittest.TestCase):
    def test_exact_split_conformal_indices(self) -> None:
        values = list(range(1, 121))
        self.assertEqual(E12.conformal_k(values, 0.90), 109)
        self.assertEqual(E12.conformal_k(values, 0.80), 97)
        self.assertEqual(E12.conformal_k(values, 0.50), 61)

    def test_80_history_fit_preserves_p4e2_contract(self) -> None:
        data = E12.e9.load("dlt")
        target = len(data) - 240
        expected = E12.e9.oracle.fit_coefficients("dlt", data, target, E12.e9.L2["dlt"])
        actual = E12.fit_coefficients("dlt", data, target, E12.e9.L2["dlt"], 80)
        self.assertEqual(actual, expected)

    def test_ensemble_is_fixed_equal_weight_score_average(self) -> None:
        coefficients = [{key: float(index + 1) for index, key in enumerate(E12.e9.oracle.FEATURE_IDS)}] * 2
        left = frozenset(E12.e9.oracle.FEATURE_IDS[:5])
        right = frozenset(E12.e9.oracle.FEATURE_IDS[:12])
        combined = E12.masked_coefficients(coefficients, (left, right), (0.5, 0.5))
        for zone in (0, 1):
            for key in E12.e9.oracle.FEATURE_IDS[:5]:
                self.assertEqual(combined[zone][key], coefficients[zone][key])
            for key in E12.e9.oracle.FEATURE_IDS[5:12]:
                self.assertEqual(combined[zone][key], coefficients[zone][key] * 0.5)
            for key in E12.e9.oracle.FEATURE_IDS[12:]:
                self.assertEqual(combined[zone][key], 0.0)


@unittest.skipUnless((BASE / "summary.json").exists(), "P4E12 artifacts have not been generated")
class Phase4E12ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = json.loads((BASE / "summary.json").read_text())

    def report(self, game: str) -> dict[str, object]:
        return json.loads((BASE / game / "report.json").read_text())

    def rows(self, game: str, name: str) -> list[dict[str, object]]:
        path = BASE / game / f"{name}-rolling-report.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_retrospective_and_serving_fences(self) -> None:
        self.assertEqual(self.summary["status"], "RETROSPECTIVE_BACKTEST_ONLY")
        self.assertFalse(self.summary["promotion_eligible"])
        self.assertTrue(self.summary["p4e6_serving_unchanged"])
        self.assertEqual(self.summary["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        self.assertEqual(self.summary["p4e6_terminal_status"], "PROSPECTIVE_ONLY")

    def test_inner_selection_is_strict_and_reproducible(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game, "inner")
            self.assertEqual(len(rows), 3 * 120)
            self.assertTrue(report["candidate_selection_window"]["strictly_before_outer"])
            self.assertFalse(report["candidate_selection_window"]["uses_final_outer_labels"])
            self.assertTrue(all(row["strict_lag"] for row in rows))
            self.assertTrue(all(row["maximum_training_position"] == row["target_position"] - 1 for row in rows))
            metrics = report["candidate_metrics"]
            for candidate in E12.CANDIDATES:
                ranks = [row["canonical_rank"] for row in rows if row["candidate"] == candidate]
                self.assertEqual(len(ranks), 120)
                self.assertEqual(metrics[candidate]["inner_k90"], E12.conformal_k(ranks, 0.90))
                self.assertEqual(metrics[candidate]["inner_k80"], E12.conformal_k(ranks, 0.80))
                self.assertEqual(metrics[candidate]["inner_k50"], E12.conformal_k(ranks, 0.50))
            selected = min(
                E12.CANDIDATES,
                key=lambda candidate: (
                    metrics[candidate]["inner_k90"], metrics[candidate]["inner_k80"],
                    metrics[candidate]["inner_k50"], E12.CANDIDATES.index(candidate),
                ),
            )
            self.assertEqual(report["selected_candidate"], selected)
            self.assertEqual(report["ranking_contract"], "joint_stable_score_key_desc_tie_canonical_ticket_asc_v1")
            self.assertEqual(report["score_order_key_id"], "P4S10HE1")

    def test_e11_baseline_and_outer_window_lineage(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            inner = self.rows(game, "inner")
            baseline = [row for row in inner if row["candidate"] == "e11_baseline_80"]
            report11 = json.loads((ROOT / f"artifacts/phase4e11/{game}/report.json").read_text())
            payloads11 = [
                json.loads(line)
                for line in (ROOT / f"artifacts/phase4e11/{game}/inner-rolling-report.jsonl").read_text().splitlines()
            ]
            rows11 = next(payload["rows"] for payload in payloads11 if payload["mask"] == report11["selected_mask"])
            self.assertEqual(
                [(row["issue"], row["canonical_rank"]) for row in baseline],
                [(row["issue"], row["canonical_rank"]) for row in rows11],
            )
            outer = self.rows(game, "outer")
            outer11 = [
                json.loads(line)
                for line in (ROOT / f"artifacts/phase4e11/{game}/outer-rolling-report.jsonl").read_text().splitlines()
            ]
            identity = [(row["issue"], row["target_position"]) for row in outer]
            identity11 = [(row["issue"], row["target_position"]) for row in outer11]
            self.assertEqual(identity, identity11)
            self.assertEqual(report["outer_window"]["identity_sha256"], E12.outer_identity_sha256(outer))
            self.assertEqual(report["outer_window"]["phase4e11_identity_sha256"], E12.outer_identity_sha256(outer11))
            self.assertTrue(report["outer_window"]["identity_matches_phase4e11"])
            self.assertTrue(report["outer_window"]["frozen_from_phase4e9_e10_e11"])

    def test_outer_split_exact_metrics_and_monotonic_compression(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            rows = self.rows(game, "outer")
            self.assertEqual(len(rows), 120)
            calibration = [row["canonical_rank"] for row in rows[:60]]
            evaluation = [row["canonical_rank"] for row in rows[60:]]
            self.assertEqual(report["first_ranked_space"]["selected_k_from_calibration_only"], E12.conformal_k(calibration, 0.90))
            previous = 0
            for k in E12.SPACES:
                observed = E12.coverage(evaluation, k, math.prod(math.comb(n, count) for n, count in E12.e9.oracle.RULES[game]))
                artifact = report["compression_evaluation"][str(k)]
                self.assertEqual(artifact["hits"], observed["hits"])
                self.assertEqual(artifact["draws"], 60)
                self.assertEqual(artifact["wilson95"], observed["wilson95"])
                self.assertGreaterEqual(artifact["hits"], previous)
                previous = artifact["hits"]
                expected_pass = artifact["rate"] >= 0.80 and artifact["wilson95"][0] >= 0.75
                self.assertEqual(artifact["acceptance"]["pass"], expected_pass)

    def test_material_stopping_evidence_is_exact(self) -> None:
        for game in ("ssq", "dlt"):
            report = self.report(game)
            self.assertEqual(set(report["candidate_improvements"]), set(E12.CANDIDATES) - {"e11_baseline_80"})
            self.assertTrue(report["candidate_expansion"]["baseline_excluded_from_improvement_search"])
            found = any(
                values["relative_k90_reduction_vs_e11"] >= 0.05
                or values["relative_k80_reduction_vs_e11"] >= 0.05
                for values in report["candidate_improvements"].values()
            )
            self.assertEqual(report["material_new_candidate_improvement_found"], found)
            self.assertEqual(report["material_new_candidate_improvement_found"], found)
            selected = report["selected_candidate"]
            selected_values = report["candidate_improvements"].get(selected)
            selected_material = bool(
                selected_values
                and (
                    selected_values["relative_k90_reduction_vs_e11"] >= 0.05
                    or selected_values["relative_k80_reduction_vs_e11"] >= 0.05
                )
            )
            self.assertEqual(report["selected_candidate_material_improvement"], selected_material)
            self.assertEqual(report["material_inner_k90_or_k80_improvement_found"], selected_material)
            self.assertEqual(report["candidate_expansion"]["stopped_for_no_material_improvement"], not selected_material)


if __name__ == "__main__":
    unittest.main()
