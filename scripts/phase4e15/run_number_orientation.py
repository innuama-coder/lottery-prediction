#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e14"))
import run_confidence_calibration as e14

e13 = e14.e13
OUT = ROOT / "artifacts/phase4e15"
E13 = ROOT / "artifacts/phase4e13"
E14 = ROOT / "artifacts/phase4e14"
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
ORACLE = ROOT / "scripts/phase4_independent/p4e2_oracle.py"
E13_SCRIPT = ROOT / "scripts/phase4e13/run_partial_hit_evaluation.py"
E14_SCRIPT = ROOT / "scripts/phase4e14/run_confidence_calibration.py"
OUTER_DRAWS = 120
OUTER_CALIBRATION_DRAWS = 60
SELECTION_DRAWS = 120
ORIENTATION_MEASURE_DRAWS = 60
ORIENTATION_SELECT_DRAWS = 60
ASSOCIATION_BUCKETS = 5
CANDIDATE_ORDER = (
    "raw_descending_marginal_mass",
    "reverse_ascending_marginal_mass_control",
)
ZONE_SIZES = {"front": e13.FRONT_SET_SIZES, "back": e13.BACK_SET_SIZES}


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def row_identity(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"issue": str(row["issue"]), "target_position": int(row["target_position"])}
        for row in rows
    ]


def e13_identity_digest(rows: Sequence[dict[str, object]]) -> str:
    return digest([(str(row["issue"]), int(row["target_position"])) for row in rows])


def raw_e14_inner_rows(game: str) -> list[dict[str, object]]:
    return read_jsonl(E14 / game / "inner-rolling-report.jsonl")


def raw_e14_outer_rows(game: str) -> list[dict[str, object]]:
    return read_jsonl(E14 / game / "outer-rolling-report.jsonl")


def raw_e14_report(game: str) -> dict[str, object]:
    return read_json(E14 / game / "report.json")


def raw_e13_outer_rows(game: str) -> list[dict[str, object]]:
    return read_jsonl(E13 / game / "outer-rolling-report.jsonl")


def raw_e13_report(game: str) -> dict[str, object]:
    return read_json(E13 / game / "report.json")


def orientation_score(candidate: str, marginal_mass: float) -> float:
    if candidate == "raw_descending_marginal_mass":
        return float(marginal_mass)
    if candidate == "reverse_ascending_marginal_mass_control":
        return -float(marginal_mass)
    raise ValueError(f"unknown orientation candidate: {candidate}")


def oriented_ranking(
    marginal_probabilities: Sequence[dict[str, object]], candidate: str
) -> list[int]:
    return [
        int(value["number"])
        for value in sorted(
            marginal_probabilities,
            key=lambda value: (
                -orientation_score(candidate, float(value["inclusion_mass"])),
                int(value["number"]),
            ),
        )
    ]


def safe_linear_association(left: Sequence[float], right: Sequence[float]) -> dict[str, float]:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("linear association requires paired observations")
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    denominator = math.fsum((value - left_mean) ** 2 for value in left)
    if denominator == 0:
        return {"intercept": right_mean, "slope": 0.0}
    slope = math.fsum((x - left_mean) * (y - right_mean) for x, y in zip(left, right)) / denominator
    return {"intercept": right_mean - slope * left_mean, "slope": slope}


def association_metrics(
    observations: Sequence[dict[str, object]], score_field: str, outcome_field: str
) -> dict[str, object]:
    if len(observations) < ASSOCIATION_BUCKETS:
        raise ValueError("association requires at least one observation per bucket")
    scores = [float(value[score_field]) for value in observations]
    outcomes = [int(value[outcome_field]) for value in observations]
    association = safe_linear_association(scores, outcomes)
    rho = e13.spearman(scores, outcomes)
    ordered = sorted(
        observations,
        key=lambda value: (
            float(value[score_field]),
            str(value.get("issue", "")),
            int(value.get("number", 0)),
        ),
    )
    buckets = []
    for bucket_index in range(ASSOCIATION_BUCKETS):
        start = bucket_index * len(ordered) // ASSOCIATION_BUCKETS
        stop = (bucket_index + 1) * len(ordered) // ASSOCIATION_BUCKETS
        group = ordered[start:stop]
        hits = sum(int(value[outcome_field]) for value in group)
        buckets.append(
            {
                "bucket": bucket_index + 1,
                "observations": len(group),
                "mean_orientation_score": math.fsum(float(value[score_field]) for value in group)
                / len(group),
                "binary_hits": hits,
                "binary_trials": len(group),
                "hit_rate": hits / len(group),
                "hit_rate_wilson95": e13.wilson(hits, len(group)),
            }
        )
    inversions = [
        {
            "lower_bucket": lower["bucket"],
            "higher_bucket": higher["bucket"],
            "magnitude": float(lower["hit_rate"]) - float(higher["hit_rate"]),
        }
        for lower, higher in zip(buckets, buckets[1:])
        if float(lower["hit_rate"]) > float(higher["hit_rate"])
    ]
    return {
        "observation_unit": "one_draw_x_one_candidate_number",
        "observations": len(observations),
        "score_field": score_field,
        "outcome": "binary_number_appears_in_actual_zone_draw",
        "score_is_true_lottery_probability": False,
        "spearman_rho": rho,
        "descriptive_linear_association": {
            **association,
            "interpretation": "descriptive OLS association, not probability calibration",
        },
        "buckets": buckets,
        "adjacent_bucket_inversions": inversions,
        "monotonic_bucket_behavior": not inversions,
        "positive_spearman_rho": rho > 0,
        "positive_descriptive_slope": association["slope"] > 0,
        "positive_association": rho > 0 and association["slope"] > 0,
    }


def individual_observations(
    rows: Sequence[dict[str, object]], zone: str, candidate: str
) -> list[dict[str, object]]:
    observations = []
    for row in rows:
        actual = set(int(number) for number in row["zones"][zone]["actual_numbers"])
        for value in row["zones"][zone]["marginal_probabilities"]:
            number = int(value["number"])
            mass = float(value["inclusion_mass"])
            observations.append(
                {
                    "issue": str(row["issue"]),
                    "target_position": int(row["target_position"]),
                    "number": number,
                    "marginal_inclusion_mass": mass,
                    "orientation_score": orientation_score(candidate, mass),
                    "binary_hit": int(number in actual),
                }
            )
    return observations


def validate_e14_selection_source(game: str, rows: Sequence[dict[str, object]]) -> None:
    report = raw_e14_report(game)
    window = report["candidate_selection_window"]
    if len(rows) != SELECTION_DRAWS:
        raise ValueError("FAIL_PHASE4E14_INNER_LENGTH")
    if digest(row_identity(rows)) != str(window["identity_sha256"]):
        raise ValueError("FAIL_PHASE4E14_INNER_IDENTITY")
    expected_splits = ["transform_fit"] * ORIENTATION_MEASURE_DRAWS + [
        "transform_holdout"
    ] * ORIENTATION_SELECT_DRAWS
    if [str(row["selection_subsplit"]) for row in rows] != expected_splits:
        raise ValueError("FAIL_PHASE4E14_INNER_SPLIT")
    outer_start = int(report["outer_window"]["first_target_position"])
    for row in rows:
        target = int(row["target_position"])
        if (
            target >= outer_start
            or not bool(row["strict_lag"])
            or int(row["maximum_training_position"]) != target - 1
        ):
            raise ValueError("FAIL_SELECTION_STRICT_LAG_OR_OUTER_LEAKAGE")


def recompute_inner_row(task: tuple[str, int]) -> dict[str, object]:
    game, target = task
    data = e13.load(game)
    coefficients = e13.fitted_selected_coefficients(game, target)
    zones = {
        zone: e13.zone_evaluation(game, data, target, zone_index, coefficients)
        for zone_index, zone in enumerate(("front", "back"))
    }
    return {
        "game": game,
        "issue": data[target].issue,
        "target_position": target,
        "maximum_training_position": target - 1,
        "maximum_training_issue": data[target - 1].issue,
        "strict_lag": True,
        "zones": zones,
    }


def compute_inner_rows(game: str, workers: int) -> list[dict[str, object]]:
    source_rows = raw_e14_inner_rows(game)
    validate_e14_selection_source(game, source_rows)
    tasks = [(game, int(row["target_position"])) for row in source_rows]
    if workers == 1:
        rows = [recompute_inner_row(task) for task in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            rows = pool.map(recompute_inner_row, tasks, chunksize=1)
    rows.sort(key=lambda row: int(row["target_position"]))
    if row_identity(rows) != row_identity(source_rows):
        raise ValueError("FAIL_RECOMPUTED_PHASE4E14_INNER_IDENTITY")
    for index, (row, source) in enumerate(zip(rows, source_rows)):
        for zone in ("front", "back"):
            if row["zones"][zone]["marginal_ranking"] != source["zones"][zone]["marginal_ranking"]:
                raise ValueError("FAIL_RECOMPUTED_PHASE4E14_INNER_RANKING")
            if row["zones"][zone]["confidence_sets"] != source["zones"][zone]["confidence_sets"]:
                raise ValueError("FAIL_RECOMPUTED_PHASE4E14_INNER_CONFIDENCE_SETS")
        row["selection_subsplit"] = (
            "orientation_fit_measure" if index < ORIENTATION_MEASURE_DRAWS else "orientation_selection"
        )
        row["phase4e14_selection_subsplit"] = source["selection_subsplit"]
        row["phase4e14_inner_row_identity_verified"] = True
    return rows


def select_orientations(
    inner_rows: Sequence[dict[str, object]],
) -> dict[str, dict[str, object]]:
    if len(inner_rows) != SELECTION_DRAWS:
        raise ValueError("FAIL_ORIENTATION_SELECTION_WINDOW")
    measure = inner_rows[:ORIENTATION_MEASURE_DRAWS]
    selection = inner_rows[ORIENTATION_MEASURE_DRAWS:]
    results: dict[str, dict[str, object]] = {}
    for zone in ("front", "back"):
        candidates = []
        for order, candidate in enumerate(CANDIDATE_ORDER):
            measure_metrics = association_metrics(
                individual_observations(measure, zone, candidate), "orientation_score", "binary_hit"
            )
            selection_metrics = association_metrics(
                individual_observations(selection, zone, candidate), "orientation_score", "binary_hit"
            )
            candidates.append(
                {
                    "candidate": candidate,
                    "candidate_order": order + 1,
                    "registered_control": candidate == "reverse_ascending_marginal_mass_control",
                    "fit_measure_metrics": measure_metrics,
                    "selection_metrics": selection_metrics,
                }
            )
        winner = max(
            candidates,
            key=lambda value: (
                bool(value["selection_metrics"]["positive_spearman_rho"]),
                bool(value["selection_metrics"]["positive_descriptive_slope"]),
                bool(value["selection_metrics"]["monotonic_bucket_behavior"]),
                float(value["selection_metrics"]["spearman_rho"]),
                float(value["selection_metrics"]["descriptive_linear_association"]["slope"]),
                -int(value["candidate_order"]),
            ),
        )
        orientation_id = f"p4e15-{winner['candidate']}-{digest({'zone': zone, 'candidate': winner['candidate'], 'selection_identity': digest(row_identity(selection))})[:16]}"
        results[zone] = {
            "selection_rule": "last-60 pre-outer individual-number observations: positive Spearman rho, then positive descriptive slope, then monotonic five-bucket behavior; remaining ties use rho, slope, and registered candidate order",
            "selection_uses_outer_labels": False,
            "orientation_fit_required": False,
            "first_60_role": "fit_measure_only_no_orientation_selection",
            "last_60_role": "orientation_selection",
            "selected_candidate": winner["candidate"],
            "selected_registered_control": winner["registered_control"],
            "selected_orientation_id": orientation_id,
            "candidates": candidates,
        }
    return results


def confidence_set(
    zone_value: dict[str, object], candidate: str, ranking: Sequence[int], size: int
) -> dict[str, object]:
    mass_by_number = {
        int(value["number"]): float(value["inclusion_mass"])
        for value in zone_value["marginal_probabilities"]
    }
    actual = set(int(number) for number in zone_value["actual_numbers"])
    ranked = list(ranking[:size])
    selected = sorted(ranked)
    overlap = len(actual.intersection(selected))
    raw_mass = math.fsum(mass_by_number[number] for number in selected)
    oriented_sum = math.fsum(orientation_score(candidate, mass_by_number[number]) for number in selected)
    zone_draw_count = int(zone_value["zone_draw_count"])
    return {
        "set_size": size,
        "ranked_numbers": ranked,
        "selected_numbers": selected,
        "overlap_count": overlap,
        "predicted_number_hits": overlap,
        "predicted_number_trials": size,
        "predicted_number_hit_rate": overlap / size,
        "actual_number_coverage_rate": overlap / zone_draw_count,
        "any_number_hit": overlap > 0,
        "exact_all_zone_numbers_hit": overlap == zone_draw_count,
        "raw_marginal_inclusion_mass_sum": raw_mass,
        "raw_marginal_confidence_mass": raw_mass / zone_draw_count,
        "orientation_score_sum": oriented_sum,
        "mean_orientation_score": oriented_sum / size,
        "score_is_true_lottery_probability": False,
    }


def validate_frozen_outer(game: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows14 = raw_e14_outer_rows(game)
    rows13 = raw_e13_outer_rows(game)
    report14 = raw_e14_report(game)
    report13 = raw_e13_report(game)
    if len(rows14) != OUTER_DRAWS or len(rows13) != OUTER_DRAWS:
        raise ValueError("FAIL_FROZEN_OUTER_LENGTH")
    projected = []
    for row in rows14:
        source = copy.deepcopy(row)
        source.pop("phase4e14_confidence_calibration")
        projected.append(source)
    if projected != rows13:
        raise ValueError("FAIL_PHASE4E14_EMBEDDED_PHASE4E13_OUTER")
    if row_identity(rows14) != row_identity(rows13):
        raise ValueError("FAIL_PHASE4E13_E14_OUTER_IDENTITY")
    identity_hash = e13_identity_digest(rows14)
    if (
        identity_hash != str(report13["outer_window"]["identity_sha256"])
        or identity_hash != str(report14["outer_window"]["identity_sha256"])
    ):
        raise ValueError("FAIL_FROZEN_OUTER_IDENTITY_HASH")
    for row in rows14:
        if not bool(row["strict_lag"]) or int(row["maximum_training_position"]) != int(row["target_position"]) - 1:
            raise ValueError("FAIL_OUTER_STRICT_LAG")
    return rows14, rows13


def decorate_outer_rows(
    rows: Sequence[dict[str, object]], selections: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    decorated = copy.deepcopy(list(rows))
    for row in decorated:
        zone_outputs: dict[str, object] = {}
        for zone, sizes in ZONE_SIZES.items():
            source = row["zones"][zone]
            candidate = str(selections[zone]["selected_candidate"])
            orientation_id = str(selections[zone]["selected_orientation_id"])
            ranking = oriented_ranking(source["marginal_probabilities"], candidate)
            actual = set(int(number) for number in source["actual_numbers"])
            raw_rank = {
                int(number): rank for rank, number in enumerate(source["marginal_ranking"], start=1)
            }
            selected_rank = {number: rank for rank, number in enumerate(ranking, start=1)}
            observations = []
            for value in source["marginal_probabilities"]:
                number = int(value["number"])
                mass = float(value["inclusion_mass"])
                observations.append(
                    {
                        "number": number,
                        "marginal_inclusion_mass": mass,
                        "orientation_score": orientation_score(candidate, mass),
                        "binary_hit": int(number in actual),
                        "raw_descending_rank": raw_rank[number],
                        "selected_orientation_rank": selected_rank[number],
                    }
                )
            zone_outputs[zone] = {
                "selected_candidate": candidate,
                "selected_registered_control": selections[zone]["selected_registered_control"],
                "selected_orientation_id": orientation_id,
                "canonical_ascending_number_tie_break": True,
                "selected_orientation_ranking": ranking,
                "number_observations": observations,
                "confidence_sets": {
                    str(size): confidence_set(source, candidate, ranking, size) for size in sizes
                },
            }
        row["phase4e15_number_orientation"] = {
            "strict_lag": True,
            "maximum_training_position": row["maximum_training_position"],
            "outer_label_used_for_orientation_selection": False,
            "zones": zone_outputs,
        }
    return decorated


def set_level_association(observations: Sequence[dict[str, object]], size: int) -> dict[str, object]:
    if len(observations) < ASSOCIATION_BUCKETS:
        raise ValueError("fixed-set association requires at least one observation per bucket")
    scores = [float(value["mean_orientation_score"]) for value in observations]
    outcomes = [int(value["overlap_count"]) / size for value in observations]
    association = safe_linear_association(scores, outcomes)
    rho = e13.spearman(scores, outcomes)
    ordered = sorted(
        observations,
        key=lambda value: (float(value["mean_orientation_score"]), str(value["issue"])),
    )
    buckets = []
    for bucket_index in range(ASSOCIATION_BUCKETS):
        start = bucket_index * len(ordered) // ASSOCIATION_BUCKETS
        stop = (bucket_index + 1) * len(ordered) // ASSOCIATION_BUCKETS
        group = ordered[start:stop]
        hits = sum(int(value["overlap_count"]) for value in group)
        trials = len(group) * size
        buckets.append(
            {
                "bucket": bucket_index + 1,
                "observations": len(group),
                "mean_orientation_score": math.fsum(
                    float(value["mean_orientation_score"]) for value in group
                )
                / len(group),
                "predicted_number_hits": hits,
                "predicted_number_trials": trials,
                "predicted_number_hit_rate": hits / trials,
                "predicted_number_hit_rate_wilson95": e13.wilson(hits, trials),
            }
        )
    inversions = [
        {
            "lower_bucket": lower["bucket"],
            "higher_bucket": higher["bucket"],
            "magnitude": float(lower["predicted_number_hit_rate"])
            - float(higher["predicted_number_hit_rate"]),
        }
        for lower, higher in zip(buckets, buckets[1:])
        if float(lower["predicted_number_hit_rate"])
        > float(higher["predicted_number_hit_rate"])
    ]
    return {
        "observation_unit": "one_draw_at_one_fixed_set_size",
        "observations": len(observations),
        "set_size": size,
        "score": "mean selected individual-number orientation score",
        "outcome": "overlap_count_divided_by_fixed_set_size",
        "score_is_true_lottery_probability": False,
        "spearman_rho": rho,
        "descriptive_linear_association": {
            **association,
            "interpretation": "descriptive OLS association, not probability calibration",
        },
        "buckets": buckets,
        "adjacent_bucket_inversions": inversions,
        "monotonic_bucket_behavior": not inversions,
        "positive_association": rho > 0 and association["slope"] > 0,
    }


def fixed_size_metrics(
    rows: Sequence[dict[str, object]], zone: str, size: int
) -> dict[str, object]:
    values = []
    selected_number_observations = []
    for row in rows:
        value = row["phase4e15_number_orientation"]["zones"][zone]["confidence_sets"][str(size)]
        values.append({"issue": str(row["issue"]), **value})
        observations_by_number = {
            int(item["number"]): item
            for item in row["phase4e15_number_orientation"]["zones"][zone]["number_observations"]
        }
        for number in value["ranked_numbers"]:
            item = observations_by_number[int(number)]
            selected_number_observations.append(
                {
                    "issue": str(row["issue"]),
                    "number": int(number),
                    "orientation_score": float(item["orientation_score"]),
                    "binary_hit": int(item["binary_hit"]),
                }
            )
    overlap_total = sum(int(value["overlap_count"]) for value in values)
    draws = len(rows)
    zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
    predicted_trials = draws * size
    coverage_trials = draws * zone_draw_count
    any_hits = sum(bool(value["any_number_hit"]) for value in values)
    exact_hits = sum(bool(value["exact_all_zone_numbers_hit"]) for value in values)
    return {
        "draws": draws,
        "set_size": size,
        "overlap_total": overlap_total,
        "average_overlap_count": overlap_total / draws,
        "predicted_number_hits": overlap_total,
        "predicted_number_trials": predicted_trials,
        "predicted_number_hit_rate": overlap_total / predicted_trials,
        "predicted_number_hit_rate_wilson95": e13.wilson(overlap_total, predicted_trials),
        "actual_number_coverage_hits": overlap_total,
        "actual_number_coverage_trials": coverage_trials,
        "actual_number_coverage_rate": overlap_total / coverage_trials,
        "actual_number_coverage_rate_wilson95": e13.wilson(overlap_total, coverage_trials),
        "any_number_hit_count": any_hits,
        "any_number_hit_rate": any_hits / draws,
        "any_number_hit_rate_wilson95": e13.wilson(any_hits, draws),
        "exact_all_zone_numbers_hit_count": exact_hits,
        "exact_all_zone_numbers_hit_rate": exact_hits / draws,
        "exact_all_zone_numbers_hit_rate_wilson95": e13.wilson(exact_hits, draws),
        "selected_number_observation_association": association_metrics(
            selected_number_observations, "orientation_score", "binary_hit"
        ),
        "fixed_set_score_vs_overlap_association": set_level_association(values, size),
        "wilson_intervals_are_descriptive": True,
    }


def per_canonical_number_metrics(
    rows: Sequence[dict[str, object]], zone: str, sizes: Sequence[int]
) -> list[dict[str, object]]:
    number_pool_size = int(rows[0]["zones"][zone]["number_pool_size"])
    result = []
    for number in range(1, number_pool_size + 1):
        observations = []
        selected_by_size = {size: [] for size in sizes}
        for row in rows:
            zone15 = row["phase4e15_number_orientation"]["zones"][zone]
            observation = next(
                value for value in zone15["number_observations"] if int(value["number"]) == number
            )
            observations.append(observation)
            for size in sizes:
                value = zone15["confidence_sets"][str(size)]
                if number in value["selected_numbers"]:
                    selected_by_size[size].append(int(observation["binary_hit"]))
        actual_hits = sum(int(value["binary_hit"]) for value in observations)
        size_metrics: dict[str, object] = {}
        for size in sizes:
            hits = sum(selected_by_size[size])
            trials = len(selected_by_size[size])
            size_metrics[str(size)] = {
                "selected_draws": trials,
                "selected_and_actual_overlap_count": hits,
                "conditional_predicted_number_hit_rate": hits / trials if trials else None,
                "conditional_predicted_number_hit_rate_wilson95": e13.wilson(hits, trials)
                if trials
                else None,
            }
        result.append(
            {
                "number": number,
                "draws": len(rows),
                "actual_appearance_count": actual_hits,
                "actual_appearance_rate": actual_hits / len(rows),
                "actual_appearance_rate_wilson95": e13.wilson(actual_hits, len(rows)),
                "mean_marginal_inclusion_mass": math.fsum(
                    float(value["marginal_inclusion_mass"]) for value in observations
                )
                / len(observations),
                "mean_orientation_score": math.fsum(
                    float(value["orientation_score"]) for value in observations
                )
                / len(observations),
                "fixed_sizes": size_metrics,
            }
        )
    return result


def split_metrics(
    rows: Sequence[dict[str, object]], selections: dict[str, dict[str, object]]
) -> dict[str, object]:
    result: dict[str, object] = {"draws": len(rows)}
    for zone, sizes in ZONE_SIZES.items():
        candidate = str(selections[zone]["selected_candidate"])
        individual = association_metrics(
            individual_observations(rows, zone, candidate), "orientation_score", "binary_hit"
        )
        fixed = {str(size): fixed_size_metrics(rows, zone, size) for size in sizes}
        result[zone] = {
            "selected_candidate": candidate,
            "selected_registered_control": selections[zone]["selected_registered_control"],
            "selected_orientation_id": selections[zone]["selected_orientation_id"],
            "individual_number_association": individual,
            "fixed_size_set_metrics": fixed,
            "per_canonical_number": per_canonical_number_metrics(rows, zone, sizes),
            "acceptance_pass": bool(individual["positive_association"]),
            "acceptance_uses_fixed_set_or_pooled_set_association": False,
        }
    return result


def selection_window_metadata(inner_rows: Sequence[dict[str, object]]) -> dict[str, object]:
    measure = inner_rows[:ORIENTATION_MEASURE_DRAWS]
    selection = inner_rows[ORIENTATION_MEASURE_DRAWS:]

    def split(name: str, rows: Sequence[dict[str, object]]) -> dict[str, object]:
        return {
            "name": name,
            "draws": len(rows),
            "first_issue": rows[0]["issue"],
            "last_issue": rows[-1]["issue"],
            "first_target_position": rows[0]["target_position"],
            "last_target_position": rows[-1]["target_position"],
            "identity_fields": ["issue", "target_position"],
            "identity_sha256": digest(row_identity(rows)),
            "strictly_pre_outer": True,
            "outer_labels_used": False,
        }

    return {
        "draws": len(inner_rows),
        "first_issue": inner_rows[0]["issue"],
        "last_issue": inner_rows[-1]["issue"],
        "first_target_position": inner_rows[0]["target_position"],
        "last_target_position": inner_rows[-1]["target_position"],
        "identity_fields": ["issue", "target_position"],
        "identity_sha256": digest(row_identity(inner_rows)),
        "phase4e14_inner_identity_verified": True,
        "immediately_before_outer": True,
        "strictly_before_outer": True,
        "outer_labels_used_for_orientation_selection": False,
        "orientation_fit_measure": split("orientation_fit_measure", measure),
        "orientation_selection": split("orientation_selection", selection),
    }


def run(game: str, workers: int) -> dict[str, object]:
    report13 = raw_e13_report(game)
    report14 = raw_e14_report(game)
    outer14, outer13 = validate_frozen_outer(game)
    inner_rows = compute_inner_rows(game, workers)
    selections = select_orientations(inner_rows)
    decorated_outer = decorate_outer_rows(outer14, selections)
    for source, row in zip(outer14, decorated_outer):
        projected = copy.deepcopy(row)
        projected.pop("phase4e15_number_orientation")
        if projected != source:
            raise ValueError("FAIL_PHASE4E14_OUTER_ROW_MUTATION")
    metrics = {
        "calibration": split_metrics(decorated_outer[:OUTER_CALIBRATION_DRAWS], selections),
        "evaluation": split_metrics(decorated_outer[OUTER_CALIBRATION_DRAWS:], selections),
        "all_120_descriptive": split_metrics(decorated_outer, selections),
    }
    evaluation_zone_pass = {
        zone: bool(metrics["evaluation"][zone]["acceptance_pass"]) for zone in ("front", "back")
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    inner_path = path / "inner-rolling-report.jsonl"
    outer_path = path / "outer-rolling-report.jsonl"
    inner_path.write_bytes(b"".join(canonical(row) for row in inner_rows))
    outer_path.write_bytes(b"".join(canonical(row) for row in decorated_outer))
    selection_window = selection_window_metadata(inner_rows)
    identity_hash = e13_identity_digest(outer14)
    source_path = SOURCE / f"{game}.jsonl"
    report = {
        "artifact_type": "phase4e15_per_number_marginal_orientation_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "diagnostic_claim": "orientation/model diagnostic; marginal inclusion scores are not true lottery probabilities",
        "outer_window": {
            **report14["outer_window"],
            "first_60_role": "frozen_outer_calibration_evaluation_only_not_selection",
            "last_60_role": "frozen_outer_evaluation",
            "identity_sha256": identity_hash,
            "identity_named_fields_sha256": digest(row_identity(outer14)),
            "identity_matches_phase4e13": row_identity(outer14) == row_identity(outer13),
            "identity_matches_phase4e14": identity_hash == report14["outer_window"]["identity_sha256"],
        },
        "orientation_selection_window": selection_window,
        "orientation_experiment": {
            "bounded_candidates": list(CANDIDATE_ORDER),
            "candidate_count": len(CANDIDATE_ORDER),
            "selection_scope": "separate_for_each_game_and_zone",
            "canonical_ascending_number_tie_break": True,
            "selection_uses_outer_labels": False,
            "selection_results": selections,
            "score_claim": "signed marginal inclusion mass used only as an orientation association score",
        },
        "outer_splits": metrics,
        "acceptance": {
            "rule": "every game/zone on the last-60 frozen outer evaluation requires individual-number Spearman rho > 0 and positive descriptive slope; fixed-size overlap and Wilson intervals are mandatory reports but not selection or acceptance gates",
            "evaluation_zone_pass": evaluation_zone_pass,
            "failed_zones": [zone for zone, passed in evaluation_zone_pass.items() if not passed],
            "accepted": all(evaluation_zone_pass.values()),
            "pooled_or_set_level_confidence_used_for_acceptance": False,
        },
        "full_ticket_comparison": report14["full_ticket_comparison"],
        "full_ticket_comparison_unchanged_from_phase4e13": report14["full_ticket_comparison"]
        == report13["full_ticket_comparison"],
        "lineage": {
            "source_data_path": str(source_path.relative_to(ROOT)),
            "source_data_sha256": sha256(source_path),
            "registered_p4e2_oracle_path": str(ORACLE.relative_to(ROOT)),
            "registered_p4e2_oracle_sha256": sha256(ORACLE),
            "phase4e13_script_path": str(E13_SCRIPT.relative_to(ROOT)),
            "phase4e13_script_sha256": sha256(E13_SCRIPT),
            "phase4e13_summary_sha256": sha256(E13 / "summary.json"),
            "phase4e13_report_sha256": sha256(E13 / game / "report.json"),
            "phase4e13_outer_rows_sha256": sha256(E13 / game / "outer-rolling-report.jsonl"),
            "phase4e14_script_path": str(E14_SCRIPT.relative_to(ROOT)),
            "phase4e14_script_sha256": sha256(E14_SCRIPT),
            "phase4e14_summary_sha256": sha256(E14 / "summary.json"),
            "phase4e14_report_sha256": sha256(E14 / game / "report.json"),
            "phase4e14_inner_rows_sha256": sha256(E14 / game / "inner-rolling-report.jsonl"),
            "phase4e14_outer_rows_sha256": sha256(E14 / game / "outer-rolling-report.jsonl"),
            "phase4e13_outer_identity_sha256": report13["outer_window"]["identity_sha256"],
            "phase4e14_outer_identity_sha256": report14["outer_window"]["identity_sha256"],
            "phase4e15_outer_identity_sha256": identity_hash,
            "phase4e15_outer_identity_named_fields_sha256": digest(row_identity(outer14)),
            "phase4e15_selection_identity_sha256": selection_window["identity_sha256"],
            "phase4e15_fit_measure_identity_sha256": selection_window["orientation_fit_measure"][
                "identity_sha256"
            ],
            "phase4e15_orientation_selection_identity_sha256": selection_window[
                "orientation_selection"
            ]["identity_sha256"],
            "phase4e15_inner_rows_sha256": sha256(inner_path),
            "phase4e15_outer_rows_sha256": sha256(outer_path),
        },
        "strict_lag": {
            "target_t_uses_through_t_minus_1_only": True,
            "all_selection_rows_strict_lag": all(bool(row["strict_lag"]) for row in inner_rows),
            "all_outer_rows_strict_lag": all(bool(row["strict_lag"]) for row in outer14),
            "all_maximum_training_positions_equal_target_minus_one": all(
                int(row["maximum_training_position"]) == int(row["target_position"]) - 1
                for row in [*inner_rows, *outer14]
            ),
            "outer_labels_used_for_orientation_selection": False,
        },
    }
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase4E15 per-number marginal orientation diagnostic")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game, args.workers) for game in ("ssq", "dlt")}
    selected = {
        game: {
            zone: report["orientation_experiment"]["selection_results"][zone]["selected_candidate"]
            for zone in ("front", "back")
        }
        for game, report in reports.items()
    }
    summary = {
        "artifact_type": "phase4e15_per_number_marginal_orientation_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "games": reports,
        "selected_orientations": selected,
        "reverse_selected_by_game_zone": {
            game: [zone for zone, candidate in zones.items() if candidate == "reverse_ascending_marginal_mass_control"]
            for game, zones in selected.items()
        },
        "acceptance_rule": "all games and zones pass individual-number rho>0 and slope>0 on last-60 frozen outer evaluation",
        "failed_game_zones": [
            {"game": game, "zone": zone}
            for game, report in reports.items()
            for zone, passed in report["acceptance"]["evaluation_zone_pass"].items()
            if not passed
        ],
        "accepted_all_games_zones": all(
            bool(report["acceptance"]["accepted"]) for report in reports.values()
        ),
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "selected_orientations": selected,
                "failed_game_zones": summary["failed_game_zones"],
                "accepted_all_games_zones": summary["accepted_all_games_zones"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
