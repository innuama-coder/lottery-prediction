from __future__ import annotations

import argparse

from bootstrap import activate

ROOT = activate()

from lottery_research.phase2_1.cli import main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 2.1 E2E in-place or in a new immutable staging bundle")
    parser.add_argument("--bundle")
    parser.add_argument("--staging-bundle")
    return parser.parse_args()


args = parse_args()
command = ["--project-root", str(ROOT), "e2e"]
if args.bundle:
    command.extend(["--bundle", args.bundle])
if args.staging_bundle:
    command.extend(["--staging-bundle", args.staging_bundle])
raise SystemExit(main(command))
