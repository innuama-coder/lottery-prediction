from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from lottery_system.phase4.alerts import audit_user_scheduler
from lottery_system.phase4.identity import content_id
from lottery_system.phase4.ledger import AppendOnlyLedger
from lottery_system.phase4.orchestrator import OrchestrationViolation, ProcessInterruption, execute_plan
from lottery_system.phase4.recovery import CheckpointViolation
from lottery_system.phase4.scheduler import ScheduleViolation, build_schedule_release, plan_key, tick_schedule, validate_schedule
from lottery_system.phase4.serialization import canonical_json_bytes, load_json
from lottery_system.phase4.storage import AdvisoryFileLock, LockUnavailable, resolve_inside


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/phase4/fixtures/schedule/dual-game.json"
PROVENANCE = {"producer_actor_id":"p4-implementation-author-i01","task_id":"T08","session_id":"/root/implementation_author","source_commit":"f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b","path":"tests","role":"implementation_author"}


def fixture() -> dict:
    return load_json(FIXTURE, reject_floats=True)


def one_plan(action: str = "prepare") -> tuple[dict, list[str], str]:
    schedule = fixture()["schedule"]
    plan = next(row for row in schedule["plans"] if row["game"] == "ssq" and row["action"] == action)
    return plan, plan_key(plan), schedule["schedule_release_id"]


class SchedulerRecoveryTests(unittest.TestCase):
    def runtime(self, temporary: tempfile.TemporaryDirectory[str]) -> Path:
        return Path(temporary.name)

    def test_dual_game_exact_tick_and_idempotent_restart(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            first = tick_schedule(ROOT, runtime, fixture(), clock="2026-01-03T09:00:00Z", provenance=PROVENANCE)
            self.assertEqual((first["due_count"], first["early_count"]), (4, 4))
            self.assertEqual(first["terminal_counts"], {"late_completed":2,"succeeded":2})
            second = tick_schedule(ROOT, runtime, fixture(), clock="2026-01-03T09:00:00Z", provenance=PROVENANCE)
            self.assertEqual(second["terminal_counts"], {"skipped_idempotent":4})
            self.assertEqual(AppendOnlyLedger(runtime, "schedule-runs").validate()["event_count"], 4)
            Draft202012Validator(json.loads((ROOT / "schemas/phase4/schedule.schema.json").read_text())).validate(fixture()["schedule"])
            checkpoint_schema = json.loads((ROOT / "schemas/phase4/checkpoint.schema.json").read_text())
            for path in (runtime / "scheduler/runs").glob("*/checkpoints/*.json"):
                Draft202012Validator(checkpoint_schema).validate(load_json(path, reject_floats=True))

    def test_early_late_and_missed_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            early = tick_schedule(ROOT, Path(raw), fixture(), clock="2026-01-01T00:00:00Z", provenance=PROVENANCE)
            self.assertEqual((early["due_count"], early["early_count"]), (0, 8))
        with tempfile.TemporaryDirectory() as raw:
            late = tick_schedule(ROOT, Path(raw), fixture(), clock="2026-01-02T05:00:00Z", provenance=PROVENANCE)
            self.assertEqual(late["terminal_counts"], {"late_completed":2})
        with tempfile.TemporaryDirectory() as raw:
            missed = tick_schedule(ROOT, Path(raw), fixture(), clock="2026-01-03T10:00:01Z", provenance=PROVENANCE)
            self.assertEqual(missed["terminal_counts"], {"late_completed":2,"missed_deadline":2})
            self.assertEqual(AppendOnlyLedger(Path(raw), "alerts").validate()["event_count"], 4)
            alert_schema = json.loads((ROOT / "schemas/phase4/alert.schema.json").read_text())
            for path in (Path(raw) / "alerts").glob("*/alert.json"):
                Draft202012Validator(alert_schema).validate(load_json(path, reject_floats=True))

    def test_compensation_and_network_game_isolation(self) -> None:
        value = fixture()
        value["behaviors"] = [{"match":{"game":"ssq","target_issue":"2026001","action":"result_probe_primary"},"behavior":{"outcome":"network_failure"}}]
        with tempfile.TemporaryDirectory() as raw:
            result = tick_schedule(ROOT, Path(raw), value, clock="2026-01-04T00:30:00Z", provenance=PROVENANCE)
            self.assertEqual(result["due_count"], 8)
            self.assertEqual(result["terminal_counts"].get("blocked"), 1)
            self.assertEqual(result["terminal_counts"].get("late_completed"), 3)
            self.assertEqual(result["terminal_counts"].get("missed_deadline"), 2)
            self.assertEqual(result["terminal_counts"].get("succeeded"), 2)

    def test_nonblocking_per_plan_lease(self) -> None:
        plan, key, schedule_id = one_plan()
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            plan_id = content_id("plan", {"plan_key":key})
            held = AdvisoryFileLock(resolve_inside(runtime, f"scheduler/leases/{plan_id}.lock")).acquire()
            try:
                with self.assertRaises(LockUnavailable):
                    execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="1"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)
            finally:
                held.release()
            self.assertEqual(AppendOnlyLedger(runtime, "alerts").validate()["event_count"], 1)

    def test_interruption_each_stage_recovers_byte_identically(self) -> None:
        plan, key, _schedule_id = one_plan()
        for stage in ("leased","effects_committed","correction_score_bound","correction_research_bound","completed"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as clean_raw, tempfile.TemporaryDirectory() as resumed_raw:
                clean = Path(clean_raw); resumed = Path(resumed_raw)
                expected = execute_plan(ROOT, clean, plan, plan_key=key, schedule_sha256="2"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)
                with self.assertRaises(ProcessInterruption):
                    execute_plan(ROOT, resumed, plan, plan_key=key, schedule_sha256="2"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False, behavior={"stop_after_stage":stage})
                actual = execute_plan(ROOT, resumed, plan, plan_key=key, schedule_sha256="2"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)
                self.assertEqual(canonical_json_bytes(expected), canonical_json_bytes(actual))
                self.assertEqual((clean / "scheduler").read_bytes() if False else load_json(next((clean / "scheduler/runs").glob("*/effect.json"))), load_json(next((resumed / "scheduler/runs").glob("*/effect.json"))))
                self.assertEqual(AppendOnlyLedger(resumed, "schedule-runs").validate()["event_count"], 1)

    def test_ledger_committed_terminal_recovers_missing_projection_without_duplicate(self) -> None:
        plan, key, _ = one_plan()
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            expected = execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="6"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)
            terminal_path = resolve_inside(runtime, f"scheduler/terminals/{expected['plan_id']}.json")
            terminal_path.unlink()
            recovered = execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="6"*64, clock="2026-01-02T04:05:00Z", provenance=PROVENANCE, late=False)
            self.assertEqual(recovered["run_id"], expected["run_id"])
            self.assertTrue(terminal_path.is_file())
            self.assertEqual(AppendOnlyLedger(runtime, "schedule-runs").validate()["event_count"], 1)

    def test_terminal_or_effect_tamper_fails_closed(self) -> None:
        plan, key, _ = one_plan()
        for target in ("terminal","effect"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as raw:
                runtime = Path(raw)
                result = execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="7"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)
                path = resolve_inside(runtime, f"scheduler/terminals/{result['plan_id']}.json") if target == "terminal" else next((runtime / "scheduler/runs").glob("*/effect.json"))
                value = load_json(path, reject_floats=True)
                value["plan_id"] = "plan-v1:" + "0"*64
                path.write_bytes(canonical_json_bytes(value))
                with self.assertRaises((OrchestrationViolation, ValueError)):
                    execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="7"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)

    def test_wrong_checkpoint_and_noncontiguous_chain_reject(self) -> None:
        plan, key, _ = one_plan()
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            with self.assertRaises(ProcessInterruption):
                execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="3"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False, behavior={"stop_after_stage":"effects_committed"})
            checkpoint = next((runtime / "scheduler/runs").glob("*/checkpoints/effects_committed.json"))
            checkpoint.write_text(checkpoint.read_text().replace('"stage":"effects_committed"','"stage":"completed"'), encoding="utf-8")
            with self.assertRaises((CheckpointViolation, ValueError)):
                execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="3"*64, clock="2026-01-02T04:00:00Z", provenance=PROVENANCE, late=False)

    def test_partial_correction_and_unaccepted_dependencies_reject(self) -> None:
        plan, key, _ = one_plan("prepare")
        plan = dict(plan); plan["action"] = "unlock_score_research"
        key = plan_key(plan)
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(OrchestrationViolation):
                execute_plan(ROOT, Path(raw), plan, plan_key=key, schedule_sha256="4"*64, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE, late=True, behavior={"outcome":"partial_correction"})
        bad = {"correction_key":["ssq","2026001","revision-v1","p4-correction-v1"],"t06_receipt_path":"missing","t06_receipt_sha256":"0"*64,"t06_verdict_path":"missing","t06_verdict_sha256":"0"*64,"t07_receipt_path":"missing","t07_receipt_sha256":"0"*64,"t07_verdict_path":"missing","t07_verdict_sha256":"0"*64}
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(OrchestrationViolation):
                execute_plan(ROOT, Path(raw), plan, plan_key=key, schedule_sha256="4"*64, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE, late=True, behavior={"correction":bad})

    def test_accepted_correction_closes_once_after_resume(self) -> None:
        plan, _old_key, _ = one_plan("prepare")
        plan = dict(plan); plan["action"] = "unlock_score_research"
        key = plan_key(plan)
        correction = {
            "correction_key":["ssq","2026001","revision-v1","p4-correction-v1"],
            "t06_receipt_path":"artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T06-I06/receipt.json","t06_receipt_sha256":"71fed261d30649d7ef9b60cd9e7a2a5e8301bc34798d6696c54503b5d25d3057",
            "t06_verdict_path":"artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T06-I06/independent-validation-I02.json","t06_verdict_sha256":"86eae7b34eda7d0a80e0856a38eb2a6d5c2b45a254405bf26cbfcf36cb86bfd8",
            "t07_receipt_path":"artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T07-I04/receipt.json","t07_receipt_sha256":"edd534b240c8c3719a8e55c77045389a9c7478c3924a42ea83f56dcb59f67344",
            "t07_verdict_path":"artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T07-I04/independent-validation-I05.json","t07_verdict_sha256":"b8a3857ef2466b2f3ecb742db4d25ddbfdacbbceb16a7def24abde3289286afd",
        }
        for stage in ("effects_committed","correction_score_bound","correction_research_bound"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as raw:
                runtime = Path(raw)
                with self.assertRaises(ProcessInterruption):
                    execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="5"*64, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE, late=True, behavior={"correction":correction,"stop_after_stage":stage})
                result = execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="5"*64, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE, late=True, behavior={"correction":correction})
                self.assertIsNotNone(result["correction_closure_id"])
                effect = load_json(next((runtime / "scheduler/runs").glob("*/effect.json")), reject_floats=True)
                self.assertIsNotNone(effect["score_effect_id"])
                self.assertIsNotNone(effect["alpha_spend_id"])
                self.assertEqual(AppendOnlyLedger(runtime, "correction-closures").validate()["event_count"], 1)
                execute_plan(ROOT, runtime, plan, plan_key=key, schedule_sha256="5"*64, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE, late=True, behavior={"correction":correction})
                self.assertEqual(AppendOnlyLedger(runtime, "correction-closures").validate()["event_count"], 1)

    def test_issue_rollback_rejects(self) -> None:
        value = fixture()
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            tick_schedule(ROOT, runtime, value, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE)
            plan_body = {"game":"dlt","target_issue":"2025001","action":"prepare","planned_at_local":"2026-01-01T12:00:00+08:00","timezone":"Asia/Shanghai","planned_at_utc":"2026-01-01T04:00:00Z"}
            identity_body = {"schema_version":"1.0.0","artifact_type":"phase4_schedule_release","calendar_release_id":"calendar-rollback-v1","plans":[plan_body]}
            schedule_id = content_id("schedule-release", identity_body)
            changed = {"schema_version":"1.0.0","artifact_type":"phase4_schedule_tick_fixture","schedule":{**{key:value for key,value in identity_body.items() if key != "plans"},"schedule_release_id":schedule_id,"plans":[{**plan_body,"schedule_release_id":schedule_id}]},"behaviors":[]}
            with self.assertRaises(ScheduleViolation):
                tick_schedule(ROOT, runtime, changed, clock="2026-01-03T09:00:00Z", provenance=PROVENANCE)

    def test_schedule_identity_order_time_and_duplicate_negatives(self) -> None:
        schedule = fixture()["schedule"]
        self.assertEqual(len(validate_schedule(schedule)), 8)
        for mutation in ("order","utc","duplicate","id"):
            bad = copy.deepcopy(schedule)
            if mutation == "order": bad["plans"][0], bad["plans"][1] = bad["plans"][1], bad["plans"][0]
            if mutation == "utc": bad["plans"][0]["planned_at_utc"] = "2026-01-02T05:00:00Z"
            if mutation == "duplicate": bad["plans"][1] = copy.deepcopy(bad["plans"][0])
            if mutation == "id": bad["schedule_release_id"] = "schedule-wrong"
            with self.subTest(mutation=mutation), self.assertRaises(ScheduleViolation):
                validate_schedule(bad)

    @unittest.skipIf(os.name == "nt", "imported Linux calendar tzdata identity is platform-bound")
    def test_build_schedule_from_explicit_calendar_and_contract(self) -> None:
        calendar = load_json(resolve_inside(ROOT / "artifacts/phase-4-runtime/p4-runtime-t03-author-i01", "calendar-releases/calendar-release-v1:adceed49ec0df3767c4517b6bcedcd62a0f026e6285af09e6076945ea9a67c48/calendar.json"), reject_floats=True)
        expected = "schedule-release-v1:f9cdca21865612d130ff9c8e1d1fedfe22117d1f68c6f22208d2175066445492"
        release = build_schedule_release(calendar, schedule_release_id=expected, contract_id="phase4-schedule-v1")
        self.assertEqual((release["schedule_release_id"], len(release["plans"])), (expected, 16))
        with self.assertRaises(ScheduleViolation):
            build_schedule_release(calendar, schedule_release_id=expected, contract_id="wrong-contract")

    def test_systemd_audit_parser_preserves_linger_no(self) -> None:
        class Completed:
            def __init__(self, code: int, out: str): self.returncode=code; self.stdout=out; self.stderr=""
        responses = iter((Completed(0,"running\n"),Completed(0,"State=active\nLinger=no\n"),Completed(0,"Normalized form...\n")))
        result = audit_user_scheduler(ROOT, ROOT / "artifacts/phase-4-runtime/audit-target", runner=lambda *args, **kwargs: next(responses))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["observed"], {"linger":"no","user_state":"active"})
        self.assertTrue(result["capability"]["no_sudo"])

    def test_systemd_audit_fails_closed_on_user_manager_or_timer_parse(self) -> None:
        class Completed:
            def __init__(self, code: int, out: str): self.returncode=code; self.stdout=out; self.stderr="unavailable" if code else ""
        for failed_index in (0, 2):
            responses = iter(Completed(1 if index == failed_index else 0, "State=active\nLinger=no\n" if index == 1 else "") for index in range(3))
            result = audit_user_scheduler(ROOT, ROOT / "artifacts/phase-4-runtime/audit-target", runner=lambda *args, **kwargs: next(responses))
            self.assertEqual((result["status"], result["terminal"]), ("HOLD","HOLD_SCHEDULER_AUDIT"))


if __name__ == "__main__":
    unittest.main()
