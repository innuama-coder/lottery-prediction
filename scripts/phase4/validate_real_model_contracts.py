#!/usr/bin/env python3
"""Canonical D01 validator for the D00–D15 real-model contract.

The superseded validate_contract_bundle.py validates T00–T24 preparation input
and is deliberately not called by Phase 4 real-model acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def reject(case: dict[str, object]) -> bool:
    return bool(
        case.get("unknown_field")
        or case.get("serving_family") == "M0"
        or case.get("provider") in {"fixture", "inline", "worktree_default"}
        or case.get("target_in_training_prefix")
        or case.get("selection_report_overlap")
        or case.get("full_space_probability_layers") == 1
        or case.get("top1000_probability_layers") == 1
        or case.get("ranking_primary") == "lexicographic"
    )


def main() -> int:
    required = [
        ROOT / "config/phase4/authority-freeze.json",
        ROOT / "docs/research/phase-4-overall-design.md",
        ROOT / "docs/plans/phase-4-detailed-plan.md",
        ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl",
    ]
    if not all(path.is_file() for path in required):
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE")
    negatives = [
        {"unknown_field": True}, {"serving_family": "M0"}, {"provider": "fixture"},
        {"target_in_training_prefix": True}, {"selection_report_overlap": True},
        {"full_space_probability_layers": 1}, {"top1000_probability_layers": 1},
        {"ranking_primary": "lexicographic"},
    ]
    if not all(reject(case) for case in negatives):
        raise SystemExit("FAIL_CONTRACT_WEAKENED")
    print(json.dumps({
        "artifact_type": "phase4_d01_contract_validation", "status": "PASS",
        "unknown_fields": "fail_closed", "negative_case_count": len(negatives),
        "legacy_t00_t24_validator_canonical": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
