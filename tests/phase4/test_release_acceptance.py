from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DRAW = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"


class ReleaseAcceptanceTests(unittest.TestCase):
    def run_command(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args], cwd=ROOT, env={"PYTHONPATH": "src"},
            text=True, capture_output=True, check=False,
        )

    def test_current_contract_validator_rejects_prohibited_paths(self) -> None:
        command = self.run_command("scripts/phase4/validate_real_model_contracts.py")
        self.assertEqual(command.returncode, 0, command.stderr + command.stdout)
        result = json.loads(command.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["negative_case_count"], 21)

    def test_from_scratch_dual_game_release_and_independent_replay(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as raw:
            release = Path(raw) / "P4-UNIT-D11"
            source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            built = self.run_command(
                "scripts/phase4/build_real_model_release.py", "--release", release.name,
                "--phase1-draws", str(DRAW), "--output", str(release),
                "--source-commit", source_commit,
            )
            self.assertEqual(built.returncode, 0, built.stderr + built.stdout)
            replay = self.run_command(
                "scripts/phase4_independent/replay_real_model_release.py",
                "--release", str(release), "--draws", str(DRAW),
                "--output", str(release / "replay/replay-report.json"),
            )
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            report = json.loads(replay.stdout)
            self.assertEqual(report["match_rate"], 1.0)
            self.assertEqual(report["mutation_detection_rate"], 1.0)
            self.assertEqual(report["product_core_import_count"], 0)
            for game in ("ssq", "dlt"):
                forecast = next((release / "forecasts" / game).glob("*/forecast.json"))
                value = json.loads(forecast.read_text())
                self.assertEqual(value["ticket_count"], 1000)
                self.assertGreater(value["distinct_probability_count"], 1)
                backtest = json.loads(next((release / "backtests" / game).glob("*/summary.json")).read_text())
                folds = [json.loads(line) for line in next((release / "backtests" / game).glob("*/report-only-fold-metrics.jsonl")).read_text().splitlines()]
                self.assertEqual(len(folds), 3)
                self.assertEqual(backtest["metrics"], ["joint_log_loss", "true_multiclass_brier", "calibration", "full_ticket_top_10_100_200_1000_recall", "permutation", "block_bootstrap"])
                self.assertTrue(all(row["fold_role"] == "report_only" and not row["used_for_selection"] for row in folds))
                self.assertTrue(all(row["brier_formula"] == "1-2*p_observed+sum_over_complete_legal_space(p_class^2)" for row in folds))

            model = json.loads(next((release / "models/ssq").glob("*/model.json")).read_text())
            forecast = self.run_command(
                "-m", "lottery_system.phase4.real_cli", "forecast", "--model-release",
                str(release / "models/ssq" / model["model_release_id"] / "model.json"),
                "--target-issue", "unit-next", "--top-k", "1000", "--lock",
            )
            self.assertEqual(forecast.returncode, 0, forecast.stderr + forecast.stdout)
            repeated = self.run_command(
                "-m", "lottery_system.phase4.real_cli", "forecast", "--model-release",
                str(release / "models/ssq" / model["model_release_id"] / "model.json"),
                "--target-issue", "unit-next", "--top-k", "1000", "--lock",
            )
            self.assertTrue(json.loads(repeated.stdout)["idempotent_replay"])
            research = self.run_command("-m", "lottery_system.phase4.real_cli", "research", "--release", str(release), "--game", "ssq")
            self.assertEqual(research.returncode, 0, research.stderr + research.stdout)
            self.assertTrue(json.loads(research.stdout)["top1000_changed"])
            score = self.run_command("-m", "lottery_system.phase4.real_cli", "score", "--release", str(release), "--game", "dlt")
            self.assertEqual(score.returncode, 0, score.stderr + score.stdout)
            repeated_score = self.run_command("-m", "lottery_system.phase4.real_cli", "score", "--release", str(release), "--game", "dlt")
            self.assertEqual(repeated_score.returncode, 0, repeated_score.stderr + repeated_score.stdout)
            self.assertTrue(json.loads(repeated_score.stdout)["idempotent_replay"])
            interrupted = self.run_command("-m", "lottery_system.phase4.real_cli", "schedule", "--release", str(release), "--game", "ssq", "--cycle-id", "fault-test", "--fail-after", "forecast_lock")
            self.assertEqual(interrupted.returncode, 20)
            resumed = self.run_command("-m", "lottery_system.phase4.real_cli", "schedule", "--release", str(release), "--game", "ssq", "--cycle-id", "fault-test")
            self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
            self.assertEqual(json.loads(resumed.stdout)["duplicate_side_effects"], 0)


if __name__ == "__main__":
    unittest.main()
