from __future__ import annotations

import sys
import hashlib
import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "tests" / "phase0"))

import p0_07_finalize as finalizer  # noqa: E402
import p0_07_reviewer_attest as attester  # noqa: E402
import p0_07_reviewer_verify as reviewer  # noqa: E402
import verify_phase0  # noqa: E402
from phase0lib import canonical_json_bytes, load_json  # noqa: E402
import test_p0_07_replay_route as replay_route  # noqa: E402


ATTEST_NOW = datetime(2026, 8, 15, 17, 20, 1, tzinfo=timezone.utc)
REVIEW_NOW = replay_route.REVIEW_NOW


class TerminalRouteTests(unittest.TestCase):
    @staticmethod
    def _terminal_paths(artifacts: Path) -> list[Path]:
        return [
            artifacts / name
            for name in (
                "revision-report.json",
                "replay-report.json",
                "stage1-handoff-fixture.json",
                "machine-acceptance-decision.json",
                "phase-0-acceptance-report.md",
                "p0-07-terminal-manifest.json",
                "p0-07-terminal-manifest.json.sha256",
            )
        ]

    def test_isolated_minimal_terminal_success(self) -> None:
        helper = replay_route.ReplayRouteTests()
        temporary, repo, artifacts = helper.fixture()
        with temporary:
            driver_patch, worker_patch = helper.patches(repo, artifacts)
            bundle = artifacts / "p0-07-review-bundle"
            receipt = artifacts / "p0-07-reviewer-verification-receipt.json"
            attestation = artifacts / "reviewer-attestation.json"
            with (
                driver_patch,
                worker_patch,
                mock.patch.multiple(
                    reviewer,
                    REPO=repo,
                    ARTIFACTS=artifacts,
                    CANDIDATE=artifacts / "p0-07-candidate",
                    BUNDLE=bundle,
                    OUTPUT=receipt,
                ),
                mock.patch.multiple(
                    attester,
                    REPO=repo,
                    ARTIFACTS=artifacts,
                    BUNDLE=bundle,
                    RECEIPT=receipt,
                    OUTPUT=attestation,
                ),
                mock.patch.multiple(
                    finalizer,
                    REPO=repo,
                    ARTIFACTS=artifacts,
                    BUNDLE=bundle,
                    REVIEWER_RECEIPT=receipt,
                    ATTESTATION=attestation,
                    MANIFEST=artifacts / "p0-07-terminal-manifest.json",
                    SIDECAR=artifacts / "p0-07-terminal-manifest.json.sha256",
                ),
            ):
                helper_module = sys.modules["p0_07_replay_driver"]
                helper_module.run_driver(
                    utcnow_fn=lambda: REVIEW_NOW,
                    run_fn=helper.success_runner(repo, artifacts),
                )
                reviewer.verify(utcnow_fn=lambda: REVIEW_NOW)
                attester.attest(utcnow_fn=lambda: ATTEST_NOW)
                sidecar = finalizer.finalize()
                finalizer.verify_terminal(require_sidecar=True)

            self.assertTrue(sidecar.is_file())
            self.assertEqual(
                (artifacts / "stage1-handoff-fixture.json").read_bytes(),
                (bundle / "proposed-stage1-handoff-fixture.json").read_bytes(),
            )
            report = load_json(artifacts / "replay-report.json")
            self.assertTrue(all(len(result["gate_results"]) == 14 for result in report["per_game_results"]))

    def test_tampered_attestation_fails_before_any_terminal_publication(self) -> None:
        helper = replay_route.ReplayRouteTests()
        temporary, repo, artifacts = helper.fixture()
        with temporary:
            driver_patch, worker_patch = helper.patches(repo, artifacts)
            bundle = artifacts / "p0-07-review-bundle"
            receipt = artifacts / "p0-07-reviewer-verification-receipt.json"
            attestation = artifacts / "reviewer-attestation.json"
            with (
                driver_patch,
                worker_patch,
                mock.patch.multiple(reviewer, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate", BUNDLE=bundle, OUTPUT=receipt),
                mock.patch.multiple(attester, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, RECEIPT=receipt, OUTPUT=attestation),
                mock.patch.multiple(finalizer, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, REVIEWER_RECEIPT=receipt, ATTESTATION=attestation, MANIFEST=artifacts / "p0-07-terminal-manifest.json", SIDECAR=artifacts / "p0-07-terminal-manifest.json.sha256"),
            ):
                sys.modules["p0_07_replay_driver"].run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=helper.success_runner(repo, artifacts))
                reviewer.verify(utcnow_fn=lambda: REVIEW_NOW)
                attester.attest(utcnow_fn=lambda: ATTEST_NOW)
                value = load_json(attestation)
                value["reviewer_id"] = "forged-reviewer"
                attestation.write_bytes(canonical_json_bytes(value) + b"\n")
                with self.assertRaises(ValueError):
                    finalizer.finalize()
                self.assertFalse(any(path.exists() for path in self._terminal_paths(artifacts)))

    def test_semantic_tamper_rejected_even_with_rehashed_manifest_and_sidecar(self) -> None:
        helper = replay_route.ReplayRouteTests()
        temporary, repo, artifacts = helper.fixture()
        with temporary:
            driver_patch, worker_patch = helper.patches(repo, artifacts)
            bundle = artifacts / "p0-07-review-bundle"
            receipt = artifacts / "p0-07-reviewer-verification-receipt.json"
            attestation = artifacts / "reviewer-attestation.json"
            manifest_path = artifacts / "p0-07-terminal-manifest.json"
            sidecar = artifacts / "p0-07-terminal-manifest.json.sha256"
            with (
                driver_patch,
                worker_patch,
                mock.patch.multiple(reviewer, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate", BUNDLE=bundle, OUTPUT=receipt),
                mock.patch.multiple(attester, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, RECEIPT=receipt, OUTPUT=attestation),
                mock.patch.multiple(finalizer, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, REVIEWER_RECEIPT=receipt, ATTESTATION=attestation, MANIFEST=manifest_path, SIDECAR=sidecar),
            ):
                sys.modules["p0_07_replay_driver"].run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=helper.success_runner(repo, artifacts))
                reviewer.verify(utcnow_fn=lambda: REVIEW_NOW)
                attester.attest(utcnow_fn=lambda: ATTEST_NOW)
                finalizer.finalize()
                report_path = artifacts / "replay-report.json"
                report = load_json(report_path)
                report["per_game_results"][0]["gate_results"][-1]["reason"] = "forged handoff evidence"
                report_path.write_bytes(canonical_json_bytes(report) + b"\n")
                manifest = load_json(manifest_path)
                for item in manifest["files"]:
                    if item["path"] == report_path.name:
                        payload = report_path.read_bytes()
                        item["size"] = len(payload)
                        item["sha256"] = hashlib.sha256(payload).hexdigest()
                manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
                sidecar.write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="ascii")
                with self.assertRaisesRegex(ValueError, "pure expected bytes"):
                    finalizer.verify_terminal(require_sidecar=True)

    def test_one_byte_fixture_tamper_after_consumer_fails_before_receipt(self) -> None:
        helper = replay_route.ReplayRouteTests()
        temporary, repo, artifacts = helper.fixture()
        with temporary:
            driver_patch, worker_patch = helper.patches(repo, artifacts)
            bundle = artifacts / "p0-07-review-bundle"
            receipt = artifacts / "p0-07-reviewer-verification-receipt.json"
            with (
                driver_patch,
                worker_patch,
                mock.patch.multiple(reviewer, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate", BUNDLE=bundle, OUTPUT=receipt),
            ):
                sys.modules["p0_07_replay_driver"].run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=helper.success_runner(repo, artifacts))
                fixture = bundle / "proposed-stage1-handoff-fixture.json"
                fixture.write_bytes(fixture.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    reviewer.verify(utcnow_fn=lambda: REVIEW_NOW)
            self.assertFalse(receipt.exists())
            self.assertFalse(any(path.exists() for path in self._terminal_paths(artifacts)))

    def test_finalizer_failure_rolls_back_terminal_only(self) -> None:
        helper = replay_route.ReplayRouteTests()
        temporary, repo, artifacts = helper.fixture()
        with temporary:
            driver_patch, worker_patch = helper.patches(repo, artifacts)
            bundle = artifacts / "p0-07-review-bundle"
            receipt = artifacts / "p0-07-reviewer-verification-receipt.json"
            attestation = artifacts / "reviewer-attestation.json"
            with (
                driver_patch,
                worker_patch,
                mock.patch.multiple(reviewer, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate", BUNDLE=bundle, OUTPUT=receipt),
                mock.patch.multiple(attester, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, RECEIPT=receipt, OUTPUT=attestation),
                mock.patch.multiple(finalizer, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, REVIEWER_RECEIPT=receipt, ATTESTATION=attestation, MANIFEST=artifacts / "p0-07-terminal-manifest.json", SIDECAR=artifacts / "p0-07-terminal-manifest.json.sha256"),
            ):
                sys.modules["p0_07_replay_driver"].run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=helper.success_runner(repo, artifacts))
                reviewer.verify(utcnow_fn=lambda: REVIEW_NOW)
                attester.attest(utcnow_fn=lambda: ATTEST_NOW)
                with mock.patch.object(finalizer, "verify_terminal", side_effect=ValueError("injected post-publication failure")):
                    with self.assertRaisesRegex(ValueError, "injected"):
                        finalizer.finalize()
                self.assertFalse(any(path.exists() for path in self._terminal_paths(artifacts)))
                self.assertTrue(receipt.is_file())
                self.assertTrue(attestation.is_file())

    def test_full_entrypoint_reaches_terminal_with_evaluation_time_seam(self) -> None:
        helper = replay_route.ReplayRouteTests()
        temporary, repo, artifacts = helper.fixture()
        with temporary:
            driver_patch, worker_patch = helper.patches(repo, artifacts)
            bundle = artifacts / "p0-07-review-bundle"
            receipt = artifacts / "p0-07-reviewer-verification-receipt.json"
            attestation = artifacts / "reviewer-attestation.json"
            manifest_path = artifacts / "p0-07-terminal-manifest.json"
            sidecar = artifacts / "p0-07-terminal-manifest.json.sha256"
            with (
                driver_patch,
                worker_patch,
                mock.patch.multiple(reviewer, REPO=repo, ARTIFACTS=artifacts, CANDIDATE=artifacts / "p0-07-candidate", BUNDLE=bundle, OUTPUT=receipt),
                mock.patch.multiple(attester, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, RECEIPT=receipt, OUTPUT=attestation),
                mock.patch.multiple(finalizer, REPO=repo, ARTIFACTS=artifacts, BUNDLE=bundle, REVIEWER_RECEIPT=receipt, ATTESTATION=attestation, MANIFEST=manifest_path, SIDECAR=sidecar),
            ):
                sys.modules["p0_07_replay_driver"].run_driver(utcnow_fn=lambda: REVIEW_NOW, run_fn=helper.success_runner(repo, artifacts))
                reviewer.verify(utcnow_fn=lambda: REVIEW_NOW)
                attester.attest(utcnow_fn=lambda: ATTEST_NOW)
                finalizer.finalize()

            command = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                "scripts/phase0/verify_phase0.ps1", "--contract",
                "docs/roadmap/phase-0-acceptance-contract.json", "--artifacts",
                "artifacts/phase-0", "--stage", "full",
            ]
            passed = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)
            self.assertNotEqual(passed.returncode, 0)
            self.assertIn("before the frozen acceptance cutoff", passed.stderr)

            original_p006 = verify_phase0.verify_p0_06_semantics

            def run_full() -> tuple[int, str, str]:
                stdout, stderr = io.StringIO(), io.StringIO()

                def verify_p006_at_cutoff(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                    return original_p006(*args, verified_at_utc=REVIEW_NOW, **kwargs)

                with (
                    mock.patch.object(verify_phase0.Path, "cwd", return_value=repo),
                    mock.patch.object(verify_phase0, "verify_p0_06_semantics", side_effect=verify_p006_at_cutoff),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    code = verify_phase0.main([
                        "--contract", str(repo / "docs" / "roadmap" / "phase-0-acceptance-contract.json"),
                        "--artifacts", str(artifacts), "--stage", "full",
                    ])
                return code, stdout.getvalue(), stderr.getvalue()

            code, stdout, stderr = run_full()
            self.assertEqual(code, 0, stderr or stdout)
            self.assertIn('"status":"PASS"', stdout)

            marker_bytes = sidecar.read_bytes()
            sidecar.unlink()
            code, _, stderr = run_full()
            self.assertNotEqual(code, 0)
            self.assertIn("terminal commit marker", stderr)
            sidecar.write_bytes(marker_bytes)

            report_path = artifacts / "replay-report.json"
            report = load_json(report_path)
            report["per_game_results"][0]["gate_results"][-1]["reason"] = "forged handoff evidence"
            report_path.write_bytes(canonical_json_bytes(report) + b"\n")
            manifest = load_json(manifest_path)
            for item in manifest["files"]:
                if item["path"] == report_path.name:
                    payload = report_path.read_bytes()
                    item["size"] = len(payload)
                    item["sha256"] = hashlib.sha256(payload).hexdigest()
            manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
            sidecar.write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="ascii")
            code, _, stderr = run_full()
            self.assertNotEqual(code, 0)
            self.assertIn('"status":"FAIL"', stderr)
            self.assertIn("pure expected bytes", stderr)


if __name__ == "__main__":
    unittest.main()
