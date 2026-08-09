from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.formal import (
    NEGATIVE_CONTROLS,
    audit_real_probability_spaces,
    execute_failure_injection,
    execute_qualification_control,
    qualification_replication,
    validate_qualification_bottom_up,
)
from lottery_research.phase3.ledger import AppendOnlyLedger
from lottery_research.phase3.work_items import validate_review_provenance


ROOT = Path(__file__).resolve().parents[2]


class WorkflowContractTests(unittest.TestCase):
    def formal_actor_assignment(self, base: Path) -> Path:
        roles = (
            "data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer",
            "run_operator", "independent_reviewer", "acceptance_engineer", "classification_approver", "release_controller",
        )
        assignments = []
        for role in roles:
            record = base / f"{role}.log"
            record.write_text(f"{role}\n", encoding="utf-8")
            assignments.append({
                "role": role, "actor_id": f"actor-{role}", "task_id": f"task-{role}", "session_id": f"session-{role}",
                "assigned_at_utc": "2026-08-09T00:00:00Z", "assigned_by": "controller", "task_record_path": record.name,
                "task_record_sha256": hashlib.sha256(record.read_bytes()).hexdigest(),
            })
        path = base / "formal-actors.json"
        path.write_text(json.dumps({
            "schema_version": "3.0.0", "artifact_type": "phase3_actor_assignment", "assignment_id": "formal-actors-i01",
            "assignment_stage": "formal_before_W07", "parent_assignment_sha256": "1" * 64, "controller_id": "controller",
            "created_at_utc": "2026-08-09T00:00:00Z", "assignments": assignments,
        }), encoding="utf-8")
        return path

    def test_registered_qualification_replication_is_complete_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            first = qualification_replication("injected", 7, "registered-test")
            second = qualification_replication("injected", 7, "registered-test")
            self.assertEqual(first, second)
            self.assertEqual(first["draw_count"], 200)
            self.assertEqual(first["outer_target_count"], 150)
            receipts = [execute_qualification_control(case, base) for case in NEGATIVE_CONTROLS]
            self.assertTrue(all(row["actual_terminal"] == "REJECTED" for row in receipts))

    def test_qualification_bottom_up_rejects_fitted_evidence_mutations(self) -> None:
        mutations = {
            "selected_lambda_mismatch_count": lambda row: row["selected_lambdas"].__setitem__(0, 1.0 if row["selected_lambdas"][0] != 1.0 else 5.0),
            "fitted_probability_mismatch_count": lambda row: row["fitted_target_probabilities"].__setitem__(0, row["fitted_target_probabilities"][0] * 0.9),
            "outer_skill_mismatch_count": lambda row: row["outer_skill_values"].__setitem__(0, row["outer_skill_values"][0] + 0.1),
            "final_theta_mismatch_count": lambda row: row["final_theta"].__setitem__(0, row["final_theta"][0] + 0.1),
        }
        for mismatch_field, mutate in mutations.items():
            with self.subTest(mismatch_field=mismatch_field), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                row = qualification_replication("injected", 0, "mutation-check")
                mutate(row)
                (base / "replications.jsonl").write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
                ledger = AppendOnlyLedger(base / "experiment-ledger.jsonl", "mutation-check")
                ledger.start("injected-world-0000", {})
                ledger.finish("injected-world-0000", "succeeded", {})
                ledger.close()
                result = validate_qualification_bottom_up(ROOT, base, "mutation-check")
                self.assertGreater(result[mismatch_field], 0)

    def test_real_probability_spaces_and_failure_recovery_execute(self) -> None:
        self.assertEqual(audit_real_probability_spaces()["status"], "PASS")
        with tempfile.TemporaryDirectory() as raw:
            result = execute_failure_injection(Path(raw), "failure-check")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["timeout"]["observed"])
        self.assertEqual(result["crash"]["observed_returncode"], 17)
        self.assertTrue(result["retry"]["failed_attempt_retained"])

    def test_run_command_requires_real_frozen_release_instead_of_fixed_hold(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            actors = self.formal_actor_assignment(base)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lottery_research.phase3",
                    "run",
                    "--identity",
                    "formal-refusal-i01",
                    "--output",
                    str(base / "formal-refusal-i01"),
                    "--release-root",
                    str(base / "release-i01"),
                    "--actor-assignments",
                    str(actors),
                ],
                cwd=ROOT,
                env={"PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotEqual(json.loads(completed.stdout)["terminal"], "HOLD")

    def test_review_provenance_is_bound_to_actor_task_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            actors_path = self.formal_actor_assignment(base)
            actors = json.loads(actors_path.read_text(encoding="utf-8"))
            by_role = {row["role"]: row for row in actors["assignments"]}
            manifest = base / "manifest.json"
            manifest.write_text('{"release":"r1"}\n', encoding="utf-8")
            reviewer = by_role["independent_reviewer"]
            review = {
                "schema_version": "3.0.0", "artifact_type": "phase3_review", "review_id": "review-r1",
                "actor_assignment_sha256": hashlib.sha256(actors_path.read_bytes()).hexdigest(),
                "reviewer_role": "independent_reviewer", "reviewer_id": reviewer["actor_id"],
                "review_task_id": reviewer["task_id"], "review_session_id": reviewer["session_id"],
                "review_task_record_sha256": reviewer["task_record_sha256"], "signed_at_utc": "2026-08-09T01:00:00Z",
                "reviewed_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "implementation_author_id": by_role["implementation_author"]["actor_id"],
                "classification_approver_id": by_role["classification_approver"]["actor_id"],
                "independence_declaration": "reviewer_is_not_implementation_author_or_classification_approver",
                "reviewed_paths": [manifest.as_posix()], "blocking_findings": 0, "status": "PASS",
            }
            review_path = base / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            validate_review_provenance(ROOT, review_path, actors_path, manifest)
            review["review_task_id"] = "invented-task"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reviewer provenance"):
                validate_review_provenance(ROOT, review_path, actors_path, manifest)


if __name__ == "__main__":
    unittest.main()
