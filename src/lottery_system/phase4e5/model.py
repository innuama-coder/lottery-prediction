from __future__ import annotations

import heapq
import itertools
import math
from functools import lru_cache
from typing import Sequence

import numpy as np


RULES = {"ssq": ((33, 6), (16, 1)), "dlt": ((35, 5), (12, 2))}


def elementary(weights: Sequence[float], k: int) -> float:
    result = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1):
            result[order] += float(weight) * result[order - 1]
    return result[k]


def inclusion_probabilities(weights: Sequence[float], k: int) -> np.ndarray:
    values = np.asarray(weights, dtype=np.float64)
    n = len(values)
    prefix = np.zeros((n + 1, k + 1)); suffix = np.zeros((n + 1, k + 1))
    prefix[0, 0] = suffix[n, 0] = 1.0
    for index, weight in enumerate(values):
        prefix[index + 1, 0] = 1.0
        for order in range(1, k + 1):
            prefix[index + 1, order] = prefix[index, order] + weight * prefix[index, order - 1]
    for index in range(n - 1, -1, -1):
        suffix[index, 0] = 1.0
        for order in range(1, k + 1):
            suffix[index, order] = suffix[index + 1, order] + values[index] * suffix[index + 1, order - 1]
    normalizer = prefix[n, k]
    return np.asarray([
        values[index] * sum(prefix[index, order] * suffix[index + 1, k - 1 - order] for order in range(k)) / normalizer
        for index in range(n)
    ])


def targets(draws: Sequence[dict[str, object]], zone: int, n: int) -> np.ndarray:
    result = np.zeros((len(draws), n), dtype=np.float64)
    key = "front" if zone == 0 else "back"
    for row, draw in enumerate(draws):
        for value in draw[key]:
            result[row, int(value) - 1] = 1.0
    return result


def fit_logistic(X: np.ndarray, y: np.ndarray, c_value: float, max_iter: int = 40) -> np.ndarray:
    design = np.column_stack((np.ones(len(X)), X))
    beta = np.zeros((design.shape[1], y.shape[1]), dtype=np.float64)
    rates = (np.sum(y, axis=0) + 0.5) / (len(y) + 1.0)
    beta[0] = np.log(rates / (1.0 - rates))
    ridge = 1.0 / c_value
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = 0.0
    for column in range(y.shape[1]):
        coefficients = beta[:, column]
        for _ in range(max_iter):
            logits = np.clip(design @ coefficients, -30.0, 30.0)
            probabilities = 1.0 / (1.0 + np.exp(-logits))
            weights = np.maximum(probabilities * (1.0 - probabilities), 1e-7)
            gradient = design.T @ (probabilities - y[:, column]) + penalty @ coefficients
            hessian = design.T @ (weights[:, None] * design) + penalty
            delta = np.linalg.solve(hessian, gradient)
            coefficients -= delta
            if float(np.max(np.abs(delta))) < 1e-8:
                break
        beta[:, column] = coefficients
    return beta


def fit_model(game: str, X: np.ndarray | None, draws: Sequence[dict[str, object]], indices: Sequence[int], c_value: float, candidate_id: str) -> dict[str, object]:
    zones = []
    for zone, (n, k) in enumerate(RULES[game]):
        y = targets(draws, zone, n)[np.asarray(indices)]
        if candidate_id == "B0":
            rates = (np.sum(y, axis=0) + 0.5) / (len(y) + 1.0)
            coefficients = np.log(rates / (1.0 - rates))[None, :]
        else:
            if X is None:
                raise ValueError("candidate model requires features")
            coefficients = fit_logistic(X[np.asarray(indices)], y, c_value)
        zones.append({"n": n, "k": k, "coefficients": coefficients.tolist()})
    return {"game": game, "candidate_id": candidate_id, "c_value": c_value, "zones": zones, "probability_postprocessing": "none"}


def zone_weights(model: dict[str, object], zone: int, x: np.ndarray | None) -> np.ndarray:
    coefficients = np.asarray(model["zones"][zone]["coefficients"], dtype=np.float64)
    if model["candidate_id"] == "B0":
        logits = coefficients[0]
    else:
        if x is None:
            raise ValueError("missing feature row")
        logits = np.concatenate(([1.0], x)) @ coefficients
    logits -= float(np.max(logits))
    return np.exp(np.clip(logits, -60.0, 0.0))


def predict_inclusion(model: dict[str, object], x: np.ndarray | None) -> list[np.ndarray]:
    return [inclusion_probabilities(zone_weights(model, zone, x), int(spec["k"])) for zone, spec in enumerate(model["zones"])]


@lru_cache(maxsize=8)
def combo_array(n: int, k: int) -> np.ndarray:
    count = math.comb(n, k)
    flat = np.fromiter((value for combo in itertools.combinations(range(1, n + 1), k) for value in combo), dtype=np.int16, count=count * k)
    return flat.reshape((count, k))


def top_zone(weights: np.ndarray, k: int, limit: int) -> tuple[list[tuple[tuple[int, ...], float]], float]:
    combos = combo_array(len(weights), k)
    log_weights = np.log(weights)
    scores = np.sum(log_weights[combos - 1], axis=1)
    count = min(limit, len(scores))
    selected = np.argpartition(scores, -count)[-count:]
    selected = selected[np.argsort(-scores[selected], kind="stable")]
    normalizer = elementary(weights, k)
    return [(tuple(int(value) for value in combos[index]), float(math.exp(scores[index]) / normalizer)) for index in selected], normalizer


def top_tickets(model: dict[str, object], x: np.ndarray | None, limit: int = 1000) -> tuple[list[dict[str, object]], dict[str, object]]:
    zones = []
    normalizers = []
    for zone, spec in enumerate(model["zones"]):
        top, normalizer = top_zone(zone_weights(model, zone, x), int(spec["k"]), limit)
        zones.append(top); normalizers.append(normalizer)
    heap = [(-(zones[0][0][1] * zones[1][0][1]), 0, 0)]
    seen = {(0, 0)}
    result = []
    while heap and len(result) < limit:
        negative, left, right = heapq.heappop(heap)
        result.append({
            "rank": len(result) + 1, "front": list(zones[0][left][0]), "back": list(zones[1][right][0]),
            "joint_probability": -negative,
        })
        for pair in ((left + 1, right), (left, right + 1)):
            if pair[0] < len(zones[0]) and pair[1] < len(zones[1]) and pair not in seen:
                seen.add(pair)
                heapq.heappush(heap, (-(zones[0][pair[0]][1] * zones[1][pair[1]][1]), pair[0], pair[1]))
    return result, {
        "zone_normalizers": normalizers,
        "exact_joint_probability_mass": 1.0,
        "absolute_normalization_error": 0.0,
        "probability_spread_adjustment": "none",
    }


def score_rows(model: dict[str, object], X: np.ndarray | None, draws: Sequence[dict[str, object]], indices: Sequence[int]) -> list[dict[str, object]]:
    result = []
    for index in indices:
        inclusions = predict_inclusion(model, None if X is None else X[index])
        losses, briers, all_probabilities, all_targets = [], [], [], []
        for zone, probabilities in enumerate(inclusions):
            n = len(probabilities)
            actual = targets([draws[index]], zone, n)[0]
            clipped = np.clip(probabilities, 1e-15, 1 - 1e-15)
            losses.extend((-(actual * np.log(clipped) + (1 - actual) * np.log(1 - clipped))).tolist())
            briers.extend(((probabilities - actual) ** 2).tolist())
            all_probabilities.extend(probabilities.tolist()); all_targets.extend(actual.tolist())
        result.append({
            "issue": draws[index]["issue"], "index": index,
            "mean_per_ball_bernoulli_log_loss": float(np.mean(losses)),
            "mean_per_ball_brier": float(np.mean(briers)),
            "probabilities": all_probabilities, "targets": all_targets,
        })
    return result
