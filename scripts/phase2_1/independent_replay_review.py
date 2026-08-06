from __future__ import annotations

import sys

from bootstrap import activate

ROOT = activate()

from lottery_research.phase2_1.cli import main

raise SystemExit(main(["--project-root", str(ROOT), "replay-review", *sys.argv[1:]]))
