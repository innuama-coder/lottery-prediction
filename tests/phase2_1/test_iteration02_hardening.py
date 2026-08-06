from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase2_1 import RELEASE_ID
from lottery_research.phase2_1.cli import execute_external_commands
from lottery_research.phase2_1.serialization import sha256
from lottery_research.phase2_1.workflow import project_root, scan_formal_history, validate_task_inputs


ROOT = project_root()
class Iteration02HardeningTests(unittest.TestCase):
    def test_task_input_directory_is_portable_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            alternate = Path(raw)
            expected = {}
            for name in ("prompt.md", "iteration-01.md", "iteration-02.md", "iteration-03.md", "iteration-04.md", "iteration-05.md"):
                path = alternate / name
                path.write_text(f"portable fixture for {name}\n", encoding="utf-8")
                expected[name] = sha256(path)
            self.assertEqual(len(validate_task_inputs(alternate, expected)), 6)
            (alternate / "iteration-05.md").unlink()
            with self.assertRaises(FileNotFoundError):
                validate_task_inputs(alternate, expected)

    def test_formal_history_scan_ignores_rejected_release_but_finds_current_release(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            destination = root / "artifacts/phase-2.1" / RELEASE_ID
            destination.mkdir(parents=True)
            task = root / "task"
            (task / "results").mkdir(parents=True)
            rejected = {"release_id": "P2.1-R00-60d02be4dbe9", "artifact_type": "phase2_1_power"}
            (task / "results/rejected.json").write_text(json.dumps(rejected), encoding="utf-8")
            self.assertEqual(scan_formal_history(root, destination, task)["count"], 0)
            current = {"release_id": RELEASE_ID, "artifact_type": "phase2_1_power"}
            (task / "results/current.json").write_text(json.dumps(current), encoding="utf-8")
            self.assertEqual(scan_formal_history(root, destination, task)["count"], 1)

    def test_logs_execute_commands_and_preserve_real_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw)
            (destination / "logs").mkdir()
            receipts = execute_external_commands(ROOT, destination, ["python3 -c 'raise SystemExit(7)'"])
            self.assertTrue(receipts[0]["executed"])
            self.assertEqual(receipts[0]["exit_code"], 7)
            self.assertEqual(sha256(destination / "logs/external-01.json"), sha256(destination / "logs/external-01.json"))
            with self.assertRaises(FileExistsError):
                execute_external_commands(ROOT, destination, ["python3 -c 'raise SystemExit(0)'"])


if __name__ == "__main__":
    unittest.main()
