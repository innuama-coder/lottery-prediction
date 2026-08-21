import copy
import hashlib
import importlib.util
import json
import math
import pathlib
import sys
import unittest

import numpy as np

PATH = pathlib.Path(__file__).parents[2] / "scripts/phase4e20/ssq_supervised_compression.py"
SPEC = importlib.util.spec_from_file_location("ssq_supervised_compression", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Phase4E20SupervisedCompressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = MODULE.load_source_rows()
        cls.history = MODULE.encode_history(cls.rows)
        cls.cutoff = len(cls.rows) - MODULE.OUTER_DRAWS
        cls.fitted = MODULE.fit_supervised_heads(cls.rows, cls.history, cls.cutoff)

    def test_supervised_training_is_strict_lag(self):
        self.assertEqual(self.fitted["maximum_fit_label_position"], self.cutoff - 1)
        self.assertLess(self.fitted["maximum_fit_label_position"], self.cutoff)
        for lineage in self.fitted["lineage"].values():
            self.assertTrue(lineage["strict_lag_per_training_target"])
            self.assertEqual(lineage["training_target_end_position_inclusive"], self.cutoff - 1)
            self.assertEqual(lineage["maximum_fit_label_position"], self.cutoff - 1)
            self.assertGreater(lineage["per_number_rows"], 10000)

    def test_outer_label_mutation_cannot_change_fitted_heads(self):
        changed = copy.deepcopy(self.rows)
        for row in changed[self.cutoff:]:
            row["front"] = [1, 2, 3, 4, 5, 6]
            row["back"] = [1]
            row["source_record_sha256"] = "changed-outer-label"
        replay = MODULE.fit_supervised_heads(changed, self.history, self.cutoff)
        self.assertEqual(self.fitted["prefix_input_sha256"], replay["prefix_input_sha256"])
        self.assertEqual(self.fitted["coefficient_bundle_sha256"], replay["coefficient_bundle_sha256"])

    def test_horizon_forecast_varies_without_in_window_labels(self):
        horizon0 = MODULE.forecast_heads(self.fitted, self.history, 0)
        horizon17 = MODULE.forecast_heads(self.fitted, self.history, 17)
        self.assertFalse(np.array_equal(horizon0["ridge_a1"]["red"], horizon17["ridge_a1"]["red"]))
        self.assertFalse(np.array_equal(horizon0["ridge_a1"]["blue"], horizon17["ridge_a1"]["blue"]))
        self.assertAlmostEqual(float(horizon17["ridge_a1"]["red"].sum()), 6.0)
        self.assertAlmostEqual(float(horizon17["ridge_a1"]["blue"].sum()), 1.0)

    def test_unique_red_first_coverage_and_nested_prefix(self):
        forecasts = MODULE.forecast_heads(self.fitted, self.history, 0)
        spec = copy.deepcopy(MODULE.PORTFOLIO_SPECS[0])
        spec["unique_red_count"] = 2000
        cache = {"ridge_a1": np.arange(2000, dtype=np.int64)}
        first = MODULE.build_compressed_portfolio(forecasts, spec, max_count=2000, ranking_cache=cache)
        second = MODULE.build_compressed_portfolio(forecasts, spec, max_count=2000, ranking_cache=cache)
        red_ids = first["ticket_ids"] // 16
        self.assertEqual(len(np.unique(red_ids)), 2000)
        self.assertEqual(len(np.unique(first["ticket_ids"])), 2000)
        self.assertTrue(np.array_equal(first["ticket_ids"], second["ticket_ids"]))
        self.assertTrue(np.array_equal(first["ticket_ids"][:1000], second["ticket_ids"][:1000]))
        self.assertEqual(first["portfolio_sha256_int64_le"], second["portfolio_sha256_int64_le"])

    def test_layer_cap_is_enforced(self):
        forecasts = MODULE.forecast_heads(self.fitted, self.history, 0)
        spec = copy.deepcopy(MODULE.PORTFOLIO_SPECS[-1])
        spec["unique_red_count"] = 600
        cache = {
            "ridge_a1": np.arange(1000, dtype=np.int64),
            "ridge_a25": np.arange(999, -1, -1, dtype=np.int64),
        }
        portfolio = MODULE.build_compressed_portfolio(forecasts, spec, max_count=1000, ranking_cache=cache)
        counts = np.bincount(portfolio["ticket_ids"] // 16)
        self.assertLessEqual(int(counts.max()), 2)
        self.assertEqual(len(np.unique(portfolio["ticket_ids"])), 1000)
        self.assertEqual(len(np.unique(portfolio["ticket_ids"] // 16)), 600)

    def test_exact_prize_arithmetic_and_strict_gate(self):
        combos = MODULE.E19.legal_red_combinations()
        red_index = int(np.flatnonzero((combos == np.arange(6)).all(axis=1))[0])
        ids = np.array([red_index * 16 + 6, red_index * 16 + 7, 0], dtype=np.int64)
        result = MODULE.evaluate_portfolio(ids, [1, 2, 3, 4, 5, 6], 7, (1, 2, 3))
        self.assertEqual(result["1"]["known_prize_total_yuan"], 5_000_000.0)
        self.assertEqual(result["2"]["known_prize_total_yuan"], 5_100_000.0)
        self.assertEqual(result["2"]["prize_tier_ticket_counts"]["1"], 1)
        self.assertEqual(result["2"]["prize_tier_ticket_counts"]["2"], 1)
        self.assertTrue(math.isclose(result["3"]["average_prize_yuan"], result["3"]["known_prize_total_yuan"] / 3))
        self.assertTrue(MODULE.acceptance_gate([2.000001, 3.0])["passed"])
        self.assertFalse(MODULE.acceptance_gate([2.0, 3.0])["passed"])

    def test_finite_registry_and_isolation(self):
        MODULE.validate_registry()
        self.assertEqual(len(MODULE.PORTFOLIO_SPECS), 4)
        self.assertEqual({config["alpha"] for config in MODULE.HEAD_CONFIGS}, {1.0, 25.0})
        isolation = MODULE.verify_isolation()
        self.assertTrue(isolation["all_dlt_hashes_match"])
        self.assertTrue(isolation["p4e6_serving_identity_matches"])

    def test_generated_evidence_if_present(self):
        root = pathlib.Path(__file__).parents[2] / "artifacts/phase4e20"
        if not (root / "report.json").exists():
            self.skipTest("pipeline artifacts are generated by the E20 runner")
        report = json.loads((root / "report.json").read_text())
        self.assertEqual(report["frozen_windows"]["inner"]["draws"], 240)
        self.assertEqual(report["frozen_windows"]["outer"]["calibration_draws"], 60)
        self.assertEqual(report["frozen_windows"]["outer"]["evaluation_draws"], 60)
        self.assertFalse(report["candidate_selection"]["selection_uses_outer_labels"])
        self.assertEqual(set(report["outer_candidate_comparison"]), {spec["candidate_id"] for spec in MODULE.PORTFOLIO_SPECS} | set(MODULE.BASELINE_IDS))
        for split in ("calibration", "evaluation", "all_120"):
            partitions = report["selected_candidate_summaries"][split]["partitions"]
            self.assertEqual(set(map(int, partitions)), set(MODULE.SSQ_PARTITION_SIZES))
            self.assertTrue(all("average_prize_yuan_confidence_interval_95" in value for value in partitions.values()))
            self.assertTrue(all(set(value["prize_tier_ticket_counts"]) == set(map(str, range(1, 7))) for value in partitions.values()))
        replay = json.loads((root / "replay-evidence.json").read_text())
        self.assertTrue(replay["exact_ticket_order_matches"])
        self.assertTrue(replay["outer_label_mutation_coefficient_hash_matches"])
        self.assertTrue(replay["outer_label_mutation_portfolio_matches"])
        independent = json.loads((root / "independent-replay-evidence.json").read_text())
        self.assertTrue(independent["deterministic_payloads_match"])
        self.assertTrue(independent["normalized_report_match"])
        manifest = json.loads((root / "delivery/manifest.json").read_text())
        for entry in manifest["files"]:
            payload = root / entry["path"]
            self.assertEqual(payload.stat().st_size, entry["bytes"])
            self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
