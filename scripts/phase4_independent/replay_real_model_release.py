from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import p4e2_oracle as oracle


RULES = oracle.RULES
ROOT = Path(__file__).resolve().parents[2]
PROTECTED_ROOTS = (
    "artifacts/phase-0", "artifacts/phase-0-multisource", "artifacts/phase-1",
    "artifacts/phase-2", "artifacts/phase-2.1", "artifacts/phase-3",
    "artifacts/phase-4/P4-RMVP-20260815-r08",
)
LOCAL_CONTRACT_PATH = ROOT / "config/phase4/local-verifier-contract.json"


def local_contract() -> dict[str, object]:
    value = load(LOCAL_CONTRACT_PATH)
    if value.get("contract_id") != "P4-LOCAL-SEMANTIC-BINARY64-1":
        raise ValueError("HOLD_LOCAL_VERIFIER_CONTRACT")
    return value


def _ordered_bits(value: float) -> int:
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    return (~bits & ((1 << 64) - 1)) if bits & (1 << 63) else bits | (1 << 63)


def ulp_distance(left: float, right: float) -> int:
    if not math.isfinite(left) or not math.isfinite(right):
        raise ValueError("FAIL_NON_FINITE_NUMERIC_REPLAY")
    return abs(_ordered_bits(left) - _ordered_bits(right))


def numeric_comparison(left: object, right: object, *, contract: dict[str, object] | None = None) -> dict[str, object]:
    """Apply the frozen conjunctive finite/absolute/relative/ULP contract."""
    policy = (contract or local_contract())["numeric_bounds"]
    try:
        observed, expected = float(left), float(right)
    except (TypeError, ValueError) as exc:
        raise ValueError("HOLD_SEMANTIC_NUMERIC_TYPE") from exc
    if not math.isfinite(observed) or not math.isfinite(expected):
        raise ValueError("FAIL_NON_FINITE_NUMERIC_REPLAY")
    absolute = abs(observed - expected)
    relative = absolute / max(abs(observed), abs(expected)) if max(abs(observed), abs(expected)) else 0.0
    ulps = ulp_distance(observed, expected)
    passed = absolute <= policy["max_absolute"] and relative <= policy["max_relative"] and ulps <= policy["max_ulps"]
    return {"passed": passed, "absolute_error": absolute, "relative_error": relative, "ulp_distance": ulps}


def _path_allowed(path: str, contract: dict[str, object]) -> bool:
    parts = path.split(".")
    return any(len(pattern.split(".")) == len(parts)
               and all(expected == "*" or expected == observed
                       for expected, observed in zip(pattern.split("."), parts))
               for pattern in contract["semantic_numeric_paths"])


def compare_value(observed: object, expected: object, path: str, *, contract: dict[str, object] | None = None) -> dict[str, int]:
    """Compare recursively, permitting approximation only on explicit leaf paths."""
    policy = contract or local_contract()
    if isinstance(observed, dict) and isinstance(expected, dict):
        if set(observed) != set(expected):
            raise ValueError(f"HOLD_REPLAY_MISMATCH:{path}:keys")
        total = {"exact": 0, "semantic": 0}
        for key in sorted(observed):
            result = compare_value(observed[key], expected[key], f"{path}.{key}", contract=policy)
            total = {name: total[name] + result[name] for name in total}
        return total
    if isinstance(observed, list) and isinstance(expected, list):
        if len(observed) != len(expected):
            raise ValueError(f"HOLD_REPLAY_MISMATCH:{path}:length")
        total = {"exact": 0, "semantic": 0}
        for index, (left, right) in enumerate(zip(observed, expected)):
            result = compare_value(left, right, f"{path}.{index}", contract=policy)
            total = {name: total[name] + result[name] for name in total}
        return total
    if _path_allowed(path, policy):
        result = numeric_comparison(observed, expected, contract=policy)
        if not result["passed"]:
            raise ValueError(f"HOLD_REPLAY_NUMERIC_BOUND:{path}:abs={result['absolute_error']}:rel={result['relative_error']}:ulp={result['ulp_distance']}")
        return {"exact": 0, "semantic": 1}
    if observed != expected:
        raise ValueError(f"HOLD_REPLAY_MISMATCH:{path}")
    return {"exact": 1, "semantic": 0}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def same_json_document(left: Path, right: Path) -> bool:
    """Compare JSON contracts by their canonical document, not presentation whitespace."""
    return canon(load(left)) == canon(load(right))


def protected_inventory() -> dict[str, object]:
    roots = []
    for relative in PROTECTED_ROOTS:
        root = ROOT / relative
        hasher = hashlib.sha256()
        files = sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            rel = path.relative_to(root).as_posix()
            hasher.update(rel.encode("utf-8") + b"\0" + str(path.stat().st_size).encode() + b"\0" + sha(path).encode() + b"\n")
        roots.append({"path": relative, "file_count": len(files), "inventory_sha256": hasher.hexdigest()})
    return {"artifact_type": "phase4_protected_inventory", "algorithm": "relative_path_nul_size_nul_sha256_newline_v1", "roots": roots}


def load_draws(path: Path, game: str) -> list[oracle.Draw]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value["game"] != game:
            continue
        if value.get("available_at_utc") is not None:
            raise ValueError("FAIL_LEAKAGE:fabricated_available_at")
        rows.append(oracle.Draw(value["issue_id"], tuple(value["front_numbers"]), tuple(value["back_numbers"]), value["core_fact_sha256"]))
    if len(rows) < 120 or len({row.issue for row in rows}) != len(rows):
        raise ValueError("HOLD_FEATURE_INPUT")
    return rows


def _single(root: Path, pattern: str) -> Path:
    rows = list(root.glob(pattern))
    if len(rows) != 1:
        raise ValueError(f"expected exactly one path: {pattern}")
    return rows[0]


def _validate_frozen_top(rows: list[dict[str, object]], scope: str) -> None:
    if len(rows) != 1000:
        raise ValueError(f"HOLD_ILLEGAL_TOP1000:{scope}")
    identities = [row.get("score_identity") for row in rows]
    positions: dict[object, list[int]] = {}
    previous_score: float | None = None
    previous_ticket: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    for rank, row in enumerate(rows, 1):
        ticket = (tuple(row["front_numbers"]), tuple(row["back_numbers"]))
        score = float(row["log_joint_score"])
        if not math.isfinite(score) or not math.isfinite(float(row["joint_probability"])):
            raise ValueError("FAIL_NON_FINITE_NUMERIC_REPLAY")
        identity = oracle.score_identity(score)
        probability_hash = hashlib.sha256(identity.encode()).hexdigest()
        if (row.get("rank") != rank or row.get("full_space_rank") != rank
                or row.get("canonical_ticket_key") != [list(ticket[0]), list(ticket[1])]
                or row.get("score_identity") != identity
                or row.get("tie_group_id") != f"tie-{probability_hash[:24]}"
                or row.get("tie_key") != f"score-identity:{probability_hash}"
                or row.get("ranking_algorithm_id") != "joint_binary64_score_desc_exact_tie_canonical_ticket_asc_v1"):
            raise ValueError(f"HOLD_TIE_IDENTITY:{scope}:{rank}")
        if previous_score is not None and (score > previous_score or (score == previous_score and ticket < previous_ticket)):
            raise ValueError(f"HOLD_TOP1000_ORDER:{scope}:{rank}")
        previous_score, previous_ticket = score, ticket
        positions.setdefault(identity, []).append(rank)
    if len({(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in rows}) != 1000:
        raise ValueError(f"HOLD_ILLEGAL_TOP1000:{scope}:duplicate")
    layer, previous_identity = 0, None
    for rank, row in enumerate(rows, 1):
        identity = row["score_identity"]
        if identity != previous_identity:
            layer += 1
            previous_identity = identity
        peers = positions[identity]
        if (row.get("probability_layer") != layer or row.get("tie_group_size") != len(peers)
                or row.get("tie_rank_lower") != min(peers) or row.get("tie_rank_upper") != max(peers)
                or row.get("tie_midrank") != format((min(peers) + max(peers)) / 2, ".1f")):
            raise ValueError(f"HOLD_TIE_BOUNDS:{scope}:{rank}")


def _compare_top(observed: list[dict[str, object]], expected: list[dict[str, object]], scope: str) -> dict[str, int]:
    _validate_frozen_top(observed, scope)
    if len(expected) != 1000:
        raise ValueError(f"HOLD_REPLAY_MISMATCH:{scope}:expected_count")
    totals = {"exact": 0, "semantic": 0}
    exact_paths = (
        "rank", "full_space_rank", "front_numbers", "back_numbers", "canonical_ticket_key",
        "probability_representation", "ranking_algorithm_id", "lineage",
        "explanation.method", "explanation.probability_primary", "explanation.feature_groups",
    )
    for index, (left, right) in enumerate(zip(observed, expected)):
        for key in exact_paths:
            left_value, right_value = left, right
            for segment in key.split("."):
                left_value, right_value = left_value[segment], right_value[segment]
            if left_value != right_value:
                raise ValueError(f"HOLD_REPLAY_MISMATCH:{scope}.{index}.{key}")
            totals["exact"] += 1
        for key in ("joint_probability", "log_joint_score"):
            result = compare_value(left[key], right[key], f"{scope}.{index}.{key}")
            totals = {name: totals[name] + result[name] for name in totals}
        for feature in oracle.FEATURE_IDS:
            result = compare_value(left["explanation"]["feature_contributions"][feature],
                                   right["explanation"]["feature_contributions"][feature],
                                   f"{scope}.{index}.explanation.feature_contributions.{feature}")
            totals = {name: totals[name] + result[name] for name in totals}
    return totals


def replay_game(release: Path, draws_path: Path, game: str) -> dict[str, object]:
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    if serving.get("family") != "P4E2-R" or serving.get("non_m0") is not True:
        raise ValueError("HOLD_M0_OR_NON_P4E2_SERVING")
    if set(serving.get("feature_ids", [])) != set(oracle.FEATURE_IDS):
        raise ValueError("HOLD_F01_F14_NOT_CONSUMED")
    if set(serving.get("feature_groups_consumed", [])) != set(oracle.FEATURE_GROUPS.values()):
        raise ValueError("HOLD_FEATURE_GROUP_MISSING")
    model_path = release / serving["model_path"]
    model_manifest = load(model_path.with_name("manifest.json"))
    model = load(model_path)
    if sha(model_path) != model_manifest["model_sha256"]:
        raise ValueError("FAIL_TAMPERED:model_hash")
    if model["model_release_id"] != serving["model_release_id"] or model["feature_release_id"] != serving["feature_release_id"]:
        raise ValueError("FAIL_TAMPERED:model_identity")
    draws = load_draws(draws_path, game)
    data_manifest = load(release / f"data/{game}/training-input-manifest.json")
    if sha(draws_path) != data_manifest["draws_sha256"] or data_manifest["available_at_fabricated"] or data_manifest["fixture_input"]:
        raise ValueError("FAIL_TAMPERED:training_input")
    cutoff = next(index for index, draw in enumerate(draws) if draw.issue == model["training_cutoff_issue"]) + 1
    if cutoff != model["training_count"] or model["training_cutoff_position"] >= model["forecast_target_position"]:
        raise ValueError("FAIL_LEAKAGE:cutoff")

    expected = oracle.train(game, draws, cutoff)
    core_keys = ("family", "game", "rule_id", "training_cutoff_issue", "training_cutoff_position",
                 "forecast_target_position", "training_count", "canonical_order_id", "canonical_comparator_id",
                 "training_dataset_id", "training_config_id", "knowledge_contract", "available_at_fabricated",
                 "feature_ids", "feature_groups_consumed", "regularization", "estimator",
                 "selection_indices", "report_only_indices", "selected_candidate_identity")
    for key in core_keys:
        if model.get(key) != expected.get(key):
            raise ValueError(f"HOLD_REPLAY_MISMATCH:model:{key}")
    comparisons = {"exact": len(core_keys), "semantic": 0}
    for key in ("objective_trace", "selection_metrics"):
        result = compare_value(model[key], expected[key], f"model.{key}")
        comparisons = {name: comparisons[name] + result[name] for name in comparisons}
    expected_model_id = f"p4e2r-{game}-{oracle.digest({'family': model['family'], 'game': game, 'cutoff': model['training_cutoff_issue'], 'l2': model['regularization']['selected'], 'coefficients': [zone['coefficients'] for zone in model['zones']], 'training_dataset_id': model['training_dataset_id'], 'training_config_id': model['training_config_id'], 'selection': model['selected_candidate_identity']})[:16]}"
    if model.get("model_release_id") != expected_model_id:
        raise ValueError("HOLD_REPLAY_MISMATCH:model:model_release_id")
    for zone_index, (observed_zone, expected_zone) in enumerate(zip(model["zones"], expected["zones"])):
        for key in ("n", "k", "coefficients", "context", "top_zone_rows", "log_normalizer", "probability_square_sum",
                    "combination_count", "normalization_method", "normalization_mass", "minimum_score", "maximum_score",
                    "minimum_probability", "maximum_probability", "probability_layer_lower_bound"):
            result = compare_value(observed_zone.get(key), expected_zone.get(key), f"model.zones.{zone_index}.{key}")
            comparisons = {name: comparisons[name] + result[name] for name in comparisons}
    selection_receipt = load(release / f"models/{game}/model-selection-receipt.json")
    selection_payload = {key: value for key, value in selection_receipt.items() if key not in {"receipt_hash", "selection_metrics"}}
    if (selection_receipt.get("receipt_hash") != oracle.digest(selection_payload)
            or model.get("selection_receipt_hash") != selection_receipt.get("receipt_hash")
            or selection_receipt.get("selected_config_identity") != expected["selected_candidate_identity"]):
        raise ValueError("HOLD_REPLAY_MISMATCH:model_selection_receipt")
    compare_value(selection_receipt.get("selection_metrics"), expected["selection_metrics"], "selection_receipt.selection_metrics")
    feature_dir = release / f"features/{game}/{serving['feature_release_id']}"
    snapshot_path = feature_dir / "feature-snapshot.jsonl"
    feature_manifest = load(feature_dir / "manifest.json")
    expected_rows = oracle.feature_snapshot_rows(game, draws[:cutoff], cutoff)
    expected_snapshot = b"".join(canon(row) for row in expected_rows)
    if snapshot_path.read_bytes() != expected_snapshot or sha(snapshot_path) != feature_manifest["snapshot_sha256"]:
        raise ValueError("HOLD_REPLAY_MISMATCH:feature_snapshot")
    if feature_manifest["pair_parameter_count"] != 0 or set(feature_manifest["feature_ids"]) != set(oracle.FEATURE_IDS):
        raise ValueError("HOLD_REPLAY_MISMATCH:feature_contract")

    formal = _single(release / f"forecasts/{game}", "*/top1000.jsonl")
    forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
    forecast, lock = load(forecast_path), load(lock_path)
    if (forecast["provider_access"] != [serving["model_path"]] or forecast["model_release_id"] != model["model_release_id"]
            or forecast.get("model_sha256") != sha(model_path) or forecast.get("feature_release_id") != model["feature_release_id"]
            or forecast.get("data_release_id") != model["training_dataset_id"] or forecast.get("config_id") != model["training_config_id"]
            or forecast.get("code_commit") != model["source_commit"] or forecast.get("dependency_identity") != model["dependency_identity"]
            or forecast.get("ranking_algorithm_id") != "joint_binary64_score_desc_exact_tie_canonical_ticket_asc_v1"
            or not forecast.get("prediction_locked_at_utc")):
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH")
    if (lock["content_sha256"] != sha(forecast_path) or lock["top1000_sha256"] != sha(formal) or lock["status"] != "LOCKED"
            or not lock.get("create_once") or lock.get("lock_id") != forecast.get("lock_id")
            or lock.get("game") != game or lock.get("target_issue") != forecast.get("target_issue")):
        raise ValueError("FAIL_TAMPERED:lock")
    expected_model = dict(expected)
    expected_model.update(feature_release_id=model["feature_release_id"], model_release_id=model["model_release_id"])
    expected_top = oracle.top_tickets(expected_model)
    observed_top = [json.loads(line) for line in formal.read_text(encoding="utf-8").splitlines()]
    top_comparisons = _compare_top(observed_top, expected_top, "top1000")
    probabilities = [float(row["joint_probability"]) for row in observed_top]
    identities = [row["score_identity"] for row in observed_top]
    if (len(set(identities)) < 2 or not all(left >= right > 0 for left, right in zip(probabilities, probabilities[1:]))
            or oracle.score_identity(1.0) == oracle.score_identity(math.nextafter(1.0, 2.0))):
        raise ValueError("HOLD_UNRELIABLE_RANKING")
    if any(observed_top[:size] != observed_top[0:size] for size in (10, 100, 200, 1000)):
        raise ValueError("HOLD_TOP_PREFIX")

    # Recompute report-only metrics, true group ablations, and raw-feature
    # derangements from the held-out labels instead of trusting report fields.
    selected_l2 = float(model["regularization"]["selected"])
    for sample_index, target in enumerate(model["report_only_indices"]):
        fold_coefficients = oracle.fit_coefficients(game, draws, target, selected_l2)
        base = oracle.evaluate_coefficients(game, draws, target, fold_coefficients, True)
        recorded = model["report_only_metrics"][sample_index]
        for key, expected_value in (("model_joint_log_loss", base["joint_log_loss"]),
                                    ("model_multiclass_brier", base["multiclass_brier"]),
                                    ("full_ticket_rank", base["outcome_rank"])):
            compare_value(recorded.get(key), expected_value, f"model.report_only_metrics.{sample_index}.{key}")
        for group in sorted(set(oracle.FEATURE_GROUPS.values())):
            ablated_coefficients = [
                {key: (0.0 if oracle.FEATURE_GROUPS[key] == group else value) for key, value in zone.items()}
                for zone in fold_coefficients
            ]
            ablated = oracle.evaluate_coefficients(game, draws, target, ablated_coefficients, True)
            evidence = next(row for row in recorded["ablation_metrics"] if row["feature_group"] == group)
            if evidence["full_ticket_rank"] != ablated["outcome_rank"] or any(zone["normalization_mass"] != 1.0 for zone in evidence["normalization"]):
                raise ValueError("HOLD_REPLAY_MISMATCH:ablation")
            compare_value(evidence["joint_log_loss"], ablated["joint_log_loss"],
                          f"model.report_only_metrics.{sample_index}.ablation_metrics.{group}.joint_log_loss")
            compare_value(evidence["multiclass_brier"], ablated["multiclass_brier"],
                          f"model.report_only_metrics.{sample_index}.ablation_metrics.{group}.multiclass_brier")
    for offset, group in enumerate(sorted(set(oracle.FEATURE_GROUPS.values())), 1):
        evidence = next(row for row in model["report_only_summary"]["permutation_evidence"] if row["feature_group"] == group)
        if evidence.get("method") != "held_out_feature_group_derangement_recompute_fitted_model_score_v1" or evidence.get("sample_size") != len(model["report_only_indices"]):
            raise ValueError("HOLD_REPLAY_MISMATCH:permutation_method")
        shift = ((offset - 1) % (len(model["report_only_indices"]) - 1)) + 1
        for sample_index, target in enumerate(model["report_only_indices"]):
            donor = model["report_only_indices"][(sample_index + shift) % len(model["report_only_indices"])]
            fold_coefficients = oracle.fit_coefficients(game, draws, target, selected_l2)
            contexts = [oracle.feature_context(game, draws[:target], zone) for zone in (0, 1)]
            observed = [oracle.combo_vector(oracle._numbers(draws[target], zone), contexts[zone]) for zone in (0, 1)]
            donated = [oracle.combo_vector(oracle._numbers(draws[donor], zone), contexts[zone]) for zone in (0, 1)]
            score_value = math.fsum(fold_coefficients[zone][key] * (donated[zone][feature_index] if oracle.FEATURE_GROUPS[key] == group else observed[zone][feature_index])
                                    for zone in (0, 1) for feature_index, key in enumerate(oracle.FEATURE_IDS))
            normalizers = [float(row["log_normalizer"]) for row in model["report_only_metrics"][sample_index]["normalization"]]
            probability = math.exp(score_value - math.fsum(normalizers))
            sample = evidence["samples"][sample_index]
            if sample["donor_position"] != donor:
                raise ValueError("HOLD_REPLAY_MISMATCH:permutation_score")
            compare_value(sample["permuted_joint_probability"], probability,
                          f"model.report_only_summary.permutation_evidence.{offset - 1}.samples.{sample_index}.permuted_joint_probability")
            compare_value(sample["permuted_joint_log_loss"], -math.log(probability),
                          f"model.report_only_summary.permutation_evidence.{offset - 1}.samples.{sample_index}.permuted_joint_log_loss")

    lifecycle = release / f"runtime/lifecycle/{game}/historical-cycle-v1"
    cycle, parent, historical_forecast, historical_lock, result, score = (load(lifecycle / name) for name in ("cycle.json", "parent-model.json", "forecast.json", "lock.json", "result-revision.json", "score.json"))
    expected_parent = oracle.train(game, draws, len(draws) - 1)
    if (cycle["target_issue"] != draws[-1].issue or parent["training_cutoff_issue"] != draws[-2].issue):
        raise ValueError("HOLD_REPLAY_MISMATCH:historical_parent")
    compare_value([zone["coefficients"] for zone in parent["zones"]],
                  [zone["coefficients"] for zone in expected_parent["zones"]], "historical_parent.zones")
    expected_parent_id = f"p4e2r-{game}-{oracle.digest({'family': parent['family'], 'game': game, 'cutoff': parent['training_cutoff_issue'], 'l2': parent['regularization']['selected'], 'coefficients': [zone['coefficients'] for zone in parent['zones']], 'training_dataset_id': parent['training_dataset_id'], 'training_config_id': parent['training_config_id'], 'selection': parent['selected_candidate_identity']})[:16]}"
    if parent["model_release_id"] != expected_parent_id:
        raise ValueError("HOLD_REPLAY_MISMATCH:historical_parent:model_release_id")
    historical_top_path = lifecycle / "top1000.jsonl"
    expected_historical_top = oracle.top_tickets({**expected_parent, "feature_release_id": parent["feature_release_id"],
                                                   "model_release_id": parent["model_release_id"]})
    observed_historical_top = [json.loads(line) for line in historical_top_path.read_text(encoding="utf-8").splitlines()]
    historical_comparisons = _compare_top(observed_historical_top, expected_historical_top, "historical_top1000")
    if (historical_lock["forecast_sha256"] != sha(lifecycle / "forecast.json")
            or historical_lock["top1000_sha256"] != sha(historical_top_path)
            or historical_forecast["model_sha256"] != sha(lifecycle / "parent-model.json")
            or result["game"] != game or result["target_issue"] != historical_forecast["target_issue"]):
        raise ValueError("HOLD_REPLAY_MISMATCH:historical_lock_result")
    expected_score = oracle.score_ticket(parent, draws[-1], expected_historical_top)
    compare_value(score["metrics"], expected_score, "score.metrics")
    if (score["forecast_id"] != historical_forecast["forecast_id"]
            or score["result_revision_id"] != result["result_revision_id"] or score["model_release_id"] != parent["model_release_id"]):
        raise ValueError("HOLD_REPLAY_MISMATCH:exact_score")

    research = release / f"research/{game}"
    diff, candidate, decision = (load(research / name) for name in ("diff.json", "candidate.json", "decision.json"))
    proposal = diff.get("change", {})
    if (proposal.get("type") != "score_driven_bounded_l2_refit" or diff.get("score_id") != score["score_id"]
            or diff.get("result_revision_id") != result["result_revision_id"] or not diff.get("non_noop")
            or diff.get("future_data_used") or diff.get("direct_promotion")):
        raise ValueError("HOLD_REPLAY_MISMATCH:research_diff")
    coefficients = oracle.fit_coefficients(game, draws, len(draws), float(proposal["child_l2"]))
    child = load(research / "child-model.json")
    compare_value([zone["coefficients"] for zone in child["zones"]], coefficients, "research_child.zones")
    if (child["parent_model_release_id"] != parent["model_release_id"]
            or child["research_score_id"] != score["score_id"] or child["research_result_revision_id"] != result["result_revision_id"]
            or candidate.get("child_model_release_id") != child["model_release_id"]
            or decision.get("child_model_release_id") != child["model_release_id"]):
        raise ValueError("HOLD_REPLAY_MISMATCH:research_child")
    child_manifest = load(research / "child-model-manifest.json")
    child_feature_manifest = load(research / "child-feature-manifest.json")
    if (child_manifest.get("child_model_sha256") != sha(research / "child-model.json")
            or child_manifest.get("shadow_top1000_sha256") != sha(research / "shadow-top1000.jsonl")
            or child_manifest.get("child_feature_snapshot_sha256") != sha(research / "child-feature-snapshot.jsonl")
            or child_feature_manifest.get("snapshot_sha256") != sha(research / "child-feature-snapshot.jsonl")
            or child_feature_manifest.get("feature_release_id") != child.get("feature_release_id")
            or child_manifest.get("score_sha256") != sha(lifecycle / "score.json")
            or child_manifest.get("result_revision_sha256") != sha(lifecycle / "result-revision.json")
            or child_manifest.get("role") != "shadow_only"):
        raise ValueError("HOLD_REPLAY_MISMATCH:research_child_manifest")
    shadow_path = research / "shadow-top1000.jsonl"
    observed_shadow = [json.loads(line) for line in shadow_path.read_text(encoding="utf-8").splitlines()]
    shadow_comparisons = _compare_top(observed_shadow, oracle.top_tickets(child), "shadow_top1000")
    if (not decision.get("probability_changed") or not decision.get("top1000_changed")
            or decision.get("serving_changed") or not decision.get("direct_promotion_attempt_rejected")):
        raise ValueError("HOLD_REPLAY_MISMATCH:research_shadow")
    immutability = load(research / "serving-immutability.json")
    serving_sha = sha(release / "selection/serving-selection.json")
    if immutability.get("serving_selection_sha256_before") != serving_sha or immutability.get("serving_selection_sha256_after") != serving_sha:
        raise ValueError("HOLD_REPLAY_MISMATCH:research_serving_immutability")
    return {
        "game": game, "feature_match": True, "selection_match": True, "coefficient_match": True,
        "normalization_match": True, "top1000_match": True, "ticket_count": 1000,
        "model_sha256": sha(model_path), "feature_snapshot_sha256": sha(snapshot_path),
        "top1000_sha256": sha(formal), "complete_space_probability_mass": 1.0,
        "selection_receipt_match": True, "ablation_match": True, "permutation_match": True,
        "historical_exact_score_match": True, "research_child_match": True, "shadow_top1000_match": True, "serving_unchanged": True,
        "numeric_contract_id": local_contract()["contract_id"],
        "semantic_numeric_comparisons": comparisons["semantic"] + top_comparisons["semantic"] + historical_comparisons["semantic"] + shadow_comparisons["semantic"],
    }


def quick_guard(release: Path, draws_path: Path) -> None:
    selection_path = release / "selection/serving-selection.json"
    selection = load(selection_path)
    serving = selection["serving_model_by_game"]["ssq"]
    if serving.get("family") != "P4E2-R" or serving.get("non_m0") is not True:
        raise ValueError("M0")
    model_path = release / serving["model_path"]
    model_manifest = load(model_path.with_name("manifest.json"))
    if sha(model_path) != model_manifest["model_sha256"]:
        raise ValueError("model")
    data = load(release / "data/ssq/training-input-manifest.json")
    if sha(draws_path) != data["draws_sha256"]:
        raise ValueError("draw")
    feature_dir = release / f"features/ssq/{serving['feature_release_id']}"
    if sha(feature_dir / "feature-snapshot.jsonl") != load(feature_dir / "manifest.json")["snapshot_sha256"]:
        raise ValueError("feature")
    formal = _single(release / "forecasts/ssq", "*/top1000.jsonl")
    forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
    forecast, lock = load(forecast_path), load(lock_path)
    if (forecast.get("provider_access") != [serving["model_path"]] or sha(forecast_path) != lock["content_sha256"]
            or forecast.get("model_sha256") != sha(model_path) or not forecast.get("feature_manifest_sha256")
            or not forecast.get("data_manifest_sha256") or not forecast.get("code_commit")
            or not forecast.get("dependency_identity") or not forecast.get("ranking_algorithm_id")
            or forecast.get("lock_id") != lock.get("lock_id")):
        raise ValueError("provider")
    if sha(formal) != lock["top1000_sha256"]:
        raise ValueError("top")
    model = load(model_path)
    if set(model["selection_indices"]) & set(model["report_only_indices"]):
        raise ValueError("fold")
    selection_receipt = load(release / "models/ssq/model-selection-receipt.json")
    payload = {key: value for key, value in selection_receipt.items() if key not in {"receipt_hash", "selection_metrics"}}
    if (selection_receipt["receipt_hash"] != oracle.digest(payload)
            or selection_receipt["selection_input"]["last_position"] >= selection_receipt["report_only_capability_boundary"]["first_position"]
            or model["selection_receipt_hash"] != selection_receipt["receipt_hash"]):
        raise ValueError("selection")
    rows = [json.loads(line) for line in formal.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row.get("score_identity") != oracle.score_identity(float(row["log_joint_score"])):
            raise ValueError("tie")
    summary = model["report_only_summary"]
    if (any(row.get("method") != "zero_group_coefficients_complete_space_renormalization_v1" or not row.get("all_complete_spaces_renormalized") for row in summary["ablation_results"])
            or any(row.get("method") != "held_out_feature_group_derangement_recompute_fitted_model_score_v1" or not row.get("samples") for row in summary["permutation_evidence"])):
        raise ValueError("science")
    lifecycle = release / "runtime/lifecycle/ssq/historical-cycle-v1"
    historical_forecast, result, score = (load(lifecycle / name) for name in ("forecast.json", "result-revision.json", "score.json"))
    if (score.get("forecast_id") != historical_forecast.get("forecast_id") or score.get("result_revision_id") != result.get("result_revision_id")
            or result.get("target_issue") != historical_forecast.get("target_issue")):
        raise ValueError("score")
    decision = load(release / "research/ssq/diff.json")
    if decision.get("score_id") != score.get("score_id") or decision.get("result_revision_id") != result.get("result_revision_id"):
        raise ValueError("research")
    recovery = load(release / "runtime/schedule/recovery-ssq-dlt.json")
    if not recovery.get("same_output_identities") or recovery.get("duplicate_side_effects") != 0:
        raise ValueError("schedule")
    for baseline in recovery["baseline_runs"].values():
        if set(baseline["stage_operation_ids"]) != {"prepare", "forecast_lock", "official_result_ingest", "unlock_score", "research_shadow"}:
            raise ValueError("schedule")


def mutation_checks(release: Path, draws_path: Path) -> dict[str, str]:
    cases = (
        "early_draw", "cutoff", "rolling", "ewma", "gap", "pair", "structure",
        "coefficient", "model_id", "probability", "top1000_order", "lock",
        "provider_reference", "m0_serving", "selection_report_overlap",
        "schedule_stage_noop", "score_forecast_mismatch", "result_target_mismatch",
        "research_without_score", "selection_after_report_labels", "fake_ablation",
        "fake_permutation", "missing_lineage", "approximate_tie", "shallow_cli_validate",
        "protected_root_change",
    )
    detected = {}
    for case in cases:
        with tempfile.TemporaryDirectory(prefix=f"p4e2-replay-{case}-") as raw:
            copy = Path(raw) / "release"
            shutil.copytree(release, copy)
            draw_copy = Path(raw) / "draws.jsonl"
            shutil.copy2(draws_path, draw_copy)
            selection_path = copy / "selection/serving-selection.json"
            selection = load(selection_path)
            serving = selection["serving_model_by_game"]["ssq"]
            model_path = copy / serving["model_path"]
            model = load(model_path)
            feature = copy / f"features/ssq/{serving['feature_release_id']}/feature-snapshot.jsonl"
            formal = _single(copy / "forecasts/ssq", "*/top1000.jsonl")
            forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
            if case in {"early_draw", "protected_root_change"}:
                rows = draw_copy.read_text().splitlines()
                value = json.loads(next(row for row in rows if json.loads(row)["game"] == "ssq"))
                position = next(index for index, row in enumerate(rows) if json.loads(row).get("core_fact_sha256") == value["core_fact_sha256"])
                value["core_fact_sha256"] = "0" * 64
                rows[position] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                draw_copy.write_text("\n".join(rows) + "\n")
            elif case in {"rolling", "ewma", "gap", "pair", "structure"}:
                rows = feature.read_text(encoding="utf-8").splitlines()
                feature_id = {"rolling": "F02", "ewma": "F03", "gap": "F04", "pair": "F06", "structure": "F08"}[case]
                for index, encoded in enumerate(rows):
                    value = json.loads(encoded)
                    if feature_id in value.get("feature_values", {}):
                        value["feature_values"][feature_id] = format(float(value["feature_values"][feature_id]) + 0.01, ".17g")
                        rows[index] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                        break
                else:
                    raise ValueError(f"missing mutation feature {feature_id}")
                feature.write_text("\n".join(rows) + "\n", encoding="utf-8")
            elif case in {"cutoff", "coefficient", "model_id", "selection_report_overlap"}:
                if case == "cutoff": model["training_count"] += 1
                elif case == "coefficient": model["zones"][0]["coefficients"]["F08"] += .01
                elif case == "model_id": model["model_release_id"] += "-tampered"
                else: model["report_only_indices"][0] = model["selection_indices"][0]
                model_path.write_bytes(canon(model))
            elif case in {"probability", "top1000_order"}:
                rows = formal.read_text().splitlines()
                if case == "probability":
                    value = json.loads(rows[0]); value["joint_probability"] = "1.0"; rows[0] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                else:
                    rows[0], rows[1] = rows[1], rows[0]
                formal.write_text("\n".join(rows) + "\n")
            elif case == "lock":
                lock = load(lock_path); lock["top1000_sha256"] = "0" * 64; lock_path.write_bytes(canon(lock))
            elif case == "provider_reference":
                forecast = load(forecast_path); forecast["provider_access"] = ["fixture"]; forecast_path.write_bytes(canon(forecast))
            elif case == "m0_serving":
                serving["family"], serving["non_m0"] = "M0", False; selection_path.write_bytes(canon(selection))
            elif case == "schedule_stage_noop":
                recovery = copy / "runtime/schedule/recovery-ssq-dlt.json"
                value = load(recovery); value["same_output_identities"] = False; recovery.write_bytes(canon(value))
            elif case in {"score_forecast_mismatch", "shallow_cli_validate"}:
                score_path = copy / "runtime/lifecycle/ssq/historical-cycle-v1/score.json"
                if case == "score_forecast_mismatch":
                    value = load(score_path); value["forecast_id"] += "-wrong"; score_path.write_bytes(canon(value))
                else:
                    score_path.unlink()
            elif case == "result_target_mismatch":
                result_path = copy / "runtime/lifecycle/ssq/historical-cycle-v1/result-revision.json"
                value = load(result_path); value["target_issue"] += "-wrong"; result_path.write_bytes(canon(value))
            elif case == "research_without_score":
                diff_path = copy / "research/ssq/diff.json"
                value = load(diff_path); value["score_id"] += "-missing"; diff_path.write_bytes(canon(value))
            elif case == "selection_after_report_labels":
                receipt_path = copy / "models/ssq/model-selection-receipt.json"
                value = load(receipt_path); value["selection_input"]["last_position"] = value["report_only_capability_boundary"]["first_position"]
                payload = {key: item for key, item in value.items() if key not in {"receipt_hash", "selection_metrics"}}
                value["receipt_hash"] = oracle.digest(payload); receipt_path.write_bytes(canon(value))
            elif case in {"fake_ablation", "fake_permutation"}:
                if case == "fake_ablation": model["report_only_summary"]["ablation_results"][0]["method"] = "asserted_zero_without_recompute"
                else: model["report_only_summary"]["permutation_evidence"][0]["method"] = "rotated_contributions"
                model_path.write_bytes(canon(model))
            elif case == "missing_lineage":
                forecast = load(forecast_path); del forecast["model_sha256"]; forecast_path.write_bytes(canon(forecast))
            elif case == "approximate_tie":
                rows = formal.read_text().splitlines(); first, second = json.loads(rows[0]), json.loads(rows[1])
                second["score_identity"] = first["score_identity"]; second["tie_group_id"] = first["tie_group_id"]
                rows[1] = json.dumps(second, sort_keys=True, separators=(",", ":")); formal.write_text("\n".join(rows) + "\n")
            try:
                quick_guard(copy, draw_copy)
            except (ValueError, KeyError, IndexError, StopIteration, json.JSONDecodeError, OSError):
                detected[case] = "DETECTED"
            else:
                raise ValueError(f"mutation escaped independent replay: {case}")
    return detected


def _release_inventory(release: Path) -> dict[str, tuple[int, str]]:
    return {path.relative_to(release).as_posix(): (path.stat().st_size, sha(path))
            for path in sorted(item for item in release.rglob("*") if item.is_file())}


def _validate_final_closure(release: Path) -> dict[str, str]:
    final_paths = {
        "acceptance/machine-acceptance.json",
        "acceptance/checklist-release-receipt.json",
        "acceptance/final-closure.json",
    }
    manifest_path = release / "manifest/delivery-manifest.json"
    acceptance_path = release / "acceptance/machine-acceptance.json"
    receipt_path = release / "acceptance/checklist-release-receipt.json"
    closure_path = release / "acceptance/final-closure.json"
    manifest, acceptance, receipt, closure = (load(path) for path in (manifest_path, acceptance_path, receipt_path, closure_path))
    entries = manifest.get("entries", [])
    if entries != sorted(entries, key=lambda row: row["path"]) or len({row["path"] for row in entries}) != len(entries):
        raise ValueError("HOLD_MANIFEST_NOT_CLOSED:ordering")
    expected_pre = {relative: identity for relative, identity in _release_inventory(release).items()
                    if relative not in final_paths | {"manifest/delivery-manifest.json"}}
    recorded_pre = {row["path"]: (row["bytes"], row["sha256"]) for row in entries}
    if recorded_pre != expected_pre:
        raise ValueError("HOLD_MANIFEST_NOT_CLOSED:inventory")
    if (closure.get("manifest_sha256") != sha(manifest_path)
            or closure.get("machine_acceptance_sha256") != sha(acceptance_path)
            or closure.get("checklist_release_receipt_sha256") != sha(receipt_path)
            or receipt.get("manifest_sha256") != sha(manifest_path)
            or receipt.get("machine_acceptance_sha256") != sha(acceptance_path)
            or receipt.get("checklist_sha256") != sha(release / "acceptance/local-product-checklist-candidate.md")
            or acceptance.get("pre_acceptance_hashes") != {row["path"]: row["sha256"] for row in entries}
            or acceptance.get("machine_state") != "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE"
            or closure.get("machine_state") != "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE"
            or not closure.get("pre_acceptance_unchanged")):
        raise ValueError("HOLD_FINAL_CLOSURE_MISMATCH")
    return {"manifest_sha256": sha(manifest_path), "machine_acceptance_sha256": sha(acceptance_path),
            "final_closure_sha256": sha(closure_path)}


def _validate_authority_and_receipts(release: Path) -> dict[str, object]:
    authority = load(ROOT / "config/phase4/authority-freeze.json")
    if load(release / "authority/authority-freeze.json") != authority:
        raise ValueError("HOLD_AUTHORITY_SYNC:release_freeze")
    for row in authority["authority_files"]:
        path = ROOT / row["path"]
        if not path.is_file() or sha(path) != row["sha256"]:
            raise ValueError(f"HOLD_AUTHORITY_SYNC:{row['path']}")
    required_tasks = [release / "contracts/D01-receipt.json"] + [release / f"receipts/D{i:02d}.json" for i in range(2, 13)] + [release / "receipts/D14.json"]
    for path in required_tasks:
        value = load(path)
        if value.get("status") != "PASS" or value.get("exit_code") != 0 or value.get("blocking_findings"):
            raise ValueError(f"HOLD_INVALID_TASK_RECEIPT:{path.name}")
    required_attempts = {"A01-compileall", "A02-phase4", "A03-phase4-oracle", "A04-phase3", "A05-phase2-1",
                         "A06-phase2", "A07-authority", "A08-contract", "A09-bottom-up", "A10-replay-validation"}
    attempts = {path.parent.name: load(path) for path in (release / "validation/attempts").glob("*/receipt.json")}
    if not required_attempts <= attempts.keys():
        raise ValueError(f"HOLD_FINAL_REGRESSION_INCOMPLETE:{sorted(required_attempts - attempts.keys())}")
    for attempt_id in required_attempts:
        value = attempts[attempt_id]
        if value.get("status") != "PASS" or value.get("exit_code") != 0:
            raise ValueError(f"HOLD_FORMAL_RECEIPT:{attempt_id}")
    contract_path = release / "contracts/local-verifier-contract.json"
    if not same_json_document(contract_path, LOCAL_CONTRACT_PATH):
        raise ValueError("HOLD_LOCAL_VERIFIER_CONTRACT:release_copy")
    return {"authority_commit": authority["authority_commit"], "formal_receipts": len(required_attempts),
            "historical_receipts_verified": ["A05-phase2-1", "A06-phase2"]}


def _validate_release_schemas(release: Path) -> int:
    try:
        import jsonschema
    except ImportError as exc:
        raise ValueError("HOLD_LOCAL_PREREQUISITE: install requirements/phase4.lock") from exc
    pairs: list[tuple[str, Path]] = [
        ("local-verifier-contract.schema.json", release / "contracts/local-verifier-contract.json"),
        ("serving-selection.schema.json", release / "selection/serving-selection.json"),
    ]
    for game in ("ssq", "dlt"):
        serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
        model_path = release / serving["model_path"]
        feature_dir = release / f"features/{game}/{serving['feature_release_id']}"
        pairs.extend([
            ("training-input-manifest.schema.json", release / f"data/{game}/training-input-manifest.json"),
            ("model-selection-receipt.schema.json", release / f"models/{game}/model-selection-receipt.json"),
            ("model-release.schema.json", model_path),
            ("training-report.schema.json", model_path.with_name("training-report.json")),
            ("backtest.schema.json", _single(release / f"backtests/{game}", "*/summary.json")),
            ("formal-forecast.schema.json", _single(release / f"forecasts/{game}", "*/forecast.json")),
            ("formal-forecast-lock.schema.json", _single(release / f"forecasts/{game}", "*/lock.json")),
        ])
        feature_schema = load(ROOT / "schemas/phase4/feature-snapshot.schema.json")
        validator = jsonschema.Draft202012Validator(feature_schema)
        for line in (feature_dir / "feature-snapshot.jsonl").read_text(encoding="utf-8").splitlines():
            validator.validate(json.loads(line))
    for schema_name, value_path in pairs:
        schema = load(ROOT / f"schemas/phase4/{schema_name}")
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(load(value_path))
    return len(pairs) + 2


def _validate_scheduler(release: Path) -> dict[str, object]:
    recovery = load(release / "runtime/schedule/recovery-ssq-dlt.json")
    if (set(recovery.get("games", [])) != {"ssq", "dlt"} or recovery.get("faults_tested") != 10
            or not recovery.get("same_output_identities") or recovery.get("duplicate_side_effects") != 0):
        raise ValueError("HOLD_RECOVERY_EVIDENCE")
    expected_stages = {"prepare", "forecast_lock", "official_result_ingest", "unlock_score", "research_shadow"}
    for game in ("ssq", "dlt"):
        baseline = recovery["baseline_runs"][game]
        if set(baseline["stage_operation_ids"]) != expected_stages:
            raise ValueError(f"HOLD_RECOVERY_EVIDENCE:{game}")
    return {"games": ["ssq", "dlt"], "faults_tested": 10, "duplicate_side_effects": 0}


def local_acceptance(release: Path, draws_path: Path) -> dict[str, object]:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise ValueError(f"HOLD_UNSUPPORTED_LOCAL_PYTHON:{sys.implementation.name}:{sys.version_info.major}.{sys.version_info.minor}")
    release = release.resolve()
    before = _release_inventory(release)
    closure = _validate_final_closure(release)
    provenance = _validate_authority_and_receipts(release)
    schema_count = _validate_release_schemas(release)
    results = [replay_game(release, draws_path, game) for game in ("ssq", "dlt")]
    mutations = mutation_checks(release, draws_path)
    scheduler = _validate_scheduler(release)
    recorded_before = load(release / "e2e/protected-inventory-before.json")
    recorded_after = load(release / "e2e/protected-inventory-after.json")
    current_protected = protected_inventory()
    if recorded_before != recorded_after or recorded_after != current_protected:
        raise ValueError("FAIL_PROTECTED_ARTIFACT_CHANGED")
    if before != _release_inventory(release):
        raise ValueError("FAIL_LOCAL_VERIFIER_WROTE_RELEASE")
    inspect = {}
    for game in ("ssq", "dlt"):
        serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
        model = load(release / serving["model_path"])
        forecast = load(_single(release / f"forecasts/{game}", "*/forecast.json"))
        inspect[game] = {"model_release_id": serving["model_release_id"], "feature_release_id": serving["feature_release_id"],
                         "target_issue": forecast["target_issue"], "training_cutoff_issue": model["training_cutoff_issue"],
                         "ticket_count": forecast["ticket_count"], "scientific_status": model["scientific_status"],
                         "first_probability": forecast["first_probability"], "last_probability": forecast["last_probability"]}
    return {"artifact_type": "phase4_local_acceptance_result", "release_id": release.name, "status": "PASS",
            "terminal_state": "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE", "python": platform_python(),
            "numeric_contract_id": local_contract()["contract_id"], "schemas_validated": schema_count,
            "authority_and_formal_evidence": provenance, "final_closure": closure, "games": inspect,
            "scheduler_recovery": scheduler, "independent_replay_match_rate": 1.0,
            "mutation_detection_rate": 1.0 if mutations and all(value == "DETECTED" for value in mutations.values()) else 0.0,
            "protected_roots_unchanged": True, "release_unchanged": True,
            "semantic_numeric_comparisons": sum(row["semantic_numeric_comparisons"] for row in results)}


def platform_python() -> dict[str, str]:
    import platform
    return {"implementation": platform.python_implementation(), "version": platform.python_version(), "platform": platform.platform()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--draws", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--local-acceptance", action="store_true")
    args = parser.parse_args()
    if sum((args.output is not None, args.check_only, args.local_acceptance)) != 1:
        parser.error("choose exactly one of --output, --check-only, or --local-acceptance")
    release = args.release.resolve()
    if args.local_acceptance:
        report = local_acceptance(release, args.draws.resolve())
        print(json.dumps(report, sort_keys=True))
        return 0
    results = [replay_game(release, args.draws, game) for game in ("ssq", "dlt")]
    mutations = mutation_checks(release, args.draws)
    recorded_before = load(release / "e2e/protected-inventory-before.json")
    recorded_after = load(release / "e2e/protected-inventory-after.json")
    current_protected = protected_inventory()
    if recorded_before != recorded_after or recorded_after != current_protected:
        raise ValueError("FAIL_PROTECTED_ARTIFACT_CHANGED")
    report = {
        "artifact_type": "phase4_independent_bottom_up_replay", "oracle": "standalone_p4e2_oracle_v1",
        "games": results, "product_core_import_count": 0, "match_rate": 1.0,
        "mutations": mutations, "mutation_detection_rate": 1.0,
        "protected_roots_unchanged": True, "protected_inventory": current_protected,
        "status": "PASS", "blocking_findings": [],
    }
    if not args.check_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise FileExistsError(args.output)
        args.output.write_bytes(canon(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
