#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import sys
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Sequence

try:
    import numpy as np
except ImportError as exc:  # exact grouped evaluation requires array enumeration
    raise SystemExit("NumPy is required for Phase 4E4 selection") from exc

from lottery_system.phase4.p4e2_model import (
    FEATURE_IDS as R12_FEATURE_IDS,
    combo_vector as r12_combo_vector,
    enumerate_zone as r12_enumerate_zone,
    feature_context as r12_feature_context,
    fit_coefficients as r12_fit_coefficients,
)
from lottery_system.phase4.real_common import Draw as LegacyDraw
from lottery_system.phase4e3.model import (
    fit_zone as transition_fit_zone,
    score_zone_observation as transition_score_zone,
    zone_distribution as transition_distribution,
)
from lottery_system.phase4e4.data import RULES, canonical, load_jsonl, sha256_bytes, sha256_file
from lottery_system.phase4e4.model import (
    FAMILIES,
    _set_feature_matrix,
    combo_features,
    configurations,
    distribution,
    fit_model,
    numbers,
    score_block,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "artifacts/phase-4e4/data-20260819/selection-prefix"
DEFAULT_OUTPUT = ROOT / "artifacts/phase-4e4/selection-20260819"
PROVENANCE = ROOT / "artifacts/phase-4e4/data-20260819/provenance/inventory.json"
R12_SELECTION = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r12/models"
E3_SELECTION = ROOT / "artifacts/phase-4e3/delivery-20260819/selection"
INNER_BLOCKS = 4
INNER_VALIDATION_DRAWS = 24
PURGE = 8
CACHE_VERSION = "phase4e4-grouped-sufficient-statistics-v1"


def digest(value: object) -> str:
    return sha256_bytes(canonical(value))


def write_once(path: Path, value: object) -> None:
    payload = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise FileExistsError(f"immutable output collision: {path}")
    path.write_bytes(payload)


def outer_folds(report_start: int) -> list[dict[str, object]]:
    specs = ((296, 288, 248), (248, 240, 200), (200, 192, 152),
             (152, 144, 104), (104, 96, 56), (56, 48, 8))
    return [
        {"fold": index, "train_end": report_start - train, "validation": [report_start - start, report_start - end]}
        for index, (train, start, end) in enumerate(specs, 1)
    ]


def inner_folds(train_end: int) -> list[dict[str, object]]:
    starts = [train_end - INNER_VALIDATION_DRAWS * offset for offset in range(INNER_BLOCKS, 0, -1)]
    return [
        {"fold": index, "train_end": start - PURGE, "validation": [start, start + INNER_VALIDATION_DRAWS]}
        for index, start in enumerate(starts, 1)
    ]


def _context_group(config: dict[str, object]) -> bytes:
    return canonical({key: value for key, value in config.items() if key not in {"l2", "temperature", "factor_l2"}})


def _derive_model(base: dict[str, object], config: dict[str, object]) -> dict[str, object]:
    model = {**base, "config": copy.deepcopy(config), "zones": []}
    for old in base["zones"]:
        zone = dict(old)
        coefficients = []
        for index, gradient in enumerate(old["gradient_at_uniform"]):
            penalty = float(config["l2"])
            if base["family"] == "E407_NONLINEAR_SET_FACTOR" and index < 2:
                penalty = float(config["factor_l2"])
            coefficients.append(max(-0.75, min(0.75, float(gradient) * float(config["temperature"]) / penalty)))
        zone.update({"history": config["history"] or "all_available", "l2": config["l2"],
                     "temperature": config["temperature"], "coefficients": coefficients})
        model["zones"].append(zone)
    if "coupling" in base:
        coupling = dict(base["coupling"])
        coupling["coefficients"] = [
            max(-0.75, min(0.75, float(value) * float(config["temperature"]) / float(config["l2"])))
            for value in coupling["gradient_at_uniform"]
        ]
        model["coupling"] = coupling
    return model


def grouped_log_losses(
    game: str,
    draws,
    cutoff: int,
    family: str,
    grid: Sequence[dict[str, object]],
    positions: Sequence[int],
) -> dict[bytes, list[float]]:
    groups: dict[bytes, list[dict[str, object]]] = {}
    for config in grid:
        groups.setdefault(_context_group(config), []).append(config)
    result: dict[bytes, list[float]] = {}
    for group_key in sorted(groups):
        configs = sorted(groups[group_key], key=canonical)
        base = fit_model(game, draws, cutoff, family, configs[0])
        models = [_derive_model(base, config) for config in configs]
        if family == "E406_CROSS_ZONE_COUPLING":
            for config, model in zip(configs, models):
                result[canonical(config)] = [float(row["joint_log_loss"]) for row in score_block(model, draws, positions)]
            continue

        zone_losses = [np.zeros((len(positions), len(configs)), dtype=np.float64) for _ in (0, 1)]
        for zone in (0, 1):
            context = base["zones"][zone]["context"]
            coefficient_matrix = np.asarray([model["zones"][zone]["coefficients"] for model in models], dtype=np.float64).T
            observed = np.asarray([combo_features(numbers(draws[target], zone), context) for target in positions], dtype=np.float64)
            observed_scores = observed @ coefficient_matrix
            if "number_features" in context:
                features = np.asarray(context["number_features"], dtype=np.float64)
                number_scores = features @ coefficient_matrix / math.sqrt(int(context["k"]))
                observed_scores = np.asarray([
                    np.sum(number_scores[np.asarray(numbers(draws[target], zone), dtype=np.int64) - 1, :], axis=0)
                    for target in positions
                ])
                log_normalizers = []
                for column in range(len(configs)):
                    weights = np.exp(np.clip(number_scores[:, column], -8.0, 8.0))
                    coefficients = np.zeros(int(context["k"]) + 1, dtype=np.float64)
                    coefficients[0] = 1.0
                    for weight in weights:
                        for order in range(int(context["k"]), 0, -1):
                            coefficients[order] += weight * coefficients[order - 1]
                    log_normalizers.append(math.log(float(coefficients[int(context["k"])])))
            else:
                matrix = _set_feature_matrix(context)
                complete_scores = matrix @ coefficient_matrix
                maxima = np.max(complete_scores, axis=0)
                log_normalizers = maxima + np.log(np.sum(np.exp(complete_scores - maxima), axis=0, dtype=np.float64))
            zone_losses[zone] = -observed_scores + np.asarray(log_normalizers)
        losses = zone_losses[0] + zone_losses[1]
        for column, config in enumerate(configs):
            result[canonical(config)] = [float(value) for value in losses[:, column]]
    return result


def m0_rows(game: str, draws, positions: Sequence[int]) -> list[dict[str, object]]:
    space = math.prod(math.comb(n, k) for n, k in RULES[game])
    probability = 1.0 / space
    square_mass = probability
    zone_briers = [(k / n) * (1.0 - k / n) for n, k in RULES[game]]
    return [
        {"target_position": target, "issue": draws[target].issue, "joint_probability": probability,
         "joint_log_loss": math.log(space), "full_multiclass_brier": 1.0 - probability,
         "zone_inclusion_brier": zone_briers, "normalization_mass": 1.0}
        for target in positions
    ]


def legacy_draws(draws) -> list[LegacyDraw]:
    return [LegacyDraw(row.issue, row.front, row.back, row.source_record_sha256) for row in draws]


def r12_rows(game: str, draws, cutoff: int, positions: Sequence[int]) -> list[dict[str, object]]:
    legacy = legacy_draws(draws)
    receipt = json.loads((R12_SELECTION / game / "model-selection-receipt.json").read_text(encoding="utf-8"))
    l2 = float(receipt["selected_config"]["l2"])
    coefficients = r12_fit_coefficients(game, legacy, cutoff, l2)
    # The comparator is refitted at the outer prefix.  Its exact normalization is
    # evaluated at that prefix and then held fixed across the purged validation block.
    contexts = [r12_feature_context(game, legacy[:cutoff], zone) for zone in (0, 1)]
    distributions = [r12_enumerate_zone(contexts[zone], coefficients[zone], False) for zone in (0, 1)]
    result = []
    for target in positions:
        probabilities, zone_briers = [], []
        for zone in (0, 1):
            vector = r12_combo_vector(numbers(draws[target], zone), contexts[zone])
            score = math.fsum(coefficients[zone][key] * value for key, value in zip(R12_FEATURE_IDS, vector))
            probabilities.append(math.exp(score - float(distributions[zone]["log_normalizer"])))
            # The frozen comparator enumerator does not expose marginals; inclusion
            # calibration is emitted as unavailable rather than silently approximated.
            zone_briers.append(None)
        probability = probabilities[0] * probabilities[1]
        square_mass = math.prod(float(row["probability_square_sum"]) for row in distributions)
        result.append({"target_position": target, "issue": draws[target].issue, "joint_probability": probability,
                       "joint_log_loss": -math.log(probability), "full_multiclass_brier": 1.0 - 2.0 * probability + square_mass,
                       "zone_inclusion_brier": zone_briers, "normalization_mass": 1.0})
    return result


def transition_rows(game: str, draws, cutoff: int, positions: Sequence[int]) -> list[dict[str, object]]:
    legacy = legacy_draws(draws)
    receipt = json.loads((E3_SELECTION / f"{game}-selection-receipt.json").read_text(encoding="utf-8"))
    config = receipt["families"]["C03_TRANSITION"]["final_config"]
    fitted = [
        transition_fit_zone(game, legacy, cutoff, zone, ("E05", "E06"), history=int(config["history"]),
                            l2=float(config["l2"]), temperature=float(config["temperature"]),
                            pair_shrinkage=float(config["pair_shrinkage"]))
        for zone in (0, 1)
    ]
    result = []
    for target in positions:
        scored = [transition_score_zone(numbers(draws[target], zone), transition_distribution(game, legacy[:target], zone, fitted[zone])) for zone in (0, 1)]
        probability = math.prod(float(row["subset_probability"]) for row in scored)
        square_mass = math.prod(float(row["probability_square_sum"]) for row in scored)
        result.append({"target_position": target, "issue": draws[target].issue, "joint_probability": probability,
                       "joint_log_loss": math.fsum(float(row["joint_log_loss"]) for row in scored),
                       "full_multiclass_brier": 1.0 - 2.0 * probability + square_mass,
                       "zone_inclusion_brier": [float(row["inclusion_brier"]) for row in scored], "normalization_mass": 1.0})
    return result


def comparator_rows(game: str, draws, cutoff: int, positions: Sequence[int]) -> dict[str, list[dict[str, object]]]:
    return {
        "M0": m0_rows(game, draws, positions),
        "P4E2_r12_retrained": r12_rows(game, draws, cutoff, positions),
        "P4E3_Transition_retrained": transition_rows(game, draws, cutoff, positions),
    }


def select_family(game: str, draws, family: str, folds: Sequence[dict[str, object]], comparators) -> dict[str, object]:
    grid = configurations(family)
    aggregate: dict[bytes, list[float]] = {canonical(config): [] for config in grid}
    values = {canonical(config): config for config in grid}
    outer_results = []
    inner_cache: dict[tuple[int, tuple[int, int]], dict[bytes, list[float]]] = {}
    for fold in folds:
        inner_results = []
        fold_scores: dict[bytes, list[float]] = {key: [] for key in aggregate}
        for inner in inner_folds(int(fold["train_end"])):
            positions = list(range(*inner["validation"]))
            cache_key = (int(inner["train_end"]), tuple(inner["validation"]))
            if cache_key not in inner_cache:
                inner_cache[cache_key] = grouped_log_losses(game, draws, int(inner["train_end"]), family, grid, positions)
            scores = inner_cache[cache_key]
            for key, losses in scores.items():
                fold_scores[key].extend(losses)
                aggregate[key].extend(losses)
            inner_results.append({**inner, "validation_draw_count": len(positions)})
        selected_key = min(fold_scores, key=lambda key: (mean(fold_scores[key]), key))
        selected = values[selected_key]
        positions = list(range(*fold["validation"]))
        model = fit_model(game, draws, int(fold["train_end"]), family, selected)
        candidate = score_block(model, draws, positions)
        comparator_metrics = {}
        favorable = {}
        for comparator, rows in comparators[int(fold["fold"])].items():
            log_delta = mean(float(left["joint_log_loss"]) - float(right["joint_log_loss"]) for left, right in zip(candidate, rows))
            brier_delta = mean(float(left["full_multiclass_brier"]) - float(right["full_multiclass_brier"]) for left, right in zip(candidate, rows))
            comparator_metrics[comparator] = {"mean_delta_joint_log_loss": log_delta, "mean_delta_full_multiclass_brier": brier_delta}
            favorable[comparator] = log_delta < 0.0
        outer_results.append({"fold": fold["fold"], "train_end": fold["train_end"], "validation": fold["validation"],
                              "purge_draws": PURGE, "embargo_draws": PURGE, "inner_folds": inner_results,
                              "selected_config": selected, "inner_mean_joint_log_loss": mean(fold_scores[selected_key]),
                              "outer_mean_joint_log_loss": mean(float(row["joint_log_loss"]) for row in candidate),
                              "comparator_metrics": comparator_metrics, "favorable_log_loss_by_comparator": favorable})
    final_key = min(aggregate, key=lambda key: (mean(aggregate[key]), key))
    favorable_counts = {
        comparator: sum(bool(fold["favorable_log_loss_by_comparator"][comparator]) for fold in outer_results)
        for comparator in ("M0", "P4E2_r12_retrained", "P4E3_Transition_retrained")
    }
    return {"candidate_id": family, "configuration_count": len(grid), "final_config": values[final_key],
            "aggregate_inner_mean_joint_log_loss": mean(aggregate[final_key]), "outer_folds": outer_results,
            "favorable_outer_fold_count_by_comparator": favorable_counts,
            "selection_direction_pass": all(count >= 5 for count in favorable_counts.values())}


def run_game(game: str, input_root: Path, output_root: Path, provenance: dict[str, object]) -> dict[str, object]:
    path = input_root / f"{game}.jsonl"
    expected = provenance["games"][game]["selection_sha256"]
    if sha256_file(path) != expected:
        raise SystemExit(f"selection-prefix digest mismatch for {game}")
    draws = load_jsonl(path, game)
    folds = outer_folds(len(draws))
    if any(int(fold["validation"][1]) > len(draws) for fold in folds):
        raise SystemExit("invalid frozen fold geometry")
    started = perf_counter()
    comparators = {}
    for fold in folds:
        comparators[int(fold["fold"])] = comparator_rows(game, draws, int(fold["train_end"]), list(range(*fold["validation"])))
    print(game, "comparators_complete", format(perf_counter() - started, ".3f"), flush=True)
    candidates = {}
    for family in FAMILIES:
        family_started = perf_counter()
        candidates[family] = select_family(game, draws, family, folds, comparators)
        print(game, family, "selection_complete", format(perf_counter() - family_started, ".3f"), flush=True)
    strongest = min(FAMILIES, key=lambda family: (float(candidates[family]["aggregate_inner_mean_joint_log_loss"]), family))
    eligible = [family for family in FAMILIES if candidates[family]["selection_direction_pass"]]
    payload = {
        "artifact_type": "phase4e4_frozen_selection_receipt", "game": game,
        "registry_sha256": sha256_file(ROOT / "config/phase4e4/experiment-registry.json"),
        "authority_sha256": sha256_file(ROOT / "config/phase4e4/authority-contract.json"),
        "selection_input_path": str(path.relative_to(ROOT)), "selection_input_sha256": expected,
        "selection_draw_count": len(draws), "selection_capability_last_position": len(draws) - 1,
        "report_labels_read": False, "original_200_labels_read": False,
        "outer_folds": folds, "purge_draws": PURGE, "embargo_draws": PURGE,
        "inner_fold_rule": "four_expanding_24_draw_blocks_each_with_eight_draw_purge",
        "cache": {"version": CACHE_VERSION, "deterministic": True, "grid_reduction": False},
        "comparators": ["M0", "P4E2_r12_retrained", "P4E3_Transition_retrained"],
        "candidates": candidates, "candidate_count": len(candidates), "eligible_for_report_only": eligible,
        "strongest_selection_candidate": strongest, "promotion_authority": bool(provenance["games"][game]["promotion_authority"]),
    }
    if game == "ssq":
        payload["promotion_authority"] = False
    payload["receipt_sha256"] = digest(payload)
    write_once(output_root / f"{game}-selection-receipt.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-prefix", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--game", choices=("ssq", "dlt"))
    args = parser.parse_args()
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    games = (args.game,) if args.game else ("ssq", "dlt")
    for game in games:
        payload = run_game(game, args.selection_prefix, args.output, provenance)
        print(game, payload["strongest_selection_candidate"], payload["eligible_for_report_only"], payload["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
