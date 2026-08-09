"""W08 controlled-interruption resume regression.

Exercises the real ``run`` command end-to-end against a synthesized W07
authorization layer (see ``_release_fixture``). A controlled interruption
(``--stop-after-targets``) followed by ``--resume`` in a distinct process must
reach the same complete scientific result as an uninterrupted run, and wrong
identity, tampered checkpoint/ledger/artifact, or a duplicate resume must all
fail closed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.serialization import canonical_json_bytes, load_json, sha256_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/phase3"))
from _release_fixture import build_authorized_release  # noqa: E402

ACTOR_REL = "control/actor-assignments-formal.json"


def _run_cli(release: Path, identity: str, *extra: str) -> subprocess.CompletedProcess:
    runs = release / "runs"
    return subprocess.run(
        [sys.executable, "-m", "lottery_research.phase3", "run", "--identity", identity, "--output", str(runs),
         "--release-root", str(release), "--actor-assignments", str(release / ACTOR_REL), *extra],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=False,
    )


def _metric_keys(runs: Path) -> list[tuple]:
    rows = [json.loads(line) for line in (runs / "metric-index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted((r["game"], r["target_issue"], r["model_id"], r["actual_joint_probability"], r["relative_skill_vs_M0"]) for r in rows)


def _forecast_distribution_summary(runs: Path) -> list[tuple]:
    rows = [json.loads(line) for line in (runs / "forecast-index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    out = []
    for row in rows:
        forecast = load_json(runs / row["path"])
        dist = forecast["distribution"]
        out.append((row["game"], row["target_issue"], row["model_id"], tuple(dist["front"]["weights"]), tuple(dist["back"]["weights"]), dist["selected_lambda"]))
    return sorted(out)


class RunResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # One shared partial (interrupted) state for the fast tampering tests.
        cls._partial_tmp = tempfile.TemporaryDirectory()
        release = Path(cls._partial_tmp.name) / "P3-W08-partial"
        build_authorized_release(ROOT, release, readiness_identity="W08-shared-W07")
        completed = _run_cli(release, "W08-shared", "--stop-after-targets", "12")
        assert completed.returncode == 20, completed.stdout + completed.stderr
        cls.partial = release

    @classmethod
    def tearDownClass(cls) -> None:
        cls._partial_tmp.cleanup()

    def _copy_partial(self, tag: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clone = Path(tmp.name) / "P3-W08-clone"
        shutil.copytree(self.partial, clone)
        return clone

    def test_resume_completes_same_scientific_result_as_uninterrupted(self) -> None:
        # Uninterrupted reference run.
        with tempfile.TemporaryDirectory() as raw_b:
            release_b = Path(raw_b) / "P3-W08-full"
            build_authorized_release(ROOT, release_b, readiness_identity="W08-full-W07")
            self.assertEqual(_run_cli(release_b, "W08-full").returncode, 0)
            canonical_b = load_json(release_b / "runs/canonical-attempts.json")
            metrics_b = _metric_keys(release_b / "runs")
            dist_b = _forecast_distribution_summary(release_b / "runs")
        # Interrupted then resumed run in distinct processes.
        with tempfile.TemporaryDirectory() as raw_a:
            release_a = Path(raw_a) / "P3-W08-resume"
            build_authorized_release(ROOT, release_a, readiness_identity="W08-resume-W07")
            stop = _run_cli(release_a, "W08-resume", "--stop-after-targets", "12")
            self.assertEqual(stop.returncode, 20)
            stage = load_json(release_a / "runs/run-stage.json")
            self.assertFalse((release_a / "runs/run-summary.json").is_file())
            resume = _run_cli(release_a, "W08-resume", "--resume")
            self.assertEqual(resume.returncode, 0, resume.stdout + resume.stderr)
            summary_a = load_json(release_a / "runs/run-summary.json")
            canonical_a = load_json(release_a / "runs/canonical-attempts.json")
            metrics_a = _metric_keys(release_a / "runs")
            dist_a = _forecast_distribution_summary(release_a / "runs")
        # Same complete scientific result.
        self.assertEqual(canonical_a, canonical_b)
        self.assertEqual(metrics_a, metrics_b)
        self.assertEqual(dist_a, dist_b)
        self.assertEqual(summary_a["canonical_coverage"], 1.0)
        self.assertEqual(summary_a["outer_target_count"], 300)
        self.assertTrue(summary_a["resumed_run"])
        # Resume executed in a distinct process from the controlled interruption.
        self.assertNotEqual(stage["pid"], summary_a["run_pid"])

    def _expect_resume_fail(self, clone: Path, identity: str = "W08-shared") -> None:
        result = _run_cli(clone, identity, "--resume")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resume_rejects_wrong_identity(self) -> None:
        clone = self._copy_partial("wrong-id")
        self._expect_resume_fail(clone, identity="W08-different-identity")

    def test_resume_rejects_tampered_checkpoint(self) -> None:
        clone = self._copy_partial("checkpoint")
        checkpoint = clone / "runs/checkpoints/target-010.json"
        self.assertTrue(checkpoint.is_file())
        payload = load_json(checkpoint)
        payload["payload"]["completed_targets"] = 999
        checkpoint.unlink()
        checkpoint.write_bytes(canonical_json_bytes(payload))
        self._expect_resume_fail(clone)

    def test_resume_rejects_tampered_ledger(self) -> None:
        clone = self._copy_partial("ledger")
        ledger = clone / "runs/experiment-ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        # Drop the final succeeded event of a completed experiment so the ledger
        # is left with an unterminated attempt.
        ledger.unlink()
        with ledger.open("wb") as handle:
            for row in rows[:-1]:
                handle.write(canonical_json_bytes(row))
        self._expect_resume_fail(clone)

    def test_resume_rejects_tampered_completed_artifact(self) -> None:
        clone = self._copy_partial("artifact")
        forecast = next((clone / "runs/forecasts").rglob("M0.json"))
        forecast.unlink()
        forecast.write_bytes(canonical_json_bytes({"tampered": True}))
        self._expect_resume_fail(clone)

    def test_resume_rejects_duplicate_completion(self) -> None:
        clone = self._copy_partial("duplicate")
        (clone / "runs/run-summary.json").write_bytes(canonical_json_bytes({"status": "PASS"}))
        self._expect_resume_fail(clone)


if __name__ == "__main__":
    unittest.main()
