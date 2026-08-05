from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lottery_research.phase2.research_engine import (
    checkpointed_prefix_statistics,
    domain_seed,
    empirical_p,
    holm_adjust_matrix,
    read_array_bundle,
    simulate_prefix_statistics,
    write_array_bundle,
)
from lottery_research.phase2.draws import load_frozen_draws
from lottery_research.phase2.formal_workflows import _qualification_direction_match, normalized_power_artifact
from lottery_research.phase2.reference import independent_reference_statistics
from lottery_research.phase2.statistics import calculate_statistics
from lottery_research.phase2.vectorized import generate_batch
from lottery_research.phase2.errors import InvalidContract
from lottery_research.phase2.workflows import _compare_grid_estimates, _project_path
from lottery_research.phase2.statistics import holm_adjust

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "artifacts/phase-2/contracts/input-manifest.json").read_text(encoding="utf-8"))
PREREGISTRATION = json.loads((ROOT / "artifacts/phase-2/contracts/preregistration.json").read_text(encoding="utf-8"))


class ResearchEngineTests(unittest.TestCase):
    def test_project_path_normalizes_relative_and_absolute_paths_and_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            relative_resolved, relative_label = _project_path(root, Path("artifacts/result.json"), label="result")
            absolute_resolved, absolute_label = _project_path(root, root / "artifacts/result.json", label="result")
            self.assertEqual(relative_resolved, absolute_resolved)
            self.assertEqual(relative_label, "artifacts/result.json")
            self.assertEqual(absolute_label, relative_label)
            with self.assertRaisesRegex(InvalidContract, "must stay within the project root"):
                _project_path(root, root.parent / "escaped.json", label="result")

    def test_formal_loader_reads_and_calendar_orders_the_frozen_400_draw_baseline(self) -> None:
        rows = load_frozen_draws(ROOT, MANIFEST)
        self.assertEqual({game: len(values) for game, values in rows.items()}, {"dlt": 200, "ssq": 200})
        for values in rows.values():
            ordering = [(row["draw_date_local"], str(row["issue_id"])) for row in values]
            self.assertEqual(ordering, sorted(ordering))

    def test_formal_and_replay_workflows_share_one_frozen_draw_loader(self) -> None:
        from lottery_research.phase2 import formal_workflows, workflows
        self.assertIs(formal_workflows.load_frozen_draws, workflows.load_frozen_draws)

    def test_reference_and_evaluation_are_independent_and_valid(self) -> None:
        rule = MANIFEST["game_rule_maps"][0]
        reference = simulate_prefix_statistics(rule, worlds=199, sample_sizes=[50, 100], family="null", effect=0.0, seed=101)
        evaluation = simulate_prefix_statistics(rule, worlds=200, sample_sizes=[50, 100], family="null", effect=0.0, seed=202)
        self.assertFalse(np.array_equal(reference[50]["marginal_inclusion"]["statistic"], evaluation[50]["marginal_inclusion"]["statistic"][:199]))
        pvalues = empirical_p(reference[100]["marginal_inclusion"]["statistic"], evaluation[100]["marginal_inclusion"]["statistic"])
        self.assertTrue(np.all((pvalues > 0) & (pvalues <= 1)))

    def test_temporal_each_prefix_uses_its_own_calendar_halves(self) -> None:
        rule = MANIFEST["game_rule_maps"][1]
        values = simulate_prefix_statistics(rule, worlds=40, sample_sizes=[50, 100], family="temporal_instability", effect=0.06, seed=303)
        self.assertEqual(len(values[50]["temporal_instability"]["statistic"]), 40)
        self.assertEqual(len(values[100]["temporal_instability"]["statistic"]), 40)

    def test_holm_matrix_equals_scalar_reference(self) -> None:
        matrix = np.array([[0.001, 0.02, 0.2], [0.2, 0.4, 0.8]])
        adjusted = holm_adjust_matrix(matrix)
        for index, row in enumerate(matrix):
            scalar = holm_adjust({str(i): float(value) for i, value in enumerate(row)})
            self.assertTrue(np.allclose(adjusted[index], [scalar[str(i)] for i in range(3)]))

    def test_array_bundle_is_byte_deterministic_and_roundtrips(self) -> None:
        arrays = {"b": np.array([3.0]), "a": np.array([1.0, 2.0])}
        with tempfile.TemporaryDirectory() as raw:
            left = Path(raw) / "left.bin"
            right = Path(raw) / "right.bin"
            write_array_bundle(left, arrays)
            write_array_bundle(right, arrays)
            self.assertEqual(left.read_bytes(), right.read_bytes())
            loaded = read_array_bundle(left)
        self.assertEqual(set(loaded), set(arrays))
        self.assertTrue(np.array_equal(loaded["a"], arrays["a"]))

    def test_real_statistics_checkpoint_interrupt_resume_matches_uninterrupted(self) -> None:
        rule = MANIFEST["game_rule_maps"][0]
        calendar = {50: list(range(2025001, 2025051)), 100: list(range(2025001, 2025101))}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaises(KeyboardInterrupt):
                checkpointed_prefix_statistics(rule, worlds=16, sample_sizes=[50, 100], family="null", effect=0.0, seed=404, checkpoint_root=root / "resumed", chunk_worlds=4, issue_ids_by_n=calendar, interrupt_after_new_batches=1)
            resumed, resumed_ledger = checkpointed_prefix_statistics(rule, worlds=16, sample_sizes=[50, 100], family="null", effect=0.0, seed=404, checkpoint_root=root / "resumed", chunk_worlds=4, issue_ids_by_n=calendar)
            full, full_ledger = checkpointed_prefix_statistics(rule, worlds=16, sample_sizes=[50, 100], family="null", effect=0.0, seed=404, checkpoint_root=root / "full", chunk_worlds=4, issue_ids_by_n=calendar)
        self.assertEqual(resumed_ledger["aggregate_sha256"], full_ledger["aggregate_sha256"])
        self.assertGreater(resumed_ledger["reused_batches"], 0)
        self.assertEqual(resumed_ledger["missing_batches"], 0)
        self.assertTrue(np.array_equal(resumed[100]["pair_dependence"]["statistic"], full[100]["pair_dependence"]["statistic"]))

    def test_checkpoint_rejects_wrong_configuration_and_extra_batch_file(self) -> None:
        rule = MANIFEST["game_rule_maps"][0]
        calendar = {50: list(range(2025001, 2025051))}
        with tempfile.TemporaryDirectory() as raw:
            checkpoint = Path(raw) / "checkpoint"
            checkpointed_prefix_statistics(rule, worlds=8, sample_sizes=[50], family="null", effect=0.0, seed=505, checkpoint_root=checkpoint, chunk_worlds=4, issue_ids_by_n=calendar)
            with self.assertRaisesRegex(ValueError, "configuration fingerprint mismatch"):
                checkpointed_prefix_statistics(rule, worlds=8, sample_sizes=[50], family="null", effect=0.0, seed=506, checkpoint_root=checkpoint, chunk_worlds=4, issue_ids_by_n=calendar)
            (checkpoint / "batch-99999.bin").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "unexpected batch files"):
                checkpointed_prefix_statistics(rule, worlds=8, sample_sizes=[50], family="null", effect=0.0, seed=505, checkpoint_root=checkpoint, chunk_worlds=4, issue_ids_by_n=calendar)

    def test_qualification_direction_rejects_wrong_component_or_sign(self) -> None:
        self.assertTrue(_qualification_direction_match("pair_dependence", {"selected_component": "front:1-2", "signed_effect": 0.2}))
        self.assertFalse(_qualification_direction_match("pair_dependence", {"selected_component": "front:1-3", "signed_effect": 0.2}))
        self.assertFalse(_qualification_direction_match("pair_dependence", {"selected_component": "front:1-2", "signed_effect": -0.2}))

    def test_all_registered_strong_qualification_generators_match_target_direction(self) -> None:
        families = ("marginal_inclusion", "set_structure", "pair_dependence", "temporal_instability", "cross_zone_dependence")
        parameters = PREREGISTRATION["qualification_parameters"]
        base_seed = PREREGISTRATION["seed_registry"]["calibration_interval_and_qualification"]
        for rule in MANIFEST["game_rule_maps"]:
            for family in families:
                effect = parameters["cross_zone_dependence_mixture_q"] if family == "cross_zone_dependence" else parameters[family]
                batch = generate_batch(rule, worlds=1, draws=200, family=family, effect=effect, seed=domain_seed(base_seed, f"qualification:{rule['game']}:{family}"))
                measured = calculate_statistics(batch.scalar_world(0), rule)[family]
                with self.subTest(game=rule["game"], family=family):
                    self.assertTrue(_qualification_direction_match(family, measured), measured)

    def test_independent_structure_reference_selects_standardized_winner(self) -> None:
        rule = MANIFEST["game_rule_maps"][0]
        draws = [
            {"issue_id": str(2025001 + index), "front_numbers": [16, 17, 18, 19, 23], "back_numbers": [7, 8]}
            for index in range(2)
        ]
        formal = calculate_statistics(draws, rule)["set_structure"]
        reference = independent_reference_statistics(draws, rule)
        self.assertEqual(formal["selected_component"], "back")
        self.assertGreater(reference["front_structure_effect"], reference["back_structure_effect"])
        self.assertEqual(reference["set_structure"], formal["effect"])

    def test_independent_seed_grid_comparison_covers_all_points_and_detects_disjoint_band(self) -> None:
        source_grid = [{"game": f"g{index:03d}", "bias_family": "family", "effect": 0.1, "sample_size": 200, "power": 0.8, "simultaneous_95_lower": 0.77, "simultaneous_95_upper": 0.83} for index in range(240)]
        replay_grid = [dict(row) for row in source_grid]
        prereg = {"replay_grid_tolerance": {"expected_point_count": 240}}
        compatible, rows = _compare_grid_estimates({"grid": source_grid}, {"grid": replay_grid}, prereg)
        self.assertTrue(compatible)
        self.assertEqual(len(rows), 240)
        replay_grid[0].update({"power": 0.9, "simultaneous_95_lower": 0.87, "simultaneous_95_upper": 0.93})
        compatible, rows = _compare_grid_estimates({"grid": source_grid}, {"grid": replay_grid}, prereg)
        self.assertFalse(compatible)
        self.assertEqual(sum(row["compatible"] for row in rows), 239)

    def test_normalized_power_artifact_ignores_checkpoint_paths_but_binds_aggregates(self) -> None:
        base = {key: [] for key in ("calibration", "power_method", "grid", "delta_star", "required_n", "key_power_rows", "metrics")}
        base.update({"calibration": {}, "power_method": {}, "metrics": {}, "checkpoint_resume": {"ledgers": [{"scenario": "s", "aggregate_sha256": "a" * 64, "path": "one"}]}})
        moved = json.loads(json.dumps(base))
        moved["checkpoint_resume"]["ledgers"][0]["path"] = "two"
        self.assertEqual(normalized_power_artifact(base), normalized_power_artifact(moved))
        moved["checkpoint_resume"]["ledgers"][0]["aggregate_sha256"] = "b" * 64
        self.assertNotEqual(normalized_power_artifact(base), normalized_power_artifact(moved))


if __name__ == "__main__":
    unittest.main()
