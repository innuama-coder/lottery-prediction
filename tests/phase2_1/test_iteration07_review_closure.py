from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase2_1 import BASELINE_SHA, RELEASE_ID
from lottery_research.phase2_1.serialization import canonical_json_bytes, identity, load_json
from lottery_research.phase2_1.workflow import (
    _freeze_staging_power_baseline,
    _input_identity,
    _run_e2e_in_place,
    accept,
    build_evidence_manifest,
    project_root,
    source_manifest,
    validate_final_bundle,
)


ROOT = project_root()
COMPLETE_REJECTED_HEAD_BUNDLE = ROOT / "artifacts/phase-2.1/P2.1-R00-60d02be4dbe9-i06"
REJECTED_BUNDLE_RELEASE_ID = "P2.1-R00-60d02be4dbe9-i06"
REJECTED_BUNDLE_BASELINE_SHA = "5e1aa705c2e0b9f33fb3ef2698e8af55301919dd"
NEGATIVE_E2E_IDS = (
    "E2E-P2.1-02-input-tamper",
    "E2E-P2.1-03-release-mismatch",
    "E2E-P2.1-05-wheelhouse-missing",
    "E2E-P2.1-06-preregistration-tamper",
    "E2E-P2.1-08-result-schema-rejection",
    "E2E-P2.1-09-recursive-hash-tamper",
)


def rebuild_mutable_closure(destination: Path) -> None:
    for relative in ("acceptance/manifest.json", "acceptance/acceptance.json"):
        path = destination / relative
        if path.exists():
            path.unlink()
    build_evidence_manifest(destination)
    accept(ROOT, destination)


def rebind_strings(value: object) -> object:
    if isinstance(value, dict):
        return {key: rebind_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rebind_strings(item) for item in value]
    if isinstance(value, str):
        return value.replace(REJECTED_BUNDLE_RELEASE_ID, RELEASE_ID).replace(REJECTED_BUNDLE_BASELINE_SHA, BASELINE_SHA)
    return value


def rebind_input_identity(value: object, anchor: dict[str, object]) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == "input_identity":
                value[key] = anchor
            else:
                rebind_input_identity(item, anchor)
    elif isinstance(value, list):
        for item in value:
            rebind_input_identity(item, anchor)


def copy_valid_staging_bundle(raw: str, *, task_results: Path | None = None) -> tuple[Path, dict[str, object]]:
    destination = Path(raw) / RELEASE_ID
    shutil.copytree(COMPLETE_REJECTED_HEAD_BUNDLE, destination)
    for path in destination.rglob("*.json"):
        path.write_bytes(canonical_json_bytes(rebind_strings(load_json(path))))
    contract_path = destination / "contracts/acceptance-contract.json"
    contract_path.write_bytes((ROOT / "docs/roadmap/phase-2.1-acceptance-contract.json").read_bytes())
    (destination / "contracts/preregistration.json").write_bytes((ROOT / "config/phase2_1/preregistration.json").read_bytes())
    fixture_inputs = ROOT / "tests/phase2_1/fixtures/i07"
    for name in ("iteration-07.md", "iteration-07-run-02-correction.md"):
        (destination / "inputs" / name).write_bytes((fixture_inputs / name).read_bytes())
    anchor = _input_identity(destination, load_json(contract_path))
    for path in destination.rglob("*.json"):
        value = load_json(path)
        rebind_input_identity(value, anchor)
        path.write_bytes(canonical_json_bytes(value))
    readiness_path = destination / "readiness/readiness.json"
    readiness = load_json(readiness_path)
    readiness["source_manifest"] = source_manifest(ROOT)
    readiness["evidence_return"]["sha256"] = hashlib.sha256(
        (destination / "readiness/evidence-return-canary.json").read_bytes()
    ).hexdigest()
    readiness["formal_history_scan"]["roots"][:2] = [
        (destination / "results").resolve().as_posix(),
        (ROOT / "artifacts/phase-2.1-protected-results" / RELEASE_ID).resolve().as_posix(),
    ]
    frozen_paths = {row["path"] for row in readiness["frozen_input_identities"]}
    frozen_paths.update({"inputs/iteration-07.md", "inputs/iteration-07-run-02-correction.md"})
    readiness["frozen_input_identities"] = [identity(destination, destination / path) for path in sorted(frozen_paths)]
    snapshot = readiness["formal_output_snapshot"]
    existing_paths = {row["path"] for row in snapshot["existing_files"]}
    existing_paths.update({"inputs/iteration-07.md", "inputs/iteration-07-run-02-correction.md"})
    snapshot["existing_files"] = [identity(destination, destination / path) for path in sorted(existing_paths)]
    snapshot["existing_inventory_sha256"] = hashlib.sha256(
        canonical_json_bytes(snapshot["existing_files"])
    ).hexdigest()
    snapshot["allowed_final_paths"] = sorted(set(snapshot["allowed_final_paths"]) | existing_paths)
    snapshot["allowed_final_paths_sha256"] = hashlib.sha256(
        canonical_json_bytes(snapshot["allowed_final_paths"])
    ).hexdigest()
    if task_results is not None:
        readiness["formal_history_scan"]["roots"][2] = task_results.resolve().as_posix()
    readiness_path.write_bytes(canonical_json_bytes(readiness))
    gate_path = destination / "gates/g0-g1.json"
    gate = load_json(gate_path)
    gate["readiness_identity"] = identity(destination, readiness_path)
    gate_path.write_bytes(canonical_json_bytes(gate))

    for relative, field in (
        ("reviews/independent-method-review.json", "reviewed_identities"),
        ("qualification/qualification.json", "input_identities"),
        ("results/historical-audit.json", "input_identities"),
        ("results/power.json", "input_identities"),
    ):
        path = destination / relative
        value = load_json(path)
        value[field] = [identity(destination, destination / row["path"]) for row in value[field]]
        path.write_bytes(canonical_json_bytes(value))
    replay_path = destination / "replay/replay.json"
    replay = load_json(replay_path)
    replay["source_power_identity"] = identity(destination, destination / "results/power.json")
    replay_path.write_bytes(canonical_json_bytes(replay))
    replay_review_path = destination / "reviews/independent-replay-review.json"
    replay_review = load_json(replay_review_path)
    replay_review["reviewed_identities"] = [
        identity(destination, destination / row["path"])
        for row in replay_review["reviewed_identities"]
    ]
    replay_review_path.write_bytes(canonical_json_bytes(replay_review))
    definitions = load_json(contract_path)["external_verification_commands"]
    external_receipts = []
    for definition in definitions:
        receipt_path = destination / "logs" / f"external-{definition['order']:02d}.json"
        receipt = load_json(receipt_path)
        receipt.update(
            command_id=definition["id"],
            order=definition["order"],
            command=definition["command"],
            working_directory_scope=definition["working_directory_scope"],
            working_directory=ROOT.resolve().as_posix(),
            offline_policy=definition["offline_policy"],
            expected_status=definition["expected_status"],
            expected_exit_code=definition["expected_exit_code"],
        )
        receipt_path.write_bytes(canonical_json_bytes(receipt))
        external_receipts.append(receipt)
    summary_path = destination / "logs/run-summary.json"
    summary = load_json(summary_path)
    summary["external_verification_commands"] = external_receipts
    summary_path.write_bytes(canonical_json_bytes(summary))
    negative_path = destination / "reviews/final-validator-negative-tests.json"
    negative = load_json(negative_path)
    seed = negative["cases"][0]
    negative["cases"] = [
        {**seed, "id": f"i07-fixture-negative-{index:02d}"}
        for index in range(1, 24)
    ]
    negative_path.write_bytes(canonical_json_bytes(negative))

    # A frozen-baseline negative validation happens before the formal
    # negative-suite command receipt is issued, so its registered count is 11.
    (destination / "logs/12-negative-suite.json").unlink()
    for case_path in (destination / "e2e").glob("E2E-*.json"):
        case_path.unlink()
    (destination / "e2e/registry.json").unlink()
    _run_e2e_in_place(destination, root=ROOT)
    rebuild_mutable_closure(destination)
    baseline = _freeze_staging_power_baseline(ROOT, destination)
    return destination, baseline


def forged_pass_receipt(operation: str) -> dict[str, object]:
    return {
        "schema_version": "2.1.0",
        "artifact_type": "phase2_1_verification_receipt",
        "release_id": RELEASE_ID,
        "operation": operation,
        "status": "PASS",
        "terminal": "PASS",
        "exit_code": 0,
        "started_at_utc": "2026-08-06T00:00:00Z",
        "finished_at_utc": "2026-08-06T00:00:01Z",
        "error": None,
    }


def forge_negative_e2e_as_zero_exit_pass(destination: Path, identifier: str) -> None:
    registry_path = destination / "e2e/registry.json"
    registry = load_json(registry_path)
    case = next(row for row in registry["cases"] if row["id"] == identifier)
    operation = case.get("evidence", {}).get("production_verification_receipt", {}).get(
        "operation", f"forged-{identifier}"
    )
    case.update(
        expected_terminal="PASS",
        observed_terminal="PASS",
        terminal="PASS",
        status="PASS",
        exit_code=0,
        evidence={"production_verification_receipt": forged_pass_receipt(operation)},
    )
    (destination / f"e2e/{identifier}.json").write_bytes(canonical_json_bytes(case))
    registry_path.write_bytes(canonical_json_bytes(registry))
    rebuild_mutable_closure(destination)


class Iteration07ReviewClosureTests(unittest.TestCase):
    def assert_valid_rejected_head_baseline(self, destination: Path, baseline: dict[str, object]) -> None:
        result = validate_final_bundle(ROOT, destination, frozen_power_baseline=baseline)
        self.assertEqual((result["status"], result["delivery_status"]), ("PASS", "GO"))

    def test_p1_a_rejects_reviewer_release_mismatch_pass_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination, baseline = copy_valid_staging_bundle(raw)
            self.assert_valid_rejected_head_baseline(destination, baseline)
            forge_negative_e2e_as_zero_exit_pass(destination, "E2E-P2.1-03-release-mismatch")

            with self.assertRaisesRegex(ValueError, "frozen canonical E2E contract mismatch"):
                validate_final_bundle(ROOT, destination, frozen_power_baseline=baseline)

    def test_p1_a_rejects_zero_exit_pass_forgery_for_every_other_negative_e2e(self) -> None:
        for identifier in (value for value in NEGATIVE_E2E_IDS if value != "E2E-P2.1-03-release-mismatch"):
            with self.subTest(identifier=identifier), tempfile.TemporaryDirectory() as raw:
                destination, baseline = copy_valid_staging_bundle(raw)
                self.assert_valid_rejected_head_baseline(destination, baseline)
                forge_negative_e2e_as_zero_exit_pass(destination, identifier)

                with self.assertRaisesRegex(ValueError, "frozen canonical E2E contract mismatch"):
                    validate_final_bundle(ROOT, destination, frozen_power_baseline=baseline)

    def test_p1_b_rejects_true_external_command_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination, baseline = copy_valid_staging_bundle(raw)
            self.assert_valid_rejected_head_baseline(destination, baseline)
            receipt_path = destination / "logs/external-01.json"
            receipt = load_json(receipt_path)
            receipt.update(
                command="true",
                stdout_summary="",
                stderr_summary="",
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
            )
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            summary_path = destination / "logs/run-summary.json"
            summary = load_json(summary_path)
            summary["external_verification_commands"][0] = receipt
            summary_path.write_bytes(canonical_json_bytes(summary))
            rebuild_mutable_closure(destination)

            with self.assertRaisesRegex(ValueError, "canonical external command contract mismatch"):
                validate_final_bundle(ROOT, destination, frozen_power_baseline=baseline)

    def test_p1_c_rejects_late_task_input_result_without_mutating_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            task_results = Path(raw) / "task-input/results"
            task_results.mkdir(parents=True)
            destination, baseline = copy_valid_staging_bundle(raw, task_results=task_results)
            self.assert_valid_rejected_head_baseline(destination, baseline)
            late = {
                "release_id": RELEASE_ID,
                "artifact_type": "phase2_1_power",
                "status": "PASS",
            }
            (task_results / "late-power.json").write_bytes(canonical_json_bytes(late))
            before = [
                identity(destination, path)
                for path in sorted(destination.rglob("*"))
                if path.is_file()
            ]

            with self.assertRaisesRegex(ValueError, "formal history changed after readiness"):
                validate_final_bundle(ROOT, destination, frozen_power_baseline=baseline)

            after = [
                identity(destination, path)
                for path in sorted(destination.rglob("*"))
                if path.is_file()
            ]
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
