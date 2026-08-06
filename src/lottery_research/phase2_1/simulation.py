"""Phase 2.1 alternative generators, including genuine gradual drift."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from lottery_research.phase2.vectorized import (
    BatchDraws,
    _sample_conditioned,
    _sample_uniform,
    precompute_combination_space,
)


def slow_drift_probabilities(base_probability: float, half_mean_difference: float, draws: int) -> np.ndarray:
    """Return a linear profile with the requested early/late-half mean gap.

    Unlike the Phase 2 generator, every adjacent draw has a different target
    probability when the effect is non-zero.  The registered effect remains the
    statistic's early-half minus late-half population mean difference.
    """
    if draws < 2:
        raise ValueError("slow drift requires at least two draws")
    positions = np.linspace(1.0, -1.0, draws, dtype=np.float64)
    midpoint = draws // 2
    contrast = float(positions[:midpoint].mean() - positions[midpoint:].mean())
    probabilities = base_probability + positions * (half_mean_difference / contrast)
    if np.any((probabilities < 0.0) | (probabilities > 1.0)):
        raise ValueError("slow drift effect produces probability outside [0, 1]")
    return probabilities


def generate_slow_drift_batch(
    rule_map: dict[str, Any],
    *,
    worlds: int,
    draws: int,
    effect: float,
    seed: int,
    issue_ids: Sequence[int | str] | None = None,
) -> BatchDraws:
    if worlds < 1:
        raise ValueError("worlds must be positive")
    space = precompute_combination_space(rule_map)
    rng = np.random.default_rng(seed)
    shape = (worlds, draws)
    profile = slow_drift_probabilities(space.front.null_inclusion_probability, effect, draws)
    front = _sample_conditioned(space.front, shape, space.front.includes_one, profile[None, :], rng)
    back = _sample_uniform(space.back, shape, rng)
    issues = np.arange(draws, dtype=np.int64) if issue_ids is None else np.asarray(issue_ids, dtype=np.int64)
    return BatchDraws(front_numbers=front, back_numbers=back, issue_ids=issues)
