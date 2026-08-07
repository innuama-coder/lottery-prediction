from __future__ import annotations

import itertools
import math
from statistics import fmean
from typing import Any


def reference_probability_checks() -> dict[str, Any]:
    """Independent direct-enumeration reference; it does not import the primary model/evaluator."""
    combinations = list(itertools.combinations(range(1, 6), 2))
    uniform = 1.0 / len(combinations)
    theta = [1.0, 0.2, 0.0, -0.2, -0.5]
    raw = {item: math.exp(sum(theta[index - 1] for index in item)) for item in combinations}
    denominator = math.fsum(raw.values())
    weighted = {item: value / denominator for item, value in raw.items()}
    skills = [math.log(weighted[item] / uniform) for item in ((1, 2), (1, 3), (1, 4), (1, 5))]
    return {
        "m0_probability": uniform,
        "m0_normalization": math.fsum(uniform for _ in combinations),
        "m1_normalization": math.fsum(weighted.values()),
        "known_bias_skill_mean": fmean(skills),
        "known_bias_direction_recovered": math.fsum(skills) > 0.0,
        "inclusion_brier_known_answer": ((0.5 - 1.0) ** 2 + (0.5 - 0.0) ** 2) / 2.0,
    }


def compare_reference(primary: dict[str, Any], tolerance: float = 1e-12) -> list[str]:
    reference = reference_probability_checks()
    pairs = {
        "m0_probability": primary["m0_probability"],
        "m0_normalization": primary["m0_normalization"],
        "m1_normalization": primary["m1_normalization"],
        "known_bias_skill_mean": primary["known_bias_skill"]["mean"],
        "inclusion_brier_known_answer": primary["inclusion_brier_known_answer"],
    }
    differences = [key for key, value in pairs.items() if not math.isclose(float(value), float(reference[key]), rel_tol=0.0, abs_tol=tolerance)]
    if primary["known_bias_direction_recovered"] != reference["known_bias_direction_recovered"]:
        differences.append("known_bias_direction_recovered")
    return differences
