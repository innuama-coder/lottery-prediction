from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
sys.path.insert(0, str(SCRIPTS))

from phase0lib import ValidationError, load_json, validate_schema_instance  # noqa: E402
from verify_phase0 import verify_field_contract, verify_rule_bundles, verify_source_catalog  # noqa: E402


class NormalizedRecordV11FailureInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(REPO / "artifacts/phase-0/schemas/normalized-records.schema.json")

    def record(self) -> dict[str, object]:
        return {
            "schema_version": "1.1.0", "artifact_type": "normalized_record", "record_id": "dlt/2026001/test",
            "game": "dlt", "issue_id": "2026001", "front_numbers": ["01", "02", "03", "04", "05"], "back_numbers": ["01", "02"],
            "draw_date_local": "2026-01-01", "draw_at": None, "page_published_at": None, "http_date": None,
            "first_seen_at": "2026-01-01T14:00:00Z", "retrieved_at": "2026-01-01T14:00:00Z", "corrected_at": None,
            "available_at": None, "number_space_version": "DLT_NS_5OF35_2OF12_V1",
            "draw_process_version": "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "prize_rule_version": "DLT_PRIZE_2019_9TIER",
            "active_promotion_ids": [], "status": "unverified", "corroboration_tier": None,
            "evidence_refs": ["EV-TEST"], "supersedes": None,
        }

    def test_nonverified_unknown_times_are_representable(self) -> None:
        validate_schema_instance(self.record(), self.schema)

    def test_verified_requires_availability_and_corroboration(self) -> None:
        record = self.record()
        record["status"] = "verified"
        with self.assertRaises(ValidationError):
            validate_schema_instance(record, self.schema)

    def test_nonverified_statuses_reject_nonnull_corroboration(self) -> None:
        for status in ("unavailable", "unverified", "conflicted", "invalid"):
            with self.subTest(status=status):
                record = self.record()
                record["status"] = status
                record["corroboration_tier"] = "shared_upstream"
                with self.assertRaises(ValidationError):
                    validate_schema_instance(record, self.schema)
        verified = self.record()
        verified.update(status="verified", available_at="2026-01-01T14:00:00Z", corroboration_tier="shared_upstream")
        validate_schema_instance(verified, self.schema)

    def test_draw_date_is_required_and_must_be_real(self) -> None:
        record = self.record()
        record["draw_date_local"] = "2026-02-30"
        with self.assertRaisesRegex(ValidationError, "real YYYY-MM-DD"):
            validate_schema_instance(record, self.schema)


class BatchBSemanticFailureInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scope = load_json(REPO / "artifacts/phase-0/scope-freeze.json")
        cls.catalog = load_json(REPO / "artifacts/phase-0/source-catalog.json")
        cls.field_contract = load_json(REPO / "artifacts/phase-0/field-contract.json")
        cls.normalized_schema = load_json(REPO / "artifacts/phase-0/schemas/normalized-records.schema.json")
        cls.rules = load_json(REPO / "artifacts/phase-0/rule-bundles.json")

    def test_current_batch_b_semantics_pass(self) -> None:
        readiness = verify_source_catalog(self.catalog)
        self.assertTrue(readiness["dlt"]["acquisition_ready"])
        self.assertFalse(readiness["ssq"]["acquisition_ready"])
        self.assertEqual(readiness["ssq"]["policy_conclusion"], "hold")
        verify_field_contract(self.field_contract, self.normalized_schema)
        verify_rule_bundles(self.scope, self.rules)

    def test_blocked_http_cannot_masquerade_as_accessible(self) -> None:
        tampered = copy.deepcopy(self.catalog)
        tampered["games"][0]["authoritative_primary"]["observed_access"]["outcome"] = "accessible"
        with self.assertRaisesRegex(ValidationError, "HTTP 567"):
            verify_source_catalog(tampered)

    def test_hold_pending_source_cannot_be_scheduled(self) -> None:
        tampered = copy.deepcopy(self.catalog)
        source = tampered["games"][1]["official_corroborators"][0]
        source["approved_use"] = "scheduled_low_rate_fetch"
        with self.assertRaisesRegex(ValidationError, "restricted terms|robots status"):
            verify_source_catalog(tampered)

    def test_scheduled_source_with_unreachable_robots_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.catalog)
        source = tampered["games"][0]["official_corroborators"][0]
        source["compliance_evidence"]["robots_status"] = "unreachable"
        with self.assertRaisesRegex(ValidationError, "robots status"):
            verify_source_catalog(tampered)

    def test_declared_readiness_drift_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.catalog)
        tampered["operational_readiness"][1]["acquisition_ready"] = True
        with self.assertRaisesRegex(ValidationError, "declared operational readiness"):
            verify_source_catalog(tampered)

    def test_field_nullability_drift_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.field_contract)
        next(item for item in tampered["fields"] if item["name"] == "draw_at")["nullable"] = False
        with self.assertRaisesRegex(ValidationError, "nullable contradicts"):
            verify_field_contract(tampered, self.normalized_schema)

    def test_missing_frozen_mapping_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.rules)
        tampered["issue_mappings"].pop()
        with self.assertRaisesRegex(ValidationError, "coverage must equal frozen 700"):
            verify_rule_bundles(self.scope, tampered)

    def test_unknown_bundle_evidence_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.rules)
        tampered["bundles"][0]["evidence_refs"][0] = "EV-RULE-DLT-UNKNOWN"
        with self.assertRaisesRegex(ValidationError, "unknown evidence reference"):
            verify_rule_bundles(self.scope, tampered)

    def test_activity_state_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.rules)
        tampered["activity_ledger"][0]["active_for_next_issue"] = False
        with self.assertRaisesRegex(ValidationError, "active_for_next_issue contradicts"):
            verify_rule_bundles(self.scope, tampered)

    def test_fuyun_threshold_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.rules)
        promotion = next(item for item in tampered["promotion_registry"] if item["promotion_id"] == "SSQ_2026_FUYUN_SPECIAL")
        promotion["state_machine"]["transitions"][0]["threshold_yuan"] = 1400000000
        with self.assertRaisesRegex(ValidationError, "thresholds are invalid"):
            verify_rule_bundles(self.scope, tampered)

    def test_bundle_promotion_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.rules)
        target = next(item for item in tampered["bundles"] if item["bundle_id"] == "ssq-2026-new-fuyun-014-050")
        target["active_promotion_ids"] = []
        with self.assertRaisesRegex(ValidationError, "bundle promotions contradict"):
            verify_rule_bundles(self.scope, tampered)


if __name__ == "__main__":
    unittest.main()
