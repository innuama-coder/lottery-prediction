import sys

from bootstrap import activate

ROOT = activate()

from lottery_research.phase2_1.cli import main

raise SystemExit(main(["--project-root", str(ROOT), "prepare", *sys.argv[1:]]))
