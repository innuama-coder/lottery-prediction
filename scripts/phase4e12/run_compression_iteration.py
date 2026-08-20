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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e9"))
import run_nested_spaces as e9

OUT = ROOT / "artifacts/phase4e12"
E11 = ROOT / "artifacts/phase4e11"
INNER_DRAWS = 120
OUTER_DRAWS = 120
CALIBRATION_DRAWS = 60
BASELINE_FIT_HISTORY = 80
LONG_FIT_HISTORY = 240
MATERIAL_IMPROVEMENT = 0.05
SPACES = (1000, 2000, 5000, 10000, 50000, 100000)
CANDIDATES = ("e11_baseline_80", "long_history_240", "strongest_masks_50_50")
E11_MASK_ORDER = ("e8_selected", "all14", "history_only", "history_structure")


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outer_identity_sha256(rows: Sequence[dict[str, object]]) -> str:
    """Hash only the frozen target identity, independent of model-produced fields."""
    identity = [
        {"issue": str(row["issue"]), "target_position": int(row["target_position"])}
        for row in rows
    ]
    return hashlib.sha256(canonical(identity)).hexdigest()


def mask_for(game: str, name: str) -> frozenset[str]:
    if name == "e8_selected":
        return frozenset(e9.MASK[game])
    if name == "all14":
        return frozenset(e9.oracle.FEATURE_IDS)
    if name == "history_only":
        return frozenset(e9.oracle.FEATURE_IDS[:5])
    if name == "history_structure":
        return frozenset(e9.oracle.FEATURE_IDS[:12])
    raise ValueError(f"unknown E11 mask: {name}")


def conformal_k(values: Sequence[int], probability: float) -> int:
    if not values:
        raise ValueError("conformal rank selection requires observations")
    ordered = sorted(int(value) for value in values)
    index = min(len(ordered), math.ceil((len(ordered) + 1) * probability)) - 1
    return ordered[index]


def coverage(ranks: Sequence[int], k: int, full_space: int) -> dict[str, object]:
    hits = sum(int(rank) <= k for rank in ranks)
    draws = len(ranks)
    interval = e9.wilson(hits, draws)
    rate = hits / draws
    return {
        "hits": hits,
        "draws": draws,
        "rate": rate,
        "wilson95": interval,
        "acceptance": {
            "minimum_evaluation_rate": 0.80,
            "minimum_wilson95_lower": 0.75,
            "pass": rate >= 0.80 and interval[0] >= 0.75,
        },
    }


def fit_coefficients(
    game: str, draws: Sequence[e9.oracle.Draw], cutoff: int, l2: float, fit_history: int
) -> list[dict[str, float]]:
    """P4E2 one-step fit with an explicit trailing-label history."""
    if l2 not in e9.oracle.L2_GRID:
        raise ValueError("HOLD_MODEL_RELEASE: unregistered or inline L2")
    if cutoff < e9.oracle.MIN_HISTORY + 8 or cutoff > len(draws):
        raise ValueError("illegal training cutoff")
    if fit_history < 1:
        raise ValueError("fit_history must be positive")
    start = max(e9.oracle.MIN_HISTORY, cutoff - fit_history)
    gradients = [[0.0] * len(e9.oracle.FEATURE_IDS) for _ in (0, 1)]
    for target in range(start, cutoff):
        for zone in (0, 1):
            context = e9.oracle.feature_context(game, draws[:target], zone)
            actual = draws[target].front if zone == 0 else draws[target].back
            observed = e9.oracle.combo_vector(actual, context)
            uniform = e9.oracle._uniform_feature_expectation(context)
            for index, (observed_value, expected_value) in enumerate(zip(observed, uniform)):
                gradients[zone][index] += (observed_value - expected_value) / (cutoff - start)
    return [
        {
            key: max(-e9.oracle.COEFFICIENT_CAP, min(e9.oracle.COEFFICIENT_CAP, value / l2))
            for key, value in zip(e9.oracle.FEATURE_IDS, gradient)
        }
        for gradient in gradients
    ]


def masked_coefficients(
    coefficients: Sequence[dict[str, float]], masks: Sequence[frozenset[str]], weights: Sequence[float]
) -> list[dict[str, float]]:
    if len(masks) != len(weights) or not math.isclose(math.fsum(weights), 1.0):
        raise ValueError("invalid fixed ensemble")
    return [
        {
            key: math.fsum(weight * coefficients[zone][key] for mask, weight in zip(masks, weights) if key in mask)
            for key in e9.oracle.FEATURE_IDS
        }
        for zone in (0, 1)
    ]


def rank_coefficients(
    game: str, data: Sequence[e9.oracle.Draw], target: int, coefficients: Sequence[dict[str, float]]
) -> dict[str, int]:
    zone_scores = []
    contexts = []
    actual_indices = []
    actual_scores = []
    actual_numbers = (data[target].front, data[target].back)
    for zone, (n, k) in enumerate(e9.oracle.RULES[game]):
        context = e9.oracle.feature_context(game, data[:target], zone)
        scores = e9.vector_scores(context, coefficients[zone])
        combos = e9.combo_array(n, k)
        matches = e9.np.flatnonzero(e9.np.all(combos == e9.np.asarray(actual_numbers[zone]), axis=1))
        if len(matches) != 1:
            raise ValueError("FAIL_ACTUAL_TICKET_IDENTITY: expected one legal combination")
        actual_index = int(matches[0])
        actual_scores.append(e9.oracle._score(actual_numbers[zone], context, coefficients[zone]))
        contexts.append(context)
        zone_scores.append(scores)
        actual_indices.append(actual_index)
    actual_tick = e9.oracle.score_order_tick(actual_scores[0] + actual_scores[1])
    greater = equal = tie_before = exact_boundary_rechecks = tick_mismatches_corrected = 0
    exact_front_cache: dict[int, float] = {}
    front_combos = e9.combo_array(*e9.oracle.RULES[game][0])
    back_combos = e9.combo_array(*e9.oracle.RULES[game][1])
    for back_index, back_score in enumerate(zone_scores[1]):
        ticks = e9.approximate_score_ticks(zone_scores[0] + float(back_score))
        boundary_indices = e9.np.flatnonzero(e9.np.abs(ticks - actual_tick) <= 1)
        if len(boundary_indices):
            exact_back = e9.oracle._score(tuple(back_combos[back_index]), contexts[1], coefficients[1])
            for front_index in boundary_indices:
                index = int(front_index)
                if index not in exact_front_cache:
                    exact_front_cache[index] = e9.oracle._score(tuple(front_combos[index]), contexts[0], coefficients[0])
                exact_tick = e9.oracle.score_order_tick(exact_front_cache[index] + exact_back)
                tick_mismatches_corrected += int(ticks[index] != exact_tick)
                ticks[index] = exact_tick
            exact_boundary_rechecks += len(boundary_indices)
        if back_index == actual_indices[1] and int(ticks[actual_indices[0]]) != actual_tick:
            raise ValueError("FAIL_SCORE_TICK_EQUIVALENCE: boundary guard did not preserve actual ticket tick")
        greater += int(e9.np.count_nonzero(ticks > actual_tick))
        same = ticks == actual_tick
        equal += int(e9.np.count_nonzero(same))
        tie_before += int(e9.np.count_nonzero(same[: actual_indices[0]]))
        if back_index < actual_indices[1] and bool(same[actual_indices[0]]):
            tie_before += 1
    return {
        "canonical_rank": greater + tie_before + 1,
        "tie_rank_lower": greater + 1,
        "tie_rank_upper": greater + equal,
        "actual_score_tick": actual_tick,
        "tie_group_size": equal,
        "exact_boundary_rechecks": exact_boundary_rechecks,
        "tick_mismatches_corrected": tick_mismatches_corrected,
    }


def e11_report(game: str) -> dict[str, object]:
    return json.loads((E11 / game / "report.json").read_text())


def strongest_distinct_masks(game: str, report: dict[str, object]) -> tuple[str, str]:
    metrics = report["candidate_metrics"]
    ranked = sorted(
        E11_MASK_ORDER,
        key=lambda name: (
            metrics[name]["inner_k90"], metrics[name]["inner_k80"], metrics[name]["inner_k50"],
            E11_MASK_ORDER.index(name),
        ),
    )
    chosen = []
    identities: set[frozenset[str]] = set()
    for name in ranked:
        identity = mask_for(game, name)
        if identity not in identities:
            chosen.append(name)
            identities.add(identity)
        if len(chosen) == 2:
            return chosen[0], chosen[1]
    raise ValueError("E11 did not provide two distinct masks")


def e11_candidate_rows(game: str, mask_name: str) -> list[dict[str, object]]:
    path = E11 / game / "inner-rolling-report.jsonl"
    payloads = [json.loads(line) for line in path.read_text().splitlines()]
    matches = [payload["rows"] for payload in payloads if payload["mask"] == mask_name]
    if len(matches) != 1:
        raise ValueError("FAIL_E11_INNER_LINEAGE: selected mask rows missing or duplicated")
    return matches[0]


def e11_outer_rows(game: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (E11 / game / "outer-rolling-report.jsonl").read_text().splitlines()]


def rank_candidate(task: tuple[str, int, str, tuple[str, str]]) -> dict[str, object]:
    game, target, candidate, ensemble_names = task
    data = e9.load(game)
    report = e11_report(game)
    l2 = float(e9.L2[game])
    fit_history = LONG_FIT_HISTORY if candidate == "long_history_240" else BASELINE_FIT_HISTORY
    fitted = fit_coefficients(game, data, target, l2, fit_history)
    if candidate == "long_history_240":
        masks = (mask_for(game, str(report["selected_mask"])),)
        weights = (1.0,)
    elif candidate == "strongest_masks_50_50":
        masks = tuple(mask_for(game, name) for name in ensemble_names)
        weights = (0.5, 0.5)
    else:
        raise ValueError(candidate)
    ranking = rank_coefficients(game, data, target, masked_coefficients(fitted, masks, weights))
    return {
        "candidate": candidate,
        "issue": data[target].issue,
        "target_position": target,
        "maximum_training_position": target - 1,
        "strict_lag": True,
        "fit_history_labels": min(fit_history, target - e9.oracle.MIN_HISTORY),
        **ranking,
    }


def compute_rows(
    game: str, targets: range, candidates: Sequence[str], ensemble_names: tuple[str, str], workers: int
) -> dict[str, list[dict[str, object]]]:
    tasks = [(game, target, candidate, ensemble_names) for target in targets for candidate in candidates]
    if workers == 1:
        computed = [rank_candidate(task) for task in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            computed = pool.map(rank_candidate, tasks, chunksize=1)
    rows = {candidate: [] for candidate in candidates}
    for row in computed:
        rows[str(row["candidate"])].append(row)
    for candidate in candidates:
        rows[candidate].sort(key=lambda row: int(row["target_position"]))
    return rows


def metric(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    ranks = [int(row["canonical_rank"]) for row in rows]
    return {
        "inner_k90": conformal_k(ranks, 0.90),
        "inner_k80": conformal_k(ranks, 0.80),
        "inner_k50": conformal_k(ranks, 0.50),
        "mean_rank": math.fsum(ranks) / len(ranks),
    }


def relative_reduction(baseline: int, candidate: int) -> float:
    return (baseline - candidate) / baseline


def run(game: str, workers: int) -> dict[str, object]:
    data = e9.load(game)
    inner_targets = range(len(data) - INNER_DRAWS - OUTER_DRAWS, len(data) - OUTER_DRAWS)
    outer_targets = range(len(data) - OUTER_DRAWS, len(data))
    report11 = e11_report(game)
    baseline_mask = str(report11["selected_mask"])
    ensemble_names = strongest_distinct_masks(game, report11)
    baseline_rows = e11_candidate_rows(game, baseline_mask)
    expected_inner_issues = [data[target].issue for target in inner_targets]
    if [row["issue"] for row in baseline_rows] != expected_inner_issues:
        raise ValueError("FAIL_E11_INNER_LINEAGE: window identity mismatch")
    for row in baseline_rows:
        row["candidate"] = "e11_baseline_80"
        row["fit_history_labels"] = min(BASELINE_FIT_HISTORY, int(row["target_position"]) - e9.oracle.MIN_HISTORY)
    new_rows = compute_rows(
        game, inner_targets, ("long_history_240", "strongest_masks_50_50"), ensemble_names, workers
    )
    candidate_rows = {"e11_baseline_80": baseline_rows, **new_rows}
    metrics = {candidate: metric(candidate_rows[candidate]) for candidate in CANDIDATES}
    selected = min(
        CANDIDATES,
        key=lambda candidate: (
            metrics[candidate]["inner_k90"], metrics[candidate]["inner_k80"],
            metrics[candidate]["inner_k50"], CANDIDATES.index(candidate),
        ),
    )
    baseline_metrics = metrics["e11_baseline_80"]
    improvements = {
        candidate: {
            "relative_k90_reduction_vs_e11": relative_reduction(
                int(baseline_metrics["inner_k90"]), int(values["inner_k90"])
            ),
            "relative_k80_reduction_vs_e11": relative_reduction(
                int(baseline_metrics["inner_k80"]), int(values["inner_k80"])
            ),
        }
        for candidate, values in metrics.items()
        if candidate != "e11_baseline_80"
    }
    material_new_candidate = any(
        values["relative_k90_reduction_vs_e11"] >= MATERIAL_IMPROVEMENT
        or values["relative_k80_reduction_vs_e11"] >= MATERIAL_IMPROVEMENT
        for values in improvements.values()
    )
    selected_improvement = improvements.get(selected)
    selected_material = bool(
        selected_improvement
        and (
            selected_improvement["relative_k90_reduction_vs_e11"] >= MATERIAL_IMPROVEMENT
            or selected_improvement["relative_k80_reduction_vs_e11"] >= MATERIAL_IMPROVEMENT
        )
    )
    e11_outer = e11_outer_rows(game)
    expected_outer_identity = outer_identity_sha256(e11_outer)
    if selected == "e11_baseline_80":
        outer_rows = e11_outer_rows(game)
        for row in outer_rows:
            row["candidate"] = selected
            row["fit_history_labels"] = min(
                BASELINE_FIT_HISTORY, int(row["target_position"]) - e9.oracle.MIN_HISTORY
            )
    else:
        outer_rows = compute_rows(game, outer_targets, (selected,), ensemble_names, workers)[selected]
    if [row["issue"] for row in outer_rows] != [data[target].issue for target in outer_targets]:
        raise ValueError("FAIL_OUTER_IDENTITY: frozen outer window mismatch")
    observed_outer_identity = outer_identity_sha256(outer_rows)
    if observed_outer_identity != expected_outer_identity:
        raise ValueError("FAIL_E11_OUTER_LINEAGE: E12 outer target identity differs from E11")
    calibration_rows = outer_rows[:CALIBRATION_DRAWS]
    evaluation_rows = outer_rows[CALIBRATION_DRAWS:]
    calibration_ranks = [int(row["canonical_rank"]) for row in calibration_rows]
    evaluation_ranks = [int(row["canonical_rank"]) for row in evaluation_rows]
    full_space = math.prod(math.comb(n, k) for n, k in e9.oracle.RULES[game])
    selected_k90 = conformal_k(calibration_ranks, 0.90)
    first_space_coverage = coverage(evaluation_ranks, selected_k90, full_space)
    compression = {str(k): coverage(evaluation_ranks, k, full_space) for k in SPACES}
    for row in outer_rows:
        rank = int(row["canonical_rank"])
        row["rank_percentile"] = rank / full_space
        row["covered"] = {str(k): rank <= k for k in SPACES}
    candidate_contracts = {
        "e11_baseline_80": {
            "fit_history": BASELINE_FIT_HISTORY,
            "masks": [baseline_mask],
            "weights": [1.0],
            "rank_source": "phase4e11_exact_rows",
        },
        "long_history_240": {
            "fit_history": LONG_FIT_HISTORY,
            "masks": [baseline_mask],
            "weights": [1.0],
            "rank_source": "phase4e12_exact_recompute",
        },
        "strongest_masks_50_50": {
            "fit_history": BASELINE_FIT_HISTORY,
            "masks": list(ensemble_names),
            "weights": [0.5, 0.5],
            "rank_source": "phase4e12_exact_recompute",
        },
    }
    report = {
        "artifact_type": "phase4e12_compression_iteration_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "candidate_selection_window": {
            "draws": INNER_DRAWS,
            "first_issue": data[inner_targets.start].issue,
            "last_issue": data[inner_targets.stop - 1].issue,
            "last_target_position": inner_targets.stop - 1,
            "outer_first_target_position": outer_targets.start,
            "strictly_before_outer": inner_targets.stop <= outer_targets.start,
            "uses_final_outer_labels": False,
        },
        "candidate_contracts": candidate_contracts,
        "candidate_metrics": metrics,
        "candidate_improvements": improvements,
        "selected_candidate": selected,
        "selection_rule": "minimum_exact_canonical_split_conformal_k90_then_k80_k50_then_candidate_order_v1",
        "material_improvement_threshold": MATERIAL_IMPROVEMENT,
        "material_inner_k90_or_k80_improvement_found": selected_material,
        "material_new_candidate_improvement_found": material_new_candidate,
        "selected_candidate_material_improvement": selected_material,
        "candidate_expansion": {
            "bounded_candidates_exhausted": True,
            "stopped_for_no_material_improvement": not selected_material,
            "evidence_metric": "selected_candidate_relative_reduction_vs_e11_baseline_inner_k90_or_inner_k80",
            "baseline_excluded_from_improvement_search": True,
        },
        "ranking_contract": e9.oracle.RANKING_ALGORITHM_ID,
        "score_order_key_id": e9.oracle.SCORE_ORDER_KEY_ID,
        "score_tick_boundary_guard": "exact_oracle_recheck_within_one_approximate_tick_of_actual_v1",
        "outer_window": {
            "draws": OUTER_DRAWS,
            "first_issue": outer_rows[0]["issue"],
            "last_issue": outer_rows[-1]["issue"],
            "calibration_draws": CALIBRATION_DRAWS,
            "evaluation_draws": OUTER_DRAWS - CALIBRATION_DRAWS,
            "frozen_from_phase4e9_e10_e11": True,
            "identity_fields": ["issue", "target_position"],
            "identity_sha256": observed_outer_identity,
            "phase4e11_identity_sha256": expected_outer_identity,
            "identity_matches_phase4e11": observed_outer_identity == expected_outer_identity,
        },
        "first_ranked_space": {
            "method": "split_conformal_rank_quantile_ceil_n_plus_1_p_v1",
            "calibration_target": 0.90,
            "selected_k_from_calibration_only": selected_k90,
            "evaluation": first_space_coverage,
        },
        "compression_ladder": list(reversed(SPACES)),
        "compression_evaluation": compression,
        "compression_acceptance": {
            "evaluation_rate_min": 0.80,
            "wilson95_lower_min": 0.75,
            "accepted_spaces": [k for k in reversed(SPACES) if compression[str(k)]["acceptance"]["pass"]],
            "target_100000_accepted": compression["100000"]["acceptance"]["pass"],
            "progressive_target_10000_accepted": compression["10000"]["acceptance"]["pass"],
            "progressive_target_2000_accepted": compression["2000"]["acceptance"]["pass"],
            "progressive_target_1000_accepted": compression["1000"]["acceptance"]["pass"],
        },
        "lineage": {
            "phase4e11_report_sha256": sha256(E11 / game / "report.json"),
            "phase4e11_inner_rows_sha256": sha256(E11 / game / "inner-rolling-report.jsonl"),
            "phase4e11_outer_rows_sha256": sha256(E11 / game / "outer-rolling-report.jsonl"),
        },
        "probability_claim": "ranking coverage only; no ticket-level probability or random-draw guarantee",
        "probability_spread_adjustment": "none",
        "rank_invariant_global_positive_scaling_evaluated": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
        "promotion_eligible": False,
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    (path / "inner-rolling-report.jsonl").write_bytes(
        b"".join(canonical(row) for candidate in CANDIDATES for row in candidate_rows[candidate])
    )
    (path / "outer-rolling-report.jsonl").write_bytes(b"".join(canonical(row) for row in outer_rows))
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bounded Phase4E12 nested-space compression iteration")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game, args.workers) for game in ("ssq", "dlt")}
    summary = {
        "artifact_type": "phase4e12_compression_iteration_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "games": reports,
        "all_first_ranked_spaces_reliable": all(
            report["first_ranked_space"]["evaluation"]["acceptance"]["pass"] for report in reports.values()
        ),
        "all_compression_to_100000_accepted": all(
            report["compression_acceptance"]["target_100000_accepted"] for report in reports.values()
        ),
        "all_progressive_targets_accepted": all(
            report["compression_acceptance"][key]
            for report in reports.values()
            for key in (
                "progressive_target_10000_accepted", "progressive_target_2000_accepted",
                "progressive_target_1000_accepted",
            )
        ),
        "candidate_expansion_stopped_for_no_material_improvement": all(
            report["candidate_expansion"]["stopped_for_no_material_improvement"] for report in reports.values()
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
