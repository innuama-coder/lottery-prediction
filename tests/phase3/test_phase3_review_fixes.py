"""Targeted regressions for the Phase 3 post-merge review fixes (R01).

These exercises the fixed verification logic directly with synthetic inputs:

* W10 ``validate_independent_reconstruction`` rejects every malformed variant.
* W11 E2E attribution never fabricates a terminal from a generic exception and
  only passes a negative case when its registered guard is observed.
* W13 manifest closure rejects a listed file tampered after acceptance and any
  unexpected post-manifest extra.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.formal import (
    E2E_CASE_GUARDS,
    E2E_POSITIVE_TERMINALS,
    _build_e2e_receipt,
    _classify_e2e_negative_outcome,
    validate_independent_reconstruction,
    verify_final_manifest_closure,
)
from lottery_research.phase3.schema import validate_payload
from lottery_research.phase3.serialization import canonical_json_bytes, canonical_sha256, sha256_file, write_new_json

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _valid_reconstruction(release_id: str, forecast_sha: str, metric_sha: str) -> dict:
    return {
        "schema_version": "3.0.0", "artifact_type": "phase3_independent_model_reconstruction",
        "release_id": release_id, "status": "PASS",
        "implementation": "standalone reference estimator with independent elementary-symmetric DP; no phase3 model/evaluator imports",
        "outer_target_count": 300, "model_target_count": 600,
        "fold_reconstruction_coverage": 1.0, "lambda_reconstruction_coverage": 1.0,
        "weight_reconstruction_coverage": 1.0, "actual_probability_match_rate": 1.0,
        "forecast_index_sha256": forecast_sha, "metric_index_sha256": metric_sha,
        "guarded_label_unlock": {"status": "PASS"}, "mismatches": [], "blocking_findings": 0,
    }


class IndependentReconstructionTests(unittest.TestCase):
    def _release_with_indices(self) -> tuple[Path, str, str]:
        tmp = Path(tempfile.mkdtemp())
        release = tmp / "P3-RECON-I01"
        (release / "runs").mkdir(parents=True)
        forecast_index = release / "runs/forecast-index.jsonl"
        metric_index = release / "runs/metric-index.jsonl"
        forecast_index.write_bytes(canonical_json_bytes({"path": "x", "sha256": "a"}))
        metric_index.write_bytes(canonical_json_bytes({"path": "y", "sha256": "b"}))
        return release, sha256_file(forecast_index), sha256_file(metric_index)

    def test_valid_reconstruction_is_accepted(self) -> None:
        release, fsha, msha = self._release_with_indices()
        _write(release / "review/independent-model-reconstruction.json", _valid_reconstruction(release.name, fsha, msha))
        artifact = validate_independent_reconstruction(ROOT, release)
        self.assertEqual(artifact["status"], "PASS")

    def _expect_reject(self, mutator) -> None:
        release, fsha, msha = self._release_with_indices()
        payload = _valid_reconstruction(release.name, fsha, msha)
        mutator(payload, release)
        _write(release / "review/independent-model-reconstruction.json", payload)
        with self.assertRaises(ValueError):
            validate_independent_reconstruction(ROOT, release)

    def test_missing_artifact_is_rejected(self) -> None:
        release, _f, _m = self._release_with_indices()
        with self.assertRaises(ValueError):
            validate_independent_reconstruction(ROOT, release)

    def test_hold_fail_status_is_rejected(self) -> None:
        for bad_status, blocking in (("HOLD", 1), ("FAIL", 0)):
            with self.subTest(status=bad_status):
                self._expect_reject(lambda p, r, s=bad_status, b=blocking: (p.__setitem__("status", s), p.__setitem__("blocking_findings", b)))

    def test_malformed_artifact_is_rejected(self) -> None:
        release, _f, _m = self._release_with_indices()
        path = release / "review/independent-model-reconstruction.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            validate_independent_reconstruction(ROOT, release)

    def test_wrong_release_identity_is_rejected(self) -> None:
        self._expect_reject(lambda p, r: p.__setitem__("release_id", "wrong-release"))

    def test_incomplete_coverage_is_rejected(self) -> None:
        self._expect_reject(lambda p, r: (p.__setitem__("model_target_count", 599), p.__setitem__("fold_reconstruction_coverage", 599 / 600)))

    def test_hash_inconsistent_artifact_is_rejected(self) -> None:
        self._expect_reject(lambda p, r: p.__setitem__("forecast_index_sha256", "0" * 64))


class E2EAttributionTests(unittest.TestCase):
    def test_staging_copy_retains_authorizing_work_item_receipts(self) -> None:
        from lottery_research.phase3.formal import _copy_e2e_staging_release

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source, destination = root / "release", root / "staging"
            (source / "work-items/W07").mkdir(parents=True)
            (source / "work-items/W07/receipt.json").write_text("{}\n", encoding="utf-8")
            (source / "e2e").mkdir()
            (source / "e2e/partial.json").write_text("{}\n", encoding="utf-8")
            _copy_e2e_staging_release(source, destination)
            self.assertTrue((destination / "work-items/W07/receipt.json").is_file())
            self.assertFalse((destination / "e2e").exists())

    def test_guard_registry_covers_every_registered_negative_case(self) -> None:
        import json as _json
        registry = _json.loads((ROOT / "config/phase3/e2e-registry.json").read_text(encoding="utf-8"))
        negative = {case["id"] for case in registry["cases"] if case["expected_terminal"] not in E2E_POSITIVE_TERMINALS}
        self.assertEqual(set(E2E_CASE_GUARDS), negative)

    def test_right_guard_passes_and_wrong_guard_fails_for_every_case(self) -> None:
        for case_id, (guard, terminal) in E2E_CASE_GUARDS.items():
            with self.subTest(case_id=case_id):
                right = _classify_e2e_negative_outcome(case_id, terminal, {"passed": False, "process_returncode": 5, "message": f"prefix {guard} suffix"})
                self.assertTrue(right["guard_reached"])
                self.assertEqual(right["status"], "PASS")
                self.assertEqual(right["actual_terminal"], terminal)
                self.assertEqual(right["actual_exit_code"], 5)
                wrong = _classify_e2e_negative_outcome(case_id, terminal, {"passed": False, "process_returncode": 5, "message": "an unrelated missing file or malformed JSON failure"})
                self.assertFalse(wrong["guard_reached"])
                self.assertEqual(wrong["status"], "FAIL")
                self.assertEqual(wrong["actual_terminal"], "WRONG_FAILURE_MODE")
                accepted = _classify_e2e_negative_outcome(case_id, terminal, {"passed": True, "process_returncode": 0, "message": None})
                self.assertEqual(accepted["status"], "FAIL")
                self.assertEqual(accepted["actual_terminal"], "ACCEPTED_UNEXPECTEDLY")

    def test_unrelated_exception_message_does_not_match_any_registered_guard(self) -> None:
        unrelated = "FileNotFoundError: /tmp/unrelated-missing-file"
        for case_id, (guard, terminal) in E2E_CASE_GUARDS.items():
            with self.subTest(case_id=case_id):
                self.assertNotIn(guard, unrelated)

    def test_positive_terminals_record_zero_exit_code(self) -> None:
        for terminal in ("PASS", "PASS_NO_SHADOW_CANDIDATE", "PASS_INDETERMINATE"):
            with self.subTest(terminal=terminal):
                classification = {"expected_guard": None, "actual_guard": None, "guard_reached": True, "expected_exit_code": 0, "actual_exit_code": 0, "actual_terminal": terminal, "status": "PASS", "validator_exception_type": None}
                receipt = _build_e2e_receipt(identity="id", case_id="c", expected_terminal=terminal, classification=classification, execution_mode="positive", mutation={"type": "positive"}, command=["python3", "validate"], process_exit_code=0, wall_seconds=0.1)
                self.assertEqual(receipt["process_exit_code"], 0)
                self.assertEqual(receipt["actual_exit_code"], 0)
                validate_payload(ROOT, "e2e_receipt", receipt)

    def test_negative_receipt_is_schema_valid(self) -> None:
        classification = _classify_e2e_negative_outcome("E2E-P3-02-input-identity-tamper", "EVIDENCE_MISMATCH", {"passed": False, "process_returncode": 5, "message": "frozen input manifest mismatch"})
        receipt = _build_e2e_receipt(identity="id-e2e-p3-02", case_id="E2E-P3-02-input-identity-tamper", expected_terminal="EVIDENCE_MISMATCH", classification=classification, execution_mode="isolated_staging_mutation_then_production_bottom_up_validator_distinct_process", mutation={"case_id": "E2E-P3-02-input-identity-tamper"}, command=["python3", "-m", "lottery_research.phase3", "validate", "--scope", "final"], process_exit_code=5, wall_seconds=0.2)
        validate_payload(ROOT, "e2e_receipt", receipt)
        self.assertEqual(receipt["expected_guard"], "frozen input manifest mismatch")
        self.assertNotIn("validator_exception_type", receipt)

    def test_guard_tokens_are_stable_validator_messages(self) -> None:
        from lottery_research.phase3.formal import require_normalized
        with self.assertRaisesRegex(ValueError, "NORMALIZATION_REJECTED"):
            require_normalized(0.9)
        self.assertEqual(E2E_CASE_GUARDS["E2E-P3-07-non-normalized-probability"][0], "NORMALIZATION_REJECTED")
        # The external-field mutation reaches the forecast schema's additional
        # properties guard, whose message names the injected field verbatim.
        base = {"schema_version": "3.0.0", "artifact_type": "phase3_forecast", "release_id": "r", "run_id": "r", "game": "dlt", "target_issue": "t", "model_id": "M0", "prediction_locked_at": "2026-01-01T00:00:00Z", "training_cutoff": "s", "training_count": 50, "inner_target_issues": [], "distribution": {"front": {"size": 35, "cardinality": 5, "weights": [1.0] * 35, "inclusion_probabilities": [1.0] * 35}, "back": {"size": 12, "cardinality": 2, "weights": [1.0] * 12, "inclusion_probabilities": [1.0] * 12}, "selected_lambda": None, "partition_independence": True}, "normalization_sum": 1.0, "top_1000_role": "diagnostic_only", "top_1000_path": "p", "top_1000_sha256": "0" * 64, "label_read": False, "training_prefix_sha256": "0" * 64, "trainer_input_capability": "training_prefix_only_no_label_store", "trainer_pid": 1, "orchestrator_pid": 2}
        base["external_current_view_without_available_at"] = True
        with self.assertRaises(ValueError) as ctx:
            validate_payload(ROOT, "forecast", base)
        self.assertIn(E2E_CASE_GUARDS["E2E-P3-04-external-post-draw-leakage"][0], str(ctx.exception))


class ManifestClosureTests(unittest.TestCase):
    def _tree(self) -> tuple[Path, Path]:
        tmp = Path(tempfile.mkdtemp())
        release = tmp / "P3-MAN-I01"
        (release / "review").mkdir(parents=True)
        (release / "runs").mkdir(parents=True)
        write_new_json(release / "review/independent-model-reconstruction.json", {"status": "PASS", "release_id": release.name})
        write_new_json(release / "runs/forecast-index.jsonl", {"path": "forecasts/x.json"})
        return tmp, release

    def _manifest(self, release: Path, files: list[Path]) -> dict:
        rows = [{"path": p.relative_to(release).as_posix(), "role": "phase3_evidence", "sha256": sha256_file(p), "bytes": p.stat().st_size, "lines": len(p.read_bytes().splitlines())} for p in files]
        return {"schema_version": "3.0.0", "artifact_type": "phase3_explicit_evidence_manifest", "identity": f"{release.name}-final", "non_formal_synthetic_only": False, "files": rows, "inventory_sha256": canonical_sha256(rows)}

    def test_closure_accepts_consistent_manifest(self) -> None:
        _tmp, release = self._tree()
        listed = [release / "review/independent-model-reconstruction.json", release / "runs/forecast-index.jsonl"]
        manifest = self._manifest(release, listed)
        manifest_path = release / "manifest/final-evidence-manifest.json"
        write_new_json(manifest_path, manifest)
        result = verify_final_manifest_closure(ROOT, release, manifest_path)
        self.assertEqual(result["verified_file_count"], len(listed))

    def test_controller_attack_reconstruction_pass_to_hold_is_rejected(self) -> None:
        _tmp, release = self._tree()
        target = release / "review/independent-model-reconstruction.json"
        listed = [release / "review/independent-model-reconstruction.json", release / "runs/forecast-index.jsonl"]
        manifest = self._manifest(release, listed)
        manifest_path = release / "manifest/final-evidence-manifest.json"
        write_new_json(manifest_path, manifest)
        # Controller changes the listed reconstruction from PASS to HOLD after
        # acceptance while leaving the manifest untouched.
        target.unlink()
        write_new_json(target, {"status": "HOLD", "release_id": release.name})
        with self.assertRaisesRegex(ValueError, "listed file mismatch"):
            verify_final_manifest_closure(ROOT, release, manifest_path)

    def test_unexpected_post_manifest_extra_is_rejected(self) -> None:
        _tmp, release = self._tree()
        listed = [release / "review/independent-model-reconstruction.json", release / "runs/forecast-index.jsonl"]
        manifest = self._manifest(release, listed)
        manifest_path = release / "manifest/final-evidence-manifest.json"
        write_new_json(manifest_path, manifest)
        write_new_json(release / "runs/evil-unlisted.json", {"injected": True})
        with self.assertRaisesRegex(ValueError, "unexpected post-manifest extra"):
            verify_final_manifest_closure(ROOT, release, manifest_path)

    def test_allowed_post_manifest_extras_are_accepted(self) -> None:
        _tmp, release = self._tree()
        listed = [release / "review/independent-model-reconstruction.json", release / "runs/forecast-index.jsonl"]
        manifest = self._manifest(release, listed)
        manifest_path = release / "manifest/final-evidence-manifest.json"
        write_new_json(manifest_path, manifest)
        write_new_json(release / "acceptance/iteration-01/acceptance.json", {"status": "PASS"})
        write_new_json(release / "handoff-validation/iteration-01/handoff-validation.json", {"status": "PASS"})
        write_new_json(release / "work-items/W12/receipt.json", {"work_item": "W12"})
        write_new_json(release / "work-items/W13/receipt.json", {"work_item": "W13"})
        result = verify_final_manifest_closure(ROOT, release, manifest_path)
        self.assertEqual(result["unexpected_extra_count"], 0)


if __name__ == "__main__":
    unittest.main()
