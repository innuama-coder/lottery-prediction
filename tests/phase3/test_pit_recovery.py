from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3 import pit_recovery as pr
from lottery_research.phase3.prerun_contract import _data_time_contract, _preregistration
from lottery_research.phase3.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PHASE3 = ROOT / "config" / "phase3"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _config_shas() -> dict[str, str]:
    return {p.name: pr.sha256(p) for p in CONFIG_PHASE3.glob("*.json")}


def _reseed_manifest(bundle: Path, identity: str) -> None:
    """Recompute and rewrite the bundle manifest after a deliberate mutation."""
    manifest = pr._bundle_manifest(bundle, identity)
    _write_json(bundle / "manifest.json", manifest)


def _rebind_contracts(bundle: Path) -> None:
    """Re-bind data-time-contract and preregistration to a mutated ledger."""
    manifest_sha = pr.sha256(bundle / "input-manifest.json")
    ledger_sha = pr.sha256(bundle / "availability-ledger.json")
    _write_json(bundle / "data-time-contract.json", _data_time_contract(manifest_sha, ledger_sha))
    contract_sha = pr.sha256(bundle / "data-time-contract.json")
    _write_json(bundle / "preregistration.json", _preregistration(manifest_sha, ledger_sha, contract_sha))


class AvailabilityAssessmentTests(unittest.TestCase):
    """Unit-level tests for the pure PIT binding rule."""

    def setUp(self) -> None:
        self.lock = "2025-04-02T18:00:00Z"
        self.draw = {"game": "dlt", "issue_id": "SYN001", "front_numbers": [1, 2, 3, 4, 5], "back_numbers": [6, 7]}

    def _archived(self, basis: str = "independent_archive_capture_timestamp", available: str = "2025-04-01T00:00:00Z", numbers=None) -> dict:
        front = numbers if numbers is not None else [1, 2, 3, 4, 5]
        return {
            "schema_version": "3.0.0", "artifact_type": "phase3_pit_archived_publication",
            "game": "dlt", "issue_id": "SYN001", "front_numbers": front, "back_numbers": [6, 7],
            "availability_basis": basis, "available_at_utc": available,
            "source_url": "https://archive.example/SYN001", "capture_timestamp": available,
            "content_sha256": hashlib.sha256(b"SYN001").hexdigest(),
        }

    def test_unknown_row_must_fail_closed(self) -> None:
        good = {"eligibility": "unknown", "evidence_method": "none", "prediction_locked_at": None,
                "available_at_utc": None, "reason_code": "PIT_AVAILABILITY_UNPROVEN"}
        self.assertFalse(pr.assess_availability_entry(good, None, self.draw)["blocking"])
        for bad in (
            {"eligibility": "unknown", "evidence_method": "archived_publication", "prediction_locked_at": None, "available_at_utc": None, "reason_code": "PIT_AVAILABILITY_UNPROVEN"},
            {"eligibility": "unknown", "evidence_method": "none", "prediction_locked_at": None, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "PIT_AVAILABILITY_UNPROVEN"},
            {"eligibility": "unknown", "evidence_method": "none", "prediction_locked_at": None, "available_at_utc": None, "reason_code": ""},
        ):
            self.assertTrue(pr.assess_availability_entry(bad, None, self.draw)["blocking"])

    def test_eligible_row_requires_archived_publication_binding(self) -> None:
        entry = {"eligibility": "eligible", "evidence_method": "archived_publication",
                 "prediction_locked_at": self.lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "bound"}
        # no original -> rejected
        self.assertTrue(pr.assess_availability_entry(entry, None, self.draw)["blocking"])
        # genuine binding -> accepted
        accepted = pr.assess_availability_entry(entry, self._archived(), self.draw)
        self.assertFalse(accepted["blocking"])
        self.assertTrue(accepted["counts_as_eligible"])

    def test_forbidden_basis_rejected(self) -> None:
        for basis in ("draw_date", "http_date", "retrieved_at", "first_seen_at", "current_page", "cms_publish_date", "scheduled_broadcast"):
            entry = {"eligibility": "eligible", "evidence_method": "archived_publication",
                     "prediction_locked_at": self.lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"}
            self.assertTrue(pr.assess_availability_entry(entry, self._archived(basis=basis), self.draw)["blocking"], basis)

    def test_number_binding_mismatch_rejected(self) -> None:
        entry = {"eligibility": "eligible", "evidence_method": "archived_publication",
                 "prediction_locked_at": self.lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"}
        self.assertTrue(pr.assess_availability_entry(entry, self._archived(numbers=[9, 9, 9, 9, 9]), self.draw)["blocking"])

    def test_ordering_violation_rejected(self) -> None:
        entry = {"eligibility": "eligible", "evidence_method": "archived_publication",
                 "prediction_locked_at": self.lock, "available_at_utc": "2025-04-03T00:00:00Z", "reason_code": "x"}
        self.assertTrue(pr.assess_availability_entry(entry, self._archived(available="2025-04-03T00:00:00Z"), self.draw)["blocking"])

    def test_allowed_basis_is_the_only_acceptable_one(self) -> None:
        entry = {"eligibility": "eligible", "evidence_method": "archived_publication",
                 "prediction_locked_at": self.lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"}
        self.assertFalse(pr.assess_availability_entry(entry, self._archived(basis="independent_archive_capture_timestamp"), self.draw)["blocking"])
        self.assertTrue(pr.assess_availability_entry(entry, self._archived(basis="something_unrecognized"), self.draw)["blocking"])


class NegativeTamperMatrixTests(unittest.TestCase):
    def test_all_tamper_cases_behave_as_expected(self) -> None:
        report = pr.run_negative_tamper_tests()
        self.assertTrue(report["summary"]["all_cases_passed"], report)
        self.assertEqual(report["summary"]["case_count"], len(report["cases"]))
        # The positive control must genuinely be accepted, proving the gate discriminates.
        positive = next(c for c in report["cases"] if c["case"].startswith("T8"))
        self.assertEqual(positive["actual"], "ACCEPTED")


class BundleBuildAndValidateTests(unittest.TestCase):
    def test_build_produces_truthful_hold_with_zero_formal_results(self) -> None:
        before = _config_shas()
        with tempfile.TemporaryDirectory() as raw:
            bundle = Path(raw) / pr.PIT_RELEASE_IDENTITY
            receipt = pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)
            self.assertEqual(receipt["status"], "HOLD")
            self.assertEqual(receipt["terminal"], pr.HOLD_TERMINAL)
            self.assertEqual(receipt["exit_code"], 20)
            self.assertFalse(receipt["formal_run_authorized"])
            self.assertEqual(receipt["formal_result_count"], 0)
            self.assertEqual(receipt["delivery_state"], "HOLD")
            self.assertEqual(receipt["acceptance_verdict"], "BLOCKED")
            self.assertTrue(receipt["evidence_only"])
            self.assertTrue(receipt["delivery_verified"])
            # Independent recompute must agree.
            validation = pr.validate_pit_preparation_bundle(ROOT, bundle)
            self.assertEqual(validation["metrics"]["eligible_feature_coverage"], 0.0)
            self.assertEqual(validation["metrics"]["formal_result_count"], 0)
            self.assertTrue(all(validation["binding_checks"].values()) or "bundle_manifest_closure" in validation["binding_checks"])
            self.assertEqual(validation["binding_checks"]["bundle_manifest_closure"]["file_count"], 8)
            # Evidence collection must preserve the negative finding, not silently drop it.
            attempt = json.loads((bundle / "evidence-collection" / "collection-attempt.json").read_text())
            self.assertEqual(attempt["outcome"][:13], "no eligible a")
        # The candidate contracts in config/phase3 must be byte-for-byte unchanged.
        self.assertEqual(before, _config_shas())

    def test_preparation_status_recomputes_before_summarizing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            bundle = work / pr.PIT_RELEASE_IDENTITY
            pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)
            status = work / "phase3-pit-preparation-status.json"
            prepared = pr.write_preparation_status(ROOT, bundle, status, identity=pr.PIT_RELEASE_IDENTITY)
            self.assertEqual(prepared["terminal"], pr.HOLD_TERMINAL)
            self.assertEqual(prepared["eligible_feature_coverage"], 0.0)
            validation = json.loads((bundle / "pit-validation.json").read_text())
            validation["metrics"]["eligible_feature_coverage"] = 1.0
            _write_json(bundle / "pit-validation.json", validation)
            with self.assertRaises(ValueError):
                pr.write_preparation_status(ROOT, bundle, work / "tampered-status.json", identity=pr.PIT_RELEASE_IDENTITY)

    def test_identity_reuse_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle = Path(raw) / pr.PIT_RELEASE_IDENTITY
            pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)
            with self.assertRaises(FileExistsError):
                pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)

    def test_forged_eligible_row_without_original_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle = Path(raw) / pr.PIT_RELEASE_IDENTITY
            pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)
            ledger_path = bundle / "availability-ledger.json"
            ledger = json.loads(ledger_path.read_text())
            ledger["entries"][0].update({
                "eligibility": "eligible", "evidence_method": "archived_publication",
                "prediction_locked_at": "2025-04-02T18:00:00Z", "available_at_utc": "2025-04-01T00:00:00Z",
                "reason_code": "forged",
            })
            _write_json(ledger_path, ledger)
            _rebind_contracts(bundle)
            _reseed_manifest(bundle, pr.PIT_RELEASE_IDENTITY)
            with self.assertRaises(ValueError):
                pr.validate_pit_preparation_bundle(ROOT, bundle)

    def test_manifest_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle = Path(raw) / pr.PIT_RELEASE_IDENTITY
            pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)
            # Corrupt a listed evidence file without updating the manifest.
            target = bundle / "collection-attempt.json"
            # The manifest lists evidence-collection/collection-attempt.json.
            attempt_path = bundle / "evidence-collection" / "collection-attempt.json"
            original = attempt_path.read_text()
            attempt_path.write_bytes(canonical_json_bytes(json.loads(original) | {"tampered": True}))
            with self.assertRaises(ValueError):
                pr.validate_pit_preparation_bundle(ROOT, bundle)

    def test_one_genuine_archived_original_is_accepted_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle = Path(raw) / pr.PIT_RELEASE_IDENTITY
            pr.build_pit_preparation_bundle(ROOT, bundle, pr.PIT_RELEASE_IDENTITY)
            draw = json.loads(next(line for line in (ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl").read_text().splitlines() if '"dlt"' in line and '"2025035"' in line))
            issue = draw["issue_id"]
            archived = {
                "schema_version": "3.0.0", "artifact_type": "phase3_pit_archived_publication",
                "game": "dlt", "issue_id": issue,
                "front_numbers": draw["front_numbers"], "back_numbers": draw["back_numbers"],
                "availability_basis": "independent_archive_capture_timestamp",
                "available_at_utc": "2025-01-01T00:00:00Z",
                "source_url": "https://archive.example/synthetic/" + issue,
                "capture_timestamp": "2025-01-01T00:00:00Z",
                "content_sha256": hashlib.sha256((issue + str(draw["front_numbers"]) + str(draw["back_numbers"])).encode()).hexdigest(),
            }
            _write_json(pr._archived_original_path(bundle, "dlt", issue), archived)
            ledger_path = bundle / "availability-ledger.json"
            ledger = json.loads(ledger_path.read_text())
            for entry in ledger["entries"]:
                if entry["game"] == "dlt" and entry["target_issue"] == issue:
                    entry.update({"eligibility": "eligible", "evidence_method": "archived_publication",
                                  "prediction_locked_at": "2025-06-01T00:00:00Z",
                                  "available_at_utc": "2025-01-01T00:00:00Z", "reason_code": "archived_publication_bound"})
            _write_json(ledger_path, ledger)
            _rebind_contracts(bundle)
            _reseed_manifest(bundle, pr.PIT_RELEASE_IDENTITY)
            validation = pr.validate_pit_preparation_bundle(ROOT, bundle)
            self.assertEqual(validation["metrics"]["blocking_findings"], 0)
            self.assertEqual(validation["metrics"]["eligible_rows"], 1)
            self.assertGreater(validation["metrics"]["eligible_feature_coverage"], 0.0)
            # One eligible row of 400 still leaves overall coverage below 1.0 -> HOLD, not READY.
            self.assertEqual(validation["status"], "HOLD")


class ForbiddenActionsTests(unittest.TestCase):
    def test_champion_promotion_remains_blocked(self) -> None:
        self.assertIn("champion_promotion", pr.FORBIDDEN_ACTIONS)

    def test_forbidden_bases_cover_all_disallowed_sources(self) -> None:
        required = {"draw_date", "http_date", "retrieved_at", "first_seen_at", "current_page", "cms_publish_date", "scheduled_broadcast"}
        self.assertTrue(required.issubset(pr.FORBIDDEN_AVAILABILITY_BASES))


if __name__ == "__main__":
    unittest.main()
