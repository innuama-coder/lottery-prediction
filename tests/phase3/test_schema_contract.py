from __future__ import annotations

import unittest
from pathlib import Path

from lottery_research.phase3.schema import SCHEMA_FILES, validate_payload


ROOT = Path(__file__).resolve().parents[2]


class SchemaContractTests(unittest.TestCase):
    def test_all_required_artifact_schemas_exist_and_are_valid(self) -> None:
        required = {
            "input_manifest", "preregistration", "model_registry", "feature_registry", "fold",
            "forecast", "metric", "experiment_ledger", "replay", "review", "manifest", "acceptance",
            "actor_assignment", "handoff", "work_item_receipt",
        }
        self.assertEqual(set(SCHEMA_FILES), required)
        for filename in SCHEMA_FILES.values():
            self.assertTrue((ROOT / "schemas/phase3" / filename).is_file())

    def test_unknown_fields_are_rejected(self) -> None:
        payload = {
            "schema_version": "3.0.0",
            "artifact_type": "phase3_fold",
            "game": "ssq",
            "target_issue": "synthetic-5",
            "training_issues": ["synthetic-1", "synthetic-2"],
            "inner_targets": ["synthetic-2"],
            "unknown": True,
        }
        with self.assertRaisesRegex(ValueError, "Additional properties"):
            validate_payload(ROOT, "fold", payload)

        payload.pop("unknown")
        validate_payload(ROOT, "fold", payload)

    def test_review_identity_conflicts_are_rejected(self) -> None:
        review = {
            "schema_version": "3.0.0", "artifact_type": "phase3_review", "review_id": "review-1",
            "actor_assignment_sha256": "1" * 64,
            "reviewer_role": "independent_reviewer", "reviewer_id": "actor-a",
            "review_task_id": "task-review", "review_session_id": "session-review",
            "review_task_record_sha256": "2" * 64, "signed_at_utc": "2026-08-09T00:00:00Z",
            "reviewed_manifest_sha256": "3" * 64,
            "implementation_author_id": "actor-a", "classification_approver_id": "actor-b",
            "independence_declaration": "reviewer_is_not_implementation_author_or_classification_approver",
            "reviewed_paths": ["evidence.json"], "blocking_findings": 0, "status": "PASS",
        }
        with self.assertRaisesRegex(ValueError, "reviewer identity conflicts"):
            validate_payload(ROOT, "review", review)


if __name__ == "__main__":
    unittest.main()
