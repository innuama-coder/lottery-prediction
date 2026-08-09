from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.workflow import (
    final_validate,
    handoff_validate,
    qualify,
    readiness,
    replay,
    verify_e2e,
)
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

    def test_synthetic_qualification_replay_and_e2e_are_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            qualification = qualify(ROOT, base / "qualification-i01", "qualification-i01")
            replay_receipt = replay(ROOT, base / "replay-i01", "replay-i01", base / "qualification-i01")
            e2e = verify_e2e(ROOT, base / "e2e-i01", "e2e-i01")

            self.assertEqual(qualification["status"], "HOLD")
            self.assertEqual(qualification["completed_replications"], {"uniform": 1, "injected": 1})
            self.assertTrue(qualification["non_formal_synthetic_only"])
            self.assertEqual(replay_receipt["status"], "PASS")
            self.assertEqual(e2e["status"], "PASS")
            self.assertEqual(e2e["required_case_coverage"], 1.0)

    def test_readiness_and_final_validator_wait_for_formal_release_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            readiness_receipt = readiness(ROOT, base / "readiness-i01", "readiness-i01")
            final = final_validate(ROOT, base / "final-i01", "final-i01")
            handoff = handoff_validate(ROOT, base / "handoff-i01", "handoff-i01", base / "missing-release")

            self.assertEqual(readiness_receipt["terminal"], "HOLD_PENDING_FORMAL_RELEASE_FREEZE")
            self.assertFalse(readiness_receipt["formal_run_authorized"])
            self.assertEqual(readiness_receipt["sequence_relation_coverage"], 1.0)
            self.assertIn("dirty", readiness_receipt["task"])
            self.assertEqual(final["terminal"], "HOLD_PENDING_FORMAL_RELEASE_FREEZE")
            self.assertEqual(final["formal_result_count"], 0)
            self.assertEqual(handoff["terminal"], "HOLD_HANDOFF_INCOMPLETE")

    def test_run_command_refuses_formal_execution(self) -> None:
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
            self.assertEqual(completed.returncode, 20)
            self.assertEqual(json.loads(completed.stdout)["terminal"], "HOLD_PENDING_FORMAL_RELEASE_FREEZE")

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
