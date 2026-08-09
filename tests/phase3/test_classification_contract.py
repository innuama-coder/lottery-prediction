from __future__ import annotations

import unittest

from lottery_research.phase3.classification import (
    PRACTICAL_SKILL_DELTA,
    classify_model,
    holm_adjust,
    moving_block_evidence,
    summarize_phase,
)


class ClassificationContractTests(unittest.TestCase):
    def test_bootstrap_is_deterministic_and_uses_frozen_ranks(self) -> None:
        values = [0.001 * ((index % 7) - 2) for index in range(40)]
        first = moving_block_evidence(values, seed="release|ssq|M1", replicates=100)
        second = moving_block_evidence(values, seed="release|ssq|M1", replicates=100)
        self.assertEqual(first, second)
        self.assertEqual(first.block_length, 5)
        self.assertEqual(first.replicates, 100)
        self.assertLessEqual(first.lower, first.upper)
        self.assertGreater(first.raw_p, 0.0)

    def test_holm_uses_canonical_tie_break_and_prefix_max(self) -> None:
        adjusted = holm_adjust({("M2", "ssq"): 0.02, ("M1", "ssq"): 0.01, ("M1", "dlt"): 0.02})
        self.assertAlmostEqual(adjusted[("M1", "ssq")], 0.03)
        self.assertAlmostEqual(adjusted[("M1", "dlt")], 0.04)
        self.assertAlmostEqual(adjusted[("M2", "ssq")], 0.04)

    def test_classification_decision_tree_is_total_and_unique(self) -> None:
        passing = {game: {"lower": PRACTICAL_SKILL_DELTA + 0.001, "upper": 0.01, "holm_adjusted_p": 0.01, "non_bootstrap_gates_passed": True} for game in ("dlt", "ssq")}
        uncertain = {"dlt": {"lower": 0.0, "upper": PRACTICAL_SKILL_DELTA + 0.001, "holm_adjusted_p": 0.2, "non_bootstrap_gates_passed": True}}
        failing = {"dlt": {"lower": -0.01, "upper": 0.0, "holm_adjusted_p": 0.8, "non_bootstrap_gates_passed": False}}
        self.assertEqual(classify_model(opened=False, integrity_passed=True, games={}), "not_opened")
        self.assertEqual(classify_model(opened=True, integrity_passed=False, games=failing), "rejected")
        self.assertEqual(classify_model(opened=True, integrity_passed=True, games=passing), "shadow_candidate")
        self.assertEqual(classify_model(opened=True, integrity_passed=True, games=uncertain), "indeterminate")
        self.assertEqual(classify_model(opened=True, integrity_passed=True, games=failing), "archived")
        self.assertEqual(summarize_phase(["archived", "indeterminate"]), "indeterminate")


if __name__ == "__main__":
    unittest.main()
