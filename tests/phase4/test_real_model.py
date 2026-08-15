from __future__ import annotations

import math
import unittest
from decimal import Decimal
from pathlib import Path

from lottery_system.phase4.real_model import elementary, load_draws, subset_probability, top_tickets, train


ROOT = Path(__file__).resolve().parents[2]
PHASE1 = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"


class RealModelTest(unittest.TestCase):
    def test_real_history_train_is_non_m0_and_fold_isolated(self) -> None:
        for game in ("ssq", "dlt"):
            draws = load_draws(PHASE1, game)
            model = train(game, draws)
            self.assertEqual(model["family"], "P4E1-R")
            self.assertFalse(set(model["selection_indices"]) & set(model["report_only_indices"]))
            self.assertTrue(any(abs(zone["theta"]) > 0 for zone in model["zones"]))
            self.assertTrue(any(max(zone["weights"]) > min(zone["weights"]) for zone in model["zones"]))

    def test_elementary_normalization_small_known_answer(self) -> None:
        zone = {"weights": [1.0, 2.0, 3.0, 4.0], "normalizer": elementary([1.0, 2.0, 3.0, 4.0], 2)}
        total = sum(subset_probability(combo, zone) for combo in ((1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)))
        self.assertTrue(math.isclose(total, 1.0, rel_tol=1e-14))

    def test_top1000_probability_primary(self) -> None:
        model = train("dlt", load_draws(PHASE1, "dlt"))
        rows = top_tickets(model)
        self.assertEqual(len(rows), 1000)
        self.assertEqual(len({(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in rows}), 1000)
        probabilities = [float(row["joint_probability"]) for row in rows]
        self.assertTrue(all(left >= right > 0 for left, right in zip(probabilities, probabilities[1:])))
        self.assertGreater(len(set(probabilities)), 1)
        exact_probabilities = [Decimal(row["joint_probability"]) for row in rows]
        self.assertTrue(all(left >= right > 0 for left, right in zip(exact_probabilities, exact_probabilities[1:])))
        required = {"tie_group_id", "tie_group_size", "tie_rank_lower", "tie_rank_upper", "tie_midrank", "tie_key"}
        self.assertTrue(all(required <= row.keys() for row in rows))
        for row in rows:
            self.assertEqual(Decimal(row["tie_midrank"]), (Decimal(row["tie_rank_lower"]) + Decimal(row["tie_rank_upper"])) / 2)
            self.assertEqual(row["probability_representation"], "P4-DECIMAL-EXACT-1")


if __name__ == "__main__":
    unittest.main()
