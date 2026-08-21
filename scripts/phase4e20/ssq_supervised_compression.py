#!/usr/bin/env python3
"""Phase4E20 frozen-origin supervised SSQ compression and coverage pipeline.

This module is intentionally SSQ-only.  It imports Phase4E19's immutable prize
arithmetic and canonical ticket encoding, but it does not import or write any
DLT or serving implementation.  Every supervised target is strictly later than
its feature history and strictly earlier than its frozen evaluation origin.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import statistics
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical/ssq.jsonl"
DEFAULT_OUT = ROOT / "artifacts/phase4e20"
E19_MODULE_PATH = ROOT / "scripts/phase4e19/ssq_prize_aware.py"
P4E6_DECISION = ROOT / "artifacts/phase4e6/delivery/decision.json"

_e19_spec = importlib.util.spec_from_file_location("phase4e19_for_e20", E19_MODULE_PATH)
if _e19_spec is None or _e19_spec.loader is None:
    raise RuntimeError("FAIL_PHASE4E20_E19_IMPORT")
E19 = importlib.util.module_from_spec(_e19_spec)
_e19_spec.loader.exec_module(E19)

SSQ_PARTITION_SIZES = E19.SSQ_PARTITION_SIZES
SSQ_FIXED_PRIZES = E19.SSQ_FIXED_PRIZES
OUTER_DRAWS = 120
CALIBRATION_DRAWS = 60
INNER_DRAWS = 240
INNER_BLOCK_DRAWS = 60
TRAINING_DRAWS = 1200
FEATURE_WINDOWS = (30, 60, 120, 240, 360, 720, 1200)
HEAD_CONFIGS: tuple[dict[str, object], ...] = (
    {"head_id": "ridge_a1", "model": "ridge", "alpha": 1.0, "training_draws": TRAINING_DRAWS},
    {"head_id": "ridge_a25", "model": "ridge", "alpha": 25.0, "training_draws": TRAINING_DRAWS},
)
PORTFOLIO_SPECS: tuple[dict[str, object], ...] = (
    {
        "candidate_id": "sup_a1_unique_rr",
        "head_ids": ["ridge_a1"],
        "red_union": False,
        "unique_red_count": 100000,
        "tickets_per_red_cap": 1,
        "blue_allocation": "probability_rank_round_robin",
    },
    {
        "candidate_id": "sup_a25_unique_latin",
        "head_ids": ["ridge_a25"],
        "red_union": False,
        "unique_red_count": 100000,
        "tickets_per_red_cap": 1,
        "blue_allocation": "probability_rank_latin",
    },
    {
        "candidate_id": "sup_ensemble_unique_latin",
        "head_ids": ["ridge_a1", "ridge_a25"],
        "red_union": True,
        "unique_red_count": 100000,
        "tickets_per_red_cap": 1,
        "blue_allocation": "ensemble_probability_rank_latin",
    },
    {
        "candidate_id": "sup_ensemble_layered2_rr",
        "head_ids": ["ridge_a1", "ridge_a25"],
        "red_union": True,
        "unique_red_count": 60000,
        "tickets_per_red_cap": 2,
        "blue_allocation": "ensemble_probability_rank_round_robin",
    },
)
BASELINE_IDS = ("e19_raw_control", "deterministic_random")
EXPECTED_SERVING_RELEASE = "P4-P4E2-20260815-r12"
EXPECTED_SERVING_STATUS = "PROSPECTIVE_ONLY"
DLT_FROZEN_HASHES = copy.deepcopy(E19.DLT_FROZEN_HASHES)


RED_FEATURE_NAMES = tuple(
    [f"frequency_{window}" for window in FEATURE_WINDOWS]
    + [
        "gap_scaled_120", "gap_deviation_360", "trend_30_120", "trend_120_360",
        "frequency_slope_30_60_120", "transition_360", "graph_neighbor_360",
        "number_centered", "number_sin", "number_cos", "number_parity",
        "zone_low", "zone_mid", "zone_high", "previous_red_low_fraction",
        "previous_red_mid_fraction", "previous_red_high_fraction",
        "previous_red_odd_fraction", "previous_red_sum_scaled",
        "previous_red_consecutive_fraction", "position_sin_3", "position_cos_3",
        "position_sin_52", "position_cos_52", "position_sin_156", "position_cos_156",
        "previous_blue_parity_interaction", "red_blue_distance_cos",
    ]
)
BLUE_FEATURE_NAMES = tuple(
    [f"frequency_{window}" for window in FEATURE_WINDOWS]
    + [
        "gap_scaled_120", "gap_deviation_360", "trend_30_120", "trend_120_360",
        "frequency_slope_30_60_120", "transition_360", "graph_degree_360",
        "number_centered", "number_sin", "number_cos", "number_parity",
        "previous_red_low_fraction", "previous_red_mid_fraction",
        "previous_red_high_fraction", "previous_red_odd_fraction",
        "previous_red_sum_scaled", "previous_red_consecutive_fraction",
        "position_sin_3", "position_cos_3", "position_sin_52", "position_cos_52",
        "position_sin_156", "position_cos_156", "previous_red_blue_parity_interaction",
        "previous_red_sum_number_interaction",
    ]
)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def vector_hash(values: np.ndarray | Sequence[float] | Sequence[int]) -> str:
    array = np.asarray(values)
    if np.issubdtype(array.dtype, np.floating):
        array = array.astype("<f8", copy=False)
    elif np.issubdtype(array.dtype, np.integer):
        array = array.astype("<i8", copy=False)
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()


def load_source_rows(path: Path = SOURCE) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if len(rows) != 3482 or any(row.get("game") != "ssq" for row in rows):
        raise ValueError("FAIL_PHASE4E20_FROZEN_SSQ_SOURCE")
    return rows


def candidate_registry() -> list[dict[str, object]]:
    return copy.deepcopy(list(PORTFOLIO_SPECS))


def validate_registry() -> None:
    ids = [str(spec["candidate_id"]) for spec in PORTFOLIO_SPECS]
    head_ids = {str(spec["head_id"]) for spec in HEAD_CONFIGS}
    if len(ids) != 4 or len(ids) != len(set(ids)):
        raise ValueError("FAIL_PHASE4E20_FINITE_CANDIDATE_REGISTRY")
    for spec in PORTFOLIO_SPECS:
        if not set(map(str, spec["head_ids"])) <= head_ids:
            raise ValueError("FAIL_PHASE4E20_UNKNOWN_HEAD")
        if int(spec["tickets_per_red_cap"]) not in (1, 2):
            raise ValueError("FAIL_PHASE4E20_LAYER_CAP")


@dataclass(frozen=True)
class EncodedHistory:
    red: np.ndarray
    blue: np.ndarray
    red_cumulative: np.ndarray
    blue_cumulative: np.ndarray
    red_last_before: np.ndarray
    blue_last_before: np.ndarray
    red_transition: np.ndarray
    blue_transition: np.ndarray
    red_graph_neighbor: np.ndarray
    blue_graph_degree: np.ndarray
    red_conditional_blue_parity: np.ndarray
    blue_conditional_red_parity: np.ndarray


def encode_history(rows: Sequence[Mapping[str, object]]) -> EncodedHistory:
    """Precompute bounded rolling statistics; row t statistics use rows < t."""
    draws = len(rows)
    red = np.zeros((draws, 33), dtype=np.float64)
    blue = np.zeros((draws, 16), dtype=np.float64)
    for position, row in enumerate(rows):
        red[position, np.asarray(row["front"], dtype=int) - 1] = 1.0
        blue[position, int(row["back"][0]) - 1] = 1.0
    red_cumulative = np.vstack((np.zeros((1, 33)), np.cumsum(red, axis=0)))
    blue_cumulative = np.vstack((np.zeros((1, 16)), np.cumsum(blue, axis=0)))
    red_last = np.full((draws + 1, 33), -1, dtype=np.int32)
    blue_last = np.full((draws + 1, 16), -1, dtype=np.int32)
    for position in range(draws):
        red_last[position + 1] = red_last[position]
        blue_last[position + 1] = blue_last[position]
        red_last[position + 1, red[position].astype(bool)] = position
        blue_last[position + 1, blue[position].astype(bool)] = position

    red_transition = np.full((draws, 33), 1.0 / 33.0)
    blue_transition = np.full((draws, 16), 1.0 / 16.0)
    red_graph_neighbor = np.zeros((draws, 33), dtype=np.float64)
    blue_graph_degree = np.zeros((draws, 16), dtype=np.float64)
    red_conditional = np.zeros((draws, 33), dtype=np.float64)
    blue_conditional = np.zeros((draws, 16), dtype=np.float64)
    for target in range(1, draws):
        start = max(1, target - 360)
        prior_red = red[start - 1:target - 1]
        next_red = red[start:target]
        prior_blue = blue[start - 1:target - 1]
        next_blue = blue[start:target]
        if len(next_red):
            red_matrix = prior_red.T @ next_red + 0.25
            blue_matrix = prior_blue.T @ next_blue + 0.25
            red_transition[target] = red[target - 1] @ red_matrix
            blue_transition[target] = blue[target - 1] @ blue_matrix
            cooccurrence = next_red.T @ next_red
            red_graph_neighbor[target] = red[target - 1] @ cooccurrence
            blue_graph_degree[target] = np.diag(next_blue.T @ next_blue)
            previous_blue_parity = np.argmax(prior_blue, axis=1) % 2
            current_red_parity = int(np.argmax(blue[target - 1]) % 2)
            mask = previous_blue_parity == current_red_parity
            red_conditional[target] = next_red[mask].sum(axis=0) if mask.any() else next_red.sum(axis=0)
            previous_red_odd = ((prior_red * ((np.arange(33) + 1) % 2)).sum(axis=1).astype(int) % 2)
            desired = int((red[target - 1] * ((np.arange(33) + 1) % 2)).sum()) % 2
            mask_blue = previous_red_odd == desired
            blue_conditional[target] = next_blue[mask_blue].sum(axis=0) if mask_blue.any() else next_blue.sum(axis=0)
    return EncodedHistory(
        red=red, blue=blue, red_cumulative=red_cumulative, blue_cumulative=blue_cumulative,
        red_last_before=red_last, blue_last_before=blue_last,
        red_transition=red_transition, blue_transition=blue_transition,
        red_graph_neighbor=red_graph_neighbor, blue_graph_degree=blue_graph_degree,
        red_conditional_blue_parity=red_conditional,
        blue_conditional_red_parity=blue_conditional,
    )


def _bounded_rate(cumulative: np.ndarray, target: int, window: int) -> np.ndarray:
    start = max(0, target - window)
    effective = max(1, target - start)
    return (cumulative[target] - cumulative[start] + 0.5) / (effective + 1.0)


def _normalize_vector(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    spread = float(values.max() - values.min())
    return (values - float(values.min())) / max(spread, 1e-12)


def _context(history: EncodedHistory, target: int) -> tuple[float, ...]:
    previous = history.red[target - 1]
    numbers = np.flatnonzero(previous) + 1
    return (
        float((numbers <= 11).sum() / 6.0),
        float(((numbers >= 12) & (numbers <= 22)).sum() / 6.0),
        float((numbers >= 23).sum() / 6.0),
        float((numbers % 2).sum() / 6.0),
        float(numbers.sum() / 200.0),
        float((np.diff(numbers) == 1).sum() / 5.0),
    )


def _fourier(position: int) -> tuple[float, ...]:
    values: list[float] = []
    for period in (3.0, 52.0, 156.0):
        angle = 2.0 * math.pi * position / period
        values.extend((math.sin(angle), math.cos(angle)))
    return tuple(values)


def number_feature_matrix(history: EncodedHistory, target: int, scope: str) -> np.ndarray:
    """Return per-number features using labels only from positions < target."""
    if target < 1200 or target > len(history.red):
        raise ValueError("FAIL_PHASE4E20_FEATURE_TARGET")
    if scope == "red":
        count = 33
        cumulative = history.red_cumulative
        last = history.red_last_before[target]
        transition = history.red_transition[target]
        graph = history.red_graph_neighbor[target]
        conditional = history.red_conditional_blue_parity[target]
    elif scope == "blue":
        count = 16
        cumulative = history.blue_cumulative
        last = history.blue_last_before[target]
        transition = history.blue_transition[target]
        graph = history.blue_graph_degree[target]
        conditional = history.blue_conditional_red_parity[target]
    else:
        raise ValueError("scope must be red or blue")
    frequencies = {window: _bounded_rate(cumulative, target, window) for window in FEATURE_WINDOWS}
    gap = target - last
    expected_gap = 1.0 / np.maximum(frequencies[360], 1e-6)
    numbers = np.arange(1, count + 1, dtype=np.float64)
    context = _context(history, target)
    seasonal = _fourier(target)
    shared = np.column_stack(
        [frequencies[window] for window in FEATURE_WINDOWS]
        + [
            np.minimum(gap, 120) / 120.0,
            np.clip((gap - expected_gap) / 120.0, -1.0, 1.0),
            frequencies[30] - frequencies[120],
            frequencies[120] - frequencies[360],
            (frequencies[30] - 2.0 * frequencies[60] + frequencies[120]),
            _normalize_vector(transition),
            _normalize_vector(graph),
            (numbers - numbers.mean()) / numbers.max(),
            np.sin(2.0 * math.pi * numbers / count),
            np.cos(2.0 * math.pi * numbers / count),
            numbers % 2,
        ]
    )
    repeated_context = np.tile(np.asarray(context, dtype=np.float64), (count, 1))
    repeated_seasonal = np.tile(np.asarray(seasonal, dtype=np.float64), (count, 1))
    if scope == "red":
        zones = np.column_stack((numbers <= 11, (numbers >= 12) & (numbers <= 22), numbers >= 23)).astype(float)
        previous_blue = int(np.argmax(history.blue[target - 1])) + 1
        interactions = np.column_stack(
            (
                ((numbers % 2) == (previous_blue % 2)).astype(float) * _normalize_vector(conditional),
                np.cos(2.0 * math.pi * (numbers / 33.0 - previous_blue / 16.0)),
            )
        )
        result = np.column_stack((shared, zones, repeated_context, repeated_seasonal, interactions))
        names = RED_FEATURE_NAMES
    else:
        prior_red_odd = int((history.red[target - 1] * ((np.arange(33) + 1) % 2)).sum()) % 2
        interactions = np.column_stack(
            (
                ((numbers % 2) == prior_red_odd).astype(float) * _normalize_vector(conditional),
                context[4] * numbers / 16.0,
            )
        )
        result = np.column_stack((shared, repeated_context, repeated_seasonal, interactions))
        names = BLUE_FEATURE_NAMES
    if result.shape != (count, len(names)) or not np.isfinite(result).all():
        raise ValueError("FAIL_PHASE4E20_FEATURE_SHAPE")
    return result.astype(np.float64)


def build_training_rows(history: EncodedHistory, cutoff: int, scope: str, training_draws: int = TRAINING_DRAWS) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    start = cutoff - training_draws
    if start < 1200:
        raise ValueError("FAIL_PHASE4E20_TRAINING_START")
    matrices = [number_feature_matrix(history, target, scope) for target in range(start, cutoff)]
    labels_source = history.red if scope == "red" else history.blue
    labels = labels_source[start:cutoff].reshape(-1).astype(np.float64)
    matrix = np.vstack(matrices)
    lineage = {
        "scope": scope,
        "training_target_start_position": start,
        "training_target_end_position_inclusive": cutoff - 1,
        "fit_cutoff_position_exclusive": cutoff,
        "maximum_fit_label_position": cutoff - 1,
        "maximum_feature_source_position": cutoff - 1,
        "training_draws": training_draws,
        "per_number_rows": len(labels),
        "feature_names": list(RED_FEATURE_NAMES if scope == "red" else BLUE_FEATURE_NAMES),
        "feature_matrix_sha256_float64_le": vector_hash(matrix),
        "label_sha256_float64_le": vector_hash(labels),
        "strict_lag_per_training_target": True,
    }
    return matrix, labels, lineage


def _ridge_fit(matrix: np.ndarray, labels: np.ndarray, alpha: float) -> dict[str, object]:
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-10] = 1.0
    standardized = (matrix - means) / scales
    design = np.column_stack((np.ones(len(standardized)), standardized))
    penalty = np.eye(design.shape[1], dtype=np.float64) * float(alpha)
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ labels)
    fitted = np.clip(design @ coefficients, 1e-6, 1.0 - 1e-6)
    return {
        "intercept": float(coefficients[0]),
        "coefficients": coefficients[1:],
        "normalization_mean": means,
        "normalization_scale": scales,
        "training_mse": float(np.mean((fitted - labels) ** 2)),
        "positive_label_rate": float(labels.mean()),
    }


def fit_supervised_heads(rows: Sequence[Mapping[str, object]], history: EncodedHistory, cutoff: int) -> dict[str, object]:
    """Fit the complete preregistered head grid using no label at/after cutoff."""
    red_matrix, red_labels, red_lineage = build_training_rows(history, cutoff, "red")
    blue_matrix, blue_labels, blue_lineage = build_training_rows(history, cutoff, "blue")
    models: dict[str, object] = {}
    for config in HEAD_CONFIGS:
        head_id = str(config["head_id"])
        red_model = _ridge_fit(red_matrix, red_labels, float(config["alpha"]))
        blue_model = _ridge_fit(blue_matrix, blue_labels, float(config["alpha"]))
        models[head_id] = {
            "config": copy.deepcopy(config),
            "red": red_model,
            "blue": blue_model,
            "fit_cutoff_position_exclusive": cutoff,
            "maximum_fit_label_position": cutoff - 1,
        }
    prefix_hash = digest([str(row["source_record_sha256"]) for row in rows[:cutoff]])
    serializable = serialize_models(models)
    return {
        "cutoff_position_exclusive": cutoff,
        "maximum_fit_label_position": cutoff - 1,
        "prefix_input_sha256": prefix_hash,
        "models": models,
        "lineage": {"red": red_lineage, "blue": blue_lineage},
        "coefficient_bundle_sha256": digest(serializable),
    }


def serialize_models(models: Mapping[str, object]) -> dict[str, object]:
    output: dict[str, object] = {}
    for head_id, model in models.items():
        output[head_id] = {
            "config": model["config"],
            "fit_cutoff_position_exclusive": model["fit_cutoff_position_exclusive"],
            "maximum_fit_label_position": model["maximum_fit_label_position"],
        }
        for scope in ("red", "blue"):
            fitted = model[scope]
            output[head_id][scope] = {
                "intercept": fitted["intercept"],
                "coefficients": np.asarray(fitted["coefficients"]).tolist(),
                "coefficient_sha256_float64_le": vector_hash(fitted["coefficients"]),
                "normalization_mean": np.asarray(fitted["normalization_mean"]).tolist(),
                "normalization_scale": np.asarray(fitted["normalization_scale"]).tolist(),
                "normalization_sha256": digest({
                    "mean": np.asarray(fitted["normalization_mean"]).tolist(),
                    "scale": np.asarray(fitted["normalization_scale"]).tolist(),
                }),
                "training_mse": fitted["training_mse"],
                "positive_label_rate": fitted["positive_label_rate"],
            }
    return output


def _project_origin_features(history: EncodedHistory, origin: int, horizon: int, scope: str) -> np.ndarray:
    """Project frozen-origin state; never inspect a row at/after the origin."""
    base = number_feature_matrix(history, origin, scope).copy()
    names = RED_FEATURE_NAMES if scope == "red" else BLUE_FEATURE_NAMES
    index = {name: position for position, name in enumerate(names)}
    # No hypothetical hit is inserted: gaps advance and finite-window trends
    # decay toward the strict-prefix long-window rate.
    base[:, index["gap_scaled_120"]] = np.minimum(1.0, base[:, index["gap_scaled_120"]] + horizon / 120.0)
    base[:, index["gap_deviation_360"]] = np.clip(base[:, index["gap_deviation_360"]] + horizon / 120.0, -1.0, 1.0)
    decay = math.exp(-horizon / 60.0)
    for name in ("trend_30_120", "trend_120_360", "frequency_slope_30_60_120", "transition_360", "graph_neighbor_360" if scope == "red" else "graph_degree_360"):
        base[:, index[name]] *= decay
    seasonal = _fourier(origin + horizon)
    for value, name in zip(seasonal, ("position_sin_3", "position_cos_3", "position_sin_52", "position_cos_52", "position_sin_156", "position_cos_156")):
        base[:, index[name]] = value
    return base


def forecast_heads(fitted: Mapping[str, object], history: EncodedHistory, horizon: int) -> dict[str, dict[str, np.ndarray]]:
    origin = int(fitted["cutoff_position_exclusive"])
    forecasts: dict[str, dict[str, np.ndarray]] = {}
    for head_id, model in fitted["models"].items():
        forecasts[head_id] = {}
        for scope, total in (("red", 6.0), ("blue", 1.0)):
            features = _project_origin_features(history, origin, horizon, scope)
            scope_model = model[scope]
            standardized = (features - scope_model["normalization_mean"]) / scope_model["normalization_scale"]
            raw = np.clip(scope_model["intercept"] + standardized @ scope_model["coefficients"], 1e-4, 1.0)
            probabilities = raw * (total / float(raw.sum()))
            forecasts[head_id][scope] = probabilities
    return forecasts


def _top_red_combinations(red_probabilities: np.ndarray, count: int) -> np.ndarray:
    combos = E19.legal_red_combinations()
    probabilities = np.clip(np.asarray(red_probabilities), 1e-8, 1.0 - 1e-8)
    logits = np.log(probabilities) - np.log1p(-probabilities)
    scores = logits[combos].sum(axis=1)
    ids = np.arange(len(combos), dtype=np.int64)
    return ids[E19._stable_top_indices(scores, ids, count)]


def _round_robin_union(rankings: Sequence[np.ndarray], count: int) -> np.ndarray:
    seen: set[int] = set()
    output: list[int] = []
    depth = 0
    while len(output) < count:
        added = False
        for ranking in rankings:
            if depth < len(ranking):
                value = int(ranking[depth])
                if value not in seen:
                    seen.add(value)
                    output.append(value)
                    added = True
                    if len(output) == count:
                        break
        depth += 1
        if not added and depth >= max(map(len, rankings)):
            break
    if len(output) != count:
        raise ValueError("FAIL_PHASE4E20_RED_UNION_EXHAUSTED")
    return np.asarray(output, dtype=np.int64)


def build_compressed_portfolio(
    forecasts: Mapping[str, Mapping[str, np.ndarray]], spec: Mapping[str, object],
    max_count: int = 100000, ranking_cache: Mapping[str, np.ndarray] | None = None,
    red_order_override: np.ndarray | None = None,
) -> dict[str, object]:
    head_ids = list(map(str, spec["head_ids"]))
    unique_count = int(spec["unique_red_count"])
    per_head_needed = min(len(E19.legal_red_combinations()), max(unique_count * len(head_ids), unique_count + 20000))
    rankings = [
        np.asarray(ranking_cache[head_id])[:per_head_needed]
        if ranking_cache is not None and head_id in ranking_cache
        else _top_red_combinations(forecasts[head_id]["red"], per_head_needed)
        for head_id in head_ids
    ]
    red_order = (
        np.asarray(red_order_override)[:unique_count]
        if red_order_override is not None
        else _round_robin_union(rankings, unique_count)
        if bool(spec["red_union"])
        else rankings[0][:unique_count]
    )
    mean_blue = np.mean([forecasts[head_id]["blue"] for head_id in head_ids], axis=0)
    blue_order = np.lexsort((np.arange(16), -mean_blue))
    ticket_layers: list[np.ndarray] = []
    layer = 0
    remaining = max_count
    combos = E19.legal_red_combinations()
    while remaining > 0 and layer < int(spec["tickets_per_red_cap"]):
        layer_red = red_order[:min(remaining, len(red_order))]
        red_ranks = np.arange(len(layer_red), dtype=np.int64)
        if "latin" in str(spec["blue_allocation"]):
            selected = combos[layer_red].astype(np.int64)
            offsets = (selected.sum(axis=1) + 3 * selected[:, 1] + 5 * selected[:, 4] + red_ranks + layer) % 16
        else:
            offsets = (red_ranks + layer) % 16
        blue_ids = blue_order[offsets]
        ticket_layers.append(layer_red * 16 + blue_ids)
        remaining -= len(layer_red)
        layer += 1
    ids = np.concatenate(ticket_layers).astype(np.int64, copy=False)
    red_multiplicity = np.bincount(ids // 16)
    if len(ids) != max_count or len(np.unique(ids)) != max_count or int(red_multiplicity.max()) > int(spec["tickets_per_red_cap"]):
        raise ValueError("FAIL_PHASE4E20_COMPRESSION_PREFIX")
    prefix_coverage = {}
    for size in SSQ_PARTITION_SIZES:
        prefix_coverage[str(size)] = {
            "unique_red_combinations": min(size, unique_count),
            "unique_complete_tickets": size,
            "maximum_tickets_per_red_combination": 1 if size <= unique_count else 2,
            "blue_numbers_covered": len(np.unique(ids[:size] % 16)),
        }
    return {
        "candidate_id": spec["candidate_id"],
        "ticket_ids": ids,
        "portfolio_sha256_int64_le": vector_hash(ids),
        "red_prefix_sha256_int64_le": vector_hash(ids // 16),
        "nested_prefix": True,
        "ranking_definition": "unique-red-first supervised red ranking with bounded red layers and deterministic predicted-blue allocation",
        "blue_allocation": spec["blue_allocation"],
        "tickets_per_red_cap": spec["tickets_per_red_cap"],
        "prefix_coverage": prefix_coverage,
    }


def e19_raw_control(rows: Sequence[Mapping[str, object]], cutoff: int) -> dict[str, object]:
    snapshot = E19.build_feature_snapshot(rows, cutoff)
    spec = next(spec for spec in E19.CANDIDATE_SPECS if spec["candidate_id"] == "raw_control")
    portfolio = E19.rank_candidate_portfolio(snapshot, spec)
    portfolio["candidate_id"] = "e19_raw_control"
    return portfolio


def random_baseline(cutoff: int) -> dict[str, object]:
    total = math.comb(33, 6) * 16
    seed_material = f"phase4e20-random-baseline-{cutoff}"
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    multiplier = (seed | 1) % total
    while math.gcd(multiplier, total) != 1:
        multiplier = (multiplier + 2) % total
    offset = (seed >> 17) % total
    ids = (multiplier * np.arange(100000, dtype=np.int64) + offset) % total
    return {
        "candidate_id": "deterministic_random", "ticket_ids": ids,
        "portfolio_sha256_int64_le": vector_hash(ids), "seed_material": seed_material,
        "multiplier": multiplier, "offset": offset, "nested_prefix": True,
    }


def acceptance_gate(averages: Sequence[float], threshold: float = 2.0) -> dict[str, object]:
    return E19.acceptance_gate(averages, threshold)


def evaluate_portfolio(ticket_ids: Sequence[int] | np.ndarray, actual_red: Iterable[int], actual_blue: int, partition_sizes: Sequence[int] = SSQ_PARTITION_SIZES) -> dict[str, object]:
    return E19.evaluate_portfolio(ticket_ids, actual_red, actual_blue, partition_sizes)


def aggregate_draw_results(draw_results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return E19.aggregate_draw_results(draw_results)


def verify_isolation() -> dict[str, object]:
    files = []
    for relative, expected in DLT_FROZEN_HASHES.items():
        observed = sha256_file(ROOT / relative)
        files.append({"path": relative, "expected_sha256": expected, "observed_sha256": observed, "matches": observed == expected})
    serving = json.loads(P4E6_DECISION.read_text())
    serving_match = serving.get("serving_release") == EXPECTED_SERVING_RELEASE and serving.get("terminal_status") == EXPECTED_SERVING_STATUS
    result = {
        "game_scope": "ssq_only", "dlt_files": files,
        "all_dlt_hashes_match": all(bool(row["matches"]) for row in files),
        "p4e6_serving_release": serving.get("serving_release"),
        "p4e6_terminal_status": serving.get("terminal_status"),
        "p4e6_serving_identity_matches": serving_match,
    }
    if not result["all_dlt_hashes_match"] or not serving_match:
        raise ValueError("FAIL_PHASE4E20_DLT_OR_SERVING_ISOLATION")
    return result


def _evaluate(rows: Sequence[Mapping[str, object]], target: int, candidate_id: str, portfolio: Mapping[str, object], cutoff: int, horizon: int) -> dict[str, object]:
    return {
        "game": "ssq", "issue": str(rows[target]["issue"]), "target_position": target,
        "origin_cutoff_position_exclusive": cutoff, "horizon": horizon,
        "maximum_feature_and_fit_source_position": cutoff - 1, "strict_lag": True,
        "frozen_window_label_used": False, "candidate_id": candidate_id,
        "portfolio_sha256_int64_le": portfolio["portfolio_sha256_int64_le"],
        "partitions": evaluate_portfolio(portfolio["ticket_ids"], rows[target]["front"], int(rows[target]["back"][0])),
    }


def select_candidate(inner_blocks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Registered maximin selection using only four inner blocks."""
    records: list[dict[str, object]] = []
    order = {str(spec["candidate_id"]): position for position, spec in enumerate(PORTFOLIO_SPECS)}
    for candidate_id, registered_position in order.items():
        cells: list[float] = []
        block_worsts: list[float] = []
        uplifts: list[float] = []
        block_details = []
        for block in inner_blocks:
            candidate = block["summaries"][candidate_id]
            raw = block["summaries"]["e19_raw_control"]
            values = [float(candidate["partitions"][str(size)]["average_prize_yuan"]) for size in SSQ_PARTITION_SIZES]
            raw_values = [float(raw["partitions"][str(size)]["average_prize_yuan"]) for size in SSQ_PARTITION_SIZES]
            worst = min(values)
            median_uplift = statistics.median(value - baseline for value, baseline in zip(values, raw_values))
            cells.extend(values)
            block_worsts.append(worst)
            uplifts.append(median_uplift)
            block_details.append({
                "block": block["block"], "worst_n_average_prize_yuan": worst,
                "binding_n": SSQ_PARTITION_SIZES[values.index(worst)],
                "median_registered_n_uplift_vs_e19_raw_yuan": median_uplift,
            })
        records.append({
            "candidate_id": candidate_id, "registered_order": registered_position + 1,
            "worst_block_worst_n_average_prize_yuan": min(block_worsts),
            "worst_all_inner_cells_average_prize_yuan": min(cells),
            "median_uplift_vs_e19_raw_yuan": statistics.median(uplifts),
            "eligible": True, "blocks": block_details,
        })
    winner = max(records, key=lambda row: (
        float(row["worst_block_worst_n_average_prize_yuan"]),
        float(row["median_uplift_vs_e19_raw_yuan"]),
        -int(row["registered_order"]),
    ))
    return {
        "selection_rule": "maximize the minimum registered-N average over each of four frozen inner blocks; then median registered-N uplift versus raw E19; then registered order",
        "selection_uses_outer_labels": False,
        "selected_candidate": winner["candidate_id"],
        "candidates": records,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def _write_jsonl(path: Path, values: Sequence[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_bytes(value) for value in values))


def _portfolio_metadata(portfolio: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in portfolio.items() if key != "ticket_ids"}


def _origin_run(rows: Sequence[Mapping[str, object]], history: EncodedHistory, cutoff: int, targets: Sequence[int]) -> dict[str, object]:
    fitted = fit_supervised_heads(rows, history, cutoff)
    raw = e19_raw_control(rows, cutoff)
    random = random_baseline(cutoff)
    by_candidate: dict[str, list[dict[str, object]]] = {candidate_id: [] for candidate_id in [*(str(spec["candidate_id"]) for spec in PORTFOLIO_SPECS), *BASELINE_IDS]}
    portfolio_records: list[dict[str, object]] = []
    first_portfolios: dict[str, np.ndarray] = {}
    for horizon, target in enumerate(targets):
        forecasts = forecast_heads(fitted, history, horizon)
        ranking_cache = {
            str(config["head_id"]): _top_red_combinations(
                forecasts[str(config["head_id"])]["red"], 200000,
            )
            for config in HEAD_CONFIGS
        }
        union_order = _round_robin_union(
            [ranking_cache["ridge_a1"], ranking_cache["ridge_a25"]], 100000,
        )
        portfolios = {
            str(spec["candidate_id"]): build_compressed_portfolio(
                forecasts, spec, ranking_cache=ranking_cache,
                red_order_override=union_order if bool(spec["red_union"]) else None,
            )
            for spec in PORTFOLIO_SPECS
        }
        portfolios["e19_raw_control"] = raw
        portfolios["deterministic_random"] = random
        for candidate_id, portfolio in portfolios.items():
            by_candidate[candidate_id].append(_evaluate(rows, target, candidate_id, portfolio, cutoff, horizon))
            metadata = _portfolio_metadata(portfolio)
            portfolio_records.append({
                "origin_cutoff_position_exclusive": cutoff, "target_position": target,
                "horizon": horizon, "candidate_id": candidate_id, **metadata,
            })
            if horizon == 0:
                first_portfolios[candidate_id] = np.asarray(portfolio["ticket_ids"]).copy()
    return {
        "fitted": fitted, "by_candidate": by_candidate,
        "portfolio_records": portfolio_records, "first_portfolios": first_portfolios,
    }


def run_pipeline(output_dir: Path = DEFAULT_OUT) -> dict[str, object]:
    started = time.perf_counter()
    validate_registry()
    isolation_before = verify_isolation()
    rows = load_source_rows()
    outer_start = len(rows) - OUTER_DRAWS
    inner_start = outer_start - INNER_DRAWS
    if (inner_start, outer_start, len(rows)) != (3122, 3362, 3482):
        raise ValueError("FAIL_PHASE4E20_FROZEN_WINDOWS")
    history = encode_history(rows)
    inner_blocks: list[dict[str, object]] = []
    inner_rolling: list[dict[str, object]] = []
    lineage_records: list[dict[str, object]] = []
    coefficient_records: list[dict[str, object]] = []
    portfolio_records: list[dict[str, object]] = []

    for block_index in range(4):
        cutoff = inner_start + block_index * INNER_BLOCK_DRAWS
        targets = list(range(cutoff, cutoff + INNER_BLOCK_DRAWS))
        origin = _origin_run(rows, history, cutoff, targets)
        summaries = {candidate_id: aggregate_draw_results(values) for candidate_id, values in origin["by_candidate"].items()}
        inner_blocks.append({
            "block": block_index + 1, "cutoff_position_exclusive": cutoff,
            "maximum_fit_label_position": cutoff - 1, "first_target_position": targets[0],
            "last_target_position": targets[-1], "draws": 60,
            "block_labels_available_to_features_fit_or_portfolios": False,
            "summaries": summaries,
        })
        for offset, target in enumerate(targets):
            inner_rolling.append({
                "game": "ssq", "block": block_index + 1, "issue": rows[target]["issue"],
                "target_position": target, "origin_cutoff_position_exclusive": cutoff,
                "horizon": offset, "maximum_feature_and_fit_source_position": cutoff - 1,
                "strict_lag": True, "candidates": {
                    candidate_id: values[offset] for candidate_id, values in origin["by_candidate"].items()
                },
            })
        lineage_records.append({
            "cutoff_position_exclusive": cutoff, "prefix_input_sha256": origin["fitted"]["prefix_input_sha256"],
            "coefficient_bundle_sha256": origin["fitted"]["coefficient_bundle_sha256"],
            "lineage": origin["fitted"]["lineage"],
        })
        coefficient_records.append({
            "cutoff_position_exclusive": cutoff, "models": serialize_models(origin["fitted"]["models"]),
        })
        portfolio_records.extend(origin["portfolio_records"])

    selection = select_candidate(inner_blocks)
    selected_id = str(selection["selected_candidate"])
    outer_targets = list(range(outer_start, len(rows)))
    outer = _origin_run(rows, history, outer_start, outer_targets)
    outer_by_candidate = outer["by_candidate"]
    comparison = {
        candidate_id: {
            "calibration": aggregate_draw_results(values[:CALIBRATION_DRAWS]),
            "evaluation": aggregate_draw_results(values[CALIBRATION_DRAWS:]),
            "all_120": aggregate_draw_results(values),
        }
        for candidate_id, values in outer_by_candidate.items()
    }
    outer_rolling = [{
        "game": "ssq", "issue": rows[target]["issue"], "target_position": target,
        "outer_split": "calibration" if offset < 60 else "evaluation",
        "origin_cutoff_position_exclusive": outer_start, "horizon": offset,
        "maximum_feature_and_fit_source_position": outer_start - 1, "strict_lag": True,
        "outer_label_used_for_features_fit_selection_or_portfolios": False,
        "candidates": {candidate_id: values[offset] for candidate_id, values in outer_by_candidate.items()},
    } for offset, target in enumerate(outer_targets)]
    lineage_records.append({
        "cutoff_position_exclusive": outer_start, "prefix_input_sha256": outer["fitted"]["prefix_input_sha256"],
        "coefficient_bundle_sha256": outer["fitted"]["coefficient_bundle_sha256"],
        "lineage": outer["fitted"]["lineage"],
    })
    coefficient_records.append({
        "cutoff_position_exclusive": outer_start, "models": serialize_models(outer["fitted"]["models"]),
    })
    portfolio_records.extend(outer["portfolio_records"])

    selected_summaries = comparison[selected_id]
    split_gates = {
        split: acceptance_gate([summary["partitions"][str(size)]["average_prize_yuan"] for size in SSQ_PARTITION_SIZES])
        for split, summary in selected_summaries.items()
    }
    hard_gate = {
        "required_splits": ["calibration", "evaluation", "all_120"],
        "required_partition_sizes": list(SSQ_PARTITION_SIZES),
        "threshold_yuan_per_ticket": 2.0, "comparison": "strictly_greater_than",
        "split_gates": split_gates, "passed": all(bool(gate["passed"]) for gate in split_gates.values()),
    }
    binding = min(
        ({"split": split, "partition_size": size, "average_prize_yuan": float(summary["partitions"][str(size)]["average_prize_yuan"])}
         for split, summary in selected_summaries.items() for size in SSQ_PARTITION_SIZES),
        key=lambda row: (row["average_prize_yuan"], row["split"], row["partition_size"]),
    )
    decision = "PROMOTION" if hard_gate["passed"] else "NO_PROMOTION"

    # A second construction from the same frozen fit proves deterministic replay.
    replay_forecasts = forecast_heads(outer["fitted"], history, 0)
    selected_spec = next(spec for spec in PORTFOLIO_SPECS if spec["candidate_id"] == selected_id)
    replay_portfolio = build_compressed_portfolio(replay_forecasts, selected_spec)
    replay_match = np.array_equal(replay_portfolio["ticket_ids"], outer["first_portfolios"][selected_id])
    mutated_rows = copy.deepcopy(rows)
    for row in mutated_rows[outer_start:]:
        row["front"] = [1, 2, 3, 4, 5, 6]
        row["back"] = [1]
        row["source_record_sha256"] = "mutated-outer-label"
    mutated_history = encode_history(mutated_rows)
    mutated_fit = fit_supervised_heads(mutated_rows, mutated_history, outer_start)
    mutation_coefficients_match = mutated_fit["coefficient_bundle_sha256"] == outer["fitted"]["coefficient_bundle_sha256"]
    mutated_forecasts = forecast_heads(mutated_fit, mutated_history, 0)
    mutated_portfolio = build_compressed_portfolio(mutated_forecasts, selected_spec)
    mutation_portfolio_match = np.array_equal(mutated_portfolio["ticket_ids"], outer["first_portfolios"][selected_id])
    if not (replay_match and mutation_coefficients_match and mutation_portfolio_match):
        raise ValueError("FAIL_PHASE4E20_REPLAY_OR_LABEL_LEAKAGE")

    isolation_after = verify_isolation()
    runtime = time.perf_counter() - started
    registry_artifact = {
        "artifact_type": "phase4e20_candidate_registry", "game": "ssq",
        "registered_before_inner_labels_evaluated": True,
        "registered_before_outer_labels_evaluated": True,
        "head_configuration_grid": copy.deepcopy(list(HEAD_CONFIGS)),
        "candidate_count": len(PORTFOLIO_SPECS), "candidates": candidate_registry(),
        "baselines": [
            {"candidate_id": "e19_raw_control", "definition": "exact frozen-origin Phase4E19 raw_control"},
            {"candidate_id": "deterministic_random", "definition": "seeded affine permutation baseline"},
        ],
    }
    registry_artifact["registry_sha256"] = digest({"heads": registry_artifact["head_configuration_grid"], "candidates": registry_artifact["candidates"]})
    lineage_artifact = {
        "artifact_type": "phase4e20_model_feature_lineage", "game": "ssq",
        "source_path": str(SOURCE.relative_to(ROOT)), "source_sha256": sha256_file(SOURCE),
        "feature_windows": list(FEATURE_WINDOWS), "training_draws": TRAINING_DRAWS,
        "forecast_projection": "frozen origin; gap advances; trend/transition/graph decay; draw-position Fourier advances; no in-window observation consumed",
        "red_feature_names": list(RED_FEATURE_NAMES), "blue_feature_names": list(BLUE_FEATURE_NAMES),
        "origins": lineage_records, "all_maximum_fit_labels_before_origin": True,
    }
    coefficient_artifact = {
        "artifact_type": "phase4e20_coefficients", "game": "ssq", "origins": coefficient_records,
        "coefficient_artifact_payload_sha256": digest(coefficient_records),
    }
    portfolio_artifact = {
        "artifact_type": "phase4e20_portfolio_hashes", "game": "ssq",
        "deterministic_nested_complete_ticket_orders": True,
        "records": portfolio_records,
        "portfolio_record_payload_sha256": digest(portfolio_records),
    }
    replay_artifact = {
        "artifact_type": "phase4e20_deterministic_replay", "game": "ssq",
        "selected_candidate": selected_id,
        "original_horizon0_portfolio_sha256_int64_le": vector_hash(outer["first_portfolios"][selected_id]),
        "replay_horizon0_portfolio_sha256_int64_le": replay_portfolio["portfolio_sha256_int64_le"],
        "exact_ticket_order_matches": replay_match,
        "outer_label_mutation_coefficient_hash_matches": mutation_coefficients_match,
        "outer_label_mutation_portfolio_matches": mutation_portfolio_match,
        "mutated_positions": [outer_start, len(rows) - 1],
    }
    report = {
        "artifact_type": "phase4e20_ssq_supervised_compression_report", "game": "ssq",
        "status": "RETROSPECTIVE_BACKTEST_ONLY", "decision": decision,
        "promotion_eligible": bool(hard_gate["passed"]),
        "primary_metric": "total known fixed prize yuan / (draws * N complete SSQ tickets)",
        "fixed_prizes_yuan": {str(key): value for key, value in SSQ_FIXED_PRIZES.items()},
        "registered_partition_sizes": list(SSQ_PARTITION_SIZES),
        "frozen_windows": {
            "inner": {"draws": 240, "blocks": 4, "block_draws": 60, "first_position": inner_start, "last_position": outer_start - 1},
            "outer": {"draws": 120, "calibration_draws": 60, "evaluation_draws": 60, "first_position": outer_start, "last_position": len(rows) - 1},
        },
        "candidate_selection": selection, "selected_candidate": selected_id,
        "inner_block_evidence": inner_blocks, "outer_candidate_comparison": comparison,
        "selected_candidate_summaries": selected_summaries, "hard_acceptance_gate": hard_gate,
        "binding_cell": binding, "replay": replay_artifact,
        "dlt_and_serving_isolation_before": isolation_before,
        "dlt_and_serving_isolation_after": isolation_after,
        "phase4e18_or_phase4e19_artifacts_overwritten": False,
        "runtime_seconds": runtime,
    }
    decision_artifact = {
        "artifact_type": "phase4e20_delivery_decision", "game": "ssq", "decision": decision,
        "hard_gate_passed": bool(hard_gate["passed"]), "selected_candidate": selected_id,
        "binding_cell": binding,
        "reason": "every calibration/evaluation/all-120 registered-N average strictly exceeds 2 yuan" if hard_gate["passed"] else "at least one calibration/evaluation/all-120 registered-N average is not strictly greater than 2 yuan",
        "p4e6_serving_changed": False, "serving_release": EXPECTED_SERVING_RELEASE,
        "serving_status": EXPECTED_SERVING_STATUS, "dlt_changed": False,
    }

    _write_json(output_dir / "candidate-registry.json", registry_artifact)
    _write_json(output_dir / "model-feature-lineage.json", lineage_artifact)
    _write_json(output_dir / "coefficients.json", coefficient_artifact)
    _write_json(output_dir / "portfolio-hashes.json", portfolio_artifact)
    _write_jsonl(output_dir / "inner-rolling-report.jsonl", inner_rolling)
    _write_jsonl(output_dir / "outer-rolling-report.jsonl", outer_rolling)
    _write_json(output_dir / "calibration-summary.json", selected_summaries["calibration"])
    _write_json(output_dir / "evaluation-summary.json", selected_summaries["evaluation"])
    _write_json(output_dir / "all-120-summary.json", selected_summaries["all_120"])
    _write_json(output_dir / "replay-evidence.json", replay_artifact)
    _write_json(output_dir / "report.json", report)
    _write_json(output_dir / "delivery/decision.json", decision_artifact)
    baseline_text = (ROOT / "artifacts/phase4e19/dlt-isolation-baseline.sha256").read_text()
    (output_dir / "dlt-isolation-baseline.sha256").write_text(baseline_text)
    manifest_paths = [
        "candidate-registry.json", "model-feature-lineage.json", "coefficients.json",
        "portfolio-hashes.json", "inner-rolling-report.jsonl", "outer-rolling-report.jsonl",
        "calibration-summary.json", "evaluation-summary.json", "all-120-summary.json",
        "replay-evidence.json", "report.json", "dlt-isolation-baseline.sha256",
        "delivery/decision.json",
    ]
    manifest = {
        "artifact_type": "phase4e20_delivery_manifest", "game": "ssq", "decision": decision,
        "files": [{"path": path, "sha256": sha256_file(output_dir / path), "bytes": (output_dir / path).stat().st_size} for path in manifest_paths],
        "dlt_isolation": isolation_after, "runtime_seconds": runtime,
    }
    _write_json(output_dir / "delivery/manifest.json", manifest)
    return {
        "decision": decision, "selected_candidate": selected_id,
        "hard_gate_passed": bool(hard_gate["passed"]), "binding_cell": binding,
        "runtime_seconds": runtime,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    arguments = parser.parse_args(argv)
    print(json.dumps(run_pipeline(arguments.output_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
