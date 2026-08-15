#!/usr/bin/env python3
"""Canonical D01 validator for the frozen P4E2-R real-model contract."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
FEATURE_IDS = {f"F{index:02d}" for index in range(1, 15)}
FEATURE_GROUPS = {"historical_change", "number_relationship", "combination_structure"}


def reject(case: dict[str, object]) -> bool:
    feature_ids = set(case.get("feature_ids", FEATURE_IDS))
    feature_groups = set(case.get("feature_groups", FEATURE_GROUPS))
    return bool(
        case.get("unknown_field")
        or feature_ids != FEATURE_IDS
        or not FEATURE_GROUPS <= feature_groups
        or case.get("serving_family") in {"M0", "P4E1-R"}
        or case.get("provider") in {"fixture", "inline", "worktree_default"}
        or case.get("target_in_training_prefix")
        or case.get("max_source_position", -1) >= case.get("target_position", 0)
        or case.get("selection_report_overlap")
        or case.get("report_labels_read_before_selection_freeze")
        or case.get("full_space_probability_layers") == 1
        or case.get("top1000_probability_layers") == 1
        or case.get("ranking_primary") == "lexicographic"
        or case.get("available_at_fabricated")
        or case.get("unbounded_pair_parameters")
        or case.get("regularization") not in (None, "L2", "group_lasso")
        or case.get("l2") not in (None, 8, 24, 72)
        or case.get("brier_formula") == "(1-p_observed)^2"
        or case.get("top_k_scope") == "partition"
        or case.get("schedule_stage_noop")
        or case.get("score_forecast_mismatch")
        or case.get("result_target_mismatch")
        or case.get("research_without_score")
        or case.get("selection_after_report_labels")
        or case.get("fake_ablation")
        or case.get("fake_permutation")
        or case.get("missing_forecast_lineage")
        or case.get("approximate_tie")
        or case.get("shallow_validate")
    )


def main() -> int:
    schemas = [
        "training-input-manifest.schema.json", "feature-snapshot.schema.json",
        "model-release.schema.json", "training-report.schema.json",
        "backtest.schema.json", "model-selection-receipt.schema.json", "formal-forecast.schema.json",
        "formal-forecast-lock.schema.json", "serving-selection.schema.json",
    ]
    required = [
        ROOT / "config/phase4/authority-freeze.json",
        ROOT / "config/phase4/feature-registry.json",
        ROOT / "config/phase4/model-registry.json",
        ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl",
        *(ROOT / "schemas/phase4" / name for name in schemas),
    ]
    if not all(path.is_file() for path in required):
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE")
    for name in schemas:
        jsonschema.Draft202012Validator.check_schema(json.loads((ROOT / "schemas/phase4" / name).read_text()))
    feature_registry = json.loads((ROOT / "config/phase4/feature-registry.json").read_text())
    model_registry = json.loads((ROOT / "config/phase4/model-registry.json").read_text())
    if {row["feature_id"] for row in feature_registry["features"]} != FEATURE_IDS:
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE:F01_F14")
    if set(feature_registry["required_serving_feature_groups"]) != FEATURE_GROUPS:
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE:FEATURE_GROUPS")
    if model_registry["serving_probability_family"]["model_family"] != "P4E2-R":
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE:P4E2")
    negatives = [
        {"unknown_field": True}, {"feature_ids": ["F01"]},
        {"feature_groups": ["historical_change"]}, {"serving_family": "M0"},
        {"serving_family": "P4E1-R"}, {"provider": "fixture"}, {"provider": "inline"},
        {"provider": "worktree_default"}, {"target_in_training_prefix": True},
        {"max_source_position": 10, "target_position": 10}, {"selection_report_overlap": True},
        {"report_labels_read_before_selection_freeze": True}, {"full_space_probability_layers": 1},
        {"top1000_probability_layers": 1}, {"ranking_primary": "lexicographic"},
        {"available_at_fabricated": True}, {"unbounded_pair_parameters": True},
        {"regularization": "unregistered"}, {"l2": 9}, {"brier_formula": "(1-p_observed)^2"},
        {"top_k_scope": "partition"},
        {"schedule_stage_noop": True}, {"score_forecast_mismatch": True},
        {"result_target_mismatch": True}, {"research_without_score": True},
        {"selection_after_report_labels": True}, {"fake_ablation": True},
        {"fake_permutation": True}, {"missing_forecast_lineage": True},
        {"approximate_tie": True}, {"shallow_validate": True},
    ]
    if not all(reject(case) for case in negatives):
        raise SystemExit("FAIL_CONTRACT_WEAKENED")
    print(json.dumps({
        "artifact_type": "phase4_d01_contract_validation", "status": "PASS",
        "schema_count": len(schemas), "feature_ids": sorted(FEATURE_IDS),
        "feature_groups": sorted(FEATURE_GROUPS), "serving_family": "P4E2-R",
        "unknown_fields": "fail_closed", "negative_case_count": len(negatives),
        "legacy_t00_t24_validator_canonical": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
