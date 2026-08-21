#!/usr/bin/env python3
"""Deterministic SSQ-only prize-aware optimization for Phase4E19.

The module contains both reusable scoring primitives and the frozen-data runner.
It intentionally imports no DLT model code and refuses to run if any registered
DLT evidence hash or the P4E6 serving identity changes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import statistics
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical/ssq.jsonl"
DEFAULT_OUT = ROOT / "artifacts/phase4e19"
P4E6_DECISION = ROOT / "artifacts/phase4e6/delivery/decision.json"

SSQ_PARTITION_SIZES = (
    1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000,
    70000, 80000, 90000, 100000,
)
SSQ_FIXED_PRIZES = {1: 5_000_000.0, 2: 100_000.0, 3: 3_000.0, 4: 200.0, 5: 10.0, 6: 5.0}
OUTER_DRAWS = 120
CALIBRATION_DRAWS = 60
INNER_DRAWS = 240
INNER_BLOCK_DRAWS = 60
WINDOWS = (360, 720, 1200)
EXPECTED_SERVING_RELEASE = "P4-P4E2-20260815-r12"
EXPECTED_SERVING_STATUS = "PROSPECTIVE_ONLY"
DLT_FROZEN_HASHES = {
    "artifacts/phase4e17/dlt/report.json": "40d52f1d4a97b2e8e4a4736aad994bf46e4a033cd342ec39e74b43dd6386d3fc",
    "artifacts/phase4e17/dlt/outer-rolling-report.jsonl": "9d8186a6d8bf3197747121fb66e9e78d846404f5af268e5fb5f5c7da66299634",
    "artifacts/phase4e17/summary.json": "ff4e61df206638ae380f2d198188fe893f435d3b417df331aeebb06a31e7146c",
    "artifacts/phase4e6/delivery/decision.json": "d117e7bb7b0fe1ccc30d58c4971a53151e2db8b457068572d6a0b19c3990967e",
}


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def vector_hash(values: np.ndarray | Sequence[float] | Sequence[int]) -> str:
    array = np.asarray(values)
    if np.issubdtype(array.dtype, np.floating):
        array = array.astype("<f8", copy=False)
    elif np.issubdtype(array.dtype, np.integer):
        array = array.astype("<i8", copy=False)
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()


def prize_tier(red_hits: int, blue_hits: int) -> int | None:
    pattern = (int(red_hits), int(blue_hits))
    patterns = {
        1: {(6, 1)},
        2: {(6, 0)},
        3: {(5, 1)},
        4: {(5, 0), (4, 1)},
        5: {(4, 0), (3, 1)},
        6: {(3, 0), (2, 1), (1, 1), (0, 1)},
    }
    return next((tier for tier, values in patterns.items() if pattern in values), None)


def ticket_prize(red: Sequence[int], blue: int, actual_red: Iterable[int], actual_blue: int) -> float:
    tier = prize_tier(len(set(red) & set(actual_red)), int(blue == actual_blue))
    return SSQ_FIXED_PRIZES.get(tier, 0.0)


def expected_prize_contributions(red_hit_probabilities: Sequence[float], blue_hit_probability: float) -> dict[int, float]:
    """Return expected yuan contribution for every registered SSQ prize tier."""
    if len(red_hit_probabilities) != 7:
        raise ValueError("red hit distribution must contain probabilities for 0..6 hits")
    probabilities = [float(value) for value in red_hit_probabilities]
    if any(value < 0.0 for value in probabilities) or not math.isclose(math.fsum(probabilities), 1.0, abs_tol=1e-10):
        raise ValueError("invalid red hit probability distribution")
    blue_p = float(blue_hit_probability)
    if not 0.0 <= blue_p <= 1.0:
        raise ValueError("invalid blue probability")
    contributions = {tier: 0.0 for tier in SSQ_FIXED_PRIZES}
    for red_hits, red_p in enumerate(probabilities):
        for blue_hits, probability in ((0, 1.0 - blue_p), (1, blue_p)):
            tier = prize_tier(red_hits, blue_hits)
            if tier is not None:
                contributions[tier] += red_p * probability * SSQ_FIXED_PRIZES[tier]
    return contributions


def ranked_ticket_partitions(
    red_scores: Sequence[float], blue_scores: Sequence[float], actual_red: Iterable[int], actual_blue: int,
    partition_sizes: Sequence[int] = SSQ_PARTITION_SIZES,
) -> dict[int, dict[str, float | int]]:
    """Compatibility primitive: additive complete-ticket rank with exact prefixes."""
    red = sorted(
        ((math.fsum(float(red_scores[n - 1]) for n in combo), tuple(combo))
         for combo in itertools.combinations(range(1, 34), 6)),
        key=lambda item: (-item[0], item[1]),
    )
    blue = sorted(((float(blue_scores[n - 1]), n) for n in range(1, 17)), key=lambda item: (-item[0], item[1]))
    # No ticket outside the first max-N red combinations for one blue can enter
    # the global max-N prefix: max-N same-blue tickets already outrank it.
    candidates = sorted(
        ((rscore + bscore, rcombo, bnumber)
         for rscore, rcombo in red[:max(partition_sizes)] for bscore, bnumber in blue),
        key=lambda item: (-item[0], item[1], item[2]),
    )[:max(partition_sizes)]
    result: dict[int, dict[str, float | int]] = {}
    total = 0.0
    winners = 0
    for rank, (_, rcombo, bnumber) in enumerate(candidates, start=1):
        prize = ticket_prize(rcombo, bnumber, actual_red, actual_blue)
        total += prize
        winners += int(prize > 0)
        if rank in partition_sizes:
            result[rank] = {
                "partition_size": rank,
                "known_prize_total_yuan": total,
                "average_prize_yuan": total / rank,
                "winning_ticket_count": winners,
            }
    return result


def acceptance_gate(averages: Sequence[float], threshold: float = 2.0) -> dict[str, object]:
    values = [float(value) for value in averages]
    return {
        "metric": "known_prize_total_yuan / complete_ticket_count",
        "threshold_yuan_per_ticket": float(threshold),
        "comparison": "strictly_greater_than",
        "passed": bool(values) and all(value > threshold for value in values),
        "minimum_observed_average_prize_yuan": min(values) if values else None,
    }


CANDIDATE_SPECS: tuple[dict[str, object], ...] = (
    {
        "candidate_id": "raw_control",
        "family": "raw_control",
        "score_kind": "raw_complete_ticket_likelihood",
        "red_weights": {"red_frequency_360": 1.0},
        "blue_weights": {"blue_frequency_360": 1.0},
        "structure_weights": {},
        "diversified": False,
    },
    {
        "candidate_id": "prize_aware_multiscale",
        "family": "prize_aware",
        "score_kind": "all_tier_expected_prize",
        "red_weights": {"red_frequency_360": .22, "red_frequency_720": .28, "red_frequency_1200": .34, "red_recency_360": .05, "red_recency_720": .04, "red_recency_1200": .03, "red_transition_720": .04},
        "blue_weights": {"blue_frequency_360": .24, "blue_frequency_720": .28, "blue_frequency_1200": .32, "blue_recency_360": .06, "blue_recency_720": .04, "blue_transition_720": .06},
        "structure_weights": {"red_zone_distribution": .05, "red_parity_structure": .05, "red_sum_structure": .03, "red_consecutive_structure": .03, "red_blue_parity_interaction": .04, "historical_combination_similarity": .02},
        "diversified": False,
    },
    {
        "candidate_id": "long_window_prize",
        "family": "long_window",
        "score_kind": "all_tier_expected_prize",
        "red_weights": {"red_frequency_720": .25, "red_frequency_1200": .50, "red_recency_720": .08, "red_recency_1200": .09, "red_transition_1200": .08},
        "blue_weights": {"blue_frequency_720": .25, "blue_frequency_1200": .50, "blue_recency_720": .08, "blue_recency_1200": .09, "blue_transition_1200": .08},
        "structure_weights": {"red_zone_distribution": .04, "red_parity_structure": .04, "red_sum_structure": .04, "red_consecutive_structure": .02, "historical_combination_similarity": .03},
        "diversified": False,
    },
    {
        "candidate_id": "transition_joint_prize",
        "family": "transition_joint",
        "score_kind": "all_tier_expected_prize",
        "red_weights": {"red_frequency_720": .22, "red_frequency_1200": .18, "red_recency_360": .08, "red_transition_360": .18, "red_transition_720": .19, "red_transition_1200": .10, "red_blue_parity_number_interaction": .05},
        "blue_weights": {"blue_frequency_720": .20, "blue_frequency_1200": .15, "blue_recency_360": .10, "blue_transition_360": .25, "blue_transition_720": .20, "blue_transition_1200": .10},
        "structure_weights": {"red_zone_distribution": .03, "red_parity_structure": .04, "red_sum_structure": .03, "red_consecutive_structure": .03, "red_blue_parity_interaction": .08, "historical_combination_similarity": .03},
        "diversified": False,
    },
    {
        "candidate_id": "diversified_prize",
        "family": "diversified",
        "score_kind": "all_tier_expected_prize",
        "red_weights": {"red_frequency_360": .22, "red_frequency_720": .28, "red_frequency_1200": .34, "red_recency_360": .05, "red_recency_720": .04, "red_recency_1200": .03, "red_transition_720": .04},
        "blue_weights": {"blue_frequency_360": .24, "blue_frequency_720": .28, "blue_frequency_1200": .32, "blue_recency_360": .06, "blue_recency_720": .04, "blue_transition_720": .06},
        "structure_weights": {"red_zone_distribution": .05, "red_parity_structure": .05, "red_sum_structure": .03, "red_consecutive_structure": .03, "red_blue_parity_interaction": .04, "historical_combination_similarity": -.06},
        "diversified": True,
        "diversity_pool_multiplier": 4,
    },
    {
        "candidate_id": "diversified_transition_joint",
        "family": "diversified_transition_joint",
        "score_kind": "all_tier_expected_prize",
        "red_weights": {"red_frequency_720": .22, "red_frequency_1200": .18, "red_recency_360": .08, "red_transition_360": .18, "red_transition_720": .19, "red_transition_1200": .10, "red_blue_parity_number_interaction": .05},
        "blue_weights": {"blue_frequency_720": .20, "blue_frequency_1200": .15, "blue_recency_360": .10, "blue_transition_360": .25, "blue_transition_720": .20, "blue_transition_1200": .10},
        "structure_weights": {"red_zone_distribution": .03, "red_parity_structure": .04, "red_sum_structure": .03, "red_consecutive_structure": .03, "red_blue_parity_interaction": .08, "historical_combination_similarity": -.07},
        "diversified": True,
        "diversity_pool_multiplier": 4,
    },
)


def candidate_registry() -> list[dict[str, object]]:
    return copy.deepcopy(list(CANDIDATE_SPECS))


def validate_candidate_registry(registry: Sequence[Mapping[str, object]] | None = None) -> None:
    specs = list(registry or CANDIDATE_SPECS)
    ids = [str(spec["candidate_id"]) for spec in specs]
    required_families = {"raw_control", "prize_aware", "long_window", "transition_joint", "diversified", "diversified_transition_joint"}
    if len(specs) != 6 or len(ids) != len(set(ids)) or {str(s["family"]) for s in specs} != required_families:
        raise ValueError("FAIL_PHASE4E19_FINITE_CANDIDATE_REGISTRY")
    if specs[0]["candidate_id"] != "raw_control":
        raise ValueError("FAIL_PHASE4E19_RAW_CONTROL_ORDER")
    for spec in specs:
        if spec["score_kind"] not in {"raw_complete_ticket_likelihood", "all_tier_expected_prize"}:
            raise ValueError("FAIL_PHASE4E19_SCORE_KIND")


_COMBOS: np.ndarray | None = None
_COMBO_PROPERTIES: dict[str, np.ndarray] | None = None


def legal_red_combinations() -> np.ndarray:
    global _COMBOS
    if _COMBOS is None:
        count = math.comb(33, 6)
        combos = np.empty((count, 6), dtype=np.uint8)
        for index, combo in enumerate(itertools.combinations(range(33), 6)):
            combos[index] = combo
        _COMBOS = combos
    return _COMBOS


def combo_properties() -> dict[str, np.ndarray]:
    global _COMBO_PROPERTIES
    if _COMBO_PROPERTIES is None:
        combos = legal_red_combinations().astype(np.int16)
        numbers = combos + 1
        zones = np.stack(((numbers <= 11).sum(1), ((numbers >= 12) & (numbers <= 22)).sum(1), (numbers >= 23).sum(1)), axis=1)
        _COMBO_PROPERTIES = {
            "zone_code": (zones[:, 0] * 49 + zones[:, 1] * 7 + zones[:, 2]).astype(np.int16),
            "odd_count": (numbers % 2).sum(1).astype(np.int8),
            "sum": numbers.sum(1).astype(np.int16),
            "consecutive_pairs": (np.diff(numbers, axis=1) == 1).sum(1).astype(np.int8),
        }
    return _COMBO_PROPERTIES


def _normalized(values: np.ndarray, total: float, cap: float | None = None) -> np.ndarray:
    result = np.maximum(np.asarray(values, dtype=np.float64), 1e-12)
    result *= total / float(result.sum())
    if cap is not None:
        fixed = np.zeros(len(result), dtype=bool)
        for _ in range(len(result)):
            over = (~fixed) & (result > cap)
            if not over.any():
                break
            result[over] = cap
            fixed |= over
            remaining = total - float(result[fixed].sum())
            if remaining <= 0 or fixed.all():
                break
            result[~fixed] *= remaining / float(result[~fixed].sum())
    return result


def _input_identity(rows: Sequence[Mapping[str, object]], start: int, cutoff: int) -> str:
    return digest([str(row["source_record_sha256"]) for row in rows[start:cutoff]])


def _feature_record(feature_id: str, scope: str, cutoff: int, window: int, rows: Sequence[Mapping[str, object]], values: np.ndarray, lineage: str) -> dict[str, object]:
    start = max(0, cutoff - window)
    return {
        "feature_id": feature_id,
        "scope": scope,
        "cutoff_position_exclusive": cutoff,
        "maximum_source_position": cutoff - 1,
        "maximum_source_issue": str(rows[cutoff - 1]["issue"]),
        "registered_window_draws": window,
        "effective_window_draws": cutoff - start,
        "input_start_position": start,
        "input_end_position_inclusive": cutoff - 1,
        "input_sha256": _input_identity(rows, start, cutoff),
        "value_sha256_float64_le": vector_hash(np.asarray(values, dtype=np.float64)),
        "lineage": lineage,
        "strict_lag": True,
    }


def build_feature_snapshot(rows: Sequence[Mapping[str, object]], cutoff: int) -> dict[str, object]:
    """Fit every registered feature using only rows strictly before cutoff."""
    if cutoff < max(WINDOWS) or cutoff > len(rows):
        raise ValueError("FAIL_PHASE4E19_FEATURE_CUTOFF")
    features: dict[str, np.ndarray] = {}
    records: list[dict[str, object]] = []
    for window in WINDOWS:
        start = cutoff - window
        history = rows[start:cutoff]
        red_counts = np.full(33, 0.5, dtype=np.float64)
        blue_counts = np.full(16, 0.5, dtype=np.float64)
        for row in history:
            red_counts[np.asarray(row["front"], dtype=int) - 1] += 1.0
            blue_counts[int(row["back"][0]) - 1] += 1.0
        red_frequency = _normalized(red_counts, 6.0, .98)
        blue_frequency = _normalized(blue_counts, 1.0)

        red_lags = np.full(33, window + 1, dtype=np.float64)
        blue_lags = np.full(16, window + 1, dtype=np.float64)
        for lag, row in enumerate(reversed(history), start=1):
            for number in row["front"]:
                if red_lags[int(number) - 1] > window:
                    red_lags[int(number) - 1] = lag
            blue = int(row["back"][0]) - 1
            if blue_lags[blue] > window:
                blue_lags[blue] = lag
        red_recency = _normalized(np.exp(-red_lags / max(30.0, window / 6.0)) + .02, 6.0, .98)
        blue_recency = _normalized(np.exp(-blue_lags / max(16.0, window / 8.0)) + .02, 1.0)

        red_transitions = np.full((33, 33), .25, dtype=np.float64)
        blue_transitions = np.full((16, 16), .25, dtype=np.float64)
        for previous, current in zip(history[:-1], history[1:]):
            previous_red = np.asarray(previous["front"], dtype=int) - 1
            current_red = np.asarray(current["front"], dtype=int) - 1
            red_transitions[np.ix_(previous_red, current_red)] += 1.0
            blue_transitions[int(previous["back"][0]) - 1, int(current["back"][0]) - 1] += 1.0
        last_red = np.asarray(history[-1]["front"], dtype=int) - 1
        last_blue = int(history[-1]["back"][0]) - 1
        red_transition = _normalized(red_transitions[last_red].sum(axis=0), 6.0, .98)
        blue_transition = _normalized(blue_transitions[last_blue], 1.0)

        for feature_id, scope, values, lineage in (
            (f"red_frequency_{window}", "red_head", red_frequency, "Jeffreys-smoothed strict-window red inclusion frequency"),
            (f"red_recency_{window}", "red_head", red_recency, "exponential strict-window red last-seen recency"),
            (f"red_transition_{window}", "red_head", red_transition, "smoothed red-to-red transition from the last pre-cutoff red set"),
            (f"blue_frequency_{window}", "blue_head", blue_frequency, "Jeffreys-smoothed strict-window blue frequency"),
            (f"blue_recency_{window}", "blue_head", blue_recency, "exponential strict-window blue last-seen recency"),
            (f"blue_transition_{window}", "blue_head", blue_transition, "smoothed blue transition from the last pre-cutoff blue"),
        ):
            features[feature_id] = values
            records.append(_feature_record(feature_id, scope, cutoff, window, rows, values, lineage))

    structure_window = 720
    history = rows[cutoff - structure_window:cutoff]
    last_blue_parity = int(rows[cutoff - 1]["back"][0]) % 2
    interaction_counts = np.full(33, .5, dtype=np.float64)
    zone_counts = np.ones(343, dtype=np.float64)
    parity_counts = np.ones(7, dtype=np.float64)
    sum_counts = np.ones(200, dtype=np.float64)
    consecutive_counts = np.ones(6, dtype=np.float64)
    red_blue_joint = np.ones((7, 2), dtype=np.float64)
    pair_counts = np.full((33, 33), .5, dtype=np.float64)
    for row in history:
        red = np.asarray(row["front"], dtype=int) - 1
        numbers = red + 1
        if int(row["back"][0]) % 2 == last_blue_parity:
            interaction_counts[red] += 1.0
        zones = (int((numbers <= 11).sum()), int(((numbers >= 12) & (numbers <= 22)).sum()), int((numbers >= 23).sum()))
        zone_counts[zones[0] * 49 + zones[1] * 7 + zones[2]] += 1.0
        odd = int((numbers % 2).sum())
        parity_counts[odd] += 1.0
        sum_counts[int(numbers.sum())] += 1.0
        consecutive_counts[int((np.diff(numbers) == 1).sum())] += 1.0
        red_blue_joint[odd, int(row["back"][0]) % 2] += 1.0
        pair_counts[np.ix_(red, red)] += 1.0
    rb_number = _normalized(interaction_counts, 6.0, .98)
    features["red_blue_parity_number_interaction"] = rb_number
    records.append(_feature_record("red_blue_parity_number_interaction", "joint_head", cutoff, structure_window, rows, rb_number, "red inclusion conditional on the last pre-cutoff blue parity"))
    structure = {
        "zone_probability": zone_counts / zone_counts.sum(),
        "parity_probability": parity_counts / parity_counts.sum(),
        "sum_probability": sum_counts / sum_counts.sum(),
        "consecutive_probability": consecutive_counts / consecutive_counts.sum(),
        "red_blue_joint_probability": red_blue_joint / red_blue_joint.sum(),
        "pair_similarity": pair_counts / structure_window,
    }
    for feature_id, values, lineage in (
        ("red_zone_distribution", structure["zone_probability"], "empirical red low/mid/high count distribution with additive smoothing"),
        ("red_parity_structure", structure["parity_probability"], "empirical red odd-count distribution with additive smoothing"),
        ("red_sum_structure", structure["sum_probability"], "empirical red ticket-sum distribution with additive smoothing"),
        ("red_consecutive_structure", structure["consecutive_probability"], "empirical adjacent-red-pair count distribution with additive smoothing"),
        ("red_blue_parity_interaction", structure["red_blue_joint_probability"], "empirical red odd-count by blue parity joint distribution"),
        ("historical_combination_similarity", structure["pair_similarity"], "historical red-pair co-occurrence similarity matrix"),
    ):
        records.append(_feature_record(feature_id, "complete_ticket", cutoff, structure_window, rows, np.asarray(values), lineage))
    coverage_values = np.array([4.0, 33.0, 16.0, 528.0], dtype=np.float64)
    records.append(_feature_record("portfolio_coverage_diversity", "portfolio", cutoff, structure_window, rows, coverage_values, "registered 4x candidate pool and round-robin blue/rarest-red bucket interleave"))
    bundle = {
        "cutoff_position_exclusive": cutoff,
        "cutoff_issue_exclusive": str(rows[cutoff]["issue"]) if cutoff < len(rows) else None,
        "maximum_source_position": cutoff - 1,
        "maximum_source_issue": str(rows[cutoff - 1]["issue"]),
        "prefix_input_sha256": _input_identity(rows, 0, cutoff),
        "features": features,
        "structure": structure,
        "lineage": records,
    }
    bundle["feature_bundle_sha256"] = digest([{k: v for k, v in record.items() if k != "lineage"} for record in records])
    return bundle


def fit_heads(snapshot: Mapping[str, object], spec: Mapping[str, object]) -> dict[str, object]:
    features = snapshot["features"]
    red = np.zeros(33, dtype=np.float64)
    blue = np.zeros(16, dtype=np.float64)
    for feature_id, weight in spec["red_weights"].items():
        red += float(weight) * np.asarray(features[feature_id], dtype=np.float64)
    for feature_id, weight in spec["blue_weights"].items():
        blue += float(weight) * np.asarray(features[feature_id], dtype=np.float64)
    red = _normalized(red, 6.0, .98)
    blue = _normalized(blue, 1.0)
    return {
        "red_probabilities": red,
        "blue_probabilities": blue,
        "red_probability_sha256_float64_le": vector_hash(red),
        "blue_probability_sha256_float64_le": vector_hash(blue),
        "red_probability_sum": float(red.sum()),
        "blue_probability_sum": float(blue.sum()),
        "fit_cutoff_position_exclusive": snapshot["cutoff_position_exclusive"],
        "maximum_fit_label_position": int(snapshot["cutoff_position_exclusive"]) - 1,
        "fit_input_sha256": snapshot["prefix_input_sha256"],
        "independent_red_blue_heads": True,
        "outer_labels_available_to_fit": False,
    }


def red_hit_distribution(red_probabilities: Sequence[float], combos: np.ndarray | None = None) -> np.ndarray:
    combinations = legal_red_combinations() if combos is None else np.asarray(combos)
    probabilities = np.asarray(red_probabilities, dtype=np.float64)
    if len(probabilities) != 33:
        raise ValueError("SSQ red head must contain 33 probabilities")
    distribution = np.zeros((7, len(combinations)), dtype=np.float64)
    distribution[0] = 1.0
    for column in range(6):
        p = probabilities[combinations[:, column]]
        for hits in range(column + 1, 0, -1):
            distribution[hits] = distribution[hits] * (1.0 - p) + distribution[hits - 1] * p
        distribution[0] *= 1.0 - p
    return distribution


_PRIZE_BY_HITS = np.zeros((7, 2), dtype=np.float64)
_TIER_BY_HITS = np.zeros((7, 2), dtype=np.uint8)
for _red_hits in range(7):
    for _blue_hits in range(2):
        _tier = prize_tier(_red_hits, _blue_hits)
        if _tier is not None:
            _PRIZE_BY_HITS[_red_hits, _blue_hits] = SSQ_FIXED_PRIZES[_tier]
            _TIER_BY_HITS[_red_hits, _blue_hits] = _tier


def _stable_top_indices(scores: np.ndarray, ticket_ids: np.ndarray, count: int) -> np.ndarray:
    if count >= len(scores):
        candidates = np.arange(len(scores))
    else:
        rough = np.argpartition(scores, len(scores) - count)[-count:]
        threshold = float(scores[rough].min())
        candidates = np.flatnonzero(scores >= threshold)
    order = np.lexsort((ticket_ids[candidates], -scores[candidates]))
    return candidates[order[:count]]


def _structure_log_score(snapshot: Mapping[str, object], spec: Mapping[str, object], blue_number: int) -> np.ndarray:
    weights = spec["structure_weights"]
    if not weights:
        return np.zeros(len(legal_red_combinations()), dtype=np.float64)
    props = combo_properties()
    structure = snapshot["structure"]
    combos = legal_red_combinations()
    result = np.zeros(len(combos), dtype=np.float64)
    sources = {
        "red_zone_distribution": np.asarray(structure["zone_probability"])[props["zone_code"]],
        "red_parity_structure": np.asarray(structure["parity_probability"])[props["odd_count"]],
        "red_sum_structure": np.asarray(structure["sum_probability"])[props["sum"]],
        "red_consecutive_structure": np.asarray(structure["consecutive_probability"])[props["consecutive_pairs"]],
        "red_blue_parity_interaction": np.asarray(structure["red_blue_joint_probability"])[props["odd_count"], blue_number % 2],
    }
    pair_matrix = np.asarray(structure["pair_similarity"])
    pair_similarity = np.zeros(len(combos), dtype=np.float64)
    for left in range(6):
        for right in range(left + 1, 6):
            pair_similarity += pair_matrix[combos[:, left], combos[:, right]]
    sources["historical_combination_similarity"] = pair_similarity / 15.0 + 1e-9
    for feature_id, weight in weights.items():
        values = sources[feature_id]
        # Centering makes positive/negative similarity weights meaningful while
        # retaining deterministic ordering for all other structure priors.
        if feature_id == "historical_combination_similarity":
            scaled = (values - float(values.mean())) / max(float(values.std()), 1e-12)
            result += float(weight) * scaled
        else:
            result += float(weight) * np.log(np.maximum(values, 1e-15))
    return result


def _diversified_prefix(pool_ids: np.ndarray, pool_scores: np.ndarray, red_probabilities: np.ndarray, count: int) -> np.ndarray:
    combos = legal_red_combinations()
    red_indices = pool_ids // 16
    blue_indices = pool_ids % 16
    selected_red = combos[red_indices]
    rare_positions = np.argmin(red_probabilities[selected_red], axis=1)
    rare_numbers = selected_red[np.arange(len(selected_red)), rare_positions]
    groups = blue_indices * 33 + rare_numbers
    positions = [np.flatnonzero(groups == group) for group in range(16 * 33)]
    output: list[int] = []
    depth = 0
    while len(output) < count:
        available = [int(values[depth]) for values in positions if depth < len(values)]
        if not available:
            break
        # pool is already score/id ordered; numeric position preserves that order.
        available.sort()
        output.extend(available[:count - len(output)])
        depth += 1
    if len(output) != count:
        raise ValueError("FAIL_PHASE4E19_DIVERSITY_POOL_EXHAUSTED")
    return pool_ids[np.asarray(output, dtype=np.int64)]


def rank_candidate_portfolio(snapshot: Mapping[str, object], spec: Mapping[str, object], max_count: int = max(SSQ_PARTITION_SIZES)) -> dict[str, object]:
    """Score every legal SSQ ticket and return an exact deterministic prefix."""
    heads = fit_heads(snapshot, spec)
    red_probabilities = np.asarray(heads["red_probabilities"])
    blue_probabilities = np.asarray(heads["blue_probabilities"])
    combos = legal_red_combinations()
    red_distribution = red_hit_distribution(red_probabilities, combos)
    red_ids = np.arange(len(combos), dtype=np.int64)
    raw_red_score = None
    structure_scores: dict[int, np.ndarray] = {}
    if spec["score_kind"] == "raw_complete_ticket_likelihood":
        odds = np.log(np.maximum(red_probabilities, 1e-15)) - np.log(np.maximum(1.0 - red_probabilities, 1e-15))
        raw_red_score = odds[combos].sum(axis=1)
    else:
        # The only blue-dependent structure feature uses parity, so cache the two
        # possible vectors instead of rebuilding pair/structure arrays 16 times.
        structure_scores = {parity: _structure_log_score(snapshot, spec, 2 if parity == 0 else 1) for parity in (0, 1)}
    gathered_scores: list[np.ndarray] = []
    gathered_ids: list[np.ndarray] = []
    per_blue_count = max_count
    for blue_index, blue_p in enumerate(blue_probabilities):
        if spec["score_kind"] == "raw_complete_ticket_likelihood":
            scores = raw_red_score + math.log(max(float(blue_p), 1e-15))
        else:
            no_blue = _PRIZE_BY_HITS[:, 0]
            yes_blue = _PRIZE_BY_HITS[:, 1]
            coefficients = (1.0 - float(blue_p)) * no_blue + float(blue_p) * yes_blue
            expected = coefficients @ red_distribution
            scores = np.log(np.maximum(expected, 1e-300)) + structure_scores[(blue_index + 1) % 2]
        ticket_ids = red_ids * 16 + blue_index
        chosen = _stable_top_indices(scores, ticket_ids, per_blue_count)
        gathered_scores.append(scores[chosen])
        gathered_ids.append(ticket_ids[chosen])
    all_scores = np.concatenate(gathered_scores)
    all_ids = np.concatenate(gathered_ids)
    pool_count = max_count * (int(spec.get("diversity_pool_multiplier", 1)) if spec.get("diversified") else 1)
    pool_indices = _stable_top_indices(all_scores, all_ids, pool_count)
    pool_scores = all_scores[pool_indices]
    pool_ids = all_ids[pool_indices]
    if bool(spec.get("diversified")):
        ranked_ids = _diversified_prefix(pool_ids, pool_scores, red_probabilities, max_count)
        ranking_definition = "all legal tickets scored; top 4N score pool round-robin interleaved by blue and rarest-red coverage bucket"
    else:
        ranked_ids = pool_ids[:max_count]
        ranking_definition = "all legal tickets scored; exact score-descending prefix with red-combination then blue ascending tie-break"
    return {
        "candidate_id": spec["candidate_id"],
        "ticket_ids": ranked_ids.astype(np.int64),
        "portfolio_sha256_int64_le": vector_hash(ranked_ids),
        "complete_legal_ticket_count_enumerated": int(len(combos) * 16),
        "partition_prefix_count": int(max_count),
        "ranking_definition": ranking_definition,
        "stable_tie_break": "red combination lexicographic ascending, then blue ascending",
        "exact_partition_prefix_arithmetic": True,
        "all_tier_expected_prize_score": spec["score_kind"] == "all_tier_expected_prize",
        "heads": {key: value for key, value in heads.items() if key not in {"red_probabilities", "blue_probabilities"}},
    }


def random_baseline_portfolio(cutoff: int, max_count: int = max(SSQ_PARTITION_SIZES)) -> dict[str, object]:
    total = math.comb(33, 6) * 16
    seed = int(hashlib.sha256(f"phase4e19-random-baseline-{cutoff}".encode()).hexdigest()[:16], 16)
    multiplier = (seed | 1) % total
    while math.gcd(multiplier, total) != 1:
        multiplier = (multiplier + 2) % total
    offset = (seed >> 17) % total
    ids = (multiplier * np.arange(max_count, dtype=np.int64) + offset) % total
    return {
        "candidate_id": "random_baseline",
        "ticket_ids": ids,
        "portfolio_sha256_int64_le": vector_hash(ids),
        "complete_legal_ticket_count": total,
        "ranking_definition": "seeded affine permutation of canonical legal ticket ids",
        "seed_material": f"phase4e19-random-baseline-{cutoff}",
        "multiplier": multiplier,
        "offset": offset,
        "exact_partition_prefix_arithmetic": True,
    }


def evaluate_portfolio(ticket_ids: Sequence[int] | np.ndarray, actual_red: Iterable[int], actual_blue: int, partition_sizes: Sequence[int] = SSQ_PARTITION_SIZES) -> dict[str, object]:
    ids = np.asarray(ticket_ids, dtype=np.int64)
    if len(ids) < max(partition_sizes) or len(np.unique(ids)) != len(ids):
        raise ValueError("FAIL_PHASE4E19_PORTFOLIO_PREFIX")
    combos = legal_red_combinations()
    red_indices = ids // 16
    blue = ids % 16 + 1
    actual_mask = np.zeros(33, dtype=np.uint8)
    actual_mask[np.asarray(list(actual_red), dtype=int) - 1] = 1
    red_hits = actual_mask[combos[red_indices]].sum(axis=1)
    blue_hits = (blue == int(actual_blue)).astype(np.uint8)
    tiers = _TIER_BY_HITS[red_hits, blue_hits]
    amounts = _PRIZE_BY_HITS[red_hits, blue_hits]
    cumulative = np.cumsum(amounts, dtype=np.float64)
    result: dict[str, object] = {}
    for size in partition_sizes:
        tier_counts = np.bincount(tiers[:size], minlength=7)
        total = float(cumulative[size - 1])
        result[str(size)] = {
            "partition_size": int(size),
            "known_prize_total_yuan": total,
            "average_prize_yuan": total / size,
            "winning_ticket_count": int(size - tier_counts[0]),
            "winning_ticket_rate": float((size - tier_counts[0]) / size),
            "prize_tier_ticket_counts": {str(tier): int(tier_counts[tier]) for tier in range(1, 7)},
        }
    return result


def _normal_mean_ci(values: Sequence[float]) -> dict[str, object]:
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"method": "draw_level_normal_approximation_95", "lower": mean, "upper": mean, "standard_error": 0.0}
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return {"method": "draw_level_normal_approximation_95", "lower": mean - 1.959963984540054 * standard_error, "upper": mean + 1.959963984540054 * standard_error, "standard_error": standard_error}


def aggregate_draw_results(draw_results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not draw_results:
        raise ValueError("cannot aggregate no draws")
    output: dict[str, object] = {}
    for size in SSQ_PARTITION_SIZES:
        values = [row["partitions"][str(size)] for row in draw_results]
        total = math.fsum(float(value["known_prize_total_yuan"]) for value in values)
        tier_counts = {str(tier): sum(int(value["prize_tier_ticket_counts"][str(tier)]) for value in values) for tier in range(1, 7)}
        tickets = len(values) * size
        output[str(size)] = {
            "draws": len(values),
            "partition_size": size,
            "complete_ticket_count": tickets,
            "known_prize_total_yuan": total,
            "average_prize_yuan": total / tickets,
            "winning_ticket_count": sum(int(value["winning_ticket_count"]) for value in values),
            "winning_ticket_rate": sum(int(value["winning_ticket_count"]) for value in values) / tickets,
            "prize_tier_ticket_counts": tier_counts,
            "average_prize_yuan_confidence_interval_95": _normal_mean_ci([float(value["average_prize_yuan"]) for value in values]),
            "draw_prize_total_sha256_float64_le": vector_hash(np.array([float(value["known_prize_total_yuan"]) for value in values])),
        }
    return {
        "draws": len(draw_results),
        "first_issue": str(draw_results[0]["issue"]),
        "last_issue": str(draw_results[-1]["issue"]),
        "primary_metric": "known_prize_total_yuan / (draws * N complete tickets)",
        "partitions": output,
    }


def _evaluate_rows(rows: Sequence[Mapping[str, object]], targets: Sequence[int], portfolio: Mapping[str, object], cutoff: int) -> list[dict[str, object]]:
    return [
        {
            "game": "ssq",
            "issue": str(rows[target]["issue"]),
            "target_position": target,
            "feature_and_fit_cutoff_position_exclusive": cutoff,
            "maximum_feature_and_fit_source_position": cutoff - 1,
            "strict_lag": cutoff <= target,
            "outer_label_used_for_features_selection_or_fit": False,
            "candidate_id": portfolio["candidate_id"],
            "portfolio_sha256_int64_le": portfolio["portfolio_sha256_int64_le"],
            "partitions": evaluate_portfolio(portfolio["ticket_ids"], rows[target]["front"], rows[target]["back"][0]),
        }
        for target in targets
    ]


def verify_isolation() -> dict[str, object]:
    files = []
    for relative, expected in DLT_FROZEN_HASHES.items():
        observed = sha256_file(ROOT / relative)
        files.append({"path": relative, "expected_sha256": expected, "observed_sha256": observed, "matches": observed == expected})
    serving = json.loads(P4E6_DECISION.read_text())
    serving_matches = serving.get("serving_release") == EXPECTED_SERVING_RELEASE and serving.get("terminal_status") == EXPECTED_SERVING_STATUS
    result = {
        "game_scope": "ssq_only",
        "dlt_files": files,
        "all_dlt_hashes_match": all(bool(row["matches"]) for row in files),
        "p4e6_serving_release": serving.get("serving_release"),
        "p4e6_terminal_status": serving.get("terminal_status"),
        "p4e6_serving_identity_matches": serving_matches,
    }
    if not result["all_dlt_hashes_match"] or not serving_matches:
        raise ValueError("FAIL_PHASE4E19_DLT_OR_SERVING_ISOLATION")
    return result


def load_source_rows(path: Path = SOURCE) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if len(rows) != 3482 or any(row.get("game") != "ssq" for row in rows):
        raise ValueError("FAIL_PHASE4E19_FROZEN_SSQ_SOURCE")
    return rows


def select_candidate(inner_blocks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    candidates = []
    for order, spec in enumerate(CANDIDATE_SPECS, start=1):
        candidate_id = str(spec["candidate_id"])
        blocks = []
        for block in inner_blocks:
            candidate = block["summaries"][candidate_id]
            raw = block["summaries"]["raw_control"]
            random = block["summaries"]["random_baseline"]
            candidate_objective = statistics.fmean(float(candidate["partitions"][str(n)]["average_prize_yuan"]) for n in SSQ_PARTITION_SIZES)
            raw_objective = statistics.fmean(float(raw["partitions"][str(n)]["average_prize_yuan"]) for n in SSQ_PARTITION_SIZES)
            random_objective = statistics.fmean(float(random["partitions"][str(n)]["average_prize_yuan"]) for n in SSQ_PARTITION_SIZES)
            blocks.append({
                "block": block["block"], "draws": INNER_BLOCK_DRAWS,
                "first_issue": block["first_issue"], "last_issue": block["last_issue"],
                "candidate_mean_registered_partition_average_prize_yuan": candidate_objective,
                "raw_control_mean_registered_partition_average_prize_yuan": raw_objective,
                "random_baseline_mean_registered_partition_average_prize_yuan": random_objective,
                "uplift_vs_raw_control_yuan": candidate_objective - raw_objective,
                "positive_uplift_vs_raw_control": candidate_objective > raw_objective,
                "catastrophic_below_half_random_baseline": candidate_objective < .5 * random_objective,
            })
        positive = sum(bool(block["positive_uplift_vs_raw_control"]) for block in blocks)
        catastrophic = any(bool(block["catastrophic_below_half_random_baseline"]) for block in blocks)
        uplifts = [float(block["uplift_vs_raw_control_yuan"]) for block in blocks]
        candidates.append({
            "candidate_id": candidate_id,
            "candidate_order": order,
            "positive_uplift_block_count": positive,
            "required_positive_uplift_blocks": 3,
            "catastrophic_block": catastrophic,
            "eligible": candidate_id != "raw_control" and positive >= 3 and not catastrophic,
            "median_block_uplift_yuan": statistics.median(uplifts),
            "lower_tail_block_uplift_yuan": min(uplifts),
            "blocks": blocks,
        })
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    if eligible:
        winner = max(eligible, key=lambda row: (float(row["median_block_uplift_yuan"]), float(row["lower_tail_block_uplift_yuan"]), -int(row["candidate_order"])))
        selected = str(winner["candidate_id"])
        status = "eligible_prize_candidate_selected"
    else:
        selected = "raw_control"
        status = "registered_raw_control_fallback"
    return {
        "selection_rule": "positive mean registered-partition prize uplift versus raw control in >=3 of 4 chronological 60-draw blocks; reject any block below half random baseline; rank by median uplift, lower-tail uplift, registered order",
        "selection_uses_outer_labels": False,
        "selected_candidate": selected,
        "selection_status": status,
        "candidates": candidates,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def _write_jsonl(path: Path, rows: Sequence[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_bytes(row) for row in rows))


def run_pipeline(output_dir: Path = DEFAULT_OUT) -> dict[str, object]:
    validate_candidate_registry()
    isolation_before = verify_isolation()
    rows = load_source_rows()
    outer_start = len(rows) - OUTER_DRAWS
    inner_start = outer_start - INNER_DRAWS
    inner_targets = list(range(inner_start, outer_start))
    outer_targets = list(range(outer_start, len(rows)))
    if (inner_start, outer_start, len(rows)) != (3122, 3362, 3482):
        raise ValueError("FAIL_PHASE4E19_FROZEN_WINDOWS")

    feature_snapshots: dict[int, dict[str, object]] = {}
    feature_lineage: list[dict[str, object]] = []
    strict_lag_cutoffs: list[dict[str, object]] = []
    inner_blocks: list[dict[str, object]] = []
    inner_rolling: list[dict[str, object]] = []
    specs = {str(spec["candidate_id"]): spec for spec in CANDIDATE_SPECS}

    for block_index in range(4):
        targets = inner_targets[block_index * INNER_BLOCK_DRAWS:(block_index + 1) * INNER_BLOCK_DRAWS]
        cutoff = targets[0]
        snapshot = build_feature_snapshot(rows, cutoff)
        feature_snapshots[cutoff] = snapshot
        feature_lineage.extend(snapshot["lineage"])
        strict_lag_cutoffs.append({key: snapshot[key] for key in ("cutoff_position_exclusive", "cutoff_issue_exclusive", "maximum_source_position", "maximum_source_issue", "prefix_input_sha256", "feature_bundle_sha256")})
        portfolios = {candidate_id: rank_candidate_portfolio(snapshot, spec) for candidate_id, spec in specs.items()}
        portfolios["random_baseline"] = random_baseline_portfolio(cutoff)
        by_candidate = {candidate_id: _evaluate_rows(rows, targets, portfolio, cutoff) for candidate_id, portfolio in portfolios.items()}
        summaries = {candidate_id: aggregate_draw_results(values) for candidate_id, values in by_candidate.items()}
        block = {
            "block": block_index + 1, "cutoff_position_exclusive": cutoff,
            "maximum_feature_and_fit_source_position": cutoff - 1,
            "first_target_position": targets[0], "last_target_position": targets[-1],
            "first_issue": rows[targets[0]]["issue"], "last_issue": rows[targets[-1]]["issue"],
            "draws": len(targets), "strict_block_holdout": True,
            "block_labels_available_to_features_or_fit": False,
            "portfolios": {candidate_id: {key: value for key, value in portfolio.items() if key != "ticket_ids"} for candidate_id, portfolio in portfolios.items()},
            "summaries": summaries,
        }
        inner_blocks.append(block)
        for offset, target in enumerate(targets):
            inner_rolling.append({
                "game": "ssq", "block": block_index + 1, "issue": rows[target]["issue"],
                "target_position": target, "feature_and_fit_cutoff_position_exclusive": cutoff,
                "maximum_feature_and_fit_source_position": cutoff - 1, "strict_lag": True,
                "block_label_used_for_features_or_fit": False,
                "candidates": {candidate_id: values[offset] for candidate_id, values in by_candidate.items()},
            })

    selection = select_candidate(inner_blocks)
    outer_snapshot = build_feature_snapshot(rows, outer_start)
    feature_snapshots[outer_start] = outer_snapshot
    feature_lineage.extend(outer_snapshot["lineage"])
    strict_lag_cutoffs.append({key: outer_snapshot[key] for key in ("cutoff_position_exclusive", "cutoff_issue_exclusive", "maximum_source_position", "maximum_source_issue", "prefix_input_sha256", "feature_bundle_sha256")})
    outer_portfolios = {candidate_id: rank_candidate_portfolio(outer_snapshot, spec) for candidate_id, spec in specs.items()}
    outer_portfolios["random_baseline"] = random_baseline_portfolio(outer_start)
    outer_by_candidate = {candidate_id: _evaluate_rows(rows, outer_targets, portfolio, outer_start) for candidate_id, portfolio in outer_portfolios.items()}

    outer_rolling = []
    for index, target in enumerate(outer_targets):
        outer_rolling.append({
            "game": "ssq", "issue": rows[target]["issue"], "target_position": target,
            "outer_split": "calibration" if index < CALIBRATION_DRAWS else "evaluation",
            "feature_and_fit_cutoff_position_exclusive": outer_start,
            "maximum_feature_and_fit_source_position": outer_start - 1,
            "strict_lag": True, "outer_label_used_for_features_selection_or_fit": False,
            "candidates": {candidate_id: values[index] for candidate_id, values in outer_by_candidate.items()},
        })

    comparison: dict[str, object] = {}
    for candidate_id, values in outer_by_candidate.items():
        comparison[candidate_id] = {
            "calibration": aggregate_draw_results(values[:CALIBRATION_DRAWS]),
            "evaluation": aggregate_draw_results(values[CALIBRATION_DRAWS:]),
            "all_120": aggregate_draw_results(values),
        }
    selected_id = str(selection["selected_candidate"])
    selected_summaries = comparison[selected_id]
    split_gates = {
        split: acceptance_gate([summary["partitions"][str(n)]["average_prize_yuan"] for n in SSQ_PARTITION_SIZES])
        for split, summary in selected_summaries.items()
    }
    hard_gate = {
        "required_splits": ["calibration", "evaluation", "all_120"],
        "required_partition_sizes": list(SSQ_PARTITION_SIZES),
        "split_gates": split_gates,
        "passed": all(bool(value["passed"]) for value in split_gates.values()),
    }
    decision = "PROMOTION" if hard_gate["passed"] else "NO_PROMOTION"

    replay_portfolio = rank_candidate_portfolio(outer_snapshot, specs[selected_id])
    replay_matches = np.array_equal(replay_portfolio["ticket_ids"], outer_portfolios[selected_id]["ticket_ids"])
    mutated = copy.deepcopy(rows)
    for row in mutated[outer_start:]:
        row["front"] = [1, 2, 3, 4, 5, 6]
        row["back"] = [1]
        row["source_record_sha256"] = "mutated-outer-label"
    mutated_snapshot = build_feature_snapshot(mutated, outer_start)
    mutation_same_features = mutated_snapshot["feature_bundle_sha256"] == outer_snapshot["feature_bundle_sha256"]
    mutated_portfolio = rank_candidate_portfolio(mutated_snapshot, specs[selected_id])
    mutation_same_portfolio = np.array_equal(mutated_portfolio["ticket_ids"], outer_portfolios[selected_id]["ticket_ids"])
    if not replay_matches or not mutation_same_features or not mutation_same_portfolio:
        raise ValueError("FAIL_PHASE4E19_REPLAY_OR_OUTER_LABEL_LEAKAGE")

    best_observed_id, best_observed_value = max(
        ((candidate_id, min(float(split["partitions"][str(n)]["average_prize_yuan"]) for split in summaries.values() for n in SSQ_PARTITION_SIZES))
         for candidate_id, summaries in comparison.items()),
        key=lambda item: (item[1], -([str(s["candidate_id"]) for s in CANDIDATE_SPECS] + ["random_baseline"]).index(item[0])),
    )
    isolation_after = verify_isolation()
    candidate_registry_artifact = {
        "artifact_type": "phase4e19_finite_candidate_registry", "game": "ssq",
        "registered_before_inner_or_outer_labels_loaded": True,
        "finite_candidate_count": len(CANDIDATE_SPECS), "candidates": candidate_registry(),
        "registry_sha256": digest(candidate_registry()),
    }
    lineage_artifact = {
        "artifact_type": "phase4e19_feature_lineage", "game": "ssq",
        "source_path": str(SOURCE.relative_to(ROOT)), "source_sha256": sha256_file(SOURCE),
        "outer_feature_fit_cutoff_position_exclusive": outer_start,
        "outer_labels_used": False, "feature_records": feature_lineage,
    }
    strict_lag_artifact = {
        "artifact_type": "phase4e19_strict_lag_hashes", "game": "ssq",
        "cutoffs": strict_lag_cutoffs,
        "all_maximum_source_positions_before_first_scored_target": True,
        "outer_label_mutation_test": {
            "mutated_positions": [outer_start, len(rows) - 1],
            "original_feature_bundle_sha256": outer_snapshot["feature_bundle_sha256"],
            "mutated_feature_bundle_sha256": mutated_snapshot["feature_bundle_sha256"],
            "feature_hashes_match": mutation_same_features,
            "portfolio_hashes_match": mutation_same_portfolio,
        },
    }
    replay_artifact = {
        "artifact_type": "phase4e19_deterministic_replay", "game": "ssq",
        "selected_candidate": selected_id,
        "first_portfolio_sha256_int64_le": outer_portfolios[selected_id]["portfolio_sha256_int64_le"],
        "replay_portfolio_sha256_int64_le": replay_portfolio["portfolio_sha256_int64_le"],
        "exact_ticket_order_matches": replay_matches,
        "feature_replay_matches_after_outer_label_mutation": mutation_same_features,
        "ticket_replay_matches_after_outer_label_mutation": mutation_same_portfolio,
    }
    report = {
        "artifact_type": "phase4e19_ssq_prize_aware_optimization_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY", "game": "ssq", "decision": decision,
        "promotion_eligible": bool(hard_gate["passed"]),
        "primary_metric": "total known fixed prize yuan / N complete SSQ tickets",
        "fixed_prizes_yuan": {str(key): value for key, value in SSQ_FIXED_PRIZES.items()},
        "registered_partition_sizes": list(SSQ_PARTITION_SIZES),
        "frozen_windows": {
            "inner": {"draws": INNER_DRAWS, "blocks": 4, "block_draws": INNER_BLOCK_DRAWS, "first_position": inner_start, "last_position": outer_start - 1, "first_issue": rows[inner_start]["issue"], "last_issue": rows[outer_start - 1]["issue"]},
            "outer": {"draws": OUTER_DRAWS, "calibration_draws": CALIBRATION_DRAWS, "evaluation_draws": OUTER_DRAWS - CALIBRATION_DRAWS, "first_position": outer_start, "last_position": len(rows) - 1, "first_issue": rows[outer_start]["issue"], "last_issue": rows[-1]["issue"]},
        },
        "candidate_selection": selection,
        "selected_candidate": selected_id,
        "inner_block_evidence": inner_blocks,
        "outer_candidate_comparison": comparison,
        "selected_candidate_summaries": selected_summaries,
        "hard_acceptance_gate": hard_gate,
        "best_observed_minimum_split_partition_average": {"candidate_id": best_observed_id, "minimum_average_prize_yuan": best_observed_value, "outer_labels_not_used_to_select_for_promotion": True},
        "leakage_checks": strict_lag_artifact,
        "replay": replay_artifact,
        "dlt_and_serving_isolation_before": isolation_before,
        "dlt_and_serving_isolation_after": isolation_after,
        "phase4e18_artifacts_overwritten": False,
    }
    delivery_decision = {
        "artifact_type": "phase4e19_delivery_decision", "game": "ssq",
        "decision": decision, "hard_gate_passed": bool(hard_gate["passed"]),
        "selected_candidate": selected_id,
        "reason": "all calibration/evaluation/all-120 registered-N averages strictly exceed 2 yuan" if hard_gate["passed"] else "at least one calibration/evaluation/all-120 registered-N average is not strictly greater than 2 yuan",
        "p4e6_serving_changed": False, "serving_release": EXPECTED_SERVING_RELEASE,
        "serving_status": EXPECTED_SERVING_STATUS, "dlt_changed": False,
    }

    _write_json(output_dir / "candidate-registry.json", candidate_registry_artifact)
    _write_json(output_dir / "feature-lineage.json", lineage_artifact)
    _write_json(output_dir / "strict-lag-hashes.json", strict_lag_artifact)
    _write_jsonl(output_dir / "inner-rolling-report.jsonl", inner_rolling)
    _write_jsonl(output_dir / "outer-rolling-report.jsonl", outer_rolling)
    _write_json(output_dir / "calibration-summary.json", selected_summaries["calibration"])
    _write_json(output_dir / "evaluation-summary.json", selected_summaries["evaluation"])
    _write_json(output_dir / "all-120-summary.json", selected_summaries["all_120"])
    _write_json(output_dir / "replay-evidence.json", replay_artifact)
    _write_json(output_dir / "report.json", report)
    _write_json(output_dir / "delivery/decision.json", delivery_decision)
    manifest_paths = [
        "candidate-registry.json", "feature-lineage.json", "strict-lag-hashes.json",
        "inner-rolling-report.jsonl", "outer-rolling-report.jsonl", "calibration-summary.json",
        "evaluation-summary.json", "all-120-summary.json", "replay-evidence.json", "report.json",
        "delivery/decision.json",
    ]
    manifest = {
        "artifact_type": "phase4e19_delivery_manifest", "game": "ssq",
        "files": [{"path": path, "sha256": sha256_file(output_dir / path), "bytes": (output_dir / path).stat().st_size} for path in manifest_paths],
        "dlt_isolation": isolation_after, "decision": decision,
    }
    _write_json(output_dir / "delivery/manifest.json", manifest)
    return {"decision": decision, "selected_candidate": selected_id, "hard_gate_passed": hard_gate["passed"], "best_observed_candidate": best_observed_id, "best_observed_minimum_average_prize_yuan": best_observed_value}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = run_pipeline(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
