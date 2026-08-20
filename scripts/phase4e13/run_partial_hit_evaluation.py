#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e12"))
import run_compression_iteration as e12

OUT = ROOT / "artifacts/phase4e13"
E11 = ROOT / "artifacts/phase4e11"
E12 = ROOT / "artifacts/phase4e12"
R12 = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r12"
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
OUTER_DRAWS = 120
CALIBRATION_DRAWS = 60
FRONT_SET_SIZES = (5, 8, 10, 12, 15, 20)
BACK_SET_SIZES = (1, 2, 3, 4, 6)
CONFIDENCE_BUCKETS = 5
TINY_INVERSION_TOLERANCE = 0.02

_DATA: dict[str, list[e12.e9.oracle.Draw]] = {}
_REPORT12: dict[str, dict[str, object]] = {}


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def array_sha256(values: np.ndarray) -> str:
    little_endian = np.asarray(values, dtype="<f8", order="C")
    return hashlib.sha256(little_endian.tobytes(order="C")).hexdigest()


def load(game: str) -> list[e12.e9.oracle.Draw]:
    if game not in _DATA:
        _DATA[game] = e12.e9.load(game)
    return _DATA[game]


def report12(game: str) -> dict[str, object]:
    if game not in _REPORT12:
        _REPORT12[game] = json.loads((E12 / game / "report.json").read_text())
    return _REPORT12[game]


def selected_contract(game: str) -> tuple[str, dict[str, object]]:
    report = report12(game)
    selected = str(report["selected_candidate"])
    contract = report["candidate_contracts"][selected]
    return selected, contract


def model_configuration(game: str) -> dict[str, object]:
    selected, contract = selected_contract(game)
    return {
        "registered_feature_model": "P4E2-R_F01-F14",
        "registered_oracle": "standalone_p4e2_oracle_v1",
        "feature_ids": list(e12.e9.oracle.FEATURE_IDS),
        "l2": e12.e9.L2[game],
        "selected_candidate": selected,
        "fit_history": int(contract["fit_history"]),
        "masks": list(contract["masks"]),
        "weights": list(contract["weights"]),
        "selection_source": "phase4e12_pre_outer_120_only",
    }


def experiment_config(game: str) -> dict[str, object]:
    return {
        "phase": "Phase4E13",
        "game": game,
        "outer_draws": OUTER_DRAWS,
        "calibration_draws": CALIBRATION_DRAWS,
        "evaluation_draws": OUTER_DRAWS - CALIBRATION_DRAWS,
        "front_set_sizes": list(FRONT_SET_SIZES),
        "back_set_sizes": list(BACK_SET_SIZES),
        "confidence_definition": "selected_cumulative_marginal_inclusion_mass_divided_by_zone_draw_count",
        "confidence_buckets": CONFIDENCE_BUCKETS,
        "tiny_inversion_tolerance": TINY_INVERSION_TOLERANCE,
        "model_configuration": model_configuration(game),
    }


def fitted_selected_coefficients(game: str, target: int) -> list[dict[str, float]]:
    data = load(game)
    _, contract = selected_contract(game)
    fitted = e12.fit_coefficients(
        game, data, target, float(e12.e9.L2[game]), int(contract["fit_history"])
    )
    masks = tuple(e12.mask_for(game, str(name)) for name in contract["masks"])
    weights = tuple(float(weight) for weight in contract["weights"])
    return e12.masked_coefficients(fitted, masks, weights)


def normalize_combination_scores(scores: np.ndarray) -> np.ndarray:
    if scores.ndim != 1 or not len(scores):
        raise ValueError("combination scores must be a non-empty vector")
    shifted = np.asarray(scores, dtype=np.float64) - float(np.max(scores))
    weights = np.exp(shifted)
    total = float(np.sum(weights, dtype=np.float64))
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid combination score normalization")
    return weights / total


def marginal_number_probabilities(
    combos: np.ndarray, probabilities: np.ndarray, n: int
) -> np.ndarray:
    if len(combos) != len(probabilities):
        raise ValueError("combination/probability length mismatch")
    marginal = np.zeros(n, dtype=np.float64)
    for column in range(combos.shape[1]):
        marginal += np.bincount(combos[:, column] - 1, weights=probabilities, minlength=n)
    return marginal


def canonical_marginal_ranking(marginal: Sequence[float]) -> list[int]:
    return sorted(range(1, len(marginal) + 1), key=lambda number: (-float(marginal[number - 1]), number))


def confidence_set(
    marginal: np.ndarray, ranking: Sequence[int], actual: Sequence[int], size: int
) -> dict[str, object]:
    ranked = list(ranking[:size])
    selected = sorted(ranked)
    actual_set = set(int(number) for number in actual)
    overlap = len(actual_set.intersection(selected))
    zone_draw_count = len(actual)
    return {
        "set_size": size,
        "ranked_numbers": ranked,
        "selected_numbers": selected,
        "confidence_mass": math.fsum(float(marginal[number - 1]) for number in selected) / zone_draw_count,
        "overlap_count": overlap,
        "number_hit_rate": overlap / zone_draw_count,
        "any_number_hit": overlap > 0,
        "exact_all_zone_numbers_hit": overlap == zone_draw_count,
    }


def zone_evaluation(
    game: str,
    data: Sequence[e12.e9.oracle.Draw],
    target: int,
    zone: int,
    coefficients: Sequence[dict[str, float]],
) -> dict[str, object]:
    n, k = e12.e9.oracle.RULES[game][zone]
    context = e12.e9.oracle.feature_context(game, data[:target], zone)
    combos = e12.e9.combo_array(n, k)
    scores = e12.e9.vector_scores(context, coefficients[zone])
    probabilities = normalize_combination_scores(scores)
    marginal = marginal_number_probabilities(combos, probabilities, n)
    if not math.isclose(float(np.sum(probabilities)), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("FAIL_COMBINATION_NORMALIZATION")
    if not math.isclose(float(np.sum(marginal)), float(k), rel_tol=0.0, abs_tol=1e-10):
        raise ValueError("FAIL_MARGINAL_NORMALIZATION")
    ranking = canonical_marginal_ranking(marginal)
    actual = data[target].front if zone == 0 else data[target].back
    sizes = FRONT_SET_SIZES if zone == 0 else BACK_SET_SIZES
    if max(sizes) > n:
        raise ValueError("illegal confidence set size")
    return {
        "zone": "front" if zone == 0 else "back",
        "number_pool_size": n,
        "zone_draw_count": k,
        "actual_numbers": list(actual),
        "combination_count": len(combos),
        "combination_score_min": float(np.min(scores)),
        "combination_score_max": float(np.max(scores)),
        "combination_score_sha256_float64_le": array_sha256(scores),
        "combination_probability_sum": float(np.sum(probabilities)),
        "combination_probability_sha256_float64_le": array_sha256(probabilities),
        "marginal_probability_sum": float(np.sum(marginal)),
        "marginal_probabilities": [
            {"number": number, "inclusion_mass": float(marginal[number - 1])}
            for number in range(1, n + 1)
        ],
        "marginal_ranking": ranking,
        "confidence_sets": {
            str(size): confidence_set(marginal, ranking, actual, size) for size in sizes
        },
    }


def outer_e12_rows(game: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (E12 / game / "outer-rolling-report.jsonl").read_text().splitlines()]


def evaluate_target(task: tuple[str, int]) -> dict[str, object]:
    game, target = task
    data = load(game)
    outer_start = len(data) - OUTER_DRAWS
    if target < outer_start or target >= len(data):
        raise ValueError("target outside frozen E9-E12 outer window")
    selected, _ = selected_contract(game)
    coefficients = fitted_selected_coefficients(game, target)
    e12_row = outer_e12_rows(game)[target - outer_start]
    if (str(e12_row["issue"]), int(e12_row["target_position"])) != (data[target].issue, target):
        raise ValueError("FAIL_PHASE4E12_OUTER_IDENTITY")
    return {
        "game": game,
        "issue": data[target].issue,
        "target_position": target,
        "maximum_training_position": target - 1,
        "maximum_training_issue": data[target - 1].issue,
        "strict_lag": True,
        "outer_split": "calibration" if target - outer_start < CALIBRATION_DRAWS else "evaluation",
        "selected_candidate": selected,
        "full_ticket": {
            "canonical_rank": int(e12_row["canonical_rank"]),
            "rank_percentile": float(e12_row["rank_percentile"]),
            "covered": e12_row["covered"],
        },
        "zones": {
            "front": zone_evaluation(game, data, target, 0, coefficients),
            "back": zone_evaluation(game, data, target, 1, coefficients),
        },
    }


def wilson(hits: int, trials: int) -> list[float]:
    if trials < 1:
        raise ValueError("Wilson interval requires positive trials")
    return e12.e9.wilson(hits, trials)


def summarize_set(rows: Sequence[dict[str, object]], zone: str, size: int) -> dict[str, object]:
    observations = [row["zones"][zone]["confidence_sets"][str(size)] for row in rows]
    zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
    overlaps = [int(observation["overlap_count"]) for observation in observations]
    overlap_total = sum(overlaps)
    any_hits = sum(bool(observation["any_number_hit"]) for observation in observations)
    all_hits = sum(bool(observation["exact_all_zone_numbers_hit"]) for observation in observations)
    draws = len(rows)
    return {
        "draws": draws,
        "set_size": size,
        "overlap_total": overlap_total,
        "average_overlap": overlap_total / draws,
        "number_level_hits": overlap_total,
        "number_level_trials": draws * zone_draw_count,
        "number_hit_rate": overlap_total / (draws * zone_draw_count),
        "number_hit_rate_wilson95": wilson(overlap_total, draws * zone_draw_count),
        "any_number_hit_count": any_hits,
        "any_number_hit_rate": any_hits / draws,
        "any_number_hit_wilson95": wilson(any_hits, draws),
        "exact_all_zone_number_hit_count": all_hits,
        "exact_all_zone_number_hit_rate": all_hits / draws,
        "exact_all_zone_number_hit_wilson95": wilson(all_hits, draws),
        "mean_confidence_mass": math.fsum(float(observation["confidence_mass"]) for observation in observations) / draws,
        "minimum_confidence_mass": min(float(observation["confidence_mass"]) for observation in observations),
        "maximum_confidence_mass": max(float(observation["confidence_mass"]) for observation in observations),
        "per_draw_indicators_location": "outer-rolling-report.jsonl::zones.<zone>.confidence_sets.<size>",
    }


def average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (float(values[index]), index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and float(values[order[end]]) == float(values[order[cursor]]):
            end += 1
        average = ((cursor + 1) + end) / 2
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation requires paired observations")
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    numerator = math.fsum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = math.sqrt(math.fsum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(math.fsum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return 0.0
    return numerator / (left_scale * right_scale)


def spearman(left: Sequence[float], right: Sequence[float]) -> float:
    return pearson(average_ranks(left), average_ranks(right))


def linear_association(left: Sequence[float], right: Sequence[float]) -> dict[str, float]:
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    denominator = math.fsum((value - left_mean) ** 2 for value in left)
    slope = math.fsum((x - left_mean) * (y - right_mean) for x, y in zip(left, right)) / denominator
    return {"intercept": right_mean - slope * left_mean, "slope": slope}


def confidence_association(
    rows: Sequence[dict[str, object]], zone: str, sizes: Sequence[int]
) -> dict[str, object]:
    zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
    observations = []
    for row in rows:
        for size in sizes:
            value = row["zones"][zone]["confidence_sets"][str(size)]
            observations.append(
                {
                    "confidence": float(value["confidence_mass"]),
                    "hit_rate": int(value["overlap_count"]) / zone_draw_count,
                    "overlap": int(value["overlap_count"]),
                    "issue": str(row["issue"]),
                    "size": size,
                }
            )
    confidence = [row["confidence"] for row in observations]
    hit_rates = [row["hit_rate"] for row in observations]
    overlaps = [float(row["overlap"]) for row in observations]
    ordered = sorted(observations, key=lambda row: (row["confidence"], row["issue"], row["size"]))
    buckets = []
    for bucket_index in range(CONFIDENCE_BUCKETS):
        start = bucket_index * len(ordered) // CONFIDENCE_BUCKETS
        stop = (bucket_index + 1) * len(ordered) // CONFIDENCE_BUCKETS
        group = ordered[start:stop]
        hits = sum(int(row["overlap"]) for row in group)
        trials = len(group) * zone_draw_count
        buckets.append(
            {
                "bucket": bucket_index + 1,
                "observations": len(group),
                "mean_confidence_mass": math.fsum(float(row["confidence"]) for row in group) / len(group),
                "number_level_hits": hits,
                "number_level_trials": trials,
                "mean_number_hit_rate": hits / trials,
                "number_hit_rate_wilson95": wilson(hits, trials),
            }
        )
    inversions = []
    for lower, higher in zip(buckets, buckets[1:]):
        magnitude = float(lower["mean_number_hit_rate"]) - float(higher["mean_number_hit_rate"])
        if magnitude > 0:
            lower_interval = lower["number_hit_rate_wilson95"]
            higher_interval = higher["number_hit_rate_wilson95"]
            intervals_overlap = max(lower_interval[0], higher_interval[0]) <= min(lower_interval[1], higher_interval[1])
            inversions.append(
                {
                    "lower_bucket": lower["bucket"],
                    "higher_bucket": higher["bucket"],
                    "magnitude": magnitude,
                    "wilson_intervals_overlap": intervals_overlap,
                    "tiny_and_wilson_overlapping": magnitude <= TINY_INVERSION_TOLERANCE and intervals_overlap,
                }
            )
    monotonic_with_tolerance = not inversions or (
        len(inversions) == 1 and bool(inversions[0]["tiny_and_wilson_overlapping"])
    )
    association = linear_association(confidence, hit_rates)
    rho_hit_rate = spearman(confidence, hit_rates)
    rho_overlap = spearman(confidence, overlaps)
    accepted = monotonic_with_tolerance and rho_hit_rate > 0 and association["slope"] > 0
    return {
        "observation_unit": "draw_x_confidence_set_size",
        "observations": len(observations),
        "confidence_definition": "cumulative marginal inclusion mass of selected numbers divided by zone draw count",
        "confidence_is_true_lottery_probability": False,
        "outcome": "overlap_count_divided_by_zone_draw_count",
        "spearman_rho_confidence_vs_number_hit_rate": rho_hit_rate,
        "spearman_rho_confidence_vs_overlap": rho_overlap,
        "linear_calibration_association": {
            **association,
            "interpretation": "descriptive OLS association, not true-probability calibration",
        },
        "quintiles": buckets,
        "adjacent_inversions": inversions,
        "monotonic_without_tolerance": not inversions,
        "monotonic_with_registered_tolerance": monotonic_with_tolerance,
        "registered_tolerance": {
            "maximum_inversions": 1,
            "maximum_absolute_hit_rate_inversion": TINY_INVERSION_TOLERANCE,
            "requires_wilson95_overlap": True,
        },
        "positive_association": rho_hit_rate > 0 and association["slope"] > 0,
        "acceptance_pass": accepted,
    }


def fixed_size_confidence_association(
    rows: Sequence[dict[str, object]], zone: str, sizes: Sequence[int]
) -> dict[str, object]:
    zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
    result: dict[str, object] = {}
    for size in sizes:
        observations = [row["zones"][zone]["confidence_sets"][str(size)] for row in rows]
        confidence = [float(value["confidence_mass"]) for value in observations]
        hit_rates = [int(value["overlap_count"]) / zone_draw_count for value in observations]
        association = linear_association(confidence, hit_rates)
        rho = spearman(confidence, hit_rates)
        result[str(size)] = {
            "draws": len(rows),
            "set_size": size,
            "spearman_rho_confidence_vs_number_hit_rate": rho,
            "linear_calibration_association": association,
            "positive_association": rho > 0 and association["slope"] > 0,
        }
    return result


def aggregate(rows: Sequence[dict[str, object]], zone: str, sizes: Sequence[int]) -> dict[str, object]:
    return {
        "set_size_metrics": {str(size): summarize_set(rows, zone, size) for size in sizes},
        "confidence_association": confidence_association(rows, zone, sizes),
        "fixed_size_confidence_association": fixed_size_confidence_association(rows, zone, sizes),
    }


def run(game: str, workers: int) -> dict[str, object]:
    data = load(game)
    outer_targets = range(len(data) - OUTER_DRAWS, len(data))
    tasks = [(game, target) for target in outer_targets]
    if workers == 1:
        rows = [evaluate_target(task) for task in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            rows = pool.map(evaluate_target, tasks, chunksize=1)
    rows.sort(key=lambda row: int(row["target_position"]))
    e12_rows = outer_e12_rows(game)
    identity = [(row["issue"], row["target_position"]) for row in rows]
    identity12 = [(row["issue"], row["target_position"]) for row in e12_rows]
    identity11 = [
        (row["issue"], row["target_position"])
        for row in (
            json.loads(line) for line in (E11 / game / "outer-rolling-report.jsonl").read_text().splitlines()
        )
    ]
    if identity != identity12 or identity != identity11:
        raise ValueError("FAIL_FROZEN_OUTER_IDENTITY")
    calibration = rows[:CALIBRATION_DRAWS]
    evaluation = rows[CALIBRATION_DRAWS:]
    splits = {
        "calibration": {
            "draws": len(calibration),
            "front": aggregate(calibration, "front", FRONT_SET_SIZES),
            "back": aggregate(calibration, "back", BACK_SET_SIZES),
        },
        "evaluation": {
            "draws": len(evaluation),
            "front": aggregate(evaluation, "front", FRONT_SET_SIZES),
            "back": aggregate(evaluation, "back", BACK_SET_SIZES),
        },
        "all_120_descriptive": {
            "draws": len(rows),
            "front": aggregate(rows, "front", FRONT_SET_SIZES),
            "back": aggregate(rows, "back", BACK_SET_SIZES),
        },
    }
    report_e12 = report12(game)
    config = experiment_config(game)
    serving = json.loads((R12 / "selection/serving-selection.json").read_text())["serving_model_by_game"][game]
    serving_model_path = R12 / str(serving["model_path"])
    model_config_hash = digest(model_configuration(game))
    split_acceptance = {
        split: {
            zone: bool(
                splits[split][zone]["confidence_association"]["acceptance_pass"]
                and all(
                    value["positive_association"]
                    for value in splits[split][zone]["fixed_size_confidence_association"].values()
                )
            )
            for zone in ("front", "back")
        }
        for split in ("calibration", "evaluation")
    }
    report = {
        "artifact_type": "phase4e13_partial_number_hit_evaluation_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "outer_window": {
            "draws": OUTER_DRAWS,
            "first_issue": rows[0]["issue"],
            "last_issue": rows[-1]["issue"],
            "first_target_position": rows[0]["target_position"],
            "last_target_position": rows[-1]["target_position"],
            "calibration_draws": CALIBRATION_DRAWS,
            "evaluation_draws": OUTER_DRAWS - CALIBRATION_DRAWS,
            "frozen_from_phase4e9_e10_e11_e12": True,
            "identity_fields": ["issue", "target_position"],
            "identity_sha256": digest(identity),
            "identity_matches_phase4e11": identity == identity11,
            "identity_matches_phase4e12": identity == identity12,
        },
        "selection_fence": {
            "selected_candidate": report_e12["selected_candidate"],
            "candidate_contract": report_e12["candidate_contracts"][report_e12["selected_candidate"]],
            "selection_window": report_e12["candidate_selection_window"],
            "selection_draws_immediately_before_outer": (
                int(report_e12["candidate_selection_window"]["last_target_position"]) + 1
                == int(rows[0]["target_position"])
            ),
            "outer_labels_used_for_selection": False,
        },
        "probability_contract": {
            "combination_support": "every legal per-zone combination",
            "combination_score_source": "registered P4E2 feature formula with selected P4E12 walk-forward coefficients",
            "combination_normalization": "stable softmax over all legal combinations in that zone",
            "marginal_derivation": "sum normalized combination mass over combinations containing each number",
            "marginal_sum_equals_zone_draw_count": True,
            "confidence_definition": config["confidence_definition"],
            "true_lottery_probability_claim": False,
            "guaranteed_winnings_claim": False,
        },
        "experiment_config": config,
        "splits": splits,
        "partial_hit_acceptance": {
            "rule": "pooled ladder and every fixed set size require monotonic/positive confidence association; pooled ladder may allow one <=0.02 inversion with Wilson overlap",
            "split_zone_pass": split_acceptance,
            "accepted": all(value for split in split_acceptance.values() for value in split.values()),
            "exact_full_ticket_gate_required": False,
        },
        "full_ticket_comparison": {
            "source": "phase4e12_unchanged",
            "first_ranked_space": report_e12["first_ranked_space"],
            "compression_evaluation": report_e12["compression_evaluation"],
            "compression_acceptance": report_e12["compression_acceptance"],
            "gates_changed": False,
        },
        "lineage": {
            "source_data_path": str((SOURCE / f"{game}.jsonl").relative_to(ROOT)),
            "source_data_sha256": sha256(SOURCE / f"{game}.jsonl"),
            "registered_p4e2_oracle_path": "scripts/phase4_independent/p4e2_oracle.py",
            "registered_p4e2_oracle_sha256": sha256(ROOT / "scripts/phase4_independent/p4e2_oracle.py"),
            "serving_selection_sha256": sha256(R12 / "selection/serving-selection.json"),
            "serving_model_release_id": serving["model_release_id"],
            "serving_model_sha256": sha256(serving_model_path),
            "phase4e12_report_sha256": sha256(E12 / game / "report.json"),
            "phase4e12_outer_rows_sha256": sha256(E12 / game / "outer-rolling-report.jsonl"),
            "model_configuration_sha256": model_config_hash,
            "experiment_config_sha256": digest(config),
        },
        "strict_lag": {
            "all_rows_strict_lag": all(bool(row["strict_lag"]) for row in rows),
            "all_maximum_training_positions_equal_target_minus_one": all(
                int(row["maximum_training_position"]) == int(row["target_position"]) - 1 for row in rows
            ),
        },
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
        "promotion_eligible": False,
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    (path / "outer-rolling-report.jsonl").write_bytes(b"".join(canonical(row) for row in rows))
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase4E13 marginal-number partial-hit evaluation")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game, args.workers) for game in ("ssq", "dlt")}
    summary = {
        "artifact_type": "phase4e13_partial_number_hit_evaluation_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "games": reports,
        "partial_hit_ladder_accepted_all_games": all(
            bool(report["partial_hit_acceptance"]["accepted"]) for report in reports.values()
        ),
        "confidence_positive_association_all_calibration_evaluation_zones": all(
            bool(report["splits"][split][zone]["confidence_association"]["positive_association"])
            for report in reports.values()
            for split in ("calibration", "evaluation")
            for zone in ("front", "back")
        ),
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
        "promotion_eligible": False,
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
