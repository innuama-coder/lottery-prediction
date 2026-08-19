from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from lottery_system.phase4e4.data import canonical, load_jsonl, sha256_bytes
from lottery_system.phase4e4.model import FAMILIES, configurations, fit_model, score_block
from scripts.phase4e4.run_selection import grouped_log_losses, inner_folds, outer_folds


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/phase4e4/run_selection.py"
INPUT = ROOT / "artifacts/phase-4e4/data-20260819/selection-prefix/dlt.jsonl"
RECEIPTS = ROOT / "artifacts/phase-4e4/selection-20260819"


class Phase4E4SelectionTests(unittest.TestCase):
    def test_frozen_fold_geometry_and_complete_grids(self) -> None:
        folds = outer_folds(1013)
        self.assertEqual(
            folds,
            [
                {"fold": 1, "train_end": 717, "validation": [725, 765]},
                {"fold": 2, "train_end": 765, "validation": [773, 813]},
                {"fold": 3, "train_end": 813, "validation": [821, 861]},
                {"fold": 4, "train_end": 861, "validation": [869, 909]},
                {"fold": 5, "train_end": 909, "validation": [917, 957]},
                {"fold": 6, "train_end": 957, "validation": [965, 1005]},
            ],
        )
        for fold in folds:
            inner = inner_folds(int(fold["train_end"]))
            self.assertEqual(len(inner), 4)
            self.assertTrue(all(row["validation"][0] - row["train_end"] == 8 for row in inner))
            self.assertTrue(all(row["validation"][1] - row["validation"][0] == 24 for row in inner))
        self.assertEqual(dict(zip(FAMILIES, map(lambda family: len(configurations(family)), FAMILIES))), {
            "E401_MULTISCALE_REGIME": 18, "E402_BAYES_RENEWAL": 18,
            "E403_HYPERGRAPH_SURPRISE": 72, "E404_TEMPORAL_GRAPH": 108,
            "E405_SET_SHAPE_INTERACTIONS": 18, "E406_CROSS_ZONE_COUPLING": 18,
            "E407_NONLINEAR_SET_FACTOR": 72,
        })

    def test_grouped_cache_is_numerically_identical_to_direct_evaluation(self) -> None:
        draws = load_jsonl(INPUT, "dlt")
        cutoff, positions = 957, list(range(965, 971))
        family = "E401_MULTISCALE_REGIME"
        grid = [configurations(family)[0], configurations(family)[-1]]
        grouped = grouped_log_losses("dlt", draws, cutoff, family, grid, positions)
        for config in grid:
            direct = [row["joint_log_loss"] for row in score_block(fit_model("dlt", draws, cutoff, family, config), draws, positions)]
            self.assertEqual(len(grouped[canonical(config)]), len(direct))
            for left, right in zip(grouped[canonical(config)], direct):
                self.assertAlmostEqual(left, right, places=12)

    def test_selection_has_no_sealed_path_and_rejects_label_arguments(self) -> None:
        self.assertNotIn("sealed-report", SCRIPT.read_text(encoding="utf-8"))
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--report-labels", "forbidden.jsonl"],
            cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True,
        )
        self.assertNotEqual(completed.returncode, 0)

    def test_completed_receipts_cover_frozen_experiment_and_capabilities(self) -> None:
        for game, draw_count in (("ssq", 3222), ("dlt", 1013)):
            receipt = json.loads((RECEIPTS / f"{game}-selection-receipt.json").read_text(encoding="utf-8"))
            recorded = receipt.pop("receipt_sha256")
            self.assertEqual(recorded, sha256_bytes(canonical(receipt)))
            self.assertEqual(receipt["candidate_count"], 7)
            self.assertEqual(receipt["selection_draw_count"], draw_count)
            self.assertFalse(receipt["report_labels_read"])
            self.assertFalse(receipt["original_200_labels_read"])
            self.assertEqual(len(receipt["outer_folds"]), 6)
            self.assertEqual(set(receipt["candidates"]), set(FAMILIES))
            self.assertEqual(receipt["comparators"], ["M0", "P4E2_r12_retrained", "P4E3_Transition_retrained"])
            for family, candidate in receipt["candidates"].items():
                self.assertEqual(candidate["configuration_count"], len(configurations(family)))
                self.assertEqual(len(candidate["outer_folds"]), 6)
                self.assertTrue(all(len(fold["inner_folds"]) == 4 for fold in candidate["outer_folds"]))
        ssq = json.loads((RECEIPTS / "ssq-selection-receipt.json").read_text(encoding="utf-8"))
        self.assertFalse(ssq["promotion_authority"])


if __name__ == "__main__":
    unittest.main()
