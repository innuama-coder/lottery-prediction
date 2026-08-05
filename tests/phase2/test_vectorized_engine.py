from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from lottery_research.phase2.statistics import PRIMARY_FAMILIES, calculate_statistics
from lottery_research.phase2.vectorized import (
    calculate_statistics_batch,
    generate_batch,
    precompute_supported_spaces,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "artifacts/phase-2/contracts/input-manifest.json").read_text(encoding="utf-8"))
RULES = MANIFEST["game_rule_maps"]


class VectorizedEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spaces = precompute_supported_spaces(RULES)

    def test_every_legal_combination_is_precomputed(self) -> None:
        expected = {
            "dlt": (math.comb(35, 5), math.comb(12, 2)),
            "ssq": (math.comb(33, 6), math.comb(16, 1)),
        }
        for game, space in self.spaces.items():
            self.assertEqual((len(space.front.combinations), len(space.back.combinations)), expected[game])
            self.assertFalse(space.front.combinations.flags.writeable)
            self.assertFalse(space.back.combinations.flags.writeable)

    def test_all_generators_only_emit_legal_tickets(self) -> None:
        effects = {
            "null": 0.0,
            "marginal_inclusion": 0.1,
            "set_structure": 3.0,
            "pair_dependence": 0.1,
            "temporal_instability": 0.2,
            "cross_zone_dependence": 0.8,
        }
        for rule in RULES:
            segment = rule["number_space_segments"][0]
            for family, effect in effects.items():
                with self.subTest(game=rule["game"], family=family):
                    batch = generate_batch(rule, worlds=3, draws=20, family=family, effect=effect, seed=13)
                    for zone in ("front", "back"):
                        values = getattr(batch, f"{zone}_numbers")
                        self.assertEqual(values.shape[2], segment[zone]["draw_count"])
                        self.assertTrue(np.all(values >= segment[zone]["min"]))
                        self.assertTrue(np.all(values <= segment[zone]["max"]))
                        self.assertTrue(np.all(np.diff(values, axis=2) > 0))

    def test_same_seed_is_array_reproducible(self) -> None:
        for rule in RULES:
            left = generate_batch(rule, worlds=4, draws=30, family="cross_zone_dependence", effect=0.6, seed=707)
            right = generate_batch(rule, worlds=4, draws=30, family="cross_zone_dependence", effect=0.6, seed=707)
            self.assertTrue(np.array_equal(left.front_numbers, right.front_numbers))
            self.assertTrue(np.array_equal(left.back_numbers, right.back_numbers))
            self.assertTrue(np.array_equal(left.issue_ids, right.issue_ids))

    def test_vectorized_statistics_equal_scalar_single_and_multiple_worlds(self) -> None:
        # A front-zone structure injection makes the standardized and raw-effect
        # winning zone identical, including under the legacy scalar path.
        for rule in RULES:
            for worlds in (1, 5):
                with self.subTest(game=rule["game"], worlds=worlds):
                    batch = generate_batch(rule, worlds=worlds, draws=40, family="set_structure", effect=15.0, seed=91)
                    vectorized = calculate_statistics_batch(batch, rule, chunk_worlds=2)
                    for world in range(worlds):
                        scalar = calculate_statistics(batch.scalar_world(world), rule)
                        for family in (*PRIMARY_FAMILIES, "negative_control"):
                            self.assertAlmostEqual(vectorized[family]["statistic"][world], scalar[family]["statistic"], places=12)
                            self.assertAlmostEqual(vectorized[family]["effect"][world], scalar[family]["effect"], places=12)

    def test_each_strong_bias_moves_its_registered_statistic_upward(self) -> None:
        effects = {
            "marginal_inclusion": 0.4,
            "set_structure": 30.0,
            "pair_dependence": 0.4,
            "temporal_instability": 0.25,
            "cross_zone_dependence": 1.0,
        }
        for game_index, rule in enumerate(RULES):
            null = generate_batch(rule, worlds=80, draws=200, seed=1000 + game_index)
            null_stats = calculate_statistics_batch(null, rule, chunk_worlds=20)
            for offset, (family, effect) in enumerate(effects.items()):
                with self.subTest(game=rule["game"], family=family):
                    biased = generate_batch(
                        rule,
                        worlds=80,
                        draws=200,
                        family=family,
                        effect=effect,
                        seed=2000 + game_index * 10 + offset,
                    )
                    biased_stats = calculate_statistics_batch(biased, rule, chunk_worlds=20)
                    self.assertGreater(
                        float(np.mean(biased_stats[family]["statistic"])),
                        float(np.mean(null_stats[family]["statistic"])),
                    )


if __name__ == "__main__":
    unittest.main()
