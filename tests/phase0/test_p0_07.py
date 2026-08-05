from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
ARTIFACTS = REPO / "artifacts" / "phase-0"
sys.path.insert(0, str(SCRIPTS))

from p0_04_http import clock_check_from_json  # noqa: E402
from p0_04_pipeline import rebuild_captures  # noqa: E402
from p0_07_closeout import (  # noqa: E402
    CloseoutHold, build_parser, build_revision_report, main, prepare, review,
    validate_derived_manifest, validate_revision_report,
)
from phase0lib import ValidationError, canonical_json_bytes, load_json, load_jsonl, sha256_file, validate_schema_instance  # noqa: E402


AFTER_CUTOFF = datetime(2026, 8, 15, 17, 0, 1, tzinfo=timezone.utc)
REVIEW_NOW = datetime(2026, 8, 15, 17, 10, 1, tzinfo=timezone.utc)
BEFORE_CUTOFF = datetime(2026, 8, 15, 16, 59, 59, tzinfo=timezone.utc)


def complete_entries(plan):  # noqa: ANN001
    entries = []
    for request in plan["requests"]:
        policy = request["execution_policy"]
        network = policy == "network_attempted"
        entries.append({
            "schema_version": "1.1.0", "artifact_type": "soak_log_entry", "request_id": request["request_id"], "game": request["game"],
            "planned_at_utc": request["planned_at_utc"], "started_at_utc": request["planned_at_utc"], "completed_at_utc": request["planned_at_utc"],
            "source_slot": request["source_slot"], "source_id": request["source_id"], "scheduled_issue_id": request["scheduled_issue_id"], "request_schedule_sha256": plan["request_schedule_sha256"],
            "execution_disposition": policy, "attempts": 1 if network else 0, "network_used": network,
            "clock_check_at_utc": request["planned_at_utc"] if network else None, "clock_offset_seconds": 0 if network else None,
            "result": "invalid" if network else policy,
            "classification_reason": "network_or_capture_failure:AcquisitionError" if network else ("source_approved_use_blocked" if policy == "policy_blocked" else "source_compliance_hold_no_collection"),
            "failure_injection": "none", "evidence_ref": None, "raw_payload_ref": None,
        })
    return entries


class P007PrepareSliceTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name)
        artifacts = repo / "artifacts" / "phase-0"
        shutil.copytree(ARTIFACTS, artifacts)
        docs = repo / "docs" / "roadmap"; docs.mkdir(parents=True)
        shutil.copy2(REPO / "docs" / "roadmap" / "phase-0-acceptance-contract.json", docs / "phase-0-acceptance-contract.json")
        scripts = repo / "scripts" / "phase0"; scripts.mkdir(parents=True)
        frozen_tools = (
            "verify_phase0.ps1", "verify_phase0.py", "phase0lib.py", "hash_artifact.py",
            "p0_06_runner.py", "install_p0_06_scheduled_task.ps1", "p0_04_http.py",
            "p0_04_pipeline.py", "p0_04_parser.py", "p0_05_history.py", "p0_07_closeout.py",
            "p0_07_decision.py", "p0_07_handoff.py",
        )
        for name in frozen_tools:
            shutil.copy2(SCRIPTS / name, scripts / name)
        verification_path = artifacts / "verification-command.json"
        verification = load_json(verification_path)
        verification["verifier_file_hashes"] = [
            {"path": f"scripts/phase0/{name}", "sha256": sha256_file(scripts / name)}
            for name in frozen_tools
        ]
        verification["schema_hashes"] = [
            {
                "path": f"artifacts/phase-0/schemas/{path.name}",
                "sha256": sha256_file(path),
            }
            for path in sorted((artifacts / "schemas").glob("*.schema.json"))
        ]
        verification_path.write_bytes(canonical_json_bytes(verification) + b"\n")
        (artifacts / "verification-command.json.sha256").write_text(
            f"{sha256_file(verification_path)}  verification-command.json\n",
            encoding="utf-8",
        )
        plan = load_json(artifacts / "p0-06-runtime-plan.json")
        (artifacts / "soak-run-log.jsonl").write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in complete_entries(plan)))
        return temporary, repo, artifacts

    @staticmethod
    def state(root: Path):
        return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in sorted(root.rglob("*")) if path.is_file()}

    @staticmethod
    def bytes_state(root: Path):
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    def test_prepare_before_cutoff_is_zero_write(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "candidate"
            before = self.state(artifacts)
            with self.assertRaisesRegex(CloseoutHold, "before the frozen acceptance cutoff"):
                prepare(repo, artifacts, output, utcnow_fn=lambda: BEFORE_CUTOFF)
            self.assertFalse(output.exists())
            self.assertEqual(self.state(artifacts), before)

    def test_prepare_missing_request_is_zero_write(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            entries = load_jsonl(artifacts / "soak-run-log.jsonl")[:-1]
            (artifacts / "soak-run-log.jsonl").write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in entries))
            output = repo / "candidate"
            before = self.state(artifacts)
            with self.assertRaisesRegex(CloseoutHold, "each of the 24"):
                prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
            self.assertFalse(output.exists())
            self.assertEqual(self.state(artifacts), before)

    def test_prepare_rejects_prepopulated_candidate_without_mutation(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "candidate"; output.mkdir(); sentinel = output / "keep.txt"; sentinel.write_bytes(b"keep")
            before_inputs = self.state(artifacts); before_output = self.state(output)
            with self.assertRaisesRegex(CloseoutHold, "not empty"):
                prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
            self.assertEqual(self.state(artifacts), before_inputs)
            self.assertEqual(self.state(output), before_output)

    def test_prepare_rebuilds_only_schema_valid_non_terminal_derived_snapshot(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "candidate"
            before = self.state(artifacts)
            path = prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
            self.assertEqual(sorted(item.name for item in output.iterdir()), ["derived", "p0-07-derived-manifest.json", "p0-07-input-manifest.json"])
            value = load_json(path)
            validate_schema_instance(value, load_json(artifacts / "schemas" / "p0-07-gate-inputs.schema.json"))
            manifest_path = output / "p0-07-input-manifest.json"
            manifest = load_json(manifest_path)
            validate_schema_instance(manifest, load_json(artifacts / "schemas" / "p0-07-input-manifest.schema.json"))
            self.assertEqual(manifest["recorded_at_utc"], "2026-08-15T17:00:01Z")
            self.assertEqual(value["recorded_at_utc"], "2026-08-15T17:00:01Z")
            manifest_paths = {item["path"] for item in manifest["files"]}
            self.assertIn("artifacts/phase-0/raw/p0-04-dlt-2026050.html", manifest_paths)
            self.assertIn("scripts/phase0/hash_artifact.py", manifest_paths)
            self.assertIn("scripts/phase0/install_p0_06_scheduled_task.ps1", manifest_paths)
            self.assertIn("scripts/phase0/p0_05_history.py", manifest_paths)
            self.assertIn("scripts/phase0/p0_07_closeout.py", manifest_paths)
            self.assertNotIn("artifacts/phase-0/normalized/p0-04-dlt-2026050.json", manifest_paths)
            self.assertNotIn("artifacts/phase-0/coverage-report.json", manifest_paths)
            self.assertEqual(value["input_manifest_sha256"], sha256_file(manifest_path))
            self.assertEqual({item["game"]: item["soak_request_count"] for item in value["games"]}, {"dlt": 12, "ssq": 12})
            derived = output / "derived"
            coverage = load_json(derived / "coverage-report.json")
            validate_schema_instance(coverage, load_json(artifacts / "schemas" / "coverage-report.schema.json"))
            self.assertEqual(coverage["generated_at_utc"], "2026-08-15T17:00:01Z")
            self.assertEqual((derived / "reconciliation.jsonl").read_bytes(), b"")
            self.assertTrue((derived / "parsed" / "p0-04-dlt-2026050.json").is_file())
            self.assertTrue((derived / "normalized" / "p0-04-dlt-2026050.json").is_file())
            revision = load_json(derived / "revision-report.json")
            normalized = load_json(derived / "normalized" / "p0-04-dlt-2026050.json")
            validate_revision_report(
                revision,
                schema=load_json(artifacts / "schemas" / "revision-report.schema.json"),
                contract=load_json(repo / "docs" / "roadmap" / "phase-0-acceptance-contract.json"),
                catalog=load_json(artifacts / "source-catalog.json"),
                evidence=load_jsonl(artifacts / "evidence-manifest.jsonl"),
                normalized_by_evidence={"p0-04-dlt-2026050": normalized},
                input_manifest_sha256=sha256_file(manifest_path),
            )
            self.assertEqual(revision["events"][0]["from_status"], "unavailable")
            self.assertEqual(revision["events"][0]["to_status"], "unverified")
            self.assertEqual(revision["observed_correction_replays"], [])
            derived_manifest = load_json(output / "p0-07-derived-manifest.json")
            derived_schema = load_json(artifacts / "schemas" / "p0-07-derived-manifest.schema.json")
            validate_derived_manifest(output, derived_manifest, input_manifest_sha256=sha256_file(manifest_path), schema=derived_schema)
            derived_paths = {item["path"] for item in derived_manifest["files"]}
            self.assertEqual(derived_paths, {
                "derived/coverage-report.json", "derived/p0-07-gate-inputs.json",
                "derived/parsed/p0-04-dlt-2026050.json", "derived/normalized/p0-04-dlt-2026050.json",
                "derived/reconciliation.jsonl", "derived/revision-report.json",
            })
            self.assertEqual(self.state(artifacts), before)
            self.assertFalse(any((output / name).exists() for name in ("revision-report.json", "replay-report.json", "reviewer-attestation.json", "stage1-handoff-fixture.json", "phase-0-acceptance-report.md")))

    def test_prepare_rejects_incomplete_or_false_frozen_tool_inventory_without_writes(self) -> None:
        for mutation, expected in (
            (lambda items: [item for item in items if item["path"] != "scripts/phase0/p0_07_closeout.py"], "does not freeze required closeout tools"),
            (lambda items: [{**item, "sha256": "0" * 64} if item["path"] == "scripts/phase0/hash_artifact.py" else item for item in items], "frozen hash mismatch"),
        ):
            with self.subTest(expected=expected):
                temporary, repo, artifacts = self.fixture()
                with temporary:
                    verification_path = artifacts / "verification-command.json"
                    verification = load_json(verification_path)
                    verification["verifier_file_hashes"] = mutation(verification["verifier_file_hashes"])
                    verification_path.write_bytes(canonical_json_bytes(verification) + b"\n")
                    output = repo / "candidate"
                    before = self.state(artifacts)
                    with self.assertRaisesRegex(CloseoutHold, expected):
                        prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
                    self.assertFalse(output.exists())
                    self.assertEqual(self.state(artifacts), before)

    def test_double_prepare_manifests_are_byte_identical_and_existing_derived_is_untrusted(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            first = repo / "candidate-a"; second = repo / "candidate-b"
            prepare(repo, artifacts, first, utcnow_fn=lambda: AFTER_CUTOFF)
            shutil.rmtree(artifacts / "parsed")
            shutil.rmtree(artifacts / "normalized")
            (artifacts / "coverage-report.json").write_bytes(b"untrusted-existing-derived")
            (artifacts / "reconciliation.jsonl").write_bytes(b"untrusted-existing-derived")
            prepare(repo, artifacts, second, utcnow_fn=lambda: AFTER_CUTOFF)
            self.assertEqual(self.bytes_state(first), self.bytes_state(second))

    def test_different_snapshot_times_change_only_time_bound_envelopes(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            first = repo / "candidate-a"; second = repo / "candidate-b"
            prepare(repo, artifacts, first, utcnow_fn=lambda: AFTER_CUTOFF)
            later = datetime(2026, 8, 15, 17, 5, 1, tzinfo=timezone.utc)
            prepare(repo, artifacts, second, utcnow_fn=lambda: later)
            stable_paths = (
                "derived/parsed/p0-04-dlt-2026050.json",
                "derived/normalized/p0-04-dlt-2026050.json",
                "derived/reconciliation.jsonl",
            )
            for relative in stable_paths:
                self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())
            first_manifest = load_json(first / "p0-07-derived-manifest.json")
            second_manifest = load_json(second / "p0-07-derived-manifest.json")
            self.assertEqual(
                {item["path"] for item in first_manifest["files"]},
                {item["path"] for item in second_manifest["files"]},
            )
            self.assertNotEqual((first / "p0-07-input-manifest.json").read_bytes(), (second / "p0-07-input-manifest.json").read_bytes())

    def test_prepare_e2e_rejects_raw_or_evidence_tamper_without_candidate(self) -> None:
        for tamper, expected in (
            (lambda artifacts: (artifacts / "raw" / "p0-04-dlt-2026050.html").write_bytes((artifacts / "raw" / "p0-04-dlt-2026050.html").read_bytes() + b"tamper"), "raw payload SHA-256"),
            (lambda artifacts: (artifacts / "evidence-manifest.jsonl").write_bytes((artifacts / "evidence-manifest.jsonl").read_bytes().replace(b'"stored_payload_sha256":"89', b'"stored_payload_sha256":"09', 1)), "raw payload SHA-256"),
        ):
            with self.subTest(expected=expected):
                temporary, repo, artifacts = self.fixture()
                with temporary:
                    tamper(artifacts)
                    output = repo / "candidate"
                    with self.assertRaisesRegex(ValidationError, expected):
                        prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
                    self.assertFalse(output.exists())

    def test_derived_manifest_rejects_omission_and_self_inclusion(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "candidate"
            prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
            schema = load_json(artifacts / "schemas" / "p0-07-derived-manifest.schema.json")
            input_hash = sha256_file(output / "p0-07-input-manifest.json")
            manifest = load_json(output / "p0-07-derived-manifest.json")
            omitted = json.loads(json.dumps(manifest))
            omitted["files"] = omitted["files"][:-1]
            with self.assertRaisesRegex(CloseoutHold, "exactly enumerate"):
                validate_derived_manifest(output, omitted, input_manifest_sha256=input_hash, schema=schema)
            self_including = json.loads(json.dumps(manifest))
            self_including["files"].append({"path": "derived/p0-07-derived-manifest.json", "size": 0, "sha256": "0" * 64})
            with self.assertRaisesRegex(CloseoutHold, "must not contain itself"):
                validate_derived_manifest(output, self_including, input_manifest_sha256=input_hash, schema=schema)

    def test_revision_report_semantic_tampering_fails_closed(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "candidate"
            prepare(repo, artifacts, output, utcnow_fn=lambda: AFTER_CUTOFF)
            base = load_json(output / "derived" / "revision-report.json")
            normalized = load_json(output / "derived" / "normalized" / "p0-04-dlt-2026050.json")
            arguments = {
                "schema": load_json(artifacts / "schemas" / "revision-report.schema.json"),
                "contract": load_json(repo / "docs" / "roadmap" / "phase-0-acceptance-contract.json"),
                "catalog": load_json(artifacts / "source-catalog.json"),
                "evidence": load_jsonl(artifacts / "evidence-manifest.jsonl"),
                "normalized_by_evidence": {"p0-04-dlt-2026050": normalized},
                "input_manifest_sha256": sha256_file(output / "p0-07-input-manifest.json"),
            }

            def clone():
                return json.loads(json.dumps(base))

            duplicate = clone(); duplicate["events"].append(json.loads(json.dumps(duplicate["events"][0])))
            illegal = clone(); illegal["events"][0]["to_status"] = "verified"
            supersedes = clone(); supersedes["events"][0]["supersedes"] = "evt-" + "0" * 24
            evidence_ref = clone(); evidence_ref["events"][0]["evidence_ref"] = "missing-evidence"
            reconstructed = clone(); reconstructed["synthetic_correction_replay"]["reconstructed"] = False
            replay_hash = clone(); replay_hash["synthetic_correction_replay"]["before_hash"] = "0" * 64
            history_hash = clone(); history_hash["history_sha256"] = "0" * 64
            current = clone(); current["current_view"][0]["status"] = "verified"
            unrelated = clone(); unrelated["synthetic_correction_replay"]["evidence_refs"] = ["p0-04-dlt-2026050"]
            cases = (
                (duplicate, "event IDs must be unique"),
                (illegal, "contract-forbidden"),
                (supersedes, "chain must begin"),
                (evidence_ref, "dangling evidence_ref"),
                (reconstructed, "expected const True"),
                (replay_hash, "hash is not reconstructable"),
                (history_hash, "history hash mismatch"),
                (current, "current view does not reconstruct"),
                (unrelated, "exact controlled embedded reference"),
            )
            for report, expected in cases:
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(ValidationError, expected):
                        validate_revision_report(report, **arguments)

    def test_revision_builder_uses_legal_multistep_paths_for_terminal_evidence_statuses(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            base = load_jsonl(artifacts / "evidence-manifest.jsonl")[0]
            invalid = json.loads(json.dumps(base)); invalid.update({
                "evidence_id": "p0-04-dlt-2026049", "issue_id": "2026049", "status": "invalid",
                "retrieved_at": "2026-08-01T04:04:49Z",
            })
            verified = json.loads(json.dumps(base)); verified.update({
                "evidence_id": "p0-04-dlt-2026048", "issue_id": "2026048", "status": "verified",
                "retrieved_at": "2026-08-01T04:05:49Z",
            })
            evidence = [invalid, verified]
            contract = load_json(repo / "docs" / "roadmap" / "phase-0-acceptance-contract.json")
            catalog = load_json(artifacts / "source-catalog.json")
            report = build_revision_report(
                contract, catalog, evidence, {}, generated_at=AFTER_CUTOFF, input_manifest_sha256="2" * 64,
            )
            by_record = {}
            for event in report["events"]:
                by_record.setdefault(event["record_id"], []).append((event["from_status"], event["to_status"]))
            self.assertEqual(by_record["dlt-2026049"], [("unavailable", "unverified"), ("unverified", "invalid")])
            self.assertEqual(by_record["dlt-2026048"], [("unavailable", "unverified"), ("unverified", "verified")])
            validate_revision_report(
                report, schema=load_json(artifacts / "schemas" / "revision-report.schema.json"),
                contract=contract, catalog=catalog, evidence=evidence, normalized_by_evidence={},
                input_manifest_sha256="2" * 64,
            )

    def test_revision_report_requires_every_detected_official_correction(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            evidence = load_jsonl(artifacts / "evidence-manifest.jsonl")
            corrected_evidence = json.loads(json.dumps(evidence[0]))
            corrected_evidence["evidence_id"] = "p0-04-dlt-2026050-correction"
            corrected_evidence["retrieved_at"] = "2026-08-01T05:03:49Z"
            evidence.append(corrected_evidence)
            original = load_json(artifacts / "normalized" / "p0-04-dlt-2026050.json")
            corrected = json.loads(json.dumps(original))
            corrected["record_id"] = "dlt-2026050-p0-04-correction"
            corrected["front_numbers"] = ["06", "10", "14", "23", "34"]
            corrected["supersedes"] = original["record_id"]
            normalized = {
                evidence[0]["evidence_id"]: original,
                corrected_evidence["evidence_id"]: corrected,
            }
            contract = load_json(repo / "docs" / "roadmap" / "phase-0-acceptance-contract.json")
            catalog = load_json(artifacts / "source-catalog.json")
            report = build_revision_report(
                contract, catalog, evidence, normalized,
                generated_at=AFTER_CUTOFF, input_manifest_sha256="1" * 64,
            )
            self.assertEqual(len(report["observed_correction_replays"]), 1)
            report["observed_correction_replays"] = []
            with self.assertRaisesRegex(CloseoutHold, "incomplete or non-deterministic"):
                validate_revision_report(
                    report,
                    schema=load_json(artifacts / "schemas" / "revision-report.schema.json"),
                    contract=contract, catalog=catalog, evidence=evidence,
                    normalized_by_evidence=normalized, input_manifest_sha256="1" * 64,
                )

    def test_raw_rebuild_ignores_existing_derived_and_rejects_raw_or_evidence_tamper(self) -> None:
        temporary, _repo, artifacts = self.fixture()
        with temporary:
            clock = clock_check_from_json(load_json(artifacts / "clock-check-p0-04.json"))
            (artifacts / "normalized" / "p0-04-dlt-2026050.json").write_bytes(b"not-an-input")
            rebuilt = artifacts.parent / "rebuilt"
            paths = rebuild_captures(artifacts, clock, rebuilt)
            self.assertEqual({path.parent.name for path in paths}, {"parsed", "normalized"})
            raw = artifacts / "raw" / "p0-04-dlt-2026050.html"; original_raw = raw.read_bytes(); raw.write_bytes(original_raw + b"tamper")
            with self.assertRaisesRegex(ValidationError, "raw payload SHA-256"):
                rebuild_captures(artifacts, clock, artifacts.parent / "raw-tamper")
            raw.write_bytes(original_raw)
            manifest = load_jsonl(artifacts / "evidence-manifest.jsonl"); manifest[0]["stored_payload_sha256"] = "0" * 64
            (artifacts / "evidence-manifest.jsonl").write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in manifest))
            with self.assertRaises(ValidationError):
                rebuild_captures(artifacts, clock, artifacts.parent / "evidence-tamper")

    def test_legacy_review_cli_is_removed(self) -> None:
        parser = build_parser()
        for arguments in (
            ["review"],
            ["review", "--candidate", "candidate"],
            ["review", "--output", "review-output"],
            ["review", "--as-of", "2026-08-15T17:10:01Z"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(arguments)

    def test_removed_direct_review_is_zero_write(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "review-output"
            with self.assertRaisesRegex(CloseoutHold, "direct review is removed"):
                review(repo, artifacts, repo / "candidate", output)
            self.assertFalse(output.exists())

    def test_removed_review_rejects_even_prepopulated_output_without_mutation(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            output = repo / "nonempty"; output.mkdir(); sentinel = output / "keep"; sentinel.write_bytes(b"keep")
            with self.assertRaisesRegex(CloseoutHold, "direct review is removed"):
                review(repo, artifacts, repo / "candidate", output)
            self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_pre_attestation_replay_schema_excludes_outer_claims(self) -> None:
        properties = load_json(ARTIFACTS / "schemas" / "technical-replay-report.schema.json")["properties"]
        for forbidden in ("reviewer_id", "executed_command", "exit_code", "execution_receipt_ref"):
            self.assertNotIn(forbidden, properties)

    def test_production_cli_has_no_clock_override(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["prepare", "--output", "candidate", "--as-of", "2026-08-16T00:00:00Z"])


if __name__ == "__main__":
    unittest.main()
