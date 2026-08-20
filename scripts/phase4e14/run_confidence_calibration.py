#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
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
sys.path.insert(0, str(ROOT / "scripts/phase4e13"))
import run_partial_hit_evaluation as e13

OUT = ROOT / "artifacts/phase4e14"
E13 = ROOT / "artifacts/phase4e13"
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
ORACLE = ROOT / "scripts/phase4_independent/p4e2_oracle.py"
E13_SCRIPT = ROOT / "scripts/phase4e13/run_partial_hit_evaluation.py"
OUTER_DRAWS = 120
OUTER_CALIBRATION_DRAWS = 60
SELECTION_DRAWS = 120
TRANSFORM_FIT_DRAWS = 60
TRANSFORM_HOLDOUT_DRAWS = 60
CONFIDENCE_BUCKETS = 5
TINY_INVERSION_TOLERANCE = 0.02
CANDIDATE_ORDER = (
    "raw_marginal_mass",
    "reverse_raw_mass_control",
    "empirical_rank_overlap_quantile",
    "isotonic_expected_overlap_pava",
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


def raw_e13_report(game: str) -> dict[str, object]:
    return read_json(E13 / game / "report.json")


def raw_e13_rows(game: str) -> list[dict[str, object]]:
    return read_jsonl(E13 / game / "outer-rolling-report.jsonl")


def row_identity(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"issue": str(row["issue"]), "target_position": int(row["target_position"])}
        for row in rows
    ]


def phase4e13_outer_identity_digest(rows: Sequence[dict[str, object]]) -> str:
    """Preserve E13's registered hash over (issue, target_position) pairs."""
    return digest([(str(row["issue"]), int(row["target_position"])) for row in rows])


def selection_window(game: str) -> dict[str, object]:
    data = e13.load(game)
    outer_start = len(data) - OUTER_DRAWS
    start = outer_start - SELECTION_DRAWS
    if start < e13.e12.e9.oracle.MIN_HISTORY:
        raise ValueError("FAIL_SELECTION_HISTORY")
    targets = list(range(start, outer_start))
    identities = [
        {"issue": data[target].issue, "target_position": target} for target in targets
    ]
    result = {
        "draws": len(targets),
        "first_issue": identities[0]["issue"],
        "last_issue": identities[-1]["issue"],
        "first_target_position": targets[0],
        "last_target_position": targets[-1],
        "outer_first_target_position": outer_start,
        "immediately_before_outer": targets[-1] + 1 == outer_start,
        "strictly_before_outer": all(target < outer_start for target in targets),
        "outer_labels_used_for_transform_selection": False,
        "identity_sha256": digest(identities),
        "transform_fit": {
            "draws": TRANSFORM_FIT_DRAWS,
            "first_issue": identities[0]["issue"],
            "last_issue": identities[TRANSFORM_FIT_DRAWS - 1]["issue"],
            "first_target_position": targets[0],
            "last_target_position": targets[TRANSFORM_FIT_DRAWS - 1],
        },
        "transform_holdout": {
            "draws": TRANSFORM_HOLDOUT_DRAWS,
            "first_issue": identities[TRANSFORM_FIT_DRAWS]["issue"],
            "last_issue": identities[-1]["issue"],
            "first_target_position": targets[TRANSFORM_FIT_DRAWS],
            "last_target_position": targets[-1],
        },
    }
    if game == "dlt" and (result["first_issue"], result["last_issue"]) != (
        "2025004",
        "2025123",
    ):
        raise ValueError("FAIL_REGISTERED_DLT_SELECTION_ISSUES")
    return result


def inner_row(task: tuple[str, int]) -> dict[str, object]:
    game, target = task
    data = e13.load(game)
    outer_start = len(data) - OUTER_DRAWS
    start = outer_start - SELECTION_DRAWS
    if target < start or target >= outer_start:
        raise ValueError("FAIL_OUTER_LABEL_IN_TRANSFORM_SELECTION")
    coefficients = e13.fitted_selected_coefficients(game, target)
    zones: dict[str, object] = {}
    for zone_index, zone in enumerate(("front", "back")):
        evaluated = e13.zone_evaluation(game, data, target, zone_index, coefficients)
        zones[zone] = {
            "zone_draw_count": evaluated["zone_draw_count"],
            "marginal_ranking": evaluated["marginal_ranking"],
            "confidence_sets": evaluated["confidence_sets"],
        }
    offset = target - start
    return {
        "game": game,
        "issue": data[target].issue,
        "target_position": target,
        "maximum_training_position": target - 1,
        "maximum_training_issue": data[target - 1].issue,
        "strict_lag": True,
        "selection_subsplit": "transform_fit" if offset < TRANSFORM_FIT_DRAWS else "transform_holdout",
        "zones": zones,
    }


def validate_selection_rows(
    game: str, rows: Sequence[dict[str, object]], outer_start: int | None = None
) -> None:
    data = e13.load(game)
    if outer_start is None:
        outer_start = len(data) - OUTER_DRAWS
    expected = list(range(outer_start - SELECTION_DRAWS, outer_start))
    observed = [int(row["target_position"]) for row in rows]
    if observed != expected:
        raise ValueError("FAIL_OUTER_LABEL_IN_TRANSFORM_SELECTION")
    for index, row in enumerate(rows):
        target = observed[index]
        expected_split = "transform_fit" if index < TRANSFORM_FIT_DRAWS else "transform_holdout"
        if target >= outer_start or str(row["issue"]) != data[target].issue:
            raise ValueError("FAIL_OUTER_LABEL_IN_TRANSFORM_SELECTION")
        if row.get("selection_subsplit") != expected_split:
            raise ValueError("FAIL_SELECTION_SUBSPLIT_IDENTITY")
        if not bool(row.get("strict_lag")) or int(row["maximum_training_position"]) != target - 1:
            raise ValueError("FAIL_SELECTION_STRICT_LAG")


def observation_vectors(
    rows: Sequence[dict[str, object]], zone: str, size: int
) -> tuple[list[float], list[float], list[int], list[str]]:
    zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
    values = [row["zones"][zone]["confidence_sets"][str(size)] for row in rows]
    raw = [float(value["confidence_mass"]) for value in values]
    overlaps = [int(value["overlap_count"]) for value in values]
    outcomes = [overlap / zone_draw_count for overlap in overlaps]
    issues = [str(row["issue"]) for row in rows]
    return raw, outcomes, overlaps, issues


def empirical_quantile(ordered: Sequence[float], probability: float) -> float:
    if not ordered:
        raise ValueError("quantile requires observations")
    if len(ordered) == 1:
        return float(ordered[0])
    position = min(1.0, max(0.0, probability)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    fraction = position - lower
    return float(ordered[lower]) * (1 - fraction) + float(ordered[upper]) * fraction


def pava_fit(raw: Sequence[float], outcomes: Sequence[float]) -> tuple[list[float], list[float]]:
    if len(raw) != len(outcomes) or not raw:
        raise ValueError("PAVA requires paired observations")
    grouped: list[list[float]] = []
    for x, y in sorted(zip(raw, outcomes), key=lambda pair: pair[0]):
        if grouped and float(x) == grouped[-1][0]:
            grouped[-1][1] += float(y)
            grouped[-1][2] += 1.0
        else:
            grouped.append([float(x), float(y), 1.0])
    blocks: list[list[float]] = []
    for index, (_, total, weight) in enumerate(grouped):
        blocks.append([float(index), float(index), total, weight])
        while len(blocks) >= 2:
            previous = blocks[-2]
            current = blocks[-1]
            if previous[2] / previous[3] <= current[2] / current[3]:
                break
            blocks[-2:] = [[previous[0], current[1], previous[2] + current[2], previous[3] + current[3]]]
    fitted = [0.0] * len(grouped)
    for start, stop, total, weight in blocks:
        for index in range(int(start), int(stop) + 1):
            fitted[index] = total / weight
    return [value[0] for value in grouped], fitted


def fit_transform(
    candidate: str,
    raw: Sequence[float],
    outcomes: Sequence[float],
    fit_identity_sha256: str,
) -> dict[str, object]:
    if candidate not in CANDIDATE_ORDER:
        raise ValueError(f"unknown transform candidate: {candidate}")
    if len(raw) != TRANSFORM_FIT_DRAWS or len(outcomes) != TRANSFORM_FIT_DRAWS:
        raise ValueError("FAIL_TRANSFORM_FIT_WINDOW")
    model: dict[str, object] = {
        "candidate": candidate,
        "fit_draws": len(raw),
        "fit_identity_sha256": fit_identity_sha256,
        "uses_outer_labels": False,
    }
    if candidate == "raw_marginal_mass":
        model.update({"score_semantics": "raw marginal confidence mass", "uses_fit_labels": False})
    elif candidate == "reverse_raw_mass_control":
        model.update(
            {
                "score_semantics": "negative raw marginal mass; registered directional control",
                "uses_fit_labels": False,
                "registered_control_only": True,
            }
        )
    elif candidate == "empirical_rank_overlap_quantile":
        model.update(
            {
                "score_semantics": "calibrated association score from raw-mass percentile mapped to empirical overlap-rate quantile",
                "uses_fit_labels": True,
                "raw_order_statistics": sorted(float(value) for value in raw),
                "overlap_rate_order_statistics": sorted(float(value) for value in outcomes),
            }
        )
    else:
        thresholds, fitted = pava_fit(raw, outcomes)
        model.update(
            {
                "score_semantics": "expected overlap score from nondecreasing PAVA fit",
                "uses_fit_labels": True,
                "raw_thresholds": thresholds,
                "fitted_expected_overlap": fitted,
            }
        )
    model["transform_id"] = f"p4e14-{candidate}-{digest(model)[:16]}"
    return model


def apply_transform(model: dict[str, object], raw_value: float) -> float:
    candidate = str(model["candidate"])
    if candidate == "raw_marginal_mass":
        return float(raw_value)
    if candidate == "reverse_raw_mass_control":
        return -float(raw_value)
    if candidate == "empirical_rank_overlap_quantile":
        raw_order = [float(value) for value in model["raw_order_statistics"]]
        outcomes = [float(value) for value in model["overlap_rate_order_statistics"]]
        left = bisect.bisect_left(raw_order, raw_value)
        right = bisect.bisect_right(raw_order, raw_value)
        percentile = ((left + right) / 2) / len(raw_order)
        return empirical_quantile(outcomes, percentile)
    if candidate == "isotonic_expected_overlap_pava":
        thresholds = [float(value) for value in model["raw_thresholds"]]
        fitted = [float(value) for value in model["fitted_expected_overlap"]]
        index = min(len(thresholds) - 1, max(0, bisect.bisect_right(thresholds, raw_value) - 1))
        return fitted[index]
    raise ValueError(f"unknown fitted transform: {candidate}")


def safe_linear_association(left: Sequence[float], right: Sequence[float]) -> dict[str, float]:
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    denominator = math.fsum((value - left_mean) ** 2 for value in left)
    if denominator == 0:
        return {"intercept": right_mean, "slope": 0.0}
    slope = math.fsum((x - left_mean) * (y - right_mean) for x, y in zip(left, right)) / denominator
    return {"intercept": right_mean - slope * left_mean, "slope": slope}


def fixed_size_metrics(
    scores: Sequence[float],
    overlaps: Sequence[int],
    issues: Sequence[str],
    zone_draw_count: int,
    size: int,
) -> dict[str, object]:
    if not (len(scores) == len(overlaps) == len(issues)) or len(scores) < CONFIDENCE_BUCKETS:
        raise ValueError("fixed-size association requires aligned observations")
    outcomes = [int(value) / zone_draw_count for value in overlaps]
    association = safe_linear_association(scores, outcomes)
    rho = e13.spearman(scores, outcomes)
    ordered = sorted(
        zip(scores, overlaps, issues), key=lambda row: (float(row[0]), str(row[2]))
    )
    quintiles = []
    for bucket_index in range(CONFIDENCE_BUCKETS):
        start = bucket_index * len(ordered) // CONFIDENCE_BUCKETS
        stop = (bucket_index + 1) * len(ordered) // CONFIDENCE_BUCKETS
        group = ordered[start:stop]
        hits = sum(int(row[1]) for row in group)
        trials = len(group) * zone_draw_count
        quintiles.append(
            {
                "quintile": bucket_index + 1,
                "observations": len(group),
                "mean_association_score": math.fsum(float(row[0]) for row in group) / len(group),
                "number_level_hits": hits,
                "number_level_trials": trials,
                "overlap_rate": hits / trials,
                "overlap_rate_wilson95": e13.wilson(hits, trials),
            }
        )
    inversions = []
    for lower, higher in zip(quintiles, quintiles[1:]):
        magnitude = float(lower["overlap_rate"]) - float(higher["overlap_rate"])
        if magnitude > 0:
            lower_interval = lower["overlap_rate_wilson95"]
            higher_interval = higher["overlap_rate_wilson95"]
            intervals_overlap = max(lower_interval[0], higher_interval[0]) <= min(
                lower_interval[1], higher_interval[1]
            )
            inversions.append(
                {
                    "lower_quintile": lower["quintile"],
                    "higher_quintile": higher["quintile"],
                    "magnitude": magnitude,
                    "wilson_intervals_overlap": intervals_overlap,
                    "within_registered_tolerance": (
                        magnitude <= TINY_INVERSION_TOLERANCE and intervals_overlap
                    ),
                }
            )
    monotonic_with_tolerance = not inversions or (
        len(inversions) == 1 and bool(inversions[0]["within_registered_tolerance"])
    )
    hits = sum(int(value) for value in overlaps)
    trials = len(overlaps) * zone_draw_count
    positive = rho > 0 and association["slope"] > 0
    return {
        "observation_unit": "one_draw_at_one_fixed_set_size",
        "draws": len(scores),
        "set_size": size,
        "score_is_true_lottery_probability": False,
        "outcome": "overlap_count_divided_by_zone_draw_count",
        "spearman_rho_score_vs_number_hit_rate": rho,
        "descriptive_linear_association": {
            **association,
            "interpretation": "descriptive OLS association, not probability calibration",
        },
        "overlap_total": hits,
        "number_level_trials": trials,
        "overlap_rate": hits / trials,
        "overlap_rate_wilson95": e13.wilson(hits, trials),
        "quintiles": quintiles,
        "adjacent_inversions": inversions,
        "monotonic_without_tolerance": not inversions,
        "monotonic_with_registered_tolerance": monotonic_with_tolerance,
        "registered_tolerance": {
            "maximum_inversions": 1,
            "maximum_absolute_overlap_rate_inversion": TINY_INVERSION_TOLERANCE,
            "requires_wilson95_overlap": True,
        },
        "positive_association": positive,
        "acceptance_pass": positive and monotonic_with_tolerance,
    }


def fit_and_select_transforms(
    game: str, rows: Sequence[dict[str, object]]
) -> dict[str, dict[str, dict[str, object]]]:
    outer_start = len(e13.load(game)) - OUTER_DRAWS
    validate_selection_rows(game, rows, outer_start)
    fit_rows = rows[:TRANSFORM_FIT_DRAWS]
    holdout_rows = rows[TRANSFORM_FIT_DRAWS:]
    fit_identity_hash = digest(row_identity(fit_rows))
    selected: dict[str, dict[str, dict[str, object]]] = {"front": {}, "back": {}}
    for zone, sizes in ZONE_SIZES.items():
        zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
        for size in sizes:
            fit_raw, fit_outcomes, _, _ = observation_vectors(fit_rows, zone, size)
            holdout_raw, _, holdout_overlaps, holdout_issues = observation_vectors(
                holdout_rows, zone, size
            )
            candidates = []
            for order, candidate in enumerate(CANDIDATE_ORDER):
                model = fit_transform(candidate, fit_raw, fit_outcomes, fit_identity_hash)
                scores = [apply_transform(model, value) for value in holdout_raw]
                metrics = fixed_size_metrics(
                    scores, holdout_overlaps, holdout_issues, zone_draw_count, size
                )
                passes_positive_metrics = bool(metrics["positive_association"])
                registered_control_only = candidate == "reverse_raw_mass_control"
                eligible = passes_positive_metrics and not registered_control_only
                candidates.append(
                    {
                        "candidate": candidate,
                        "candidate_order": order + 1,
                        "registered_control_only": registered_control_only,
                        "passes_positive_rho_and_slope": passes_positive_metrics,
                        "eligible_positive_rho_and_slope": eligible,
                        "model": model,
                        "inner_holdout_metrics": metrics,
                    }
                )
            winner = max(
                candidates,
                key=lambda value: (
                    not bool(value["registered_control_only"]),
                    bool(value["eligible_positive_rho_and_slope"]),
                    float(value["inner_holdout_metrics"]["spearman_rho_score_vs_number_hit_rate"]),
                    bool(value["inner_holdout_metrics"]["monotonic_with_registered_tolerance"]),
                    -int(value["candidate_order"]),
                ),
            )
            selected[zone][str(size)] = {
                "selection_rule": "among non-control transforms: positive Spearman rho and positive descriptive slope, then maximum rho; exact ties use monotonicity then registered candidate order; reverse mass is reported control-only and cannot win",
                "selection_uses_outer_labels": False,
                "selected_candidate": winner["candidate"],
                "selected_transform_id": winner["model"]["transform_id"],
                "selected_candidate_eligible": winner["eligible_positive_rho_and_slope"],
                "selected_model": winner["model"],
                "candidates": candidates,
            }
    return selected


def decorate_inner_rows(
    rows: Sequence[dict[str, object]], selections: dict[str, dict[str, dict[str, object]]]
) -> list[dict[str, object]]:
    decorated = copy.deepcopy(list(rows))
    for row in decorated:
        transform_ids: dict[str, object] = {"front": {}, "back": {}}
        candidate_scores: dict[str, object] = {"front": {}, "back": {}}
        for zone, sizes in ZONE_SIZES.items():
            for size in sizes:
                raw = float(row["zones"][zone]["confidence_sets"][str(size)]["confidence_mass"])
                selection = selections[zone][str(size)]
                transform_ids[zone][str(size)] = selection["selected_transform_id"]
                candidate_scores[zone][str(size)] = {
                    candidate["model"]["transform_id"]: apply_transform(candidate["model"], raw)
                    for candidate in selection["candidates"]
                }
        row["transform_ids"] = transform_ids
        row["candidate_association_scores"] = candidate_scores
    return decorated


def decorate_outer_rows(
    game: str,
    rows: Sequence[dict[str, object]],
    selections: dict[str, dict[str, dict[str, object]]],
) -> list[dict[str, object]]:
    if len(rows) != OUTER_DRAWS:
        raise ValueError("FAIL_FROZEN_OUTER_LENGTH")
    decorated = copy.deepcopy(list(rows))
    for source, row in zip(rows, decorated):
        if not bool(source["strict_lag"]):
            raise ValueError("FAIL_OUTER_STRICT_LAG")
        calibration: dict[str, object] = {"front": {}, "back": {}}
        transform_ids: dict[str, object] = {"front": {}, "back": {}}
        for zone, sizes in ZONE_SIZES.items():
            for size in sizes:
                raw = float(source["zones"][zone]["confidence_sets"][str(size)]["confidence_mass"])
                selection = selections[zone][str(size)]
                model = selection["selected_model"]
                transform_id = str(selection["selected_transform_id"])
                transform_ids[zone][str(size)] = transform_id
                calibration[zone][str(size)] = {
                    "set_size": size,
                    "raw_marginal_confidence_mass": raw,
                    "calibrated_association_score": apply_transform(model, raw),
                    "transform_candidate": selection["selected_candidate"],
                    "transform_id": transform_id,
                    "score_semantics": model["score_semantics"],
                    "score_is_true_lottery_probability": False,
                }
        row["phase4e14_confidence_calibration"] = {
            "strict_lag": True,
            "maximum_training_position": source["maximum_training_position"],
            "transform_ids": transform_ids,
            "zones": calibration,
        }
    return decorated


def outer_split_metrics(
    decorated: Sequence[dict[str, object]],
    raw_report: dict[str, object],
    selections: dict[str, dict[str, dict[str, object]]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    split_rows = {
        "calibration": decorated[:OUTER_CALIBRATION_DRAWS],
        "evaluation": decorated[OUTER_CALIBRATION_DRAWS:],
    }
    for split, rows in split_rows.items():
        result[split] = {"draws": len(rows)}
        for zone, sizes in ZONE_SIZES.items():
            zone_draw_count = int(rows[0]["zones"][zone]["zone_draw_count"])
            fixed: dict[str, object] = {}
            for size in sizes:
                scores = [
                    float(
                        row["phase4e14_confidence_calibration"]["zones"][zone][str(size)][
                            "calibrated_association_score"
                        ]
                    )
                    for row in rows
                ]
                overlaps = [
                    int(row["zones"][zone]["confidence_sets"][str(size)]["overlap_count"])
                    for row in rows
                ]
                issues = [str(row["issue"]) for row in rows]
                metrics = fixed_size_metrics(scores, overlaps, issues, zone_draw_count, size)
                metrics.update(
                    {
                        "selected_candidate": selections[zone][str(size)]["selected_candidate"],
                        "transform_id": selections[zone][str(size)]["selected_transform_id"],
                        "raw_phase4e13_fixed_size_association": raw_report["splits"][split][zone][
                            "fixed_size_confidence_association"
                        ][str(size)],
                        "raw_phase4e13_set_size_metrics": raw_report["splits"][split][zone][
                            "set_size_metrics"
                        ][str(size)],
                    }
                )
                fixed[str(size)] = metrics
            result[split][zone] = {
                "fixed_size_association": fixed,
                "all_fixed_sizes_acceptance_pass": all(
                    bool(value["acceptance_pass"]) for value in fixed.values()
                ),
                "pooled_set_size_acceptance_used": False,
            }
    return result


def compute_inner_rows(game: str, workers: int) -> list[dict[str, object]]:
    data = e13.load(game)
    outer_start = len(data) - OUTER_DRAWS
    tasks = [(game, target) for target in range(outer_start - SELECTION_DRAWS, outer_start)]
    if workers == 1:
        rows = [inner_row(task) for task in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            rows = pool.map(inner_row, tasks, chunksize=1)
    rows.sort(key=lambda row: int(row["target_position"]))
    validate_selection_rows(game, rows, outer_start)
    return rows


def run(game: str, workers: int) -> dict[str, object]:
    raw_report = raw_e13_report(game)
    raw_rows = raw_e13_rows(game)
    raw_identity = row_identity(raw_rows)
    inherited_identity_hash = phase4e13_outer_identity_digest(raw_rows)
    if len(raw_rows) != OUTER_DRAWS or inherited_identity_hash != raw_report["outer_window"]["identity_sha256"]:
        raise ValueError("FAIL_PHASE4E13_OUTER_IDENTITY")
    inner_rows = compute_inner_rows(game, workers)
    selections = fit_and_select_transforms(game, inner_rows)
    decorated_inner = decorate_inner_rows(inner_rows, selections)
    decorated_outer = decorate_outer_rows(game, raw_rows, selections)
    if [
        {key: value for key, value in row.items() if key != "phase4e14_confidence_calibration"}
        for row in decorated_outer
    ] != raw_rows:
        raise ValueError("FAIL_RAW_PHASE4E13_ROW_MUTATION")
    metrics = outer_split_metrics(decorated_outer, raw_report, selections)
    evaluation_pass = all(
        bool(metrics["evaluation"][zone]["all_fixed_sizes_acceptance_pass"])
        for zone in ("front", "back")
    )
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    inner_path = path / "inner-rolling-report.jsonl"
    outer_path = path / "outer-rolling-report.jsonl"
    inner_path.write_bytes(b"".join(canonical(row) for row in decorated_inner))
    outer_path.write_bytes(b"".join(canonical(row) for row in decorated_outer))
    window = selection_window(game)
    source_path = SOURCE / f"{game}.jsonl"
    report = {
        "artifact_type": "phase4e14_fixed_size_confidence_calibration_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "outer_window": {
            **raw_report["outer_window"],
            "first_60_role": "outer_calibration_evaluation_only_not_transform_selection",
            "last_60_role": "frozen_outer_evaluation",
            "identity_sha256": inherited_identity_hash,
            "identity_named_fields_sha256": digest(raw_identity),
            "identity_matches_phase4e13": True,
        },
        "candidate_selection_window": window,
        "transform_experiment": {
            "bounded_candidates": list(CANDIDATE_ORDER),
            "candidate_count": len(CANDIDATE_ORDER),
            "selection_scope": "separate_for_every_game_zone_fixed_set_size",
            "number_ranking_or_selected_sets_changed": False,
            "score_claim": "calibrated association score or expected overlap score; never a lottery probability",
            "transform_fit_labels": "first 60 of pre-outer 120 only",
            "transform_selection_labels": "last 60 of pre-outer 120 only",
            "outer_labels_used": False,
            "selection_results": selections,
        },
        "outer_splits": metrics,
        "acceptance": {
            "rule": "every game/zone/fixed set size in the last-60 outer evaluation requires rho>0, slope>0, and no registered monotonic inversion beyond tolerance; no pooling",
            "evaluation_zone_pass": {
                zone: metrics["evaluation"][zone]["all_fixed_sizes_acceptance_pass"]
                for zone in ("front", "back")
            },
            "accepted": evaluation_pass,
            "pooled_set_sizes_used": False,
        },
        "raw_phase4e13_metrics": {
            "splits": raw_report["splits"],
            "partial_hit_acceptance": raw_report["partial_hit_acceptance"],
            "source_report_sha256": sha256(E13 / game / "report.json"),
            "unchanged": True,
        },
        "full_ticket_comparison": raw_report["full_ticket_comparison"],
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
            "phase4e13_outer_identity_sha256": raw_report["outer_window"]["identity_sha256"],
            "phase4e14_outer_identity_sha256": inherited_identity_hash,
            "phase4e14_outer_identity_named_fields_sha256": digest(raw_identity),
            "phase4e14_selection_identity_sha256": window["identity_sha256"],
            "phase4e14_inner_rows_sha256": sha256(inner_path),
            "phase4e14_outer_rows_sha256": sha256(outer_path),
        },
        "strict_lag": {
            "all_selection_rows_strict_lag": all(bool(row["strict_lag"]) for row in inner_rows),
            "all_outer_rows_strict_lag": all(bool(row["strict_lag"]) for row in raw_rows),
            "all_maximum_training_positions_equal_target_minus_one": all(
                int(row["maximum_training_position"]) == int(row["target_position"]) - 1
                for row in [*inner_rows, *raw_rows]
            ),
        },
    }
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase4E14 fixed-size confidence calibration")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game, args.workers) for game in ("ssq", "dlt")}
    summary = {
        "artifact_type": "phase4e14_fixed_size_confidence_calibration_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "games": reports,
        "acceptance_rule": "all games, zones, and fixed set sizes pass on last-60 frozen outer evaluation; no pooling",
        "accepted_all_games": all(bool(report["acceptance"]["accepted"]) for report in reports.values()),
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "accepted_all_games": summary["accepted_all_games"],
                "game_acceptance": {
                    game: report["acceptance"] for game, report in reports.items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
