from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lottery_data.serialization import core_fact_sha256, make_observation_id
from lottery_data.steps.live_policy import LivePolicyError
from tests.phase1 import run_acceptance as acceptance_runner
from tests.phase1.e2e05_live_case import (
    ASSERTION_IDS,
    CONTRACT_TO_CURRENT_ASSERTION_IDS,
    CURRENT_EXECUTION_PROFILE,
    CURRENT_STATIC_REQUEST_IDS,
    LEGACY_CONTRACT_ASSERTION_IDS,
    POLICY_PATH,
    DeadlineDerivationError,
    _derive_watchdog_budget,
    _run_owned_worker,
    execute_live_case,
)


REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "artifacts" / "phase-1" / "releases" / "baseline-v1" / "draws.jsonl"
CONTRACT = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"


def _draws() -> list[dict]:
    return [json.loads(line) for line in BASELINE.read_text(encoding="utf-8").splitlines() if line]


def fake_fetch(request, policy, raw_root, throttle_root, **kwargs):
    body = request["request_id"].encode("ascii")
    path = raw_root / f"{request['request_id']}.raw"
    path.write_bytes(body)
    return {"raw_path": path, "raw_sha256": hashlib.sha256(body).hexdigest(), "url": request["url"]}


def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
    latest = max((row for row in _draws() if row["game"] == request["game"]), key=lambda row: row["issue_id"])
    value = {
        "observation_schema_version": "1.0.0", "observation_id": "",
        "source_id": request["source_id"], "publisher_id": publisher_id,
        "game": request["game"], "raw_issue_id": latest["issue_id"], "issue_id": latest["issue_id"],
        "draw_date_local": latest["draw_date_local"], "front_numbers": latest["front_numbers"],
        "back_numbers": latest["back_numbers"], "source_url": request["url"],
        "captured_at_utc": request["provenance"]["captured_at_utc"],
        "raw_ref": request["provenance"]["raw_ref"], "raw_sha256": request["provenance"]["raw_sha256"],
        "parser_id": parser_id, "parser_version": parser_version,
        "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": "", "parse_status": "parsed",
    }
    value["core_fact_sha256"] = core_fact_sha256(value)
    value["observation_id"] = make_observation_id(value["source_id"], value["game"], value["issue_id"], value["raw_sha256"], parser_version)
    return [value]


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _runner_helper_report(decision: str, underlying: int, acceptance: int, *, failing_id: str | None = None) -> dict:
    assertions = {assertion_id: assertion_id != failing_id for assertion_id in ASSERTION_IDS}
    formal = {
        "current-release.json": "a", "releases/baseline-v1": "b",
        "baseline-v1": "c", "runs/p1-baseline-v1": "d",
    }
    return {
        "case_id": "E2E-05", "decision": decision,
        "underlying_exit_code": underlying, "acceptance_runner_exit_code": acceptance,
        "observed_policy_sha256": "e" * 64, "assertions": assertions,
        "watchdog_parent_formal_state": {"before": formal, "after": dict(formal), "equal": True},
        "watchdog_budget": {
            "execution_profile": CURRENT_EXECUTION_PROFILE,
            "maximum_effective_requests": 8, "worker_deadline_seconds": 288.0,
            "cleanup_grace_seconds": 5.0, "total_deadline_seconds": 293.0,
        },
        "worker_process": {
            "timed_out": False, "terminated": False, "killed": False, "reaped": True,
            "returncode": acceptance, "workspace_cleanup_verified": True,
            "stdout_length": 100, "stderr_length": 0,
            "workspace": r"C:\forbidden\temporary", "stdout": "forbidden response body",
        },
        "execution_profile": {
            "profile": CURRENT_EXECUTION_PROFILE, "live_policy_schema_version": "1.3.0",
            "run_schema_version": "1.3.0", "event_schema_versions": ["1.3.0"],
            "static_request_count": 4, "effective_request_count": 4, "request_started_count": 4,
            "request_ids": list(CURRENT_STATIC_REQUEST_IDS), "request_kinds": ["history"] * 4,
            "request_discovered_event_count": 0, "child_authorization_present": False,
        },
        "primary_result": {
            "status": "published" if decision == "PASS" else "rejected", "exit_code": underlying,
            "request_stats": {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0},
        },
        "primary_errors": [], "raw_hash_evidence": [{"closed": True}] * 4,
        "parse_entry_evidence": [{"closed": True}] * 4,
    }


class E2ELiveCaseContractTests(unittest.TestCase):
    def test_fixed_fake_live_run_reports_all_29_assertions_from_actual_artifacts(self) -> None:
        report = execute_live_case(fetch_hook=fake_fetch, parse_hook=fake_parse)
        self.assertEqual((report["decision"], report["underlying_exit_code"], report["acceptance_runner_exit_code"]), ("PASS", 0, 0), report)
        self.assertEqual(tuple(report["assertions"]), ASSERTION_IDS)
        self.assertEqual(len(report["assertions"]), 29)
        self.assertTrue(all(report["assertions"].values()), report)
        self.assertIn(report["primary_result"]["status"], {"published", "no_change"})
        self.assertEqual(len(report["raw_hash_evidence"]), 4)
        self.assertTrue(all(row["closed"] for row in report["raw_hash_evidence"]), report)
        self.assertEqual(len(report["parse_entry_evidence"]), 4)
        self.assertTrue(all(row["closed"] for row in report["parse_entry_evidence"]), report)
        self.assertEqual(report["execution_profile"], {
            "profile": CURRENT_EXECUTION_PROFILE, "live_policy_schema_version": "1.3.0",
            "run_schema_version": "1.3.0", "event_schema_versions": ["1.3.0"],
            "static_request_count": 4, "effective_request_count": 4, "request_started_count": 4,
            "request_ids": list(CURRENT_STATIC_REQUEST_IDS), "request_kinds": ["history"] * 4,
            "request_discovered_event_count": 0, "child_authorization_present": False,
        })
        self.assertEqual(
            [(item["fault"], item["underlying_exit_code"], item["acceptance_runner_exit_code"], item["request_created"], item["run_created"], item["release_created"], item["invented_artifact_refs"]) for item in report["preflight_probe_results"]],
            [("hash", 4, 20, False, False, False, False), ("schema", 4, 20, False, False, False, False), ("expiry", 4, 20, False, False, False, False)],
        )
        self.assertEqual(report["runtime_probe_result"], {
            "underlying_exit_code": 3, "request_started": 2, "request_failed": 2,
            "release_created": False, "pointer_unchanged": True, "closed": True,
        })

    def test_network_unavailable_is_hold20_and_never_assumed_pass(self) -> None:
        failure = LivePolicyError(
            "dns_timeout_tls_or_required_source_unavailable", "controlled DNS unavailable",
            stage="runtime", exit_code=3, retryable=True,
        )
        report = execute_live_case(fetch_hook=lambda *args, **kwargs: (_ for _ in ()).throw(failure))
        self.assertEqual((report["decision"], report["underlying_exit_code"], report["acceptance_runner_exit_code"]), ("HOLD", 3, 20), report)
        self.assertNotEqual(report["decision"], "PASS")
        self.assertEqual(report["execution_profile"]["effective_request_count"], 4)
        self.assertEqual(report["execution_profile"]["request_started_count"], 2)
        with patch("tests.phase1.run_acceptance.execute_live_case", return_value=report):
            code, runner_report = acceptance_runner._live_case_report(_contract(), CONTRACT)
        self.assertEqual((code, runner_report["status"]), (20, "HOLD"), runner_report)

    def test_changed_policy_is_preflight_hold20_with_underlying4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "live-source-policy.json"
            policy.write_bytes(POLICY_PATH.read_bytes() + b"\n")
            report = execute_live_case(policy_path=policy)
        self.assertEqual((report["decision"], report["underlying_exit_code"], report["acceptance_runner_exit_code"]), ("HOLD", 4, 20), report)
        self.assertTrue(report["assertions"]["acceptance_report_preserves_underlying_exit_code=true"])
        self.assertNotIn("primary_result", report)

    def test_watchdog_budget_is_mechanically_derived_and_bounded(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        budget = _derive_watchdog_budget(policy)
        self.assertEqual(budget, {
            "execution_profile": CURRENT_EXECUTION_PROFILE,
            "static_request_count": 4, "max_dynamic_children": 0,
            "maximum_effective_requests": 8, "distinct_host_count": 3,
            "same_host_wait_count": 5, "request_timeout_seconds": 30.0,
            "throttle_interval_seconds": 2.0, "request_budget_seconds": 240.0,
            "throttle_budget_seconds": 10.0, "retry_budget_seconds": 8.0,
            "orchestration_margin_seconds": 30.0,
            "worker_deadline_seconds": 288.0, "cleanup_grace_seconds": 5.0,
            "total_deadline_seconds": 293.0, "safety_ceiling_seconds": 300.0,
        })
        changed_timeout = copy.deepcopy(policy)
        changed_timeout["network_policy"]["request_timeout_seconds"] = 29
        self.assertEqual(
            (_derive_watchdog_budget(changed_timeout)["worker_deadline_seconds"], _derive_watchdog_budget(changed_timeout)["total_deadline_seconds"]),
            (279.0, 284.0),
        )
        changed_throttle = copy.deepcopy(policy)
        changed_throttle["network_policy"]["cross_process_same_host_min_interval_seconds"] = 3
        self.assertEqual(
            (_derive_watchdog_budget(changed_throttle)["throttle_budget_seconds"], _derive_watchdog_budget(changed_throttle)["cleanup_grace_seconds"], _derive_watchdog_budget(changed_throttle)["total_deadline_seconds"]),
            (15.0, 6.0, 299.0),
        )
        above_ceiling = copy.deepcopy(policy)
        above_ceiling["network_policy"]["request_timeout_seconds"] = 31
        with self.assertRaisesRegex(DeadlineDerivationError, "300 second safety ceiling"):
            _derive_watchdog_budget(above_ceiling)

    def test_invalid_deadline_input_fails_before_worker_spawn(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        policy["network_policy"]["request_timeout_seconds"] = 0
        with (
            patch("tests.phase1.e2e05_live_case.load_live_policy", return_value=policy),
            patch("tests.phase1.e2e05_live_case.subprocess.Popen") as popen,
        ):
            report = execute_live_case()
        popen.assert_not_called()
        self.assertEqual((report["decision"], report["underlying_exit_code"], report["acceptance_runner_exit_code"]), ("HOLD", 4, 20))
        self.assertFalse(report["worker_started"])
        self.assertIn("DeadlineDerivationError", report["deadline_derivation_error"])

    def test_owned_worker_timeout_reaps_process_and_removes_workspace(self) -> None:
        budget = {"worker_deadline_seconds": 0.05, "cleanup_grace_seconds": 0.5}
        worker = _run_owned_worker(
            [sys.executable, "-c", (
                "import pathlib,sys,time; "
                "pathlib.Path(sys.argv[-1]).joinpath('marker').write_text('started'); time.sleep(60)"
            )],
            cwd=REPO, env=dict(os.environ), budget=budget,
        )
        self.assertTrue(worker["timed_out"], worker)
        self.assertTrue(worker["terminated"], worker)
        self.assertTrue(worker["reaped"], worker)
        self.assertTrue(worker["workspace_cleanup_verified"], worker)
        self.assertFalse(Path(worker["workspace"]).exists())

    def test_watchdog_timeout_detects_formal_tree_tampering_in_parent(self) -> None:
        before = {"current-release.json": "a", "releases/baseline-v1": "b", "baseline-v1": "c", "runs/p1-baseline-v1": "d"}
        after = {**before, "current-release.json": "changed"}
        worker = {
            "timed_out": True, "terminated": True, "killed": False, "reaped": True,
            "returncode": -15, "stdout": "", "stderr": "", "spawn_error": None,
            "workspace": "controlled", "workspace_cleanup_verified": True,
            "workspace_cleanup_error": None,
        }
        with (
            patch("tests.phase1.e2e05_live_case._formal_state", side_effect=[before, after]),
            patch("tests.phase1.e2e05_live_case._run_owned_worker", return_value=worker),
        ):
            report = execute_live_case()
        self.assertEqual((report["decision"], report["underlying_exit_code"], report["acceptance_runner_exit_code"]), ("FAIL", 3, 1))
        self.assertEqual(report["watchdog_parent_formal_state"], {"before": before, "after": after, "equal": False})
        self.assertFalse(report["assertions"]["no_production_pointer_change=true"])

    def test_runner_maps_live_pass_fail_and_holds_without_leaking_worker_payload(self) -> None:
        cases = (
            ("PASS", 0, 0, None, 0, "PASS"),
            ("FAIL", 2, 1, ASSERTION_IDS[-2], 1, "FAIL"),
            ("HOLD", 4, 20, ASSERTION_IDS[0], 20, "HOLD"),
            ("HOLD", 3, 20, ASSERTION_IDS[1], 20, "HOLD"),
        )
        for decision, underlying, helper_exit, failing_id, expected_code, expected_status in cases:
            helper = _runner_helper_report(decision, underlying, helper_exit, failing_id=failing_id)
            with self.subTest(decision=decision, underlying=underlying), patch(
                "tests.phase1.run_acceptance.execute_live_case", return_value=helper,
            ) as execute:
                code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
            execute.assert_called_once_with()
            self.assertEqual((code, report["status"], report["underlying_exit_code"], report["acceptance_runner_exit_code"]), (expected_code, expected_status, underlying, expected_code))
            self.assertEqual([row["id"] for row in report["assertions"]], list(ASSERTION_IDS))
            self.assertEqual(len(report["assertions"]), 29)
            self.assertTrue(all(row["expected"] is True and row["actual"] is helper["assertions"][row["id"]] for row in report["assertions"]))
            expected_sha = hashlib.sha256(acceptance_runner.canonical_bytes(report["helper_evidence"])).hexdigest()
            self.assertEqual(report["helper_evidence_sha256"], expected_sha)
            self.assertTrue(all(row["evidence"]["helper_evidence_sha256"] == expected_sha for row in report["assertions"]))
            self.assertEqual(
                [row["evidence"]["contract_assertion_id"] for row in report["assertions"]],
                list(ASSERTION_IDS),
            )
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn(r"C:\forbidden\temporary", serialized)
            self.assertNotIn("forbidden response body", serialized)
            self.assertNotIn('"stdout":', serialized)
            self.assertTrue(report["current_session_live"]["explicit_cli"])
            self.assertEqual(report["formal_state"]["before"], report["formal_state"]["after"])

    def test_runner_treats_helper_assertion_mapping_order_as_non_semantic(self) -> None:
        baseline = _runner_helper_report("PASS", 0, 0)
        mappings = (
            dict(sorted(baseline["assertions"].items())),
            dict(reversed(tuple(baseline["assertions"].items()))),
        )
        rendered = []
        for helper_assertions in mappings:
            helper = copy.deepcopy(baseline)
            helper["assertions"] = helper_assertions
            with patch("tests.phase1.run_acceptance.execute_live_case", return_value=helper):
                code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
            self.assertEqual((code, report["status"]), (0, "PASS"), report)
            rendered.append([
                (row["id"], row["actual"], row["status"])
                for row in report["assertions"]
            ])
        self.assertEqual(rendered[0], rendered[1])
        self.assertEqual([row[0] for row in rendered[0]], list(ASSERTION_IDS))

    def test_runner_preserves_exactly_five_false_helper_assertions(self) -> None:
        helper = _runner_helper_report("HOLD", 3, 20)
        failing_ids = set(ASSERTION_IDS[:5])
        helper["assertions"] = dict(sorted(
            (assertion_id, assertion_id not in failing_ids)
            for assertion_id in ASSERTION_IDS
        ))
        with patch("tests.phase1.run_acceptance.execute_live_case", return_value=helper):
            code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
        self.assertEqual((code, report["status"]), (20, "HOLD"), report)
        self.assertEqual(
            [row["id"] for row in report["assertions"] if row["status"] == "FAIL"],
            list(ASSERTION_IDS[:5]),
        )
        self.assertEqual(sum(row["status"] == "PASS" for row in report["assertions"]), 24)

    def test_runner_rejects_missing_extra_and_non_boolean_helper_assertions(self) -> None:
        malformed = []
        missing = _runner_helper_report("HOLD", 3, 20)
        missing["assertions"].pop(ASSERTION_IDS[-1])
        malformed.append(("missing", missing))
        extra = _runner_helper_report("HOLD", 3, 20)
        extra["assertions"]["unexpected"] = True
        malformed.append(("extra", extra))
        non_boolean = _runner_helper_report("HOLD", 3, 20)
        non_boolean["assertions"][ASSERTION_IDS[-1]] = 1
        malformed.append(("non_boolean", non_boolean))
        for name, helper in malformed:
            with self.subTest(name=name), patch(
                "tests.phase1.run_acceptance.execute_live_case", return_value=helper,
            ):
                code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
            self.assertEqual((code, report["status"]), (1, "FAIL"), report)
            self.assertEqual([row["id"] for row in report["assertions"]], list(ASSERTION_IDS))
            self.assertTrue(any(row["status"] == "FAIL" for row in report["assertions"]))

    def test_runner_rejects_legacy_mixed_and_forged_current_profiles(self) -> None:
        legacy_fixture = json.loads((
            REPO / "tests" / "phase1" / "fixtures" / "live-execution" / "valid-manifest-v1.1.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(legacy_fixture["run_schema_version"], "1.1.0")
        self.assertEqual(legacy_fixture["request_plan"][3]["request_kind"], "discovery")
        self.assertIn("child_authorization", legacy_fixture["request_plan"][3])
        self.assertEqual(json.loads(POLICY_PATH.read_text(encoding="utf-8"))["live_policy_schema_version"], "1.3.0")

        malformed = []
        legacy = _runner_helper_report("PASS", 0, 0)
        legacy["execution_profile"].update({
            "profile": "legacy-discovery-v1.1", "live_policy_schema_version": "1.1.1",
            "run_schema_version": legacy_fixture["run_schema_version"],
            "event_schema_versions": ["1.1.0"], "effective_request_count": 5,
            "request_discovered_event_count": 1, "child_authorization_present": True,
        })
        malformed.append(("explicit_legacy", legacy))
        mixed = _runner_helper_report("PASS", 0, 0)
        mixed["execution_profile"]["request_discovered_event_count"] = 1
        malformed.append(("mixed_discovery", mixed))
        extra_profile = _runner_helper_report("PASS", 0, 0)
        extra_profile["execution_profile"]["parent_request_id"] = "hidden-legacy-parent"
        malformed.append(("extra_profile_field", extra_profile))
        forged = _runner_helper_report("PASS", 0, 0)
        forged["primary_result"]["request_stats"]["planned"] = 5
        malformed.append(("forged_count", forged))
        forged_evidence = _runner_helper_report("PASS", 0, 0)
        forged_evidence["raw_hash_evidence"] = ["not-evidence"] * 4
        malformed.append(("forged_evidence_shape", forged_evidence))
        for name, helper in malformed:
            with self.subTest(name=name), patch("tests.phase1.run_acceptance.execute_live_case", return_value=helper):
                code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
            self.assertEqual((code, report["status"]), (1, "FAIL"), report)

    def test_runner_rejects_pass_with_partial_started_profile_and_stat_mismatch(self) -> None:
        cases = []
        partial_profile = _runner_helper_report("PASS", 0, 0)
        partial_profile["execution_profile"]["request_started_count"] = 1
        cases.append(("partial_profile", partial_profile))
        partial_stats = _runner_helper_report("PASS", 0, 0)
        partial_stats["primary_result"]["request_stats"].update(
            started=1, succeeded=0, failed=1, not_started=3,
        )
        cases.append(("partial_stats", partial_stats))
        inconsistent_success = _runner_helper_report("PASS", 0, 0)
        inconsistent_success["primary_result"]["request_stats"]["succeeded"] = 3
        cases.append(("inconsistent_success", inconsistent_success))
        for name, helper in cases:
            with self.subTest(name=name), patch("tests.phase1.run_acceptance.execute_live_case", return_value=helper):
                code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
            self.assertEqual((code, report["status"]), (1, "FAIL"), report)

    def test_runner_rejects_pass_with_rejected_primary_status_or_nonzero_exit(self) -> None:
        cases = []
        rejected = _runner_helper_report("PASS", 0, 0)
        rejected["primary_result"].update(status="rejected", exit_code=3)
        cases.append(("rejected", rejected))
        nonzero = _runner_helper_report("PASS", 0, 0)
        nonzero["primary_result"]["exit_code"] = 3
        cases.append(("nonzero_exit", nonzero))
        for name, helper in cases:
            with self.subTest(name=name), patch("tests.phase1.run_acceptance.execute_live_case", return_value=helper):
                code, report = acceptance_runner._live_case_report(_contract(), CONTRACT)
            self.assertEqual((code, report["status"]), (1, "FAIL"), report)

    def test_assertion_id_migration_is_unique_positional_and_complete(self) -> None:
        self.assertEqual(len(ASSERTION_IDS), 29)
        self.assertEqual(len(set(ASSERTION_IDS)), 29)
        self.assertEqual(len(LEGACY_CONTRACT_ASSERTION_IDS), 29)
        self.assertEqual(len(set(LEGACY_CONTRACT_ASSERTION_IDS)), 29)
        self.assertEqual(
            [CONTRACT_TO_CURRENT_ASSERTION_IDS[item] for item in LEGACY_CONTRACT_ASSERTION_IDS],
            list(ASSERTION_IDS),
        )

    def test_runner_contract_id_mismatch_never_calls_live_helper(self) -> None:
        for name, mutate in (
            ("reordered", lambda ids: ids.__setitem__(slice(0, 2), list(reversed(ids[:2])))),
            ("missing", lambda ids: ids.pop()),
        ):
            contract = _contract()
            declared = next(row for row in contract["e2e_cases"] if row["id"] == "E2E-05")["assertions"]
            mutate(declared)
            with self.subTest(name=name), patch("tests.phase1.run_acceptance.execute_live_case") as execute:
                code, report = acceptance_runner._live_case_report(contract, CONTRACT)
            execute.assert_not_called()
            self.assertEqual((code, report["status"], report["assertions"]), (1, "FAIL", []))
            self.assertFalse(report["current_session_live"]["helper_called"])

    def test_runner_other_case_does_not_call_live_helper_and_unknown_is_exit1(self) -> None:
        contract = _contract()
        with (
            patch("tests.phase1.run_acceptance.execute_live_case") as execute,
            patch("tests.phase1.run_acceptance._case_report", return_value=(0, {"status": "PASS"})) as other,
        ):
            code, report = acceptance_runner._dispatch_case("E2E-03", contract, CONTRACT)
        execute.assert_not_called()
        other.assert_called_once_with("E2E-03", contract, CONTRACT)
        self.assertEqual((code, report["status"]), (0, "PASS"))

        completed = subprocess.run(
            [sys.executable, str(REPO / "tests/phase1/run_acceptance.py"), "--contract", str(CONTRACT), "--execute-case", "UNKNOWN"],
            cwd=REPO, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        unknown = json.loads(completed.stdout)
        self.assertEqual((unknown["status"], unknown["error_type"]), ("FAIL", "KeyError"))

        with tempfile.TemporaryDirectory() as directory:
            forbidden_output = Path(directory) / "e2e05.json"
            rejected = subprocess.run(
                [
                    sys.executable, str(REPO / "tests/phase1/run_acceptance.py"),
                    "--contract", str(CONTRACT), "--execute-case", "E2E-05",
                    "--output", str(forbidden_output),
                ],
                cwd=REPO, text=True, capture_output=True, check=False,
            )
            self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
            self.assertFalse(forbidden_output.exists())
            self.assertEqual(json.loads(rejected.stdout)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
