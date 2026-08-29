#!/usr/bin/env python3
"""Read-only, portable Phase 4 P4E2 local product acceptance entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import replay_real_model_release as independent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only local acceptance for a finalized P4E2 release")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    args = parser.parse_args()
    try:
        release = args.release.resolve()
        release.relative_to(ROOT.resolve())
        report = independent.local_acceptance(release, args.draws.resolve())
    except Exception as exc:
        print(f"LOCAL ACCEPTANCE: FAIL\nreason: {exc}", file=sys.stderr)
        return 20
    print(f"LOCAL ACCEPTANCE: PASS ({report['terminal_state']})")
    print(f"release: {report['release_id']}")
    print(f"runtime: {report['python']['implementation']} {report['python']['version']} on {report['python']['platform']}")
    print(f"numeric contract: {report['numeric_contract_id']}; replay={report['independent_replay_match_rate']:.0%}; mutations={report['mutation_detection_rate']:.0%}")
    for game in ("ssq", "dlt"):
        row = report["games"][game]
        print(f"{game.upper()}: model={row['model_release_id']} feature={row['feature_release_id']} cutoff={row['training_cutoff_issue']} target={row['target_issue']} rows={row['ticket_count']}")
        print(f"{game.upper()} probability: first={row['first_probability']} last={row['last_probability']} scientific={row['scientific_status']}")
    print("evidence: acceptance/final-closure.json, manifest/delivery-manifest.json, replay/replay-report.json")
    print("release unchanged: yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
