from __future__ import annotations

import copy
import unittest
from decimal import Decimal
from pathlib import Path

from lottery_system.phase4.commands.forecast import _snapshot_from_ticks
from lottery_system.phase4.forecast import (
    ForecastViolation,
    generate_forecast,
    prepare_label_free_snapshot,
    validate_forecast_diagnostic,
)
from lottery_system.phase4.rules import game_rule
from lottery_system.phase4.serialization import load_json
from lottery_system.phase4.time_gate import MixedTimeClass, TimeContractViolation


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "artifacts/phase-4-prep/p4-prep-phase4-mvp-20260813-r01-i01/work-items/T10/attempts/T10-I01/known-answers"


class ForecastDiagnosticTests(unittest.TestCase):
    def test_m0_two_stage_identity_and_full_space_diagnostic(self) -> None:
        for game in ("ssq", "dlt"):
            rule = game_rule(game)
            snapshot = _snapshot_from_ticks({"rule_id": rule.rule_id}, game, "M0", [0] * rule.front_n, [0] * rule.back_n)
            first = generate_forecast(snapshot)
            second = generate_forecast(snapshot)
            self.assertEqual(first, second)
            self.assertEqual(len(first["forecast"]["tickets"]), 1000)
            self.assertEqual(len({row["tie_group_id"] for row in first["forecast"]["tickets"]}), 1)
            self.assertEqual(first["diagnostic"]["histogram_count"], 1)
            self.assertIsNone(first["diagnostic"]["result_revision_id"])
            changed = copy.deepcopy(snapshot)
            changed["config_id"] = "different-config-v1"
            from lottery_system.phase4.identity import content_id

            changed["feature_snapshot_id"] = content_id("feature-snapshot", changed, excluded_fields=("feature_snapshot_id",))
            self.assertNotEqual(generate_forecast(changed)["forecast"]["forecast_id"], first["forecast"]["forecast_id"])

    def test_full_rule_diagnostic_matches_accepted_oracle_coverage(self) -> None:
        known = load_json(ORACLE / "full-rule-oracle.json", reject_floats=True)
        spec = load_json(ROOT / "qualification-design/full-rule-spec-candidate.json", reject_floats=True)
        for expected in known["results"]:
            snapshot = _snapshot_from_ticks(expected, expected["game"], spec["spec_id"], expected["front_ticks"], expected["back_ticks"])
            diagnostic = generate_forecast(snapshot)["diagnostic"]
            for cell in expected["cells"]:
                difference = abs(Decimal(diagnostic["coverage_at_k"][str(cell["K"])]) - Decimal(cell["candidate_coverage"]))
                self.assertLessEqual(difference, Decimal(cell["absolute_error_bound"]))

    def test_diagnostic_result_binding_and_identity_mutations_fail(self) -> None:
        rule = game_rule("ssq")
        generated = generate_forecast(_snapshot_from_ticks({"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n))
        diagnostic = generated["diagnostic"]
        for field, value in (
            ("result_revision_id", "result-v1:deadbeef"),
            ("forecast_id", "wrong-forecast"),
            ("metric_contract_id", "wrong-metric"),
            ("top_k_nested", False),
        ):
            mutated = dict(diagnostic)
            mutated[field] = value
            with self.assertRaises(ForecastViolation):
                validate_forecast_diagnostic(
                    mutated,
                    forecast_id=generated["forecast"]["forecast_id"],
                    metric_contract_id="phase4-metric-v1",
                )

    def test_three_time_classes_are_not_coercible(self) -> None:
        history = [{
            "time_class": "retrospective_sequence_safe", "source_issue": "2026001",
            "numbers": {"front": [1, 2, 3, 4, 5, 6], "back": [1]},
        }]
        external = [{
            "time_class": "external_point_in_time", "feature_id": "context-v1", "value": "a",
            "available_at_utc": "2026-01-01T00:00:00Z", "availability_evidence_sha256": "a" * 64,
            "availability_evidence_kind": "publisher_timestamp",
        }]
        snapshot = prepare_label_free_snapshot(
            game="ssq", target_issue="2026002", model_id="M0", model_release_id="M0-v1",
            config_id="M0-config-v1", data_release_id="data-v1", training_cutoff="2026001",
            calendar_release_id="calendar-v1", schedule_release_id="schedule-v1", seed_id="seed-v1",
            metric_contract_id="phase4-metric-v1", historical_features=history, external_features=external,
            proposed_prediction_locked_at="2026-01-02T09:00:00Z",
            model_config={"shrinkage": 1, "training_window": "expanding", "recency_half_life": "none", "front_offsets": {}, "back_offsets": {}},
        )
        self.assertEqual(snapshot["external_features"][0]["time_class"], "external_point_in_time")
        mixed = copy.deepcopy(history)
        mixed[0]["time_class"] = "official_result_label"
        with self.assertRaises(MixedTimeClass):
            prepare_label_free_snapshot(
                game="ssq", target_issue="2026002", model_id="M0", model_release_id="M0-v1",
                config_id="M0-config-v1", data_release_id="data-v1", training_cutoff="2026001",
                calendar_release_id="calendar-v1", schedule_release_id="schedule-v1", seed_id="seed-v1",
                metric_contract_id="phase4-metric-v1", historical_features=mixed, external_features=[],
                proposed_prediction_locked_at="2026-01-02T09:00:00Z",
                model_config={"shrinkage": 1, "training_window": "expanding", "recency_half_life": "none", "front_offsets": {}, "back_offsets": {}},
            )
        late_external = copy.deepcopy(external)
        late_external[0]["available_at_utc"] = "2026-01-02T09:00:00Z"
        with self.assertRaises(TimeContractViolation):
            prepare_label_free_snapshot(
                game="ssq", target_issue="2026002", model_id="M0", model_release_id="M0-v1",
                config_id="M0-config-v1", data_release_id="data-v1", training_cutoff="2026001",
                calendar_release_id="calendar-v1", schedule_release_id="schedule-v1", seed_id="seed-v1",
                metric_contract_id="phase4-metric-v1", historical_features=history, external_features=late_external,
                proposed_prediction_locked_at="2026-01-02T09:00:00Z",
                model_config={"shrinkage": 1, "training_window": "expanding", "recency_half_life": "none", "front_offsets": {}, "back_offsets": {}},
            )


if __name__ == "__main__":
    unittest.main()
