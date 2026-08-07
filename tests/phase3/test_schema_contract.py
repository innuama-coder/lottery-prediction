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


if __name__ == "__main__":
    unittest.main()
