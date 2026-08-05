from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

from lottery_research.phase2.intervals import clopper_pearson, regularized_beta
from lottery_research.phase2.simulation import generate_null_draws, generate_strong_positive, simulate_null_statistics
from lottery_research.phase2.statistics import PRIMARY_FAMILIES, calculate_statistics, holm_adjust

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "artifacts/phase-2/contracts/input-manifest.json").read_text(encoding="utf-8"))


class StatisticalEngineTests(unittest.TestCase):
    def test_exact_binomial_interval_reference_value(self) -> None:
        lower, upper = clopper_pearson(1000, 20000)
        self.assertAlmostEqual(lower, 0.04702009046, places=10)
        self.assertAlmostEqual(upper, 0.05311156794, places=10)
        self.assertAlmostEqual(regularized_beta(0.5, 2, 2), 0.5, places=12)

    def test_holm_is_monotone_and_controls_order(self) -> None:
        adjusted = holm_adjust({"a": 0.001, "b": 0.02, "c": 0.2})
        self.assertEqual(adjusted, {"a": 0.003, "b": 0.04, "c": 0.2})

    def test_null_tickets_and_statistics_obey_contract(self) -> None:
        for game_index in (0, 1):
            with self.subTest(game_index=game_index):
                rule = MANIFEST["game_rule_maps"][game_index]
                issues = [str(2026000 + index) for index in range(50)]
                draws = generate_null_draws(rule, issues, random.Random(7))
                space = rule["number_space_segments"][0]
                for draw in draws:
                    for zone in ("front", "back"):
                        values = draw[f"{zone}_numbers"]
                        self.assertEqual(len(values), space[zone]["draw_count"])
                        self.assertEqual(len(values), len(set(values)))
                        self.assertGreaterEqual(min(values), space[zone]["min"])
                        self.assertLessEqual(max(values), space[zone]["max"])
                statistics = calculate_statistics(draws, rule)
                self.assertEqual(set(statistics), set(PRIMARY_FAMILIES) | {"negative_control"})
                self.assertTrue(all(row["statistic"] >= 0 for row in statistics.values()))

    def test_every_strong_positive_path_exceeds_null_99th_percentile(self) -> None:
        for game_index in (0, 1):
            rule = MANIFEST["game_rule_maps"][game_index]
            issues = [str(2025000 + index) for index in range(200)]
            null = simulate_null_statistics(rule, issues, 199, 100 + game_index)
            for family in PRIMARY_FAMILIES:
                with self.subTest(game_index=game_index, family=family):
                    injected = generate_strong_positive(rule, issues, family, random.Random(900 + game_index))
                    observed = calculate_statistics(injected, rule)[family]["statistic"]
                    self.assertEqual(sum(row[family]["statistic"] >= observed for row in null), 0)

    def test_fixed_seed_is_byte_reproducible(self) -> None:
        rule = MANIFEST["game_rule_maps"][0]
        issues = [str(2025000 + index) for index in range(20)]
        left = simulate_null_statistics(rule, issues, 5, 123)
        right = simulate_null_statistics(rule, issues, 5, 123)
        self.assertEqual(json.dumps(left, sort_keys=True, separators=(",", ":")), json.dumps(right, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
