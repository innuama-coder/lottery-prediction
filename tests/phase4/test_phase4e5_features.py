from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import numpy as np

from lottery_system.phase4e5.features import (
    apply_preprocessor, build_feature_rows, candidate_names, fit_preprocessor,
    load_draws, load_metadata, raw_matrix, require_strict_prior,
)


ROOT = Path(__file__).resolve().parents[2]


class Phase4E5FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draws = load_draws(ROOT / "artifacts/phase-4e4/data-20260819/selection-prefix/dlt.jsonl")[-480:]
        cls.metadata = load_metadata(ROOT / "artifacts/phase-4e5/metadata-audit/dlt-official-metadata.jsonl")

    def test_current_and_future_join_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "current/future metadata rejected"):
            require_strict_prior("2024006", {"issue": "2024006"})
        with self.assertRaisesRegex(ValueError, "current/future metadata rejected"):
            require_strict_prior("2024006", {"issue": "2024007"})
        require_strict_prior("2024006", {"issue": "2024005"})

    def test_current_row_is_not_consumed(self) -> None:
        draw = self.draws[10]
        metadata = {str(draw["issue"]): self.metadata[str(draw["issue"])]}
        row = build_feature_rows("dlt", [draw], metadata)[0]
        self.assertIsNone(row["maximum_metadata_issue"])
        self.assertTrue(all(row["field_missing"].values()))

    def test_future_metadata_mutation_cannot_change_prior_features(self) -> None:
        rows = build_feature_rows("dlt", self.draws, self.metadata)
        mutated = copy.deepcopy(self.metadata)
        last_issue = str(self.draws[-1]["issue"])
        mutated[last_issue]["sales"] = 10**30
        changed = build_feature_rows("dlt", self.draws, mutated)
        self.assertEqual(rows[:-1], changed[:-1])
        self.assertEqual(rows[-1], changed[-1])

    def test_ssq_missingness_is_explicit(self) -> None:
        draws = load_draws(ROOT / "artifacts/phase-4e4/data-20260819/selection-prefix/ssq.jsonl")[:4]
        rows = build_feature_rows("ssq", draws, {})
        self.assertTrue(all(all(row["field_missing"].values()) for row in rows))
        self.assertTrue(all(row["values"]["block_missing_fraction"] == 1.0 for row in rows))

    def test_preprocessing_fit_is_prefix_only_and_exactly_replayable(self) -> None:
        feature_rows = build_feature_rows("dlt", self.draws, self.metadata)
        names = candidate_names("O2")
        matrix = raw_matrix(feature_rows, names)
        train = list(range(300))
        first = fit_preprocessor(matrix, train, names, "log1p_robust_z", (0.01, 0.99))
        mutated = matrix.copy(); mutated[300:] = 10**20
        second = fit_preprocessor(mutated, train, names, "log1p_robust_z", (0.01, 0.99))
        self.assertEqual(first, second)
        np.testing.assert_array_equal(apply_preprocessor(matrix, first)[:300], apply_preprocessor(mutated, second)[:300])
        np.testing.assert_array_equal(apply_preprocessor(matrix, first), apply_preprocessor(matrix, first))


if __name__ == "__main__":
    unittest.main()
