from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


AUTHORITY = {
    "path": "tasks/phase3/README.md",
    "sha256": "7c8b1cf67b1a8e2bc6710c8c5ddc674e56250a216684c985f133a2b2689e1685",
    "identity_status": "candidate_pending_release_commit",
}

FROZEN_INPUTS = (
    ("phase1_acceptance", "artifacts/phase-1/acceptance/phase1-acceptance.json", "959b1dddacf453dbff347786d572de4cd8c52d1b7eb2e7a3805cffa2a166bb18", "phase1_delivery_closure"),
    ("phase1_manifest", "artifacts/phase-1/baseline-v1/manifest.json", "0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1", "data_identity"),
    ("phase1_draws", "artifacts/phase-1/baseline-v1/draws.jsonl", "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1", "retrospective_sequence_safe_features_and_labels"),
    ("phase2_rule_manifest", "artifacts/phase-2/contracts/input-manifest.json", "36ad90a204a2d0ebab5ddbfff3a4246f267e02cdd2cfe961200e515c27ef90ad", "game_rule_identity"),
    ("phase2_1_acceptance", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json", "d5dde1d4488290e41998c1e7f6d04b1b3ae094408716571ceb5451324cb8e8b4", "accepted_statistical_boundary"),
    ("phase2_1_manifest", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/manifest.json", "c2fb2e4a60ed214ce4648a93a1d8b11aed2ebd41b920dd549158e5adc821e3c6", "accepted_evidence_identity"),
    ("phase2_1_historical_audit", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/historical-audit.json", "a3d0f1f2dc371e3ff53256c6f09d5b47471f84567e33feaa8efa9c8349b8a8d1", "registered_historical_context"),
    ("phase2_1_power", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/power.json", "99bca12e9452435fbc32c67686d4dc905ea4771b8bb7b7d62c02983e24b98a10", "power_boundary"),
)

REQUIRED_FORBIDDEN_FIELDS = frozenset({
    "external_current_view_without_available_at",
    "future_draw_result",
    "post_draw_sales",
    "jackpot",
    "winner_count",
    "machine_or_ball_set_unknown",
    "global_normalization",
})

SCHEMAS = {
    "input_manifest": "input-manifest.schema.json",
    "availability_ledger": "availability-ledger.schema.json",
    "data_time_contract": "data-time-contract.schema.json",
    "preregistration": "preregistration.schema.json",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def schema_root(root: Path) -> Path:
    return root / "schemas/phase3"


def validate_schema(root: Path, kind: str, payload: Any) -> None:
    if kind not in SCHEMAS:
        raise ValueError(f"unknown Phase 3 schema kind: {kind}")
    schema = load_json(schema_root(root) / SCHEMAS[kind])
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(map(str, first.absolute_path)) or "$"
        raise ValueError(f"{kind} schema violation at {location}: {first.message}")


def _config_path(root: Path, name: str) -> Path:
    return root / "config/phase3" / name


def _read_draws(root: Path) -> list[dict[str, Any]]:
    path = root / "artifacts/phase-1/baseline-v1/draws.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _input_manifest(root: Path, draws: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for role, relative, expected_sha256, allowed_use in FROZEN_INPUTS:
        path = root / relative
        if not path.is_file() or sha256(path) != expected_sha256:
            raise ValueError(f"frozen input identity mismatch: {relative}")
        rows.append({
            "role": role,
            "path": relative,
            "sha256": expected_sha256,
            "bytes": path.stat().st_size,
            "allowed_use": allowed_use,
        })
    counts = Counter(row["game"] for row in draws)
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_input_manifest",
        "status": "frozen_candidate",
        "authority": AUTHORITY,
        "files": rows,
        "draw_inventory": {"draw_count": len(draws), "by_game": dict(sorted(counts.items())), "source_observation_count": 800},
        "game_rules": {
            "dlt": "dlt-ns-35c5-12c2-v1",
            "ssq": "ssq-ns-33c6-16c1-v1",
        },
    }


def _availability_ledger(draws: list[dict[str, Any]], input_manifest_sha256: str) -> dict[str, Any]:
    entries = []
    for game in ("dlt", "ssq"):
        ordered = sorted((row for row in draws if row["game"] == game), key=lambda row: row["issue_id"])
        for target_index in range(50, len(ordered)):
            source_issues = [row["issue_id"] for row in ordered[:target_index]]
            entries.append({
                "game": game,
                "target_issue": ordered[target_index]["issue_id"],
                "source_field": "prior_draw_result",
                "source_issues": source_issues,
                "source_count": len(source_issues),
                "source_path": "artifacts/phase-1/baseline-v1/draws.jsonl",
                "source_sha256": "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1",
                "evidence_method": "strict_issue_order",
                "eligibility": "eligible",
                "reason_code": "RETROSPECTIVE_SEQUENCE_SAFE",
            })
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_temporal_eligibility_ledger",
        "status": "sequence_safe",
        "mode": "retrospective_sequence_safe",
        "input_manifest_sha256": input_manifest_sha256,
        "append_only": True,
        "entries": entries,
    }


def _data_time_contract(input_manifest_sha256: str, ledger_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_data_time_contract",
        "status": "ready_for_results_blind_freeze",
        "input_manifest_sha256": input_manifest_sha256,
        "availability_ledger_sha256": ledger_sha256,
        "historical_mode": "retrospective_sequence_safe",
        "feature_fields": [
            {
                "field_id": "prior_draw_result",
                "source_path": "artifacts/phase-1/baseline-v1/draws.jsonl",
                "availability_class": "historical_ordered_result",
                "historical_timestamp_required": False,
                "eligibility_rule": "source_issue_strictly_before_target_issue",
                "fit_scope": "strictly_before_outer_target",
            }
        ],
        "label_fields": ["front_numbers", "back_numbers"],
        "label_unlock_state_machine": ["started", "forecast_locked", "label_unlocked", "scored", "terminal"],
        "external_feature_policy": "require_genuine_available_at_before_prediction_lock_or_reject",
        "forbidden_fields": [
            "external_current_view_without_available_at",
            "future_draw_result",
            "post_draw_sales",
            "jackpot",
            "winner_count",
            "machine_or_ball_set_unknown",
            "global_normalization",
        ],
        "rule_policy": "one registered rule segment per game and target issue; zero or multiple matches reject the candidate release",
    }


def _preregistration(input_manifest_sha256: str, ledger_sha256: str, data_time_contract_sha256: str) -> dict[str, Any]:
    draws = _read_draws(project_root())
    targets = {
        game: [row["issue_id"] for row in sorted((item for item in draws if item["game"] == game), key=lambda item: item["issue_id"])[50:]]
        for game in ("dlt", "ssq")
    }
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_preregistration",
        "status": "results_blind_candidate",
        "formal_run_authorized": False,
        "input_manifest_sha256": input_manifest_sha256,
        "availability_ledger_sha256": ledger_sha256,
        "data_time_contract_sha256": data_time_contract_sha256,
        "games": {
            "dlt": {"outer_targets": targets["dlt"], "minimum_training_draws": 50, "inner_validation_targets": 20, "inner_minimum_training_draws": 30},
            "ssq": {"outer_targets": targets["ssq"], "minimum_training_draws": 50, "inner_validation_targets": 20, "inner_minimum_training_draws": 30},
        },
        "activation_requirements": [
            "all prior_draw_result relations satisfy source_issue strictly before target_issue",
            "forecast_locked event and hash exist before label_unlocked for every canonical attempt",
            "external time-varying features are absent or have genuine available_at_utc before prediction_locked_at",
            "W01-W06 receipts bind to preparation actor assignments and W07 freezes conflict-free formal actor assignments",
        ],
        "models": {
            "M0": {"role": "permanent_champion", "required": True, "joint_probability": "uniform_fixed_cardinality_without_replacement"},
            "M1": {"role": "mandatory_challenger", "required": True, "joint_probability": "fixed_cardinality_exponential_weights", "zero_parameter_equivalence": "M0"},
            "M2": {"role": "not_opened", "required": False},
            "M3": {"role": "not_opened", "required": False},
            "M4": {"role": "not_opened", "required": False},
        },
        "metrics": {
            "primary": "relative_joint_log_score_skill_vs_M0",
            "secondary": ["inclusion_brier", "calibration_ece_10_equal_width", "reliability", "stability"],
            "top_1000": "diagnostic_only",
            "numeric_tolerance": {"absolute": 1e-12, "relative": 1e-10},
        },
        "m1_contract": {
            "estimator": "centered_log_smoothed_counts_divided_by_max_lambda_plus_expected_1",
            "lambda_grid": [1.0, 5.0, 20.0, 100.0],
            "inner_validation_targets": 20,
            "inner_minimum_training_draws": 30,
            "selection_metric": "mean_joint_log_score",
            "selection_direction": "minimize",
            "tie_break": "largest_lambda_then_canonical_config_bytes",
            "seed_derivation": "sha256(release_id|game|model|target_issue|purpose)[0:64bits]",
        },
        "classification_gate": {
            "bootstrap": {
                "method": "non_circular_overlapping_moving_block",
                "replicates": 10000,
                "block_length": "max(5,ceil(n^(1/3)))",
                "block_starts": "0..n-L_inclusive",
                "blocks_per_replication": "ceil(n/L)",
                "sampling": "sha256(seed|replicate_index|block_index)_unsigned_mod_block_count",
                "concatenation": "selected_blocks_in_order_then_truncate_to_n",
                "statistic": "arithmetic_mean",
                "quantile": "nearest_rank_1_based",
                "lower_rank": "ceil(0.05*replicates)",
                "upper_rank": "ceil(0.95*replicates)",
                "null_centering": "x-mean(x)+delta",
                "p_value": "(1+count(null_bootstrap_mean>=observed_mean))/(replicates+1)",
            },
            "minimum_mean_skill": 0.0009995003330834232,
            "holm": {
                "family": "all_opened_eligible_model_game_hypotheses_in_release",
                "alpha": 0.05,
                "order": "raw_p_then_model_id_then_game",
                "adjustment": "min(1,max_prefix((m-rank+1)*ordered_raw_p))",
                "model_requires_all_games": True,
            },
            "chronological_halves_positive": True,
            "single_target_share_of_sum_positive_skill_max": 0.2,
            "rule_segment_min_targets": 20,
            "rule_segment_mean_positive": True,
            "brier_delta_max": 0.0,
            "ece_delta_max": 0.005,
            "sensitivity": ["drop_earliest_10_percent_mean_skill_positive", "drop_latest_10_percent_mean_skill_positive"],
            "blocking_findings": 0,
            "classification_decision_tree": [
                "not_opened_if_model_not_opened",
                "rejected_if_probability_leakage_ledger_or_integrity_gate_fails",
                "shadow_candidate_if_all_shadow_gates_pass_for_both_games",
                "indeterminate_if_any_game_interval_straddles_delta_and_all_non_bootstrap_gates_pass_for_that_game",
                "archived_otherwise_for_complete_process",
            ],
        },
        "qualification_contract": {
            "number_space": 10,
            "cardinality": 3,
            "draws_per_replication": 200,
            "minimum_training_draws": 50,
            "replicates_per_world": 1000,
            "injected_theta": [0.4, 0.3, 0.2, 0.1, 0.0, 0.0, -0.1, -0.2, -0.3, -0.4],
            "direction_recovered_when": "outer_mean_skill_positive_and_fitted_injected_theta_spearman_positive",
            "uniform_false_selection_rate_max": 0.05,
            "injected_direction_recovery_rate_min": 0.9,
            "generator_identity_required": True,
        },
        "workload_contract": {
            "logical_experiments": 600,
            "max_attempts_per_experiment": 2,
            "checkpoint_every_targets": 10,
            "benchmark_repetitions": 20,
            "benchmark_components": ["m0_target", "m1_target_with_4x20_inner", "qualification_replication", "bootstrap_1000", "replay_target", "e2e_suite", "acceptance"],
            "component_counts": {
                "m0_target_attempts_max": 600,
                "m1_target_attempts_max": 600,
                "qualification_replications": 2000,
                "bootstrap_1000_batches_per_hypothesis_evaluate_and_replay": 20,
                "replay_targets": 300,
                "e2e_suites": 1,
                "acceptance_iterations_max": 2,
            },
            "eligible_hypothesis_formula": "H=2*eligible_challenger_count",
            "component_timeout_formula": "max(60,ceil(4*p95_component_seconds))",
            "total_wall_seconds_formula": "ceil(1.25*(600*p95_m0_target_seconds+600*p95_m1_target_seconds+2000*p95_qualification_replication_seconds+20*H*p95_bootstrap_1000_seconds+300*p95_replay_target_seconds+p95_e2e_suite_seconds+2*p95_acceptance_seconds))",
            "artifact_bytes_formula": "ceil(1.25*(600*p95_m0_target_bytes+600*p95_m1_target_bytes+2000*p95_qualification_replication_bytes+20*H*p95_bootstrap_1000_bytes+300*p95_replay_target_bytes+p95_e2e_suite_bytes+2*p95_acceptance_bytes))",
        },
        "replay_contract": {"all_outer_observed_probabilities": True, "all_metrics_and_classification": True, "full_distribution_targets_per_game": ["first", "middle", "last"]},
        "execution_contract": {"max_attempts_per_experiment": 2, "canonical_attempt_rule": "lowest_attempt_ordinal_complete_PASS", "max_acceptance_iterations_per_release": 2, "exhausted_terminal": "RETRY_BUDGET_EXHAUSTED"},
        "roles": {
            "preparation_binding_stage": "before_W01",
            "formal_binding_stage": "W07_before_any_formal_result",
            "assignment_fields": ["actor_id", "role", "task_id", "session_id", "assigned_at_utc", "assigned_by", "task_record_sha256"],
            "required_roles": ["data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer", "run_operator", "independent_reviewer", "acceptance_engineer", "classification_approver", "release_controller"],
            "constraints": ["independent_reviewer_id!=implementation_author_id", "independent_reviewer_id!=classification_approver_id", "classification_approver_id!=implementation_author_id"],
        },
        "classification": ["rejected", "archived", "shadow_candidate", "not_opened", "indeterminate"],
        "forbidden_actions": ["champion_promotion", "production_prediction", "public_non_uniform_prediction", "betting", "automatic_purchase", "yield_claim"],
        "results": [],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def bootstrap_prerun_contract(root: Path) -> dict[str, Any]:
    """Write the deterministic W01-W03 candidate package without a formal release."""
    root = root.resolve()
    authority_path = root / AUTHORITY["path"]
    if sha256(authority_path) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority identity mismatch")
    draws = _read_draws(root)
    manifest = _input_manifest(root, draws)
    manifest_path = _config_path(root, "input-manifest.json")
    _write_json(manifest_path, manifest)
    ledger = _availability_ledger(draws, sha256(manifest_path))
    ledger_path = _config_path(root, "availability-ledger.json")
    _write_json(ledger_path, ledger)
    data_time = _data_time_contract(sha256(manifest_path), sha256(ledger_path))
    data_time_path = _config_path(root, "data-time-contract.json")
    _write_json(data_time_path, data_time)
    preregistration = _preregistration(sha256(manifest_path), sha256(ledger_path), sha256(data_time_path))
    preregistration_path = _config_path(root, "preregistration.json")
    _write_json(preregistration_path, preregistration)
    return validate_prerun_contract(root)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_prerun_contract(root: Path) -> dict[str, Any]:
    """Validate the candidate contract and report whether a formal run is authorized."""
    root = root.resolve()
    payloads = {
        "input_manifest": load_json(_config_path(root, "input-manifest.json")),
        "availability_ledger": load_json(_config_path(root, "availability-ledger.json")),
        "data_time_contract": load_json(_config_path(root, "data-time-contract.json")),
        "preregistration": load_json(_config_path(root, "preregistration.json")),
    }
    for kind, payload in payloads.items():
        validate_schema(root, kind, payload)

    manifest = payloads["input_manifest"]
    if manifest["authority"] != AUTHORITY:
        raise ValueError("Phase 3 authority reference mismatch")
    if sha256(root / AUTHORITY["path"]) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority file identity mismatch")
    expected_roles = [row[0] for row in FROZEN_INPUTS]
    if [row["role"] for row in manifest["files"]] != expected_roles:
        raise ValueError("input manifest role coverage mismatch")
    for expected, actual in zip(FROZEN_INPUTS, manifest["files"], strict=True):
        role, relative, expected_sha256, allowed_use = expected
        if actual["path"] != relative or actual["sha256"] != expected_sha256 or actual["allowed_use"] != allowed_use:
            raise ValueError(f"input manifest mismatch: {role}")
        path = root / relative
        if not path.is_file() or sha256(path) != expected_sha256 or actual["bytes"] != path.stat().st_size:
            raise ValueError(f"frozen input identity mismatch: {relative}")

    draws = _read_draws(root)
    counts = Counter(row["game"] for row in draws)
    if manifest["draw_inventory"] != {"draw_count": 400, "by_game": {"dlt": 200, "ssq": 200}, "source_observation_count": 800}:
        raise ValueError("input manifest draw inventory mismatch")
    if len(draws) != 400 or dict(counts) != {"dlt": 200, "ssq": 200}:
        raise ValueError("frozen draw inventory mismatch")

    manifest_sha256 = sha256(_config_path(root, "input-manifest.json"))
    ledger = payloads["availability_ledger"]
    if ledger["input_manifest_sha256"] != manifest_sha256:
        raise ValueError("availability ledger input manifest mismatch")
    ordered_by_game = {
        game: sorted((row for row in draws if row["game"] == game), key=lambda row: row["issue_id"])
        for game in ("dlt", "ssq")
    }
    expected_ledger_keys = {
        (game, row["issue_id"], "prior_draw_result")
        for game, ordered in ordered_by_game.items()
        for row in ordered[50:]
    }
    observed_ledger_keys = {(row["game"], row["target_issue"], row["source_field"]) for row in ledger["entries"]}
    if observed_ledger_keys != expected_ledger_keys or len(ledger["entries"]) != len(expected_ledger_keys):
        raise ValueError("temporal eligibility ledger coverage mismatch")
    expanded_relation_count = 0
    for row in ledger["entries"]:
        ordered = ordered_by_game[row["game"]]
        target_index = next((index for index, draw in enumerate(ordered) if draw["issue_id"] == row["target_issue"]), -1)
        expected_sources = [draw["issue_id"] for draw in ordered[:target_index]] if target_index >= 50 else []
        if row["source_issues"] != expected_sources or row["source_count"] != len(expected_sources):
            raise ValueError(f"sequence ledger source prefix mismatch: {row['game']} {row['target_issue']}")
        if any(source >= row["target_issue"] for source in row["source_issues"]):
            raise ValueError("sequence ledger contains same/future issue")
        expanded_relation_count += row["source_count"]
    if expanded_relation_count != 37350:
        raise ValueError("sequence ledger expanded relation count mismatch")

    ledger_sha256 = sha256(_config_path(root, "availability-ledger.json"))
    data_time = payloads["data_time_contract"]
    if data_time["input_manifest_sha256"] != manifest_sha256 or data_time["availability_ledger_sha256"] != ledger_sha256:
        raise ValueError("data-time contract identity mismatch")
    if data_time["historical_mode"] != "retrospective_sequence_safe":
        raise ValueError("data-time contract historical mode mismatch")
    if data_time["external_feature_policy"] != "require_genuine_available_at_before_prediction_lock_or_reject":
        raise ValueError("data-time contract external feature policy mismatch")
    if data_time["label_unlock_state_machine"] != ["started", "forecast_locked", "label_unlocked", "scored", "terminal"]:
        raise ValueError("data-time contract label unlock state machine mismatch")
    if not REQUIRED_FORBIDDEN_FIELDS.issubset(set(data_time["forbidden_fields"])):
        raise ValueError("forbidden field coverage is incomplete")

    preregistration = payloads["preregistration"]
    if preregistration["input_manifest_sha256"] != manifest_sha256 or preregistration["availability_ledger_sha256"] != ledger_sha256:
        raise ValueError("preregistration input identity mismatch")
    if preregistration["data_time_contract_sha256"] != sha256(_config_path(root, "data-time-contract.json")):
        raise ValueError("preregistration data-time identity mismatch")
    if preregistration["results"]:
        raise ValueError("results are prohibited before Phase 3 formal release")
    if preregistration["models"]["M0"]["role"] != "permanent_champion" or not preregistration["models"]["M1"]["required"]:
        raise ValueError("M0/M1 preregistration contract mismatch")
    if any(preregistration["models"][model]["role"] != "not_opened" for model in ("M2", "M3", "M4")):
        raise ValueError("M2-M4 preregistration contract mismatch")
    if preregistration["metrics"]["primary"] != "relative_joint_log_score_skill_vs_M0" or preregistration["metrics"]["top_1000"] != "diagnostic_only":
        raise ValueError("preregistration metric contract mismatch")
    for game, ordered in ordered_by_game.items():
        expected_targets = [row["issue_id"] for row in ordered[50:]]
        if preregistration["games"][game]["outer_targets"] != expected_targets:
            raise ValueError(f"{game} outer targets mismatch")
    if preregistration["m1_contract"]["lambda_grid"] != [1.0, 5.0, 20.0, 100.0]:
        raise ValueError("M1 lambda grid mismatch")
    if preregistration["m1_contract"]["inner_validation_targets"] != 20 or preregistration["m1_contract"]["inner_minimum_training_draws"] != 30:
        raise ValueError("M1 inner validation contract mismatch")
    bootstrap = preregistration["classification_gate"]["bootstrap"]
    if bootstrap["replicates"] != 10000 or bootstrap["method"] != "non_circular_overlapping_moving_block":
        raise ValueError("classification bootstrap contract mismatch")
    if len(preregistration["classification_gate"]["classification_decision_tree"]) != 5:
        raise ValueError("classification decision tree mismatch")
    if preregistration["qualification_contract"]["replicates_per_world"] != 1000:
        raise ValueError("qualification replication contract mismatch")
    workload = preregistration["workload_contract"]
    if workload["logical_experiments"] != 600 or len(workload["benchmark_components"]) != 7:
        raise ValueError("formal workload contract mismatch")
    if preregistration["execution_contract"]["max_acceptance_iterations_per_release"] != 2:
        raise ValueError("acceptance iteration budget mismatch")
    if preregistration["formal_run_authorized"]:
        raise ValueError("formal run cannot be authorized by the candidate preregistration")

    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_prerun_contract_receipt",
        "status": "READY",
        "terminal": "READY_FOR_RESULTS_BLIND_FREEZE",
        "formal_run_authorized": False,
        "hold_reasons": [],
        "metrics": {
            "input_identity_coverage": 1.0,
            "draw_inventory_coverage": 1.0,
            "outer_target_coverage": 1.0,
            "sequence_relation_coverage": 1.0,
            "outer_target_count": len(ledger["entries"]),
            "expanded_sequence_relation_count": expanded_relation_count,
            "external_time_varying_feature_count": 0,
            "formal_result_count": 0,
        },
    }


def validate_prerun_work_item(root: Path, work_item: str) -> dict[str, Any]:
    """Run only the checks owned by W01, W02, or W03."""
    root = root.resolve()
    if work_item == "W03":
        complete = validate_prerun_contract(root)
        return {
            "terminal": "W03_PREREGISTRATION_READY",
            "metrics": {
                "outer_target_count": complete["metrics"]["outer_target_count"],
                "classification_algorithm_frozen": True,
                "workload_components_frozen": 7,
                "formal_result_count": complete["metrics"]["formal_result_count"],
            },
        }

    manifest = load_json(_config_path(root, "input-manifest.json"))
    validate_schema(root, "input_manifest", manifest)
    if manifest["authority"] != AUTHORITY or sha256(root / AUTHORITY["path"]) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority reference mismatch")
    if [row["role"] for row in manifest["files"]] != [row[0] for row in FROZEN_INPUTS]:
        raise ValueError("input manifest role coverage mismatch")
    for expected, actual in zip(FROZEN_INPUTS, manifest["files"], strict=True):
        role, relative, expected_sha256, allowed_use = expected
        path = root / relative
        if (actual["path"], actual["sha256"], actual["allowed_use"]) != (relative, expected_sha256, allowed_use):
            raise ValueError(f"input manifest mismatch: {role}")
        if not path.is_file() or sha256(path) != expected_sha256 or actual["bytes"] != path.stat().st_size:
            raise ValueError(f"frozen input identity mismatch: {relative}")
    draws = _read_draws(root)
    counts = Counter(row["game"] for row in draws)
    if len(draws) != 400 or dict(counts) != {"dlt": 200, "ssq": 200}:
        raise ValueError("frozen draw inventory mismatch")
    if work_item == "W01":
        return {"terminal": "W01_INPUTS_READY", "metrics": {"input_identity_coverage": 1.0, "draw_count": 400, "game_count": 2}}
    if work_item != "W02":
        raise ValueError(f"unsupported prerun work item: {work_item}")

    ledger = load_json(_config_path(root, "availability-ledger.json"))
    data_time = load_json(_config_path(root, "data-time-contract.json"))
    validate_schema(root, "availability_ledger", ledger)
    validate_schema(root, "data_time_contract", data_time)
    manifest_sha256 = sha256(_config_path(root, "input-manifest.json"))
    if ledger["input_manifest_sha256"] != manifest_sha256:
        raise ValueError("availability ledger input manifest mismatch")
    ordered_by_game = {game: sorted((row for row in draws if row["game"] == game), key=lambda row: row["issue_id"]) for game in ("dlt", "ssq")}
    expected_keys = {(game, row["issue_id"], "prior_draw_result") for game, ordered in ordered_by_game.items() for row in ordered[50:]}
    observed_keys = {(row["game"], row["target_issue"], row["source_field"]) for row in ledger["entries"]}
    if observed_keys != expected_keys or len(ledger["entries"]) != 300:
        raise ValueError("temporal eligibility ledger coverage mismatch")
    expanded = 0
    for row in ledger["entries"]:
        ordered = ordered_by_game[row["game"]]
        target_index = next(index for index, draw in enumerate(ordered) if draw["issue_id"] == row["target_issue"])
        expected_sources = [draw["issue_id"] for draw in ordered[:target_index]]
        if row["source_issues"] != expected_sources or any(source >= row["target_issue"] for source in row["source_issues"]):
            raise ValueError("sequence ledger source prefix mismatch")
        expanded += len(row["source_issues"])
    if expanded != 37350:
        raise ValueError("sequence ledger expanded relation count mismatch")
    if data_time["availability_ledger_sha256"] != sha256(_config_path(root, "availability-ledger.json")):
        raise ValueError("data-time contract identity mismatch")
    if data_time["label_unlock_state_machine"] != ["started", "forecast_locked", "label_unlocked", "scored", "terminal"]:
        raise ValueError("data-time contract label unlock state machine mismatch")
    if not REQUIRED_FORBIDDEN_FIELDS.issubset(set(data_time["forbidden_fields"])):
        raise ValueError("forbidden field coverage is incomplete")
    return {"terminal": "W02_SEQUENCE_TIME_READY", "metrics": {"outer_target_count": 300, "expanded_sequence_relation_count": 37350, "same_or_future_relation_count": 0, "external_time_varying_feature_count": 0}}
