from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RealImportOrderTests(unittest.TestCase):
    def test_p4e2_and_real_ops_import_in_clean_process_before_real_model(self) -> None:
        command = subprocess.run(
            [sys.executable, "-c",
             "from lottery_system.phase4 import p4e2_model, real_ops; "
             "assert len(p4e2_model.FEATURE_IDS) == 14; "
             "assert callable(real_ops.validate_release_bottom_up)"],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": "src"}, text=True, capture_output=True, check=False,
        )
        self.assertEqual(command.returncode, 0, command.stderr + command.stdout)


if __name__ == "__main__":
    unittest.main()
