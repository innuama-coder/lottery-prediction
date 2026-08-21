import copy
import importlib.util
import json
import math
import pathlib
import sys
import unittest

import numpy as np


path = pathlib.Path(__file__).parents[2] / "scripts/phase4e19/ssq_prize_aware.py"
spec = importlib.util.spec_from_file_location("ssq_prize_aware", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class SSQPrizeAwareTests(unittest.TestCase):
    def test_registered_prize_rules_are_complete(self):
        expected = {
            (6, 1): 1, (6, 0): 2, (5, 1): 3,
            (5, 0): 4, (4, 1): 4, (4, 0): 5, (3, 1): 5,
            (3, 0): 6, (2, 1): 6, (1, 1): 6, (0, 1): 6,
        }
        observed = {
            (red, blue): module.prize_tier(red, blue)
            for red in range(7) for blue in range(2)
            if module.prize_tier(red, blue) is not None
        }
        self.assertEqual(observed, expected)
        self.assertEqual(module.SSQ_FIXED_PRIZES, {1: 5_000_000.0, 2: 100_000.0, 3: 3_000.0, 4: 200.0, 5: 10.0, 6: 5.0})

    def test_fixed_first_and_second_prize_benchmarks(self):
        self.assertEqual(module.ticket_prize([1, 2, 3, 4, 5, 6], 7, [1, 2, 3, 4, 5, 6], 7), 5_000_000.0)
        self.assertEqual(module.ticket_prize([1, 2, 3, 4, 5, 6], 7, [1, 2, 3, 4, 5, 6], 8), 100_000.0)

    def test_expected_prize_score_sums_all_tier_contributions(self):
        red_hit_distribution = [.02, .03, .05, .10, .20, .25, .35]
        contributions = module.expected_prize_contributions(red_hit_distribution, .125)
        manual = 0.0
        for red_hits, red_probability in enumerate(red_hit_distribution):
            for blue_hits, blue_probability in ((0, .875), (1, .125)):
                tier = module.prize_tier(red_hits, blue_hits)
                manual += red_probability * blue_probability * module.SSQ_FIXED_PRIZES.get(tier, 0.0)
        self.assertTrue(math.isclose(sum(contributions.values()), manual, abs_tol=1e-10))
        self.assertEqual(set(contributions), set(range(1, 7)))

    def test_full_ticket_ranking_has_stable_score_then_ticket_tie_break(self):
        scores = np.array([3.0, 4.0, 4.0, 1.0, 4.0])
        ticket_ids = np.array([8, 7, 2, 1, 5])
        selected = module._stable_top_indices(scores, ticket_ids, 3)
        self.assertEqual(ticket_ids[selected].tolist(), [2, 5, 7])

    def test_exact_partition_prefix_arithmetic(self):
        combos = module.legal_red_combinations()
        winning_red_index = int(np.flatnonzero((combos == np.arange(6)).all(axis=1))[0])
        first_prize = winning_red_index * 16 + 6  # blue index 6 => canonical blue 7
        second_prize = winning_red_index * 16 + 7
        losing = 0
        metrics = module.evaluate_portfolio([first_prize, second_prize, losing], [1, 2, 3, 4, 5, 6], 7, (1, 2, 3))
        self.assertEqual(metrics["1"]["known_prize_total_yuan"], 5_000_000.0)
        self.assertEqual(metrics["2"]["known_prize_total_yuan"], 5_100_000.0)
        self.assertEqual(metrics["2"]["prize_tier_ticket_counts"]["1"], 1)
        self.assertEqual(metrics["2"]["prize_tier_ticket_counts"]["2"], 1)
        self.assertEqual(metrics["3"]["average_prize_yuan"], metrics["3"]["known_prize_total_yuan"] / 3)

    def test_deterministic_random_baseline_replay(self):
        first = module.random_baseline_portfolio(3362, 1000)
        second = module.random_baseline_portfolio(3362, 1000)
        self.assertTrue(np.array_equal(first["ticket_ids"], second["ticket_ids"]))
        self.assertEqual(first["portfolio_sha256_int64_le"], second["portfolio_sha256_int64_le"])
        self.assertEqual(len(np.unique(first["ticket_ids"])), 1000)

    def test_outer_labels_cannot_change_feature_hash(self):
        rows = module.load_source_rows()
        cutoff = len(rows) - module.OUTER_DRAWS
        original = module.build_feature_snapshot(rows, cutoff)
        changed = copy.deepcopy(rows)
        for row in changed[cutoff:]:
            row["front"] = [1, 2, 3, 4, 5, 6]
            row["back"] = [1]
            row["source_record_sha256"] = "changed-label"
        replay = module.build_feature_snapshot(changed, cutoff)
        self.assertEqual(original["feature_bundle_sha256"], replay["feature_bundle_sha256"])
        self.assertLess(original["maximum_source_position"], cutoff)

    def test_finite_registered_candidate_family(self):
        module.validate_candidate_registry()
        registry = module.candidate_registry()
        self.assertEqual(len(registry), 6)
        self.assertEqual(registry[0]["candidate_id"], "raw_control")
        self.assertEqual(len({row["candidate_id"] for row in registry}), 6)
        self.assertTrue(any(row["diversified"] for row in registry))

    def test_dlt_and_serving_isolation(self):
        result = module.verify_isolation()
        self.assertTrue(result["all_dlt_hashes_match"])
        self.assertTrue(result["p4e6_serving_identity_matches"])
        self.assertEqual(result["p4e6_serving_release"], "P4-P4E2-20260815-r12")
        self.assertEqual(result["p4e6_terminal_status"], "PROSPECTIVE_ONLY")

    def test_generated_artifacts_cover_frozen_splits_candidates_and_evidence(self):
        root = pathlib.Path(__file__).parents[2] / "artifacts/phase4e19"
        report = json.loads((root / "report.json").read_text())
        self.assertEqual(report["decision"], "NO_PROMOTION")
        self.assertEqual(report["selected_candidate"], "raw_control")
        self.assertEqual(report["frozen_windows"]["inner"]["draws"], 240)
        self.assertEqual(report["frozen_windows"]["outer"]["calibration_draws"], 60)
        self.assertEqual(report["frozen_windows"]["outer"]["evaluation_draws"], 60)
        self.assertEqual(set(report["outer_candidate_comparison"]), {row["candidate_id"] for row in module.CANDIDATE_SPECS} | {"random_baseline"})
        for split in ("calibration", "evaluation", "all_120"):
            partitions = report["selected_candidate_summaries"][split]["partitions"]
            self.assertEqual(set(map(int, partitions)), set(module.SSQ_PARTITION_SIZES))
            self.assertTrue(all("average_prize_yuan_confidence_interval_95" in value for value in partitions.values()))
            self.assertTrue(all(set(value["prize_tier_ticket_counts"]) == set(map(str, range(1, 7))) for value in partitions.values()))
        replay = json.loads((root / "replay-evidence.json").read_text())
        self.assertTrue(replay["exact_ticket_order_matches"])
        self.assertTrue(replay["feature_replay_matches_after_outer_label_mutation"])
        self.assertTrue(replay["ticket_replay_matches_after_outer_label_mutation"])
        lineage = json.loads((root / "feature-lineage.json").read_text())
        self.assertEqual(len(lineage["feature_records"]), 130)
        required = {"cutoff_position_exclusive", "registered_window_draws", "input_sha256", "value_sha256_float64_le", "lineage", "strict_lag"}
        self.assertTrue(all(required <= set(row) for row in lineage["feature_records"]))

    def test_acceptance_gate_is_strict_for_every_value(self):
        self.assertTrue(module.acceptance_gate([2.1, 2.5])["passed"])
        self.assertFalse(module.acceptance_gate([2.1, 2.0])["passed"])
        self.assertFalse(module.acceptance_gate([2.1, 1.9])["passed"])


if __name__ == "__main__":
    unittest.main()
