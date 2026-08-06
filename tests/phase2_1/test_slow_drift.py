from __future__ import annotations

import json
import unittest

import numpy as np

from lottery_research.phase2.vectorized import calculate_statistics_batch
from lottery_research.phase2_1.simulation import generate_slow_drift_batch, slow_drift_probabilities
from lottery_research.phase2_1.workflow import project_root


ROOT = project_root()


def maps() -> dict[str, dict]:
    payload = json.loads((ROOT / "artifacts/phase-2/contracts/input-manifest.json").read_text(encoding="utf-8"))
    return {row["game"]: row for row in payload["game_rule_maps"]}


class SlowDriftTests(unittest.TestCase):
    def test_profile_is_genuine_gradual_drift_with_exact_estimand(self) -> None:
        profile = slow_drift_probabilities(5 / 35, 0.06, 200)
        self.assertEqual(len(np.unique(profile)), 200)
        self.assertTrue(np.all(np.diff(profile) < 0))
        self.assertAlmostEqual(float(profile[:100].mean() - profile[100:].mean()), 0.06, places=14)

    def test_zero_effect_profile_is_constant(self) -> None:
        profile = slow_drift_probabilities(6 / 33, 0.0, 50)
        self.assertTrue(np.all(profile == 6 / 33))

    def test_impossible_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            slow_drift_probabilities(0.1, 1.0, 20)

    def test_generator_is_legal_and_deterministic(self) -> None:
        rule = maps()["dlt"]
        left = generate_slow_drift_batch(rule, worlds=32, draws=200, effect=0.06, seed=7)
        right = generate_slow_drift_batch(rule, worlds=32, draws=200, effect=0.06, seed=7)
        self.assertTrue(np.array_equal(left.front_numbers, right.front_numbers))
        self.assertTrue(np.array_equal(left.back_numbers, right.back_numbers))
        self.assertTrue(np.all(np.diff(left.front_numbers, axis=2) > 0))
        self.assertTrue(np.all((left.front_numbers >= 1) & (left.front_numbers <= 35)))
        self.assertTrue(np.all(np.diff(left.back_numbers, axis=2) > 0))

    def test_strong_slow_drift_recovers_positive_half_contrast(self) -> None:
        rule = maps()["ssq"]
        batch = generate_slow_drift_batch(rule, worlds=128, draws=200, effect=0.12, seed=11)
        target = np.any(batch.front_numbers == 1, axis=2)
        contrast = target[:, :100].mean(axis=1) - target[:, 100:].mean(axis=1)
        self.assertGreater(float(contrast.mean()), 0.09)
        statistics = calculate_statistics_batch(batch, rule)["temporal_instability"]["statistic"]
        self.assertGreaterEqual(float(np.mean(statistics >= 0.08)), 0.95)


if __name__ == "__main__":
    unittest.main()
