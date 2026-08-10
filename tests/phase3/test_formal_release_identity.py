from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class FormalReleaseIdentityTests(unittest.TestCase):
    def test_release_creator_binds_current_controller_task_worktree_and_branch(self) -> None:
        roles = (
            "data_custodian", "implementation_author", "statistical_owner",
            "independent_method_reviewer", "run_operator", "independent_reviewer",
            "acceptance_engineer", "classification_approver", "release_controller",
        )
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            prep = temporary / "prep"
            (prep / "control").mkdir(parents=True)
            (prep / "control/actor-assignments-preparation.json").write_text("{}\n", encoding="utf-8")
            for directory in ("work-items", "benchmark"):
                (prep / directory).mkdir()
            for filename in ("wheelhouse-manifest.json", "offline-rebuild-receipt.json"):
                (prep / filename).write_text("{}\n", encoding="utf-8")
            records = temporary / "records"
            records.mkdir()
            command = [
                sys.executable, "scripts/phase3/create_formal_release.py",
                "--release-root", str(temporary / "release"),
                "--prep-root", str(prep), "--project-root", str(ROOT),
                "--controller-id", "controller-current",
            ]
            for role in roles:
                record = records / f"{role}.json"
                record.write_text(json.dumps({"role": role}) + "\n", encoding="utf-8")
                task_id = "phase3-formal-replay-current" if role == "release_controller" else f"task-{role}"
                command.extend(["--assignment", f"{role}|actor-{role}|{task_id}|session-{role}|{record}"])
            completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            control = json.loads((temporary / "release/control/release-control.json").read_text(encoding="utf-8"))
            branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=ROOT, check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(control["task_id"], "phase3-formal-replay-current")
            self.assertEqual(control["worktree"], ROOT.as_posix())
            self.assertEqual(control["branch"], branch)


if __name__ == "__main__":
    unittest.main()
