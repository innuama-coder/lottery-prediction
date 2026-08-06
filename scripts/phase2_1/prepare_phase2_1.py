from __future__ import annotations

import sys
from pathlib import Path

from bootstrap import activate

ROOT = activate()

from lottery_research.phase2_1.cli import main

raise SystemExit(main(["--project-root", str(ROOT), "prepare", "--wheelhouse", str(ROOT / ".phase2_1/wheelhouse"), "--task-input-dir", "/home/royzuo/codex-tasks/lottery-phase-2.1-20260805"]))
