from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean
from typing import Sequence

from .probability import FixedCardinalityDistribution, posterior_theta


@dataclass(frozen=True)
class InnerFold:
    training: tuple[object, ...]
    target: object


@dataclass(frozen=True)
class OuterFold:
    training: tuple[object, ...]
    target: object
    inner: tuple[InnerFold, ...]


def rolling_folds(targets: Sequence[object], minimum_training: int, inner_folds: int) -> list[OuterFold]:
    if minimum_training < 2 or inner_folds < 1:
        raise ValueError("rolling fold parameters are invalid")
    if len(set(targets)) != len(targets):
        raise ValueError("outer targets must be unique")
    folds = []
    for outer_index in range(minimum_training, len(targets)):
        training = tuple(targets[:outer_index])
        candidate_indices = range(max(1, outer_index - inner_folds), outer_index)
        inner = tuple(InnerFold(tuple(targets[:index]), targets[index]) for index in candidate_indices)
        folds.append(OuterFold(training=training, target=targets[outer_index], inner=inner))
    return folds


def joint_log_score(probability: float) -> float:
    if not math.isfinite(probability) or probability <= 0.0 or probability > 1.0:
        raise ValueError("joint probability must be finite and in (0, 1]")
    return -math.log(probability)


def relative_joint_log_score_skill(m0_probability: float, challenger_probability: float) -> float:
    return joint_log_score(m0_probability) - joint_log_score(challenger_probability)


def inclusion_brier(probabilities: Sequence[float], observed: set[int]) -> float:
    if not probabilities:
        raise ValueError("inclusion probabilities must not be empty")
    if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in probabilities):
        raise ValueError("illegal inclusion probability")
    if any(value < 1 or value > len(probabilities) for value in observed):
        raise ValueError("observed inclusion index is illegal")
    return fmean((probability - (1.0 if index in observed else 0.0)) ** 2 for index, probability in enumerate(probabilities, start=1))


def calibration_error(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("calibration inputs must be nonempty and aligned")
    if any(outcome not in (0, 1) for outcome in outcomes):
        raise ValueError("calibration outcomes must be binary")
    return abs(fmean(probabilities) - fmean(outcomes))


def summarize_skill(values: Sequence[float]) -> dict[str, float | int]:
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("skill values must be finite and nonempty")
    mean = fmean(values)
    variance = fmean((value - mean) ** 2 for value in values)
    return {
        "count": len(values),
        "mean": mean,
        "minimum": min(values),
        "maximum": max(values),
        "population_standard_deviation": math.sqrt(variance),
    }


def evaluate_rolling_subsets(
    draws: Sequence[Sequence[int]],
    *,
    size: int,
    cardinality: int,
    minimum_training: int,
    inner_folds: int,
    shrinkage: float,
) -> list[dict[str, object]]:
    """Synthetic/offline rolling evaluator; each target is scored exactly once."""
    folds = rolling_folds(list(range(len(draws))), minimum_training, inner_folds)
    results = []
    m0 = FixedCardinalityDistribution.uniform(size, cardinality)
    for fold in folds:
        training = [draws[int(index)] for index in fold.training]
        theta = posterior_theta(training, size, cardinality, shrinkage)
        m1 = FixedCardinalityDistribution.from_theta(theta, cardinality)
        target = tuple(draws[int(fold.target)])
        m0_probability = m0.probability(target)
        m1_probability = m1.probability(target)
        results.append({
            "target_index": fold.target,
            "training_indices": fold.training,
            "inner_target_indices": tuple(inner.target for inner in fold.inner),
            "m0_probability": m0_probability,
            "m1_probability": m1_probability,
            "relative_skill_vs_M0": relative_joint_log_score_skill(m0_probability, m1_probability),
        })
    return results
