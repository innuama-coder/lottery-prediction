from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from lottery_system.phase4.baseline_model import (
    build_point_in_time_dataset, feature_rows, fit_logistic,
)

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "artifacts/phase4e31_baseline/summary.json"


class Phase4E31BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ROOT / "artifacts/phase4e30_data_expansion/dlt-draws-full.jsonl"
        cls.draws = [json.loads(line)["back_numbers"] for line in source.read_text().splitlines()[:50]]

    def test_model_trains_and_probabilities_are_strict(self):
        x, y = build_point_in_time_dataset(self.draws, 12, 2)
        model = fit_logistic([row for draw in x for row in draw],
                             [value for draw in y for value in draw], 1e-3)
        self.assertTrue(any(abs(value) > 1e-12 for value in model.theta))
        probabilities = model.predict_proba(feature_rows(self.draws, 12, 2))
        self.assertTrue(all(0.0 < value < 1.0 for value in probabilities))

    def test_target_draw_cannot_affect_its_prediction(self):
        t = 40
        original = list(self.draws)
        mutated = list(self.draws)
        mutated[t] = [1, 12] if original[t] != [1, 12] else [2, 11]
        x1, y1 = build_point_in_time_dataset(original, 12, 2)
        x2, y2 = build_point_in_time_dataset(mutated, 12, 2)
        self.assertEqual(x1[t], x2[t])
        model1 = fit_logistic([row for draw in x1[:t] for row in draw],
                              [v for draw in y1[:t] for v in draw], 1e-3)
        model2 = fit_logistic([row for draw in x2[:t] for row in draw],
                              [v for draw in y2[:t] for v in draw], 1e-3)
        self.assertEqual(model1.predict_proba(x1[t]), model2.predict_proba(x2[t]))

    def test_deterministic_for_same_seed_and_input(self):
        x, y = build_point_in_time_dataset(self.draws, 12, 2)
        rows = [row for draw in x for row in draw]
        labels = [v for draw in y for v in draw]
        first = fit_logistic(rows, labels, 1e-3)
        second = fit_logistic(rows, labels, 1e-3)
        self.assertEqual(first, second)

    def test_summary_schema_and_finite_losses(self):
        summary = json.loads(SUMMARY.read_text())
        for field in ("evaluation_window", "lambda_candidates", "lambda_selection_prefix_only",
                      "standardization_prefix_only", "zones", "paired_significance",
                      "scientific_conclusion"):
            self.assertIn(field, summary)
        for zone in ("front", "back"):
            for field in ("mean_model_log_loss", "mean_uniform_log_loss", "mean_normalized_rank"):
                self.assertTrue(math.isfinite(summary["zones"][zone][field]))
        self.assertIn(summary["scientific_conclusion"], ("no_confirmed_lift", "confirmed_lift"))


if __name__ == "__main__":
    unittest.main()
