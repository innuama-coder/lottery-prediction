from __future__ import annotations

import copy
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
ARTIFACTS = REPO / "artifacts" / "phase-0"
sys.path.insert(0, str(SCRIPTS))

from p0_04_http import AcquisitionError, ClockCheck, FetchResult  # noqa: E402
from p0_04_pipeline import verify_captures  # noqa: E402
from p0_06_runner import RuntimeHold, append_soak_entry, build_runtime_plan, execute_one, write_runtime_plan  # noqa: E402
from phase0lib import ValidationError, canonical_json_bytes, load_json, load_jsonl, sha256_file, validate_jsonl_file, validate_schema_instance  # noqa: E402
from verify_phase0 import VERIFIER_FILES, verify_p0_06_install_audit_semantics, verify_p0_06_semantics, verify_provenance  # noqa: E402


NOW = datetime(2026, 8, 3, 14, 10, tzinfo=timezone.utc)


def utc_sequence(*values: datetime):
    iterator = iter(values)
    return lambda: next(iterator)


def passing_clock(_: int = 5) -> ClockCheck:
    return ClockCheck("2026-08-03T14:09:59Z", "fixture", 1, 5, True, "0" * 64)


def failing_clock(_: int = 5) -> ClockCheck:
    return ClockCheck("2026-08-03T14:09:59Z", "fixture", 9, 5, False, "0" * 64)


class NeverCollector:
    def fetch(self, *_args, **_kwargs):
        raise AssertionError("collector must not be called")


class FailingCollector:
    def fetch(self, *_args, **_kwargs):
        raise AcquisitionError("fixture connection failure")


class SuccessfulCollector:
    def fetch(self, url, *, clock_check):
        del clock_check
        return FetchResult(url, ({"url": url, "status": 200},), url, "2026-08-03T14:10:00Z", b"fixture", ({"name": "Content-Type", "value": "text/html; charset=UTF-8"},), ())


class FutureFixtureCollector:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def fetch(self, url, *, clock_check):
        del clock_check
        return FetchResult(url, ({"url": url, "status": 200},), url, "2026-08-03T14:10:00Z", self.raw, ({"name": "Content-Type", "value": "text/html; charset=UTF-8"},), ())


class P006RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.observation = load_json(ARTIFACTS / "observation-plan.json")
        cls.catalog = load_json(ARTIFACTS / "source-catalog.json")
        cls.plan = build_runtime_plan(cls.observation, cls.catalog)

    def temp_artifacts(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "schemas").mkdir()
        for name in ("p0-06-runtime-plan.schema.json", "observation-plan.schema.json", "source-catalog.schema.json"):
            shutil.copy2(ARTIFACTS / "schemas" / name, root / "schemas" / name)
        (root / "observation-plan.json").write_bytes(canonical_json_bytes(self.observation) + b"\n")
        (root / "source-catalog.json").write_bytes(canonical_json_bytes(self.catalog) + b"\n")
        digest = write_runtime_plan(root, self.plan)
        (root / "evidence-manifest.jsonl").write_bytes(b"")
        return temporary, root, digest

    def valid_verifier_chain(self):
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name); artifacts = repo / "artifacts" / "phase-0"
        raw_path = artifacts / "raw" / "p0-06-dlt-2026087.html"; raw_path.parent.mkdir(parents=True); raw_path.write_bytes(b"raw-evidence")
        normalized = {"fixture": "normalized"}
        normalized_path = artifacts / "normalized" / "p0-06-dlt-2026087.json"; normalized_path.parent.mkdir(); normalized_path.write_bytes(canonical_json_bytes(normalized) + b"\n")
        evidence_id = "p0-06-dlt-2026087"
        evidence = {"evidence_id": evidence_id, "game": "dlt", "issue_id": "2026087", "status": "unverified", "field_parsing_succeeded": True, "stored_payload_path": "artifacts/phase-0/raw/p0-06-dlt-2026087.html", "stored_payload_sha256": hashlib.sha256(b"raw-evidence").hexdigest(), "normalized_record_ref": "artifacts/phase-0/normalized/p0-06-dlt-2026087.json", "normalized_record_sha256": hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()}
        (artifacts / "evidence-manifest.jsonl").write_bytes(canonical_json_bytes(evidence) + b"\n")
        entry = {"schema_version": "1.1.0", "artifact_type": "soak_log_entry", "request_id": "REQ-DLT-2026-08-03-CORROBORATOR", "game": "dlt", "planned_at_utc": "2026-08-03T14:03:00Z", "started_at_utc": "2026-08-03T14:10:00Z", "completed_at_utc": "2026-08-03T14:10:07Z", "source_slot": "official_corroborator_if_available", "source_id": "dlt-gd-official-issue-pages", "scheduled_issue_id": "2026087", "request_schedule_sha256": self.plan["request_schedule_sha256"], "execution_disposition": "network_attempted", "attempts": 1, "network_used": True, "clock_check_at_utc": "2026-08-03T14:09:59Z", "clock_offset_seconds": 1, "result": "unverified", "classification_reason": "captured_unverified_pending_independent_corroboration", "failure_injection": "none", "evidence_ref": f"artifacts/phase-0/evidence-manifest.jsonl#{evidence_id}", "raw_payload_ref": evidence["stored_payload_path"]}
        return temporary, repo, artifacts, entry, evidence

    def raw_only_provenance_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name); artifacts = repo / "artifacts" / "phase-0"
        environment = artifacts / "environment-lock.json"; environment.parent.mkdir(parents=True)
        shutil.copy2(ARTIFACTS / "environment-lock.json", environment)
        entry = copy.deepcopy(load_jsonl(ARTIFACTS / "evidence-manifest.jsonl")[0])
        source_raw = REPO / entry["stored_payload_path"]
        destination_raw = repo / entry["stored_payload_path"]; destination_raw.parent.mkdir(parents=True); shutil.copy2(source_raw, destination_raw)
        entry["status"] = "invalid"; entry["normalized_record_sha256"] = "0" * 64
        entry["normalized_record_ref"] = "artifacts/phase-0/normalized/p0-06-raw-only.json"
        return temporary, repo, environment, entry, repo / entry["normalized_record_ref"]

    def valid_install_audit(self):
        evidence = ARTIFACTS / "evidence-manifest.jsonl"
        return {
            "schema_version": "1.0.0",
            "artifact_type": "p0_06_scheduler_install_audit",
            "contract_version": "1.3",
            "recorded_at_utc": "2026-08-01T07:00:00Z",
            "task_name": "AutoresearchLotte-P0-06",
            "scope": "current_user",
            "runtime_plan_sha256": sha256_file(ARTIFACTS / "p0-06-runtime-plan.json"),
            "installed": True,
            "matches_frozen_plan": True,
            "checks": {
                "ActionCountOne": True,
                "ExecuteExact": True,
                "ArgumentsExact": True,
                "WorkingDirectoryExact": True,
                "TriggerCount24": True,
                "TriggerTimesExact": True,
                "StartWhenAvailable": True,
                "ExecutionTimeLimit15Minutes": True,
                "MultipleInstancesIgnoreNew": True,
                "PrincipalCurrentSid": True,
                "PrincipalInteractive": True,
                "PrincipalLimited": True,
            },
            "trigger_count": 24,
            "next_run_local": "2026-08-02T22:00:00+08:00",
            "last_run_state": "never_run",
            "last_task_result": 267011,
            "missed_runs": 0,
            "soak_log_bytes": 0,
            "evidence_manifest_sha256": sha256_file(evidence),
            "evidence_manifest_last_write_utc": datetime.fromtimestamp(evidence.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
            "verify_command": "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/install_p0_06_scheduled_task.ps1 -Action Verify -AuditPath artifacts/phase-0/p0-06-scheduler-install-audit.json",
            "exit_code": 0,
            "os_state_claim_scope": "point_in_time_snapshot_not_continuous_os_proof",
        }

    def complete_soak_entries(self):
        entries = []
        for request in self.plan["requests"]:
            planned = datetime.fromisoformat(request["planned_at_utc"].replace("Z", "+00:00"))
            started = planned.replace(microsecond=0)
            completed = started.replace(microsecond=0)
            policy = request["execution_policy"]
            network = policy == "network_attempted"
            reason = {
                "policy_blocked": "source_approved_use_blocked",
                "compliance_hold": "source_compliance_hold_no_collection",
                "network_attempted": "network_or_capture_failure:AcquisitionError",
            }[policy]
            entries.append({
                "schema_version": "1.1.0", "artifact_type": "soak_log_entry", "request_id": request["request_id"], "game": request["game"],
                "planned_at_utc": request["planned_at_utc"], "started_at_utc": started.isoformat().replace("+00:00", "Z"), "completed_at_utc": completed.isoformat().replace("+00:00", "Z"),
                "source_slot": request["source_slot"], "source_id": request["source_id"], "scheduled_issue_id": request["scheduled_issue_id"], "request_schedule_sha256": self.plan["request_schedule_sha256"],
                "execution_disposition": policy, "attempts": 1 if network else 0, "network_used": network,
                "clock_check_at_utc": started.isoformat().replace("+00:00", "Z") if network else None, "clock_offset_seconds": 0 if network else None,
                "result": "invalid" if network else policy, "classification_reason": reason, "failure_injection": "none", "evidence_ref": None, "raw_payload_ref": None,
            })
        return entries

    def empty_verification_repo(self):
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name)
        artifacts = repo / "artifacts" / "phase-0"
        artifacts.mkdir(parents=True)
        (artifacts / "evidence-manifest.jsonl").write_bytes(b"")
        return temporary, repo

    def test_install_audit_schema_and_semantic_baseline(self) -> None:
        audit = self.valid_install_audit()
        validate_schema_instance(audit, load_json(ARTIFACTS / "schemas" / "p0-06-scheduler-install-audit.schema.json"))
        verify_p0_06_install_audit_semantics(audit, self.plan, ARTIFACTS)

    def test_install_audit_semantic_failure_injections(self) -> None:
        cases = ("plan_hash", "check", "trigger_count", "next_run", "run_state", "last_result", "missed_runs", "soak_bytes", "evidence_hash", "evidence_mtime", "verify_command", "exit_code")
        for case in cases:
            with self.subTest(case=case):
                audit = self.valid_install_audit()
                if case == "plan_hash": audit["runtime_plan_sha256"] = "0" * 64
                elif case == "check": audit["checks"]["ExecuteExact"] = False
                elif case == "trigger_count": audit["trigger_count"] = 23
                elif case == "next_run": audit["next_run_local"] = "2026-08-02T22:03:00+08:00"
                elif case == "run_state": audit["last_run_state"] = "has_run"
                elif case == "last_result": audit["last_task_result"] = 0
                elif case == "missed_runs": audit["missed_runs"] = 1
                elif case == "soak_bytes": audit["soak_log_bytes"] = 1
                elif case == "evidence_hash": audit["evidence_manifest_sha256"] = "0" * 64
                elif case == "evidence_mtime": audit["evidence_manifest_last_write_utc"] = "2026-08-01T08:00:00Z"
                elif case == "verify_command": audit["verify_command"] = "powershell verify"
                else: audit["exit_code"] = 1
                with self.assertRaises(ValidationError):
                    verify_p0_06_install_audit_semantics(audit, self.plan, ARTIFACTS)

    def test_install_audit_allows_only_append_only_current_evidence(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name)
        with temporary:
            copied = repo / "artifacts" / "phase-0"
            shutil.copytree(ARTIFACTS, copied)
            evidence = copied / "evidence-manifest.jsonl"
            baseline = evidence.read_bytes()
            evidence.write_bytes(baseline + b"{}\n")
            verify_p0_06_install_audit_semantics(load_json(copied / "p0-06-scheduler-install-audit.json"), self.plan, copied)
            evidence.write_bytes(b"X" + baseline[1:])
            with self.assertRaisesRegex(ValidationError, "append-only"):
                verify_p0_06_install_audit_semantics(load_json(copied / "p0-06-scheduler-install-audit.json"), self.plan, copied)

    def test_full_completion_gate_requires_cutoff_and_exact_24(self) -> None:
        temporary, repo = self.empty_verification_repo()
        with temporary:
            entries = self.complete_soak_entries()
            after_cutoff = datetime(2026, 8, 15, 17, 0, 1, tzinfo=timezone.utc)
            verify_p0_06_semantics(self.plan, entries, self.observation, self.catalog, repo, require_complete=True, verified_at_utc=after_cutoff)
            with self.assertRaisesRegex(ValidationError, "before the frozen acceptance cutoff"):
                verify_p0_06_semantics(self.plan, entries, self.observation, self.catalog, repo, require_complete=True, verified_at_utc=datetime(2026, 8, 15, 16, 59, 59, tzinfo=timezone.utc))
            with self.assertRaisesRegex(ValidationError, "each of the 24"):
                verify_p0_06_semantics(self.plan, entries[:-1], self.observation, self.catalog, repo, require_complete=True, verified_at_utc=after_cutoff)
            with self.assertRaisesRegex(ValidationError, "unknown/duplicate"):
                verify_p0_06_semantics(self.plan, entries + [copy.deepcopy(entries[0])], self.observation, self.catalog, repo, require_complete=True, verified_at_utc=after_cutoff)

    def test_full_completion_gate_rejects_late_network_and_budget_overrun(self) -> None:
        temporary, repo = self.empty_verification_repo()
        with temporary:
            after_cutoff = datetime(2026, 8, 15, 17, 0, 1, tzinfo=timezone.utc)
            entries = self.complete_soak_entries()
            network = next(item for item in entries if item["network_used"])
            network["started_at_utc"] = "2026-08-15T17:00:01Z"; network["completed_at_utc"] = "2026-08-15T17:00:02Z"
            with self.assertRaisesRegex(ValidationError, "network was used after"):
                verify_p0_06_semantics(self.plan, entries, self.observation, self.catalog, repo, require_complete=True, verified_at_utc=after_cutoff)
            entries = self.complete_soak_entries(); next(item for item in entries if item["network_used"])["attempts"] = 2
            with self.assertRaisesRegex(ValidationError, "network-attempt budget"):
                verify_p0_06_semantics(self.plan, entries, self.observation, self.catalog, repo, require_complete=True, verified_at_utc=after_cutoff)

    def test_install_audit_schema_forbids_continuous_os_claim(self) -> None:
        audit = self.valid_install_audit()
        audit["os_state_claim_scope"] = "continuous_os_proof"
        with self.assertRaises(ValidationError):
            validate_schema_instance(audit, load_json(ARTIFACTS / "schemas" / "p0-06-scheduler-install-audit.schema.json"))

    def test_exact_schedule_mapping_and_authorization(self) -> None:
        self.assertEqual(len(self.plan["requests"]), 24)
        self.assertEqual(len(self.plan["scheduler"]["triggers"]), 24)
        dlt = [item for item in self.plan["requests"] if item["game"] == "dlt" and item["source_slot"] == "official_corroborator_if_available"]
        self.assertEqual([item["scheduled_issue_id"] for item in dlt], ["2026087", "2026088", "2026089", "2026090", "2026091", "2026092"])
        ssq = [item for item in self.plan["requests"] if item["game"] == "ssq" and item["source_slot"] == "official_corroborator_if_available"]
        self.assertEqual([item["scheduled_issue_id"] for item in ssq], ["2026088", "2026089", "2026090", "2026091", "2026092", "2026093"])
        self.assertTrue(all(item["mapping_status"] == "conditional_hold" and item["request_url"] is None for item in ssq))
        self.assertEqual(self.plan["network_authorization"]["authorized_request_ids"], [item["request_id"] for item in dlt])
        self.assertEqual({trigger["local_at"][11:19] for trigger in self.plan["scheduler"]["triggers"]}, {"22:00:00", "22:03:00"})
        self.assertEqual(self.plan["scheduler"]["execution_time_limit_minutes"], 15)

    def test_runtime_dependency_hash_inventory_is_complete(self) -> None:
        required = {"scripts/phase0/p0_06_runner.py", "scripts/phase0/install_p0_06_scheduled_task.ps1", "scripts/phase0/p0_04_http.py", "scripts/phase0/p0_04_pipeline.py", "scripts/phase0/p0_04_parser.py"}
        self.assertTrue(required <= VERIFIER_FILES)
        schema = load_json(ARTIFACTS / "schemas" / "verification-command.schema.json")
        enum = set(schema["$defs"]["verifier_file"]["properties"]["path"]["enum"])
        self.assertEqual(enum, VERIFIER_FILES)
        self.assertEqual(schema["properties"]["verifier_file_hashes"]["minItems"], len(VERIFIER_FILES))
        self.assertEqual(schema["properties"]["verifier_file_hashes"]["maxItems"], len(VERIFIER_FILES))

    def test_too_early_writes_nothing(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            with self.assertRaisesRegex(RuntimeHold, "not due"):
                execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(datetime(2026, 8, 3, 14, 2, tzinfo=timezone.utc)), allow_network=True, collector_factory=NeverCollector)
            self.assertFalse((root / "soak-run-log.jsonl").exists())

    def test_blocked_and_hold_are_zero_network(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            blocked = execute_one(self.plan, "REQ-DLT-2026-08-03-PRIMARY", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, collector_factory=NeverCollector)
            hold = execute_one(self.plan, "REQ-SSQ-2026-08-02-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, collector_factory=NeverCollector)
            for entry in (blocked, hold):
                self.assertEqual(entry["attempts"], 0); self.assertFalse(entry["network_used"]); self.assertIsNone(entry["evidence_ref"]); self.assertIsNone(entry["raw_payload_ref"])

    def test_cli_switch_cannot_bypass_frozen_plan_authorization(self) -> None:
        temporary, root, digest = self.temp_artifacts(); plan = copy.deepcopy(self.plan)
        with temporary:
            plan["network_authorization"]["authorized_request_ids"].remove("REQ-DLT-2026-08-03-CORROBORATOR")
            with self.assertRaisesRegex(RuntimeHold, "differs"):
                execute_one(plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW), allow_network=True, collector_factory=NeverCollector)

    def test_schema_valid_plan_drifts_fail_before_clock_or_collector(self) -> None:
        for drift in ("url", "policy", "authorization"):
            with self.subTest(drift=drift):
                temporary, root, _ = self.temp_artifacts()
                with temporary:
                    plan = copy.deepcopy(self.plan)
                    request = next(item for item in plan["requests"] if item["request_id"] == "REQ-DLT-2026-08-03-CORROBORATOR")
                    if drift == "url": request["request_url"] = "https://www.gdlottery.cn/f_html/kjgg/P085_26088.html"
                    elif drift == "policy": request["execution_policy"] = "policy_blocked"
                    else: plan["network_authorization"]["authorized_request_ids"][0] = "REQ-DLT-2026-08-04-CORROBORATOR"
                    digest = write_runtime_plan(root, plan)
                    with self.assertRaisesRegex(RuntimeHold, "does not replay"):
                        execute_one(plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=lambda: self.fail("utcnow called before plan validation"), allow_network=True, clock_fn=lambda _: self.fail("clock called"), collector_factory=lambda: self.fail("collector called"))

    def test_source_policy_and_hash_tampering_fail_before_clock(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            catalog = copy.deepcopy(self.catalog)
            catalog["games"][0]["official_corroborators"][0]["approved_use"] = "hold_pending"
            (root / "source-catalog.json").write_bytes(canonical_json_bytes(catalog) + b"\n")
            with self.assertRaisesRegex(RuntimeHold, "source policy"):
                execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=lambda: self.fail("utcnow called"), allow_network=True, clock_fn=lambda _: self.fail("clock called"), collector_factory=lambda: self.fail("collector called"))
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            (root / "p0-06-runtime-plan.json.sha256").write_text("0" * 64 + "\n", encoding="ascii")
            with self.assertRaisesRegex(RuntimeHold, "sidecar mismatch"):
                execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=lambda: self.fail("utcnow called"), allow_network=True)
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            wrong = "f" * 64 if digest != "f" * 64 else "e" * 64
            with self.assertRaisesRegex(RuntimeHold, "expected SHA"):
                execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=wrong, utcnow_fn=lambda: self.fail("utcnow called"), allow_network=True)

    def test_execution_without_expected_hash_holds_before_clock(self) -> None:
        temporary, root, _ = self.temp_artifacts()
        with temporary:
            with self.assertRaisesRegex(RuntimeHold, "expected-plan-sha256"):
                execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, allow_network=True, utcnow_fn=lambda: self.fail("utcnow called"), clock_fn=lambda _: self.fail("clock called"))

    def test_network_failure_is_auditable_without_evidence(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, clock_fn=passing_clock, collector_factory=FailingCollector)
            self.assertEqual((entry["execution_disposition"], entry["attempts"], entry["result"]), ("network_attempted", 1, "invalid"))
            self.assertTrue(entry["network_used"]); self.assertIsNone(entry["evidence_ref"]); self.assertIsNone(entry["raw_payload_ref"])
            validate_jsonl_file(root / "soak-run-log.jsonl", ARTIFACTS / "schemas" / "soak-log.schema.json")

    def test_verifier_accepts_attempt_failure_without_evidence(self) -> None:
        temporary, execution_artifacts, digest = self.temp_artifacts()
        with temporary:
            entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=execution_artifacts, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, clock_fn=passing_clock, collector_factory=FailingCollector)
            repo = execution_artifacts / "verification-root"; artifacts = repo / "artifacts" / "phase-0"; artifacts.mkdir(parents=True)
            (artifacts / "evidence-manifest.jsonl").write_bytes(b"")
            verify_p0_06_semantics(self.plan, [entry], self.observation, self.catalog, repo)

    def test_linked_unverified_evidence_chain_baseline_passes(self) -> None:
        temporary, repo, _artifacts, entry, _evidence = self.valid_verifier_chain()
        with temporary:
            verify_p0_06_semantics(self.plan, [entry], self.observation, self.catalog, repo)

    def test_linked_evidence_relationship_failure_injections(self) -> None:
        cases = ("prefix", "empty_fragment", "cross_raw", "game", "issue", "status", "raw_hash", "normalized")
        for case in cases:
            with self.subTest(case=case):
                temporary, repo, artifacts, entry, evidence = self.valid_verifier_chain()
                with temporary:
                    if case == "prefix": entry["evidence_ref"] = "arbitrary-prefix#" + evidence["evidence_id"]
                    elif case == "empty_fragment": entry["evidence_ref"] = "artifacts/phase-0/evidence-manifest.jsonl#"
                    elif case == "cross_raw": entry["raw_payload_ref"] = "artifacts/phase-0/raw/other.html"
                    elif case == "game": evidence["game"] = "ssq"
                    elif case == "issue": evidence["issue_id"] = "2026088"
                    elif case == "status": evidence["status"] = "invalid"
                    elif case == "raw_hash": evidence["stored_payload_sha256"] = "0" * 64
                    else: evidence["normalized_record_sha256"] = "f" * 64
                    (artifacts / "evidence-manifest.jsonl").write_bytes(canonical_json_bytes(evidence) + b"\n")
                    with self.assertRaises(ValidationError):
                        verify_p0_06_semantics(self.plan, [entry], self.observation, self.catalog, repo)

    def test_raw_only_invalid_provenance_passes(self) -> None:
        temporary, repo, environment, entry, normalized_path = self.raw_only_provenance_fixture()
        with temporary:
            self.assertFalse(normalized_path.exists())
            verify_provenance(repo, environment, [entry])

    def test_raw_only_provenance_failure_injections(self) -> None:
        for case in ("declared_normalized_missing", "wrong_status", "normalized_exists"):
            with self.subTest(case=case):
                temporary, repo, environment, entry, normalized_path = self.raw_only_provenance_fixture()
                with temporary:
                    if case == "declared_normalized_missing": entry["normalized_record_sha256"] = "f" * 64
                    elif case == "wrong_status": entry["status"] = "unverified"
                    else: normalized_path.parent.mkdir(parents=True); normalized_path.write_bytes(b"{}\n")
                    with self.assertRaises(ValidationError):
                        verify_provenance(repo, environment, [entry])

    def test_verifier_rejects_invalid_or_contradictory_raw_only_refs(self) -> None:
        temporary, execution_artifacts, digest = self.temp_artifacts()
        with temporary:
            entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=execution_artifacts, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, clock_fn=passing_clock, collector_factory=FailingCollector)
            entry["evidence_ref"] = "artifacts/phase-0/evidence-manifest.jsonl#fake-evidence"
            entry["raw_payload_ref"] = "artifacts/phase-0/raw/fake.html"
            entry["classification_reason"] = "captured_raw_core_fields_parsed_rule_mapping_unavailable"
            repo = execution_artifacts / "verification-root"; artifacts = repo / "artifacts" / "phase-0"; artifacts.mkdir(parents=True)
            (artifacts / "evidence-manifest.jsonl").write_bytes(b"")
            with self.assertRaisesRegex(ValidationError, "referenced evidence does not exist"):
                verify_p0_06_semantics(self.plan, [entry], self.observation, self.catalog, repo)
            raw = artifacts / "raw" / "fake.html"; raw.parent.mkdir(); raw.write_bytes(b"raw")
            evidence = {"evidence_id": "fake-evidence", "game": "dlt", "issue_id": "2026087", "status": "unverified", "field_parsing_succeeded": True, "stored_payload_path": "artifacts/phase-0/raw/fake.html", "stored_payload_sha256": hashlib.sha256(b"raw").hexdigest(), "normalized_record_sha256": "0" * 64, "normalized_record_ref": "artifacts/phase-0/normalized/fake.json"}
            (artifacts / "evidence-manifest.jsonl").write_bytes(canonical_json_bytes(evidence) + b"\n")
            with self.assertRaisesRegex(ValidationError, "contradicts evidence"):
                verify_p0_06_semantics(self.plan, [entry], self.observation, self.catalog, repo)

    def test_completed_timestamp_records_simulated_elapsed_work(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            completed = datetime(2026, 8, 3, 14, 10, 7, tzinfo=timezone.utc)
            entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, completed), allow_network=True, clock_fn=passing_clock, collector_factory=FailingCollector)
            self.assertEqual(entry["started_at_utc"], "2026-08-03T14:10:00Z")
            self.assertEqual(entry["completed_at_utc"], "2026-08-03T14:10:07Z")
            self.assertNotEqual(entry["started_at_utc"], entry["completed_at_utc"])

    def test_failed_attempts_still_enforce_cross_process_sixty_second_spacing(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            first_start = datetime(2026, 8, 15, 16, 0, 0, tzinfo=timezone.utc); first_end = datetime(2026, 8, 15, 16, 0, 20, tzinfo=timezone.utc)
            execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(first_start, first_end), allow_network=True, clock_fn=passing_clock, collector_factory=FailingCollector)
            sleeps = []
            second_start = datetime(2026, 8, 15, 16, 0, 30, tzinfo=timezone.utc); second_end = datetime(2026, 8, 15, 16, 0, 40, tzinfo=timezone.utc)
            execute_one(self.plan, "REQ-DLT-2026-08-05-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(second_start, second_end), allow_network=True, clock_fn=passing_clock, collector_factory=FailingCollector, sleeper=sleeps.append)
            self.assertEqual(sleeps, [50.0])

    def test_cutoff_blocks_only_planned_network_requests_without_clock_or_collector(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            after = datetime(2026, 8, 15, 17, 0, 1, tzinfo=timezone.utc); completed = datetime(2026, 8, 15, 17, 0, 2, tzinfo=timezone.utc)
            cutoff = execute_one(self.plan, "REQ-DLT-2026-08-15-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(after, completed), allow_network=True, clock_fn=lambda _: self.fail("clock called after cutoff"), collector_factory=lambda: self.fail("collector called after cutoff"))
            self.assertEqual((cutoff["execution_disposition"], cutoff["result"], cutoff["attempts"], cutoff["classification_reason"]), ("policy_blocked", "invalid", 0, "acceptance_cutoff_passed_no_collection"))
            blocked = execute_one(self.plan, "REQ-DLT-2026-08-15-PRIMARY", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(after, completed), allow_network=True)
            hold = execute_one(self.plan, "REQ-SSQ-2026-08-13-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(after, completed), allow_network=True)
            self.assertEqual(blocked["classification_reason"], "source_approved_use_blocked"); self.assertEqual(hold["classification_reason"], "source_compliance_hold_no_collection")
            repo = root / "verification-root"; artifacts = repo / "artifacts" / "phase-0"; artifacts.mkdir(parents=True); (artifacts / "evidence-manifest.jsonl").write_bytes(b"")
            verify_p0_06_semantics(self.plan, [cutoff, blocked, hold], self.observation, self.catalog, repo)

    def test_clock_failure_is_zero_network_and_auditable(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, clock_fn=failing_clock, collector_factory=NeverCollector)
            self.assertEqual(entry["execution_disposition"], "policy_blocked"); self.assertEqual(entry["attempts"], 0); self.assertFalse(entry["network_used"])
            self.assertEqual(entry["classification_reason"], "fresh_clock_check_failed"); self.assertEqual(entry["clock_offset_seconds"], 9)

    def test_mock_success_has_evidence_and_raw(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            evidence = {"evidence_id": "p0-06-dlt-2026087", "status": "unverified", "stored_payload_path": "artifacts/phase-0/raw/p0-06-dlt-2026087.html"}
            outcome = SimpleNamespace(evidence=evidence, parse_result={"issue_id": "2026087"})
            def write_fixture(_root, _outcomes):
                raw = root / "raw" / "p0-06-dlt-2026087.html"; raw.parent.mkdir(parents=True); raw.write_bytes(b"fixture")
                (root / "evidence-manifest.jsonl").write_bytes(canonical_json_bytes(evidence) + b"\n")
            with patch("p0_06_runner.process_capture", return_value=outcome), patch("p0_06_runner.write_run_artifacts", side_effect=write_fixture):
                entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, clock_fn=passing_clock, collector_factory=SuccessfulCollector)
            self.assertEqual(entry["result"], "unverified"); self.assertIsNotNone(entry["evidence_ref"]); self.assertIsNotNone(entry["raw_payload_ref"])

    def test_future_issue_raw_capture_replays_without_rule_inference(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            raw = (Path(__file__).with_name("fixtures") / "p0_04_dlt_valid.html").read_bytes().replace(b"26014", b"26087")
            entry = execute_one(self.plan, "REQ-DLT-2026-08-03-CORROBORATOR", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW), allow_network=True, clock_fn=passing_clock, collector_factory=lambda: FutureFixtureCollector(raw))
            self.assertEqual(entry["result"], "invalid")
            self.assertEqual(entry["classification_reason"], "captured_raw_core_fields_parsed_rule_mapping_unavailable")
            self.assertTrue((root / "raw" / "p0-06-dlt-2026087.html").is_file())
            self.assertFalse((root / "normalized" / "p0-06-dlt-2026087.json").exists())
            evidence = load_jsonl(root / "evidence-manifest.jsonl")
            self.assertEqual(evidence[0]["status"], "invalid"); self.assertTrue(evidence[0]["field_parsing_succeeded"])
            self.assertEqual(verify_captures(root, passing_clock()), 1)

    def test_idempotence_conflict_and_catch_up_timestamps(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            first = execute_one(self.plan, "REQ-DLT-2026-08-03-PRIMARY", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW))
            second = execute_one(self.plan, "REQ-DLT-2026-08-03-PRIMARY", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(datetime(2026, 8, 4, tzinfo=timezone.utc)))
            self.assertEqual(first, second); self.assertEqual(first["planned_at_utc"], "2026-08-03T14:00:00Z"); self.assertEqual(first["started_at_utc"], "2026-08-03T14:10:00Z")
            conflicting = copy.deepcopy(first); conflicting["classification_reason"] = "tampered"
            with self.assertRaisesRegex(AcquisitionError, "conflict"):
                append_soak_entry(root / "soak-run-log.jsonl", conflicting)

    def test_canonical_contract_log_path_only(self) -> None:
        temporary, root, digest = self.temp_artifacts()
        with temporary:
            execute_one(self.plan, "REQ-DLT-2026-08-03-PRIMARY", artifacts=root, expected_plan_sha256=digest, utcnow_fn=utc_sequence(NOW, NOW))
            self.assertTrue((root / "soak-run-log.jsonl").is_file()); self.assertFalse((root / "soak-log.jsonl").exists())
            self.assertEqual(len(load_jsonl(root / "soak-run-log.jsonl")), 1)

    def test_installer_install_dry_run_does_not_mutate(self) -> None:
        completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / "install_p0_06_scheduled_task.ps1"), "-Action", "Install", "-DryRun"], cwd=REPO, capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("DryRun", completed.stdout); self.assertIn("False", completed.stdout); self.assertIn("24", completed.stdout)
        installer = (SCRIPTS / "install_p0_06_scheduled_task.ps1").read_text(encoding="utf-8")
        self.assertIn("XmlConvert]::ToTimeSpan", installer)
        self.assertIn("ExecutionTimeLimit15Minutes", installer)
        self.assertIn("New-TimeSpan -Minutes 15", installer)
        self.assertIn("--expected-plan-sha256", installer)
        self.assertIn("MultipleInstancesIgnoreNew", installer)
        self.assertIn("Resolve-AccountSid", installer)
        self.assertIn("Translate([System.Security.Principal.SecurityIdentifier])", installer)
        self.assertIn("PrincipalCurrentSid", installer)
        self.assertIn("point_in_time_snapshot_not_continuous_os_proof", installer)
        self.assertIn("Get-ScheduledTaskInfo", installer)
        self.assertIn("evidence_manifest_last_write_utc", installer)
        self.assertNotIn("PrincipalCurrentIdentity =", installer)
        self.assertNotIn("Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force", installer)

    def test_windows_short_account_name_resolves_to_current_sid(self) -> None:
        command = "$current=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $account=[System.Security.Principal.NTAccount]::new($env:USERNAME); $resolved=$account.Translate([System.Security.Principal.SecurityIdentifier]).Value; if($resolved -ne $current){exit 1}; Write-Output $resolved"
        completed = subprocess.run(["powershell", "-NoProfile", "-Command", command], cwd=REPO, capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(completed.stdout.strip().startswith("S-1-"))

    def test_installer_default_verify_is_read_only(self) -> None:
        audit_path = ARTIFACTS / "p0-06-scheduler-install-audit.json"
        before = audit_path.read_bytes() if audit_path.exists() else None
        # Windows Task Scheduler queries can fluctuate near 30 seconds; keep
        # headroom without weakening the read-only and no-file-change checks.
        completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / "install_p0_06_scheduled_task.ps1")], cwd=REPO, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("Verify", completed.stdout); self.assertIn("Mutated", completed.stdout); self.assertIn("False", completed.stdout)
        after = audit_path.read_bytes() if audit_path.exists() else None
        self.assertEqual(after, before)


if __name__ == "__main__": unittest.main()
