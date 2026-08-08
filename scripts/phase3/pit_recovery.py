#!/usr/bin/env python3
"""Phase 3 point-in-time (PIT) evidence preparation CLI.

Subcommands:
  build     Create a new immutable PIT preparation release directory with
            hash-bound contracts, an independent validator receipt, a negative
            tamper report, an independent review and a HOLD/READY receipt.
  validate  Independently recompute hashes and coverage for an existing bundle.
  tamper    Run the synthetic negative tamper matrix.

Network is preparation-only and never used by this script; a formal run is
authorized only when the recomputed eligible-feature coverage reaches 100%.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lottery_research.phase3.pit_recovery import (  # noqa: E402
    PIT_RELEASE_IDENTITY,
    build_pit_preparation_bundle,
    run_negative_tamper_tests,
    validate_pit_preparation_bundle,
    write_preparation_status,
)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 scripts/phase3/pit_recovery.py")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="create a new PIT preparation release")
    build_p.add_argument("--identity", default=PIT_RELEASE_IDENTITY)
    build_p.add_argument("--output", required=True, type=Path)
    build_p.add_argument("--receipts-dir", type=Path, default=None,
                         help="optional directory of preparation-period HTTP recon receipts to freeze into the bundle")
    build_p.add_argument("--status-output", type=Path, default=None,
                         help="optional derived preparation-status path outside the immutable bundle")

    val_p = sub.add_parser("validate", help="recompute hashes and coverage for a bundle")
    val_p.add_argument("--bundle", required=True, type=Path)

    sub.add_parser("tamper", help="run the synthetic negative tamper matrix")

    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    if args.command == "build":
        receipts: dict[str, dict] = {}
        if args.receipts_dir is not None:
            for path in sorted(args.receipts_dir.glob("*.json")):
                if path.name == "_summary.json":
                    continue
                receipts[path.stem] = json.loads(path.read_text())
        receipt = build_pit_preparation_bundle(root, args.output.resolve(), args.identity, collection_receipts=receipts)
        if args.status_output is not None:
            write_preparation_status(root, args.output.resolve(), args.status_output.resolve(), identity=args.identity)
        _emit(receipt)
        return int(receipt["exit_code"])
    if args.command == "validate":
        validation = validate_pit_preparation_bundle(root, args.bundle.resolve())
        _emit(validation)
        return 0 if validation["status"] == "READY" else 20
    if args.command == "tamper":
        report = run_negative_tamper_tests()
        _emit(report)
        return 0 if report["summary"]["all_cases_passed"] else 5
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
