from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
from pathlib import Path
from statistics import mean
from typing import Sequence

from lottery_system.phase4.p4e2_model import NUMBER_IDS as R12_NUMBER_IDS
from lottery_system.phase4.p4e2_model import feature_context as r12_context
from lottery_system.phase4.real_common import RULES, canonical, digest
from lottery_system.phase4.real_model import load_draws, write_once
from lottery_system.phase4e3.model import (
    FEATURE_FAMILIES,
    build_context,
    fit_shape_zone,
    fit_zone,
    score_shape_observation,
    score_zone_observation,
    shape_distribution,
    shape_vector,
    zone_distribution,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "config/phase4e3/phase-contract.json"
DEFAULT_DRAWS = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"
DEFAULT_OUT = ROOT / "artifacts/phase-4e3/delivery-20260819/selection"
FIT_CACHE: dict[bytes, dict[str, object]] = {}


def ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end - 1) / 2.0
        for position in order[cursor:end]:
            result[position] = rank
        cursor = end
    return result


def correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("invalid correlation input")
    left_mean, right_mean = mean(left), mean(right)
    numerator = math.fsum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(math.fsum((x - left_mean) ** 2 for x in left) * math.fsum((y - right_mean) ** 2 for y in right))
    return 0.0 if denominator < 1e-15 else numerator / denominator


def spearman(left: Sequence[float], right: Sequence[float]) -> float:
    return correlation(ranks(left), ranks(right))


def configs(family: str) -> list[dict[str, float | int]]:
    shrinkages = (20.0, 60.0) if family == "C04_GRAPH" else (20.0,)
    return [
        {"history": history, "l2": l2, "temperature": temperature, "pair_shrinkage": shrinkage}
        for history, l2, temperature, shrinkage in itertools.product((80, 120), (4.0, 12.0, 36.0), (0.5, 1.0), shrinkages)
    ]


def m0_zone(game: str, zone: int, observed: Sequence[int]) -> dict[str, float]:
    n, k = RULES[game][zone]
    probability = 1.0 / math.comb(n, k)
    marginal = k / n
    observed_set = set(observed)
    inclusion_log_loss = -math.fsum(math.log(marginal if number in observed_set else 1 - marginal) for number in range(1, n + 1)) / n
    inclusion_brier = math.fsum((marginal - float(number in observed_set)) ** 2 for number in range(1, n + 1)) / n
    return {"subset_probability": probability, "joint_log_loss": -math.log(probability), "inclusion_log_loss": inclusion_log_loss,
            "inclusion_brier": inclusion_brier, "probability_square_sum": probability}


def applicable_features(game: str, zone: int, family: str, cutoff_draws) -> tuple[str, ...]:
    feature_ids = FEATURE_FAMILIES[family]
    if family == "C05_SET_SHAPE":
        return feature_ids
    context = build_context(game, cutoff_draws, zone)
    return tuple(feature_id for feature_id in feature_ids if max(context["feature_values"][feature_id]) - min(context["feature_values"][feature_id]) > 1e-12)


def fit_family(game: str, draws, cutoff: int, zone: int, family: str, config: dict[str, object], feature_ids: Sequence[str] | None = None):
    selected = tuple(feature_ids or applicable_features(game, zone, family, draws[:cutoff]))
    if not selected:
        return {"kind": "uniform", "feature_ids": []}
    if family == "C05_SET_SHAPE":
        return {"kind": "shape", "model": fit_shape_zone(game, draws, cutoff, zone, history=int(config["history"]), l2=float(config["l2"]), temperature=float(config["temperature"]))}
    cache_key = canonical({
        "game": game, "cutoff": cutoff, "zone": zone, "family": family, "features": selected,
        "history": config["history"], "l2": config["l2"], "pair_shrinkage": config["pair_shrinkage"],
    })
    if cache_key not in FIT_CACHE:
        FIT_CACHE[cache_key] = fit_zone(
            game, draws, cutoff, zone, selected, history=int(config["history"]), l2=float(config["l2"]),
            temperature=1.0, pair_shrinkage=float(config["pair_shrinkage"]),
        )
    model = copy.deepcopy(FIT_CACHE[cache_key])
    model["coefficients"] = [value * float(config["temperature"]) for value in model["coefficients"]]
    model["temperature"] = float(config["temperature"])
    return {"kind": "number", "model": model}


def evaluate_block(game: str, draws, cutoff: int, positions: Sequence[int], family: str, config: dict[str, object], feature_ids: Sequence[str] | None = None) -> list[dict[str, object]]:
    fitted = [fit_family(game, draws, cutoff, zone, family, config, feature_ids) for zone in (0, 1)]
    fixed_shape = [shape_distribution(game, zone, item["model"]) if item["kind"] == "shape" else None for zone, item in enumerate(fitted)]
    rows = []
    for target in positions:
        zones, m0_zones = [], []
        for zone, item in enumerate(fitted):
            observed = draws[target].front if zone == 0 else draws[target].back
            if item["kind"] == "uniform":
                scored = m0_zone(game, zone, observed)
            elif item["kind"] == "shape":
                scored = score_shape_observation(observed, fixed_shape[zone])
            else:
                scored = score_zone_observation(observed, zone_distribution(game, draws[:target], zone, item["model"]))
            zones.append(scored)
            m0_zones.append(m0_zone(game, zone, observed))
        rows.append({
            "target_position": target, "issue": draws[target].issue,
            "joint_log_loss": math.fsum(zone["joint_log_loss"] for zone in zones),
            "m0_joint_log_loss": math.fsum(zone["joint_log_loss"] for zone in m0_zones),
            "delta_joint_log_loss_vs_m0": math.fsum(zone["joint_log_loss"] - base["joint_log_loss"] for zone, base in zip(zones, m0_zones)),
            "zones": zones,
        })
    return rows


def audit_gates(game: str, draws, family: str, fold_cutoffs: Sequence[int]) -> dict[str, object]:
    if family == "C05_SET_SHAPE":
        correlations = []
        for zone in (0, 1):
            n, k = RULES[game][zone]
            combos = list(itertools.islice(itertools.combinations(range(1, n + 1), k), 4096))
            if len(combos) < 4:
                continue
            shape = list(zip(*(shape_vector(combo, n, k) for combo in combos)))
            context = r12_context(game, draws[:fold_cutoffs[-1]], zone)
            existing = list(zip(*(r12_combo_vector(combo, context)[7:] for combo in combos)))
            for feature_index, values in enumerate(shape):
                correlations.append({"feature_id": f"E{9 + feature_index:02d}", "max_abs_spearman_vs_r12": max(abs(spearman(values, old)) for old in existing)})
        maximum = max((row["max_abs_spearman_vs_r12"] for row in correlations), default=1.0)
        return {"nonconstant_fold_share": 1.0, "maximum_absolute_spearman_vs_r12": maximum, "correlations": correlations,
                "nonconstancy_pass": True, "redundancy_pass": maximum <= 0.95}
    pooled_new = {feature_id: [] for feature_id in FEATURE_FAMILIES[family]}
    pooled_old = {feature_id: [] for feature_id in R12_NUMBER_IDS}
    nonconstant = {feature_id: [] for feature_id in FEATURE_FAMILIES[family]}
    for cutoff in fold_cutoffs:
        for zone in (0, 1):
            current = build_context(game, draws[:cutoff], zone)
            accepted = r12_context(game, draws[:cutoff], zone)
            for feature_id in FEATURE_FAMILIES[family]:
                values = current["feature_values"][feature_id]
                pooled_new[feature_id].extend(values)
                nonconstant[feature_id].append(max(values) - min(values) > 1e-12)
            for feature_id in R12_NUMBER_IDS:
                pooled_old[feature_id].extend(accepted["number_features"][feature_id])
    correlations = []
    for feature_id, values in pooled_new.items():
        maximum = max(abs(spearman(values, old)) for old in pooled_old.values())
        correlations.append({"feature_id": feature_id, "max_abs_spearman_vs_r12": maximum})
    shares = {feature_id: sum(flags) / len(flags) for feature_id, flags in nonconstant.items()}
    applicable_shares = [share for share in shares.values() if share > 0]
    maximum = max(row["max_abs_spearman_vs_r12"] for row in correlations)
    return {
        "nonconstant_fold_share_by_feature": shares,
        "nonconstant_fold_share": min(applicable_shares, default=0.0),
        "maximum_absolute_spearman_vs_r12": maximum,
        "correlations": correlations,
        "nonconstancy_pass": bool(applicable_shares) and min(applicable_shares) >= 0.75,
        "redundancy_pass": maximum <= 0.95,
    }


def r12_combo_vector(combo, context):
    from lottery_system.phase4.p4e2_model import combo_vector
    return combo_vector(combo, context)


def tune_in_fold(game: str, draws, family: str, train_end: int, validation: Sequence[int], feature_ids: Sequence[str] | None = None) -> dict[str, object]:
    inner_cutoff = train_end - 12
    inner_positions = list(range(inner_cutoff + 2, train_end))
    grid_rows = []
    for config in configs(family if family != "C06_GATED_COMPOSITE_NONLINEAR" else "C01_SURPRISE_REGIME"):
        inner = evaluate_block(game, draws, inner_cutoff, inner_positions, family, config, feature_ids)
        grid_rows.append({"config": config, "inner_mean_joint_log_loss": mean(row["joint_log_loss"] for row in inner)})
    selected = min(grid_rows, key=lambda row: (row["inner_mean_joint_log_loss"], canonical(row["config"])))
    outer = evaluate_block(game, draws, train_end, validation, family, selected["config"], feature_ids)
    return {
        "inner_train_cutoff": inner_cutoff,
        "inner_validation_positions": inner_positions,
        "grid_metrics": grid_rows,
        "selected_config": selected["config"],
        "outer_rows": outer,
        "outer_mean_delta_joint_log_loss_vs_m0": mean(row["delta_joint_log_loss_vs_m0"] for row in outer),
    }


def select_family(game: str, draws, family: str, folds: Sequence[dict[str, object]], feature_ids: Sequence[str] | None = None) -> dict[str, object]:
    results = []
    for fold_index, fold in enumerate(folds, 1):
        row = tune_in_fold(game, draws, family, int(fold["train_end"]), list(range(*fold["validation"])), feature_ids)
        results.append({"fold_id": f"outer-{fold_index}", "train_end": fold["train_end"], "validation": fold["validation"], **row})
    config_scores: dict[bytes, list[float]] = {}
    config_values: dict[bytes, dict[str, object]] = {}
    for fold in results:
        for row in fold["grid_metrics"]:
            key = canonical(row["config"])
            config_values[key] = row["config"]
            config_scores.setdefault(key, []).append(float(row["inner_mean_joint_log_loss"]))
    final_key = min(config_scores, key=lambda key: (mean(config_scores[key]), key))
    directions = [float(fold["outer_mean_delta_joint_log_loss_vs_m0"]) <= 0 for fold in results]
    mean_delta = mean(float(fold["outer_mean_delta_joint_log_loss_vs_m0"]) for fold in results)
    return {
        "family": family, "feature_ids": list(feature_ids or FEATURE_FAMILIES[family]), "outer_folds": results,
        "final_config": config_values[final_key], "nested_mean_delta_joint_log_loss_vs_m0": mean_delta,
        "favorable_outer_fold_count": sum(directions),
        "selection_direction_pass": mean_delta < 0 and sum(directions) >= 3,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--draws", type=Path, default=DEFAULT_DRAWS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--game", choices=("ssq", "dlt"))
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text())
    folds = contract["evaluation"]["outer_selection_folds"]
    games = (args.game,) if args.game else ((tuple(contract["games"])) if "games" in contract else ("ssq", "dlt"))
    for game in games:
        draws = load_draws(args.draws, game)
        families = {}
        for family in ("C01_SURPRISE_REGIME", "C02_RENEWAL_HAZARD", "C03_TRANSITION", "C04_GRAPH", "C05_SET_SHAPE"):
            gates = audit_gates(game, draws, family, [int(fold["train_end"]) for fold in folds])
            if gates["nonconstancy_pass"] and gates["redundancy_pass"]:
                selection = select_family(game, draws, family, folds)
            else:
                selection = {"family": family, "feature_ids": list(FEATURE_FAMILIES[family]), "selection_direction_pass": False,
                             "rejected_before_model_selection": True, "rejection_reasons": [key for key in ("nonconstancy", "redundancy") if not gates[f"{key}_pass"]]}
            families[family] = {**selection, "feature_gates": gates}
        survivors = [family for family in ("C01_SURPRISE_REGIME", "C02_RENEWAL_HAZARD", "C03_TRANSITION", "C04_GRAPH") if families[family].get("selection_direction_pass")]
        composite_features = list(dict.fromkeys(feature for family in survivors for feature in FEATURE_FAMILIES[family]))
        if {"E01", "E03"} <= set(composite_features):
            composite_features.append("N01")
        if {"E05", "E08"} <= set(composite_features):
            composite_features.append("N02")
        composite_features = composite_features[:12]
        nonlinear_features = [feature for feature in composite_features if feature.startswith("N")]
        if composite_features and nonlinear_features:
            composite = select_family(game, draws, "C06_GATED_COMPOSITE_NONLINEAR", folds, composite_features)
            composite["eligible_source_families"] = survivors
        else:
            reason = "no_source_family_passed_selection_direction_gate" if not composite_features else "no_preregistered_nonlinearity_eligible_composite_would_duplicate_source"
            composite = {"family": "C06_GATED_COMPOSITE_NONLINEAR", "feature_ids": composite_features, "eligible_source_families": survivors,
                         "selection_direction_pass": False, "rejected_before_model_selection": True,
                         "rejection_reasons": [reason]}
        families["C06_GATED_COMPOSITE_NONLINEAR"] = composite
        eligible = [family for family, row in families.items() if row.get("selection_direction_pass")]
        payload = {
            "artifact_type": "phase4e3_frozen_selection_receipt", "game": game,
            "contract_sha256": digest(json.loads(args.contract.read_text())),
            "data_sha256": contract["data_authority"]["sha256"],
            "selection_capability_last_position": 173, "report_only_first_position": 176,
            "purge_positions": [174, 175], "report_only_labels_read": False,
            "families": families, "eligible_for_report_only": eligible,
            "strongest_selection_candidate": min(eligible, key=lambda family: families[family]["nested_mean_delta_joint_log_loss_vs_m0"]) if eligible else None,
        }
        payload["receipt_sha256"] = digest(payload)
        write_once(args.output / f"{game}-selection-receipt.json", payload)
        print(game, payload["strongest_selection_candidate"], eligible, payload["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
