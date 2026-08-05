from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO / "scripts" / "phase0_amendment"
sys.path.insert(0, str(SCRIPT_DIR))

from assess_immediate_feasibility import AssessmentError, INPUTS, _load_json, _load_jsonl, _validate_report, build_report  # noqa: E402


def passing_runs() -> list[dict]:
    empty_hash = hashlib.sha256(b"").hexdigest()
    return [
        {
            "command": ["python", pattern], "exit_code": 0, "observed_test_count": count,
            "result": "PASS", "stdout_ref": f"stdout-{pattern}", "stderr_ref": f"stderr-{pattern}",
            "stdout_sha256": empty_hash, "stderr_sha256": empty_hash,
        }
        for pattern, count in (("test_p0_04.py", 30), ("test_p0_05.py", 6))
    ]


class ImmediateAssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = _load_json(INPUTS["source_catalog"])
        cls.scope = _load_json(INPUTS["scope_freeze"])
        cls.coverage = _load_json(INPUTS["coverage_report"])
        cls.evidence = _load_jsonl(INPUTS["evidence_manifest"])
        cls.corrective = _load_json(INPUTS["corrective_paths"])

    def report(self, coverage=None) -> dict:
        return build_report(
            evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(), catalog=self.catalog,
            scope=self.scope, coverage=self.coverage if coverage is None else coverage, evidence=self.evidence,
            corrective_paths=self.corrective,
        )

    def test_current_evidence_completes_phase0_with_real_hold_reasons(self) -> None:
        report = self.report()
        self.assertEqual(report["project_decision"], "HOLD")
        self.assertEqual(report["phase0_assessment_status"], "COMPLETE_WITH_HOLD")
        self.assertFalse(report["prospective_observation"]["blocking"])
        games = {item["game"]: item for item in report["games"]}
        self.assertEqual(games["dlt"]["coverage"]["target_observed"], 1)
        self.assertEqual(games["dlt"]["coverage"]["verified_records"], 0)
        self.assertEqual(games["ssq"]["coverage"]["target_observed"], 0)
        self.assertTrue(all(item["independent_conflict_test"] == "NOT_ASSESSABLE" for item in games.values()))
        self.assertTrue(all(not item["modeling_data_sufficient"] for item in games.values()))
        _validate_report(report)

    def test_pending_soak_never_changes_immediate_decision(self) -> None:
        first = self.report()
        runtime_plan = _load_json(REPO / "artifacts" / "phase-0" / "p0-06-runtime-plan.json")
        self.assertEqual(runtime_plan["status"], "prepared_not_started")
        second = self.report()
        self.assertEqual(first["project_decision"], second["project_decision"])
        self.assertEqual(first["games"], second["games"])

    def test_false_coverage_tier_does_not_make_unverified_data_model_ready(self) -> None:
        tampered = copy.deepcopy(self.coverage)
        tampered["games"][0]["coverage_tier"] = "minimum_viable"
        report = self.report(coverage=tampered)
        dlt = next(item for item in report["games"] if item["game"] == "dlt")
        self.assertFalse(dlt["modeling_data_sufficient"])
        self.assertEqual(dlt["outcome"], "HOLD")

    def test_go_requires_target_coverage_and_verified_records_for_both_games(self) -> None:
        coverage = copy.deepcopy(self.coverage)
        catalog = copy.deepcopy(self.catalog)
        for readiness in catalog["operational_readiness"]:
            readiness["acquisition_ready"] = True
            readiness["policy_conclusion"] = "ready"
        evidence = []
        for game in coverage["games"]:
            game["coverage_tier"] = "target"
            game["target_observed_issues"] = list(game["target_expected_issues"])
            game["minimum_observed_issues"] = list(game["minimum_expected_issues"])
            game["missing"] = []
            evidence.extend({"game": game["game"], "issue_id": issue, "status": "verified"} for issue in game["target_expected_issues"])
        report = build_report(
            evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(),
            catalog=catalog, scope=self.scope, coverage=coverage, evidence=evidence,
            corrective_paths=self.corrective,
        )
        self.assertEqual(report["project_decision"], "GO")
        self.assertTrue(all(item["outcome"] == "PASS_FULL" for item in report["games"]))

    def test_duplicate_verified_rows_and_declared_tier_cannot_create_limited_go(self) -> None:
        coverage = copy.deepcopy(self.coverage)
        evidence = []
        for game in coverage["games"]:
            game["coverage_tier"] = "minimum_viable"
            repeated = game["minimum_expected_issues"][0]
            evidence.extend({"game": game["game"], "issue_id": repeated, "status": "verified"} for _ in range(200))
            game["target_observed_issues"] = [repeated]
            game["minimum_observed_issues"] = [repeated]
            game["missing"] = [
                {"issue_id": issue} for issue in game["target_expected_issues"] if issue != repeated
            ]
        report = build_report(
            evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(),
            catalog=self.catalog, scope=self.scope, coverage=coverage, evidence=evidence,
            corrective_paths=self.corrective,
        )
        self.assertEqual(report["project_decision"], "HOLD")
        self.assertTrue(all(item["outcome"] == "HOLD" for item in report["games"]))
        self.assertTrue(all(item["coverage"]["verified_records"] == 1 for item in report["games"]))
        data_gate = next(item for item in report["immediate_gates"] if item["id"] == "I-DATA-SUFFICIENCY")
        self.assertEqual(data_gate["status"], "FAIL")
        self.assertNotIn("Neither is model-ready", data_gate["finding"])

    def test_empty_or_shrunk_expected_universe_cannot_create_a_pass(self) -> None:
        for label, keep in (("empty", 0), ("shrunk", 10)):
            with self.subTest(label=label):
                coverage = copy.deepcopy(self.coverage)
                evidence = []
                for game in coverage["games"]:
                    selected = list(game["target_expected_issues"][:keep])
                    game["target_expected_issues"] = selected
                    game["minimum_expected_issues"] = selected
                    game["target_observed_issues"] = selected
                    game["minimum_observed_issues"] = selected
                    game["missing"] = []
                    game["coverage_tier"] = "target"
                    evidence.extend({"game": game["game"], "issue_id": issue, "status": "verified"} for issue in selected)
                report = build_report(
                    evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(),
                    catalog=self.catalog, scope=self.scope, coverage=coverage, evidence=evidence,
                    corrective_paths=self.corrective,
                )
                self.assertEqual(report["project_decision"], "HOLD")
                self.assertTrue(all(item["outcome"] == "HOLD" for item in report["games"]))

    def test_stop_requires_explicit_exhausted_alternatives_for_both_games(self) -> None:
        corrective = copy.deepcopy(self.corrective)
        for game in corrective["games"]:
            game["compliant_corrective_action_available"] = False
            game["alternatives_exhausted_no_evidentiary_path"] = True
            game["actions"] = []
            game["exhaustion_evidence_refs"] = ["artifacts/phase-0/source-catalog.json"]
            for alternative in game["audited_alternatives"]:
                alternative["status"] = "exhausted"
        report = build_report(
            evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(), catalog=self.catalog,
            scope=self.scope, coverage=self.coverage, evidence=self.evidence, corrective_paths=corrective,
        )
        self.assertEqual(report["project_decision"], "STOP")
        self.assertEqual(report["phase0_assessment_status"], "COMPLETE_WITH_STOP")
        self.assertTrue(all(item["outcome"] == "STOP" for item in report["games"]))

    def test_incomplete_or_contradictory_corrective_classification_fails_closed(self) -> None:
        for available, exhausted in ((False, False), (True, True)):
            with self.subTest(available=available, exhausted=exhausted):
                corrective = copy.deepcopy(self.corrective)
                corrective["games"][0]["compliant_corrective_action_available"] = available
                corrective["games"][0]["alternatives_exhausted_no_evidentiary_path"] = exhausted
                with self.assertRaises(AssessmentError):
                    build_report(
                        evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(), catalog=self.catalog,
                        scope=self.scope, coverage=self.coverage, evidence=self.evidence, corrective_paths=corrective,
                    )

    def test_nonexistent_corrective_evidence_reference_fails_closed(self) -> None:
        corrective = copy.deepcopy(self.corrective)
        corrective["games"][0]["actions"][0]["evidence_refs"] = ["artifacts/does-not-exist.json"]
        with self.assertRaisesRegex(AssessmentError, "does not exist"):
            build_report(
                evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(), catalog=self.catalog,
                scope=self.scope, coverage=self.coverage, evidence=self.evidence, corrective_paths=corrective,
            )

    def test_evidence_free_stop_fails_closed(self) -> None:
        corrective = copy.deepcopy(self.corrective)
        for game in corrective["games"]:
            game["compliant_corrective_action_available"] = False
            game["alternatives_exhausted_no_evidentiary_path"] = True
            game["actions"] = []
            game["exhaustion_evidence_refs"] = []
            for alternative in game["audited_alternatives"]:
                alternative["status"] = "exhausted"
        with self.assertRaisesRegex(AssessmentError, "requires exhaustion evidence"):
            build_report(
                evaluated_at_utc="2026-08-02T02:00:00Z", test_runs=passing_runs(), catalog=self.catalog,
                scope=self.scope, coverage=self.coverage, evidence=self.evidence, corrective_paths=corrective,
            )


if __name__ == "__main__":
    unittest.main()
