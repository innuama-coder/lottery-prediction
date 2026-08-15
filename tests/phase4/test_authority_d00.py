from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class D00AuthorityTest(unittest.TestCase):
    def run_checker(self, script: str, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(ROOT / script), "--check", *args],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return json.loads(completed.stdout)

    def test_primary_checker(self) -> None:
        result = self.run_checker(
            "scripts/phase4/freeze_authority.py",
            "--require-serving-model-per-game",
            "--reject-baseline-only-pass",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["required_semantics"]["serving_must_be_non_m0"])
        self.assertFalse(result["required_semantics"]["baseline_only_product_pass_allowed"])

    def test_independent_semantic_checker(self) -> None:
        result = self.run_checker("scripts/phase4_independent/check_authority_semantics.py")
        self.assertEqual(result["old_m0_product_success_paths"], 0)
        self.assertTrue(all(result["checks"].values()))

    def test_authority_commit_contains_exact_four_paths(self) -> None:
        config = json.loads((ROOT / "config/phase4/authority-freeze.json").read_text())
        self.assertEqual(len(config["authority_files"]), 4)
        changed = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", config["authority_commit"]],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        self.assertEqual(set(changed), {row["path"] for row in config["authority_files"]})


if __name__ == "__main__":
    unittest.main()
