from __future__ import annotations

import tempfile
import unittest
import copy
from pathlib import Path

from lottery_system.phase4.commands.forecast import _snapshot_from_ticks
from lottery_system.phase4.forecast import generate_forecast
from lottery_system.phase4.identity import content_id
from lottery_system.phase4.lock import ForecastLockViolation, load_locked_forecast, lock_forecast
from lottery_system.phase4.rules import game_rule
from lottery_system.phase4.serialization import canonical_json_bytes
from lottery_system.phase4.storage import resolve_inside, write_once_json
from lottery_system.phase4.time_gate import TimeContractViolation


PROVENANCE = {
    "producer_actor_id": "p4-implementation-author-i01", "task_id": "T05",
    "session_id": "/root/implementation_author", "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
    "path": "tests/phase4/test_forecast_lock.py", "role": "implementation_author",
}


def build_runtime(base: Path) -> tuple[Path, dict]:
    rule = game_rule("ssq")
    generated = generate_forecast(_snapshot_from_ticks({"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n))
    forecast = generated["forecast"]
    write_once_json(base / f"data-releases/{forecast['data_release_id']}/data-release.json", {"data_release_id": forecast["data_release_id"]})
    write_once_json(base / f"calendar-releases/{forecast['calendar_release_id']}/calendar.json", {"calendar_release_id": forecast["calendar_release_id"]})
    return base, generated


class ForecastLockTests(unittest.TestCase):
    def test_atomic_lock_idempotency_and_complete_hash_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime, generated = build_runtime(Path(temporary))
            first = lock_forecast(
                runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
            )
            self.assertFalse(first["idempotent_resume"])
            second = lock_forecast(
                runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
            )
            self.assertTrue(second["idempotent_resume"])
            locked = load_locked_forecast(runtime, generated["forecast"]["forecast_id"])
            self.assertEqual(locked["diagnostic"]["forecast_id"], generated["forecast"]["forecast_id"])
            self.assertIsNone(locked["diagnostic"]["result_revision_id"])

    def test_deadline_and_prepublication_fault_never_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime, generated = build_runtime(Path(temporary))
            with self.assertRaises(TimeContractViolation):
                lock_forecast(
                    runtime, generated, prediction_locked_at="2026-01-02T10:00:00Z",
                    hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
                )
            destination = resolve_inside(runtime, f"forecasts/{generated['forecast']['forecast_id']}")
            self.assertFalse(destination.exists())

            def fail(stage: str) -> None:
                if stage == "before_publish":
                    raise OSError("injected prepublication failure")

            with self.assertRaises(OSError):
                lock_forecast(
                    runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                    hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE, fault=fail,
                )
            self.assertFalse(destination.exists())

        with tempfile.TemporaryDirectory() as temporary:
            runtime, generated = build_runtime(Path(temporary))
            destination = resolve_inside(runtime, f"forecasts/{generated['forecast']['forecast_id']}")
            def fail_after_publish(stage: str) -> None:
                if stage == "after_publish":
                    raise OSError("injected postpublication failure")
            with self.assertRaises(OSError):
                lock_forecast(
                    runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                    hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
                    fault=fail_after_publish,
                )
            self.assertTrue(destination.is_dir())
            recovered = lock_forecast(
                runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
            )
            self.assertTrue(recovered["idempotent_resume"])

    def test_post_lock_body_and_diagnostic_tamper_fail_closed(self) -> None:
        for name in ("forecast.json", "diagnostic.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                runtime, generated = build_runtime(Path(temporary))
                lock_forecast(
                    runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                    hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
                )
                path = resolve_inside(runtime, f"forecasts/{generated['forecast']['forecast_id']}/{name}")
                path.write_bytes(canonical_json_bytes({"tampered": True}))
                with self.assertRaises(ForecastLockViolation):
                    load_locked_forecast(runtime, generated["forecast"]["forecast_id"])

    def test_actual_lock_time_is_content_bound_and_rechecks_external_pit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            rule = game_rule("ssq")
            base = _snapshot_from_ticks({"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n)
            snapshot = copy.deepcopy(base)
            snapshot["prediction_locked_at"] = "2026-01-02T10:00:00Z"
            snapshot["external_features"] = [{
                "time_class": "external_point_in_time", "feature_id": "late-context-v1", "value": "x",
                "available_at_utc": "2026-01-02T09:30:00Z", "availability_evidence_sha256": "b" * 64,
                "availability_evidence_kind": "signed_fixture_timestamp",
            }]
            snapshot["feature_snapshot_id"] = content_id("feature-snapshot", snapshot, excluded_fields=("feature_snapshot_id",))
            generated = generate_forecast(snapshot)
            forecast = generated["forecast"]
            write_once_json(runtime / f"data-releases/{forecast['data_release_id']}/data-release.json", {"data_release_id": forecast["data_release_id"]})
            write_once_json(runtime / f"calendar-releases/{forecast['calendar_release_id']}/calendar.json", {"calendar_release_id": forecast["calendar_release_id"]})
            with self.assertRaises(ForecastLockViolation):
                lock_forecast(
                    runtime, generated, prediction_locked_at="2026-01-02T09:00:00Z",
                    hard_deadline_at="2026-01-02T11:00:00Z", producer_provenance=PROVENANCE,
                )
            self.assertFalse((runtime / "forecasts" / forecast["forecast_id"]).exists())
            accepted = lock_forecast(
                runtime, generated, prediction_locked_at="2026-01-02T10:00:00Z",
                hard_deadline_at="2026-01-02T11:00:00Z", producer_provenance=PROVENANCE,
            )
            self.assertEqual(accepted["lock_receipt"]["prediction_locked_at"], snapshot["prediction_locked_at"])


if __name__ == "__main__":
    unittest.main()
