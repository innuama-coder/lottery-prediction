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
        "formal-forecast-lock.schema.json", "serving-selection.schema.json", "p4e2-ranking.schema.json",
        "probability-qualification.schema.json",
        "local-verifier-contract.schema.json",
    ]
    required = [
        ROOT / "config/phase4/authority-freeze.json",
        ROOT / "config/phase4/feature-registry.json",
        ROOT / "config/phase4/model-registry.json",
        ROOT / "config/phase4/local-verifier-contract.json",
        ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl",
        *(ROOT / "schemas/phase4" / name for name in schemas),
    ]
    if not all(path.is_file() for path in required):
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE")
    for name in schemas:
        jsonschema.Draft202012Validator.check_schema(json.loads((ROOT / "schemas/phase4" / name).read_text()))
    feature_registry = json.loads((ROOT / "config/phase4/feature-registry.json").read_text())
    model_registry = json.loads((ROOT / "config/phase4/model-registry.json").read_text())
    local_contract = json.loads((ROOT / "config/phase4/local-verifier-contract.json").read_text())
    jsonschema.Draft202012Validator(json.loads((ROOT / "schemas/phase4/local-verifier-contract.schema.json").read_text())).validate(local_contract)
    if {row["feature_id"] for row in feature_registry["features"]} != FEATURE_IDS:
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE:F01_F14")
    if set(feature_registry["required_serving_feature_groups"]) != FEATURE_GROUPS:
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE:FEATURE_GROUPS")
    if model_registry["serving_probability_family"]["model_family"] != "P4E2-R":
        raise SystemExit("HOLD_CONTRACT_INCOMPLETE:P4E2")
    expected_profiles = {
        "tight_recomputed_v1": {"finite_required": True, "require_all_bounds": True,
                                "max_absolute": 1e-12, "max_relative": 1e-12, "max_ulps": 8},
        "derived_feature_context_v2": {"finite_required": True, "require_all_bounds": True,
                                       "max_absolute": 3.3306690738754696e-16,
                                       "max_relative": 3e-14, "max_ulps": 151},
        "derived_coefficient_v1": {"finite_required": True, "require_all_bounds": True,
                                   "max_absolute": 1e-12, "max_relative": 1e-12, "max_ulps": 16},
        "top1000_derived_probability_display_v3": {
            "finite_required": True, "require_all_bounds": True,
            "max_absolute": 4.235164736271502e-22,
            "max_relative": 3.774758283725532e-15, "max_ulps": 32,
        },
    }
    numeric_paths = [path for row in local_contract["path_numeric_profiles"] for path in row["paths"]]
    profile_ids = [row["profile_id"] for row in local_contract["path_numeric_profiles"]]
    paths_by_profile = {row["profile_id"]: set(row["paths"]) for row in local_contract["path_numeric_profiles"]}
    expected_derived_feature_paths = {
        f"feature_snapshot.*.feature_values.{feature_id}" for feature_id in sorted(FEATURE_IDS)
    } | {
        f"feature_snapshot.*.normalization.{feature_id}.{statistic}"
        for feature_id in sorted(FEATURE_IDS) for statistic in ("mean", "scale")
    } | {
        "model.zones.*.context.normalization.*.mean",
        "model.zones.*.context.normalization.*.scale",
        "model.zones.*.context.number_features.*.*",
    }
    expected_coefficient_paths = {
        "model.objective_trace.gradient_at_zero_by_zone.*.*",
        "model.zones.*.coefficients.*",
    }
    expected_tight_paths = {
        "model.selection_metrics.*.joint_log_loss",
        "selection_receipt.selection_metrics.*.joint_log_loss",
        "model.zones.*.context.ewma_raw.*.*",
        "model.zones.*.context.pair_matrix.*.*",
        "model.zones.*.context.pair_values.*",
        "model.zones.*.context.recency_gap_raw.*",
        "model.zones.*.context.rolling_raw.*.*",
        "model.zones.*.top_zone_rows.*.0",
        "model.zones.*.log_normalizer",
        "model.zones.*.probability_square_sum",
        "model.zones.*.normalization_mass",
        "model.zones.*.minimum_score",
        "model.zones.*.maximum_score",
        "model.zones.*.minimum_probability",
        "model.zones.*.maximum_probability",
        "model.report_only_metrics.*.model_joint_log_loss",
        "model.report_only_metrics.*.model_multiclass_brier",
        "model.report_only_metrics.*.ablation_metrics.*.joint_log_loss",
        "model.report_only_metrics.*.ablation_metrics.*.multiclass_brier",
        "model.report_only_summary.permutation_evidence.*.samples.*.permuted_joint_probability",
        "model.report_only_summary.permutation_evidence.*.samples.*.permuted_joint_log_loss",
        "score.metrics.joint_log_loss",
        "score.metrics.actual_joint_probability",
        "score.metrics.multiclass_brier",
        "top1000.*.log_joint_score",
        "top1000.*.explanation.feature_contributions.*",
        "historical_top1000.*.log_joint_score",
        "historical_top1000.*.explanation.feature_contributions.*",
        "shadow_top1000.*.log_joint_score",
        "shadow_top1000.*.explanation.feature_contributions.*",
        "feature_snapshot.*.raw.ewma_rates.10",
        "feature_snapshot.*.raw.ewma_rates.30",
        "feature_snapshot.*.raw.recency_gap",
        "feature_snapshot.*.raw.rolling_rates.10",
        "feature_snapshot.*.raw.rolling_rates.30",
        "feature_snapshot.*.raw.rolling_rates.60",
    }
    expected_top_probability_paths = {
        "top1000.*.joint_probability",
        "historical_top1000.*.joint_probability",
        "shadow_top1000.*.joint_probability",
    }
    expected_score_order_contract = {
        "score_order_key_id": "P4S10HE1",
        "canonical_source": "exact finite binary64 value converted to an exact decimal rational",
        "quantum": "0.0000000001",
        "rounding": "ROUND_HALF_EVEN",
        "ranking": ["stable_score_order_key_desc", "canonical_ticket_asc_within_stable_score_key_tie"],
        "identity_fields_exact": ["score_order_key", "score_identity", "tie_key", "tie_group_id", "probability_layer", "tie_group_size", "tie_rank_lower", "tie_rank_upper", "tie_midrank"],
        "observed_cross_runtime_score_drift": "2.8e-17",
        "preserved_r10_minimum_adjacent_distinct_gap": "4.326295779955025012e-10",
    }
    actual_score_order_contract = dict(local_contract.get("score_order_contract", {}))
    actual_score_order_contract.pop("resolution_rationale", None)
    if (local_contract.get("contract_id") != "P4-LOCAL-PATH-CLASSIFIED-BINARY64-4"
            or local_contract.get("schema_version") != "1.5.0"
            or actual_score_order_contract != expected_score_order_contract
            or local_contract.get("default_numeric_profile") != "tight_recomputed_v1"
            or local_contract.get("numeric_profiles") != expected_profiles
            or set(profile_ids) != set(expected_profiles) or len(profile_ids) != len(set(profile_ids))
            or paths_by_profile.get("tight_recomputed_v1") != expected_tight_paths
            or paths_by_profile.get("derived_feature_context_v2") != expected_derived_feature_paths
            or paths_by_profile.get("derived_coefficient_v1") != expected_coefficient_paths
            or paths_by_profile.get("top1000_derived_probability_display_v3") != expected_top_probability_paths
            or len(numeric_paths) != len(set(numeric_paths)) or any("**" in path for path in numeric_paths)
            or local_contract["historical_formal_evidence"]["local_reexecution_required"] is not False
            or any("/home/" in path or "/usr/bin/" in path for path in numeric_paths)):
        raise SystemExit("FAIL_CONTRACT_WEAKENED:LOCAL_VERIFIER")
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
        "local_verifier_contract": local_contract["contract_id"],
        "unknown_fields": "fail_closed", "negative_case_count": len(negatives),
        "legacy_t00_t24_validator_canonical": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
