from __future__ import annotations

import json
import io
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
ARTIFACTS = REPO / "artifacts" / "phase-0"
PYTHON = Path(sys.executable)
sys.path.insert(0, str(SCRIPTS))

import p0_07_clean_worker as worker_module  # noqa: E402
import p0_07_replay_driver as driver_module  # noqa: E402
import p0_07_stage1_consumer as consumer_module  # noqa: E402
from p0_07_closeout import prepare  # noqa: E402
from phase0lib import canonical_json_bytes, load_json, schemas_manifest_sha256, sha256_file  # noqa: E402
from verify_phase0 import VERIFIER_FILES  # noqa: E402


AFTER_CUTOFF = datetime(2026, 8, 15, 17, 0, 1, tzinfo=timezone.utc)
REVIEW_NOW = datetime(2026, 8, 15, 17, 10, 1, tzinfo=timezone.utc)


def complete_entries(plan):  # noqa: ANN001
    entries = []
    for request in plan["requests"]:
        network = request["execution_policy"] == "network_attempted"
        entries.append({
            "schema_version": "1.1.0", "artifact_type": "soak_log_entry",
            "request_id": request["request_id"], "game": request["game"],
            "planned_at_utc": request["planned_at_utc"], "started_at_utc": request["planned_at_utc"],
            "completed_at_utc": request["planned_at_utc"], "source_slot": request["source_slot"],
            "source_id": request["source_id"], "scheduled_issue_id": request["scheduled_issue_id"],
            "request_schedule_sha256": plan["request_schedule_sha256"],
            "execution_disposition": request["execution_policy"], "attempts": 1 if network else 0,
            "network_used": network, "clock_check_at_utc": request["planned_at_utc"] if network else None,
            "clock_offset_seconds": 0 if network else None,
            "result": "invalid" if network else request["execution_policy"],
            "classification_reason": "network_or_capture_failure:AcquisitionError" if network else (
                "source_approved_use_blocked" if request["execution_policy"] == "policy_blocked"
                else "source_compliance_hold_no_collection"
            ),
            "failure_injection": "none", "evidence_ref": None, "raw_payload_ref": None,
        })
    return entries


class ReplayRouteTests(unittest.TestCase):
    def test_standalone_consumer_derivation_order_and_tier_priority(self) -> None:
        self.assertEqual(consumer_module.derive_project_decision(["PASS_FULL", "PASS_FULL"]), "GO")
        self.assertEqual(consumer_module.derive_project_decision(["PASS_LIMITED", "STOP"]), "LIMITED_GO")
        self.assertEqual(consumer_module.derive_project_decision(["HOLD", "STOP"]), "HOLD")
        self.assertEqual(consumer_module.derive_project_decision(["STOP", "STOP"]), "STOP")

        def counts(corroborated: int, shared: int, primary: int):
            return [
                {"tier": "corroborated_official", "count": corroborated},
                {"tier": "shared_upstream", "count": shared},
                {"tier": "primary_only", "count": primary},
            ]

        self.assertEqual(consumer_module.derive_corroboration_tier(counts(0, 0, 0)), "none")
        self.assertEqual(consumer_module.derive_corroboration_tier(counts(1, 1, 1)), "primary_only")
        self.assertEqual(consumer_module.derive_corroboration_tier(counts(1, 1, 0)), "shared_upstream")
        self.assertEqual(consumer_module.derive_corroboration_tier(counts(1, 0, 0)), "corroborated_official")

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name)
        artifacts = repo / "artifacts" / "phase-0"
        shutil.copytree(ARTIFACTS, artifacts)
        repair_manifests = sorted(artifacts.glob("repair-manifest*.json"))
        if not repair_manifests:
            # During the short pre-create refreeze window, seed the isolated
            # fixture from the explicitly preserved immutable pending evidence.
            # Never restore a mutable root draft or create canonical bytes.
            preserved_name = "p0-20260801-c-after-superseded-hardcoded-validation.pending-manifest.json"
            preserved = artifacts / "batches" / preserved_name
            preserved_sidecar = preserved.with_suffix(preserved.suffix + ".sha256")
            destination = artifacts / "repair-manifest-p0-20260801-c-review-pending.json"
            if not preserved.is_file() or not preserved_sidecar.is_file():
                raise AssertionError("immutable superseded repair evidence is unavailable")
            shutil.copy2(preserved, destination)
            shutil.copy2(preserved_sidecar, destination.with_suffix(destination.suffix + ".sha256"))
            repair_manifests = [destination]
        if len(repair_manifests) != 1:
            raise AssertionError(f"fixture requires exactly one repair manifest, got {len(repair_manifests)}")
        shutil.copytree(SCRIPTS, repo / "scripts" / "phase0")
        docs = repo / "docs" / "roadmap"
        docs.mkdir(parents=True)
        shutil.copy2(REPO / "docs" / "roadmap" / "phase-0-acceptance-contract.json", docs / "phase-0-acceptance-contract.json")
        for name in ("p0-07-candidate", "p0-07-review-bundle", ".p0-07-replay-staging"):
            path = artifacts / name
            if path.exists():
                shutil.rmtree(path)
        plan = load_json(artifacts / "p0-06-runtime-plan.json")
        (artifacts / "soak-run-log.jsonl").write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in complete_entries(plan)))
        verification_path = artifacts / "verification-command.json"
        verification = load_json(verification_path)
        verification["command"] = driver_module.CANONICAL_COMMAND
        verification["full_replay_command"] = driver_module.CANONICAL_COMMAND
        verification["replay_command"] = driver_module.CANONICAL_COMMAND
        verification["launcher_path"] = driver_module.LAUNCHER_REF
        verification["finalize_command"] = "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/p0_07_finalize_launcher.ps1"
        verification["full_verify_command"] = "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/verify_phase0.ps1 --contract docs/roadmap/phase-0-acceptance-contract.json --artifacts artifacts/phase-0 --stage full"
        verification["finalize_launcher_path"] = "scripts/phase0/p0_07_finalize_launcher.ps1"
        verification["finalize_launcher_sha256"] = sha256_file(repo / "scripts/phase0/p0_07_finalize_launcher.ps1")
        verification["finalizer_path"] = "scripts/phase0/p0_07_finalize.py"
        verification["finalizer_sha256"] = sha256_file(repo / "scripts/phase0/p0_07_finalize.py")
        verification["interpreter_path"] = str(PYTHON)
        verification["interpreter_sha256"] = sha256_file(PYTHON)
        future_verifier_files = set(VERIFIER_FILES) | set(driver_module.FROZEN_TOOL_REFS) | {
            "scripts/phase0/p0_05_history.py", "scripts/phase0/p0_07_closeout.py",
            "scripts/phase0/p0_07_decision.py", "scripts/phase0/p0_07_handoff.py",
        }
        verification["verifier_file_hashes"] = [
            {"path": ref, "sha256": sha256_file(repo / ref)} for ref in sorted(future_verifier_files)
        ]
        verification["schema_hashes"] = [
            {"path": f"artifacts/phase-0/schemas/{path.name}", "sha256": sha256_file(path)}
            for path in sorted((artifacts / "schemas").glob("*.schema.json"))
        ]
        verification["schemas_manifest_sha256"] = schemas_manifest_sha256(artifacts / "schemas")
        verification_path.write_bytes(canonical_json_bytes(verification) + b"\n")
        (artifacts / "verification-command.json.sha256").write_text(sha256_file(verification_path) + "\n", encoding="ascii")
        prepare(repo, artifacts, artifacts / "p0-07-candidate", utcnow_fn=lambda: AFTER_CUTOFF)
        return temporary, repo, artifacts

    @staticmethod
    def patches(repo: Path, artifacts: Path):
        staging = artifacts / ".p0-07-replay-staging"
        return (
            mock.patch.multiple(
                driver_module, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate",
                STAGING=staging, OUTPUT=artifacts / "p0-07-review-bundle",
            ),
            mock.patch.multiple(
                worker_module, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate",
                STAGING=staging, WORKER=staging / "worker",
            ),
        )

    @staticmethod
    def success_runner(repo: Path, artifacts: Path):
        def run(argv, **kwargs):  # noqa: ANN001
            if argv[1] == driver_module.WORKER_REF:
                facts = worker_module.run_worker(utcnow_fn=lambda: REVIEW_NOW)
                stdout = canonical_json_bytes({
                    "status": "PASS",
                    "facts_sha256": sha256_file(artifacts / ".p0-07-replay-staging" / "worker" / "facts.json"),
                }) + b"\n"
                self_check = facts["artifact_type"] == "p0_07_clean_replay_facts"
                if not self_check:
                    raise AssertionError("worker test seam did not produce clean replay facts")
                return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")
            return subprocess.run(argv, **kwargs)
        return run

    def test_isolated_success_publishes_two_level_manifest_and_exact_fixture(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            driver_patch, worker_patch = self.patches(repo, artifacts)
            with driver_patch, worker_patch:
                report = driver_module.run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=self.success_runner(repo, artifacts))
                bundle = artifacts / "p0-07-review-bundle"
                self.assertEqual(report, bundle / "technical-replay-report.json")
                driver_module.validate_bundle(bundle)
                replay = load_json(report)
                self.assertNotIn("execution_receipt_ref", replay)
                root = load_json(bundle / "bundle-manifest.json")
                self.assertEqual([item["path"] for item in root["files"]], ["content-manifest.json", "execution-receipt.json"])
                execution = load_json(bundle / "execution-receipt.json")
                self.assertEqual(execution["canonical_command"], driver_module.CANONICAL_COMMAND)
                self.assertEqual(
                    execution["canonical_command_sha256"],
                    hashlib.sha256(driver_module.CANONICAL_COMMAND.encode("utf-8")).hexdigest(),
                )
                self.assertNotIn("outer_exit_code", execution)
                self.assertEqual([item["role"] for item in execution["processes"]], ["clean_replay_worker", "stage1_external_consumer"])
                self.assertTrue(all(item["argv"] == [str(PYTHON), item["script_ref"]] for item in execution["processes"]))
                self.assertTrue(all(
                    [entry["key"] for entry in item["environment"]]
                    == ["PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "PYTHONNOUSERSITE", "SystemRoot", "TEMP", "TMP", "TZ"]
                    for item in execution["processes"]
                ))
                self.assertEqual(
                    (bundle / "proposed-stage1-handoff-fixture.json").read_bytes(),
                    (bundle / "worker" / "proposed-stage1-handoff-fixture.json").read_bytes(),
                )

    def test_manifest_and_bound_content_tampering_fail_closed(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            driver_patch, worker_patch = self.patches(repo, artifacts)
            with driver_patch, worker_patch:
                driver_module.run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=self.success_runner(repo, artifacts))
                bundle = artifacts / "p0-07-review-bundle"
                cases = (
                    ("technical-replay-report.json", b"tamper", "content manifest"),
                    ("content-manifest.json", b" ", "root manifest"),
                    ("execution-receipt.json", b" ", "root manifest"),
                )
                for relative, suffix, expected in cases:
                    path = bundle / relative
                    original = path.read_bytes()
                    path.write_bytes(original + suffix)
                    with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, expected):
                        driver_module.validate_bundle(bundle)
                    path.write_bytes(original)
                execution_path = bundle / "execution-receipt.json"
                root_path = bundle / "bundle-manifest.json"
                original_execution = execution_path.read_bytes()
                original_root = root_path.read_bytes()
                execution = load_json(execution_path)
                execution["canonical_command_sha256"] = "0" * 64
                execution_path.write_bytes(canonical_json_bytes(execution) + b"\n")
                root = load_json(root_path)
                record = next(item for item in root["files"] if item["path"] == "execution-receipt.json")
                record.update({"size": execution_path.stat().st_size, "sha256": sha256_file(execution_path)})
                root_path.write_bytes(canonical_json_bytes(root) + b"\n")
                with self.assertRaisesRegex(ValueError, "canonical command binding"):
                    driver_module.validate_bundle(bundle)
                execution_path.write_bytes(original_execution)
                root_path.write_bytes(original_root)

                fixture = load_json(bundle / "proposed-stage1-handoff-fixture.json")
                fixture["project_decision"] = "GO" if fixture["project_decision"] != "GO" else "STOP"
                fixture_bytes = canonical_json_bytes(fixture) + b"\n"
                for path in (
                    bundle / "proposed-stage1-handoff-fixture.json",
                    bundle / "worker" / "proposed-stage1-handoff-fixture.json",
                ):
                    path.write_bytes(fixture_bytes)
                receipt = load_json(bundle / "p0-07-stage1-consumer-receipt.json")
                receipt.update({
                    "fixture_sha256": hashlib.sha256(canonical_json_bytes(fixture)).hexdigest(),
                    "consumed_fixture_file_bytes_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                    "project_decision": fixture["project_decision"],
                })
                receipt_bytes = canonical_json_bytes(receipt) + b"\n"
                for path in (
                    bundle / "p0-07-stage1-consumer-receipt.json",
                    bundle / "consumer" / "p0-07-stage1-consumer-receipt.json",
                ):
                    path.write_bytes(receipt_bytes)
                facts_path = bundle / "worker" / "facts.json"
                facts = load_json(facts_path)
                facts["proposed_handoff_file_bytes_sha256"] = sha256_file(bundle / "worker" / "proposed-stage1-handoff-fixture.json")
                facts_path.write_bytes(canonical_json_bytes(facts) + b"\n")
                worker_stdout_path = bundle / "process" / "clean_replay_worker.stdout"
                worker_stdout_path.write_bytes(canonical_json_bytes({"status": "PASS", "facts_sha256": sha256_file(facts_path)}) + b"\n")
                replay_path = bundle / "technical-replay-report.json"
                replay = load_json(replay_path)
                replay["proposed_handoff_file_bytes_sha256"] = sha256_file(bundle / "proposed-stage1-handoff-fixture.json")
                replay["stage1_consumer_receipt_sha256"] = sha256_file(bundle / "p0-07-stage1-consumer-receipt.json")
                replay_path.write_bytes(canonical_json_bytes(replay) + b"\n")
                content_path = bundle / "content-manifest.json"
                content = load_json(content_path)
                content_files = [
                    path for path in bundle.rglob("*")
                    if path.is_file() and path.relative_to(bundle).as_posix() not in driver_module.META_FILES
                ]
                content["files"] = driver_module._file_records(content_files, bundle)
                content_path.write_bytes(canonical_json_bytes(content) + b"\n")
                execution = load_json(execution_path)
                worker_process = execution["processes"][0]
                worker_process.update({"stdout_size": worker_stdout_path.stat().st_size, "stdout_sha256": sha256_file(worker_stdout_path)})
                execution.update({
                    "content_manifest_sha256": sha256_file(content_path),
                    "technical_report_sha256": sha256_file(replay_path),
                    "stage1_consumer_receipt_sha256": sha256_file(bundle / "p0-07-stage1-consumer-receipt.json"),
                    "proposed_handoff_file_bytes_sha256": sha256_file(bundle / "proposed-stage1-handoff-fixture.json"),
                })
                execution_path.write_bytes(canonical_json_bytes(execution) + b"\n")
                root = load_json(root_path)
                root["files"] = driver_module._file_records([content_path, execution_path], bundle)
                root_path.write_bytes(canonical_json_bytes(root) + b"\n")
                with self.assertRaisesRegex(ValueError, "project decision differs"):
                    driver_module.validate_bundle(bundle)

    def test_worker_and_consumer_failure_leave_zero_canonical_output(self) -> None:
        for failed_role in ("worker", "consumer"):
            with self.subTest(failed_role=failed_role):
                temporary, repo, artifacts = self.fixture()
                with temporary:
                    driver_patch, worker_patch = self.patches(repo, artifacts)
                    success = self.success_runner(repo, artifacts)

                    def fail_runner(argv, **kwargs):  # noqa: ANN001
                        is_worker = argv[1] == driver_module.WORKER_REF
                        if (failed_role == "worker" and is_worker) or (failed_role == "consumer" and not is_worker):
                            return subprocess.CompletedProcess(argv, 9, stdout=b"", stderr=f"real-{failed_role}-stderr\n".encode())
                        return success(argv, **kwargs)

                    with driver_patch, worker_patch, self.assertRaises(driver_module.ChildProcessFailure) as raised:
                        driver_module.run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=fail_runner)
                    self.assertEqual(raised.exception.child_stderr, f"real-{failed_role}-stderr\n".encode())
                    self.assertFalse((artifacts / ".p0-07-replay-staging").exists())
                    self.assertFalse((artifacts / "p0-07-review-bundle").exists())

    def test_standalone_consumer_rejects_three_semantic_contradictions(self) -> None:
        temporary, repo, artifacts = self.fixture()
        with temporary:
            driver_patch, worker_patch = self.patches(repo, artifacts)
            with driver_patch, worker_patch:
                staging = artifacts / ".p0-07-replay-staging"
                staging.mkdir()
                worker_module.run_worker(utcnow_fn=lambda: REVIEW_NOW)
                fixture_path = staging / "worker" / "proposed-stage1-handoff-fixture.json"
                base = load_json(fixture_path)
                cases = (
                    ("project", lambda value: value.update({"project_decision": "GO" if value["project_decision"] != "GO" else "STOP"}), "project decision"),
                    ("coverage", lambda value: value["game_results"][0].update({"per_game_outcome": "PASS_FULL", "coverage_tier": "minimum_viable"}), "PASS_FULL requires target"),
                    ("corroboration", lambda value: value["game_results"][0].update({"corroboration_tier": "primary_only"}), "corroboration tier differs"),
                )
                consumer_script = repo / driver_module.CONSUMER_REF
                for label, mutate, expected in cases:
                    fixture = json.loads(json.dumps(base))
                    mutate(fixture)
                    fixture_path.write_bytes(canonical_json_bytes(fixture) + b"\n")
                    completed = subprocess.run([str(PYTHON), str(consumer_script)], cwd=repo, capture_output=True, check=False)
                    with self.subTest(label=label):
                        self.assertEqual(completed.returncode, 2)
                        self.assertIn(expected.encode(), completed.stderr)
                        self.assertFalse((staging / "consumer").exists())

    def test_driver_main_preserves_child_stderr_bytes(self) -> None:
        class CapturedStderr:
            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, value):  # noqa: ANN001
                self.buffer.write(value.encode("utf-8"))

        captured = CapturedStderr()
        failure = driver_module.ChildProcessFailure("worker failed", b"exact-child-stderr\r\n")
        with (
            mock.patch.object(driver_module, "run_driver", side_effect=failure),
            mock.patch.object(sys, "stderr", captured),
            mock.patch.object(sys, "argv", [driver_module.DRIVER_REF]),
        ):
            self.assertEqual(driver_module.main(), 2)
        self.assertTrue(captured.buffer.getvalue().startswith(b"exact-child-stderr\r\n"))

        hold_stderr = CapturedStderr()
        with (
            mock.patch.object(driver_module, "run_driver", side_effect=driver_module.CloseoutHold("cutoff not reached")),
            mock.patch.object(sys, "stderr", hold_stderr),
            mock.patch.object(sys, "argv", [driver_module.DRIVER_REF]),
        ):
            self.assertEqual(driver_module.main(), 1)
        self.assertIn(b'"status":"HOLD"', hold_stderr.buffer.getvalue())
        self.assertIn(b'"network_used":false', hold_stderr.buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
