from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from lottery_data import cli  # noqa: E402
from lottery_data.__main__ import main as module_main  # noqa: E402
from lottery_data.models import ContractViolation  # noqa: E402
from lottery_data.steps.publish import PublishError  # noqa: E402
from lottery_data.steps.preflight import BootstrapArguments, IncrementalArguments  # noqa: E402
from lottery_data.steps.recovery import RecoveryConflict  # noqa: E402
from lottery_data.workflow import classify_failure, execute_bootstrap, execute_incremental  # noqa: E402


class BootstrapCliTests(unittest.TestCase):
    def invoke(self, argv: list[str]) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1, stdout.getvalue())
        return code, json.loads(lines[0]), stderr.getvalue()

    def test_module_and_console_entrypoints_share_main(self) -> None:
        self.assertIs(module_main, cli.main)
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('lottery-data = "lottery_data.cli:main"', pyproject)

    def test_unknown_command_fails_closed_without_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifacts"
            code, result, _ = self.invoke(["future-command", "--artifacts-root", str(root)])
            self.assertEqual(code, 4)
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["exit_code"], 4)
            self.assertFalse(root.exists())

    def test_out_of_scope_mode_fails_before_creating_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "artifacts"
            code, result, _ = self.invoke([
                "run", "--mode", "incremental", "--source-mode", "live",
                "--phase0-snapshot", str(base / "snapshot"), "--run-id", "r1",
                "--release-id", "rel1", "--artifacts-root", str(root),
            ])
            self.assertEqual((code, result["exit_code"]), (4, 4))
            self.assertFalse(root.exists())

    def test_valid_parse_delegates_to_workflow_and_prints_one_json(self) -> None:
        expected = {"status": "published", "exit_code": 0, "run_id": "r1"}
        with patch("lottery_data.cli.execute_bootstrap", return_value=(0, expected)) as execute:
            code, result, _ = self.invoke([
                "run", "--mode", "bootstrap", "--source-mode", "snapshot",
                "--phase0-snapshot", "snapshot", "--run-id", "r1", "--release-id", "rel1",
                "--artifacts-root", "artifacts", "--config-root", "config",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(result, expected)
        execute.assert_called_once()

    def test_contractual_error_categories_map_to_frozen_exit_codes(self) -> None:
        self.assertEqual(classify_failure(ContractViolation("bootstrap-transform", "raw evidence SHA-256 mismatch")).exit_code, 5)
        self.assertEqual(classify_failure(ContractViolation("bootstrap-transform", "source catalog is not approved")).exit_code, 4)
        self.assertEqual(classify_failure(ContractViolation("bootstrap-transform", "conflicting draw fact")).exit_code, 2)
        self.assertEqual(classify_failure(PublishError("publish lock is held")).exit_code, 6)

    def test_bootstrap_legacy_publish_lock_returns_structured_prestart_exit6(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifacts"
            root.mkdir()
            (root / ".publish.lock").write_bytes(b"legacy-owner")
            code, result = execute_bootstrap(BootstrapArguments(
                mode="bootstrap", source_mode="snapshot", phase0_snapshot=root / "unused-snapshot",
                artifacts_root=root, config_root=None, run_id="bootstrap-contended",
                release_id="bootstrap-release",
            ))
            self.assertEqual((code, result["exit_code"], result["mode"], result["source_mode"]),
                             (6, 6, "bootstrap", "snapshot"))
            self.assertEqual((result["status"], result["request_stats"]["started"]), ("interrupted", 0))
            self.assertNotIn("manifest_ref", result)
            self.assertFalse((root / "runs" / "bootstrap-contended").exists())

    def test_direct_apis_structure_prestart_recovery_conflict_without_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifacts"
            bootstrap = BootstrapArguments(
                mode="bootstrap", source_mode="snapshot", phase0_snapshot=root / "unused-snapshot",
                artifacts_root=root, config_root=None, run_id="bootstrap-conflict",
                release_id="bootstrap-release",
            )
            incremental = IncrementalArguments(
                mode="incremental", source_mode="live", snapshot_root=None,
                artifacts_root=root, config_root=REPO / "config" / "phase1",
                run_id="incremental-conflict", release_id="incremental-release",
            )
            with patch("lottery_data.workflow.recover_stale_publications", side_effect=RecoveryConflict("injected")):
                for arguments, execute in ((bootstrap, execute_bootstrap), (incremental, execute_incremental)):
                    with self.subTest(mode=arguments.mode):
                        code, result = execute(arguments)
                        self.assertEqual((code, result["exit_code"], result["status"]), (6, 6, "interrupted"))
                        self.assertEqual((result["mode"], result["source_mode"]),
                                         (arguments.mode, arguments.source_mode))
                        self.assertEqual(result["request_stats"]["started"], 0)
                        self.assertNotIn("manifest_ref", result)
                        self.assertFalse((root / "runs" / arguments.run_id).exists())


if __name__ == "__main__":
    unittest.main()
