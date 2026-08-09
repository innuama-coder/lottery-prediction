from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3 import pit_recovery as pr


ROOT = Path(__file__).resolve().parents[2]


class LegacyAvailabilityAssessmentTests(unittest.TestCase):
    """The old timestamp rule remains testable only for preserved legacy evidence."""

    def setUp(self) -> None:
        self.lock = "2025-04-02T18:00:00Z"
        self.draw = {"game": "dlt", "issue_id": "SYN001", "front_numbers": [1, 2, 3, 4, 5], "back_numbers": [6, 7]}

    def archived(self, *, basis: str = "independent_archive_capture_timestamp", available: str = "2025-04-01T00:00:00Z") -> dict:
        return {
            "schema_version": "3.0.0", "artifact_type": "phase3_pit_archived_publication",
            "game": "dlt", "issue_id": "SYN001", "front_numbers": [1, 2, 3, 4, 5], "back_numbers": [6, 7],
            "availability_basis": basis, "available_at_utc": available,
            "source_url": "https://archive.example/SYN001", "capture_timestamp": available,
            "content_sha256": hashlib.sha256(b"SYN001").hexdigest(),
        }

    def test_preserved_legacy_rule_still_rejects_forged_timestamp_evidence(self) -> None:
        entry = {"eligibility": "eligible", "evidence_method": "archived_publication",
                 "prediction_locked_at": self.lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "bound"}
        self.assertFalse(pr.assess_availability_entry(entry, self.archived(), self.draw)["blocking"])
        self.assertTrue(pr.assess_availability_entry(entry, None, self.draw)["blocking"])
        forbidden = self.archived(basis="retrieved_at")
        self.assertTrue(pr.assess_availability_entry(entry, forbidden, self.draw)["blocking"])

    def test_unknown_legacy_row_must_remain_fail_closed(self) -> None:
        row = {"eligibility": "unknown", "evidence_method": "none", "prediction_locked_at": None,
               "available_at_utc": None, "reason_code": "PIT_AVAILABILITY_UNPROVEN"}
        self.assertFalse(pr.assess_availability_entry(row, None, self.draw)["blocking"])
        row["available_at_utc"] = "2025-04-01T00:00:00Z"
        self.assertTrue(pr.assess_availability_entry(row, None, self.draw)["blocking"])


class SupersededPITBundleTests(unittest.TestCase):
    def test_preserved_bundle_remains_verifiable_as_historical_hold(self) -> None:
        bundle = ROOT / "artifacts/phase-3-pit" / pr.PIT_RELEASE_IDENTITY
        validation = pr.validate_pit_preparation_bundle(ROOT, bundle)
        self.assertEqual(validation["status"], "HOLD")
        self.assertEqual(validation["terminal"], pr.HOLD_TERMINAL)
        self.assertEqual(validation["metrics"]["eligible_feature_coverage"], 0.0)
        self.assertEqual(validation["metrics"]["formal_result_count"], 0)

    def test_new_legacy_pit_collection_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ValueError, "PIT_CONTRACT_SUPERSEDED"):
                pr.build_pit_preparation_bundle(ROOT, Path(raw) / "new-legacy-pit", "new-legacy-pit")

    def test_legacy_negative_tamper_matrix_still_discriminates(self) -> None:
        report = pr.run_negative_tamper_tests()
        self.assertTrue(report["summary"]["all_cases_passed"], report)
        positive = next(case for case in report["cases"] if case["case"].startswith("T8"))
        self.assertEqual(positive["actual"], "ACCEPTED")


class ForbiddenActionsTests(unittest.TestCase):
    def test_legacy_hold_never_authorizes_champion_promotion(self) -> None:
        self.assertIn("champion_promotion", pr.FORBIDDEN_ACTIONS)

    def test_external_feature_timestamp_bases_remain_strict(self) -> None:
        required = {"draw_date", "http_date", "retrieved_at", "first_seen_at", "current_page", "cms_publish_date", "scheduled_broadcast"}
        self.assertTrue(required.issubset(pr.FORBIDDEN_AVAILABILITY_BASES))


if __name__ == "__main__":
    unittest.main()
