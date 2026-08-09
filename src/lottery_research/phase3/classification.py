from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from statistics import fmean
from typing import Mapping, Sequence


PRACTICAL_SKILL_DELTA = math.log(1.001)


@dataclass(frozen=True)
class BootstrapEvidence:
    observed_mean: float
    lower: float
    upper: float
    raw_p: float
    block_length: int
    replicates: int


def _selection(seed: str, replicate_index: int, block_index: int, block_count: int) -> int:
    payload = f"{seed}|{replicate_index}|{block_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % block_count


def moving_block_evidence(
    values: Sequence[float],
    *,
    seed: str,
    replicates: int = 10_000,
    delta: float = PRACTICAL_SKILL_DELTA,
) -> BootstrapEvidence:
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("bootstrap values must be finite and nonempty")
    if replicates < 20:
        raise ValueError("bootstrap requires at least 20 replications")
    n = len(values)
    block_length = min(n, max(5, math.ceil(n ** (1.0 / 3.0))))
    blocks = [tuple(values[start:start + block_length]) for start in range(n - block_length + 1)]
    blocks_per_replication = math.ceil(n / block_length)
    observed = fmean(values)
    centered = tuple(value - observed + delta for value in values)
    centered_blocks = [tuple(centered[start:start + block_length]) for start in range(n - block_length + 1)]
    sampled_means: list[float] = []
    null_means: list[float] = []
    for replicate_index in range(replicates):
        sampled: list[float] = []
        null_sampled: list[float] = []
        for block_index in range(blocks_per_replication):
            selected = _selection(seed, replicate_index, block_index, len(blocks))
            sampled.extend(blocks[selected])
            null_sampled.extend(centered_blocks[selected])
        sampled_means.append(fmean(sampled[:n]))
        null_means.append(fmean(null_sampled[:n]))
    ordered = sorted(sampled_means)
    lower_rank = math.ceil(0.05 * replicates)
    upper_rank = math.ceil(0.95 * replicates)
    raw_p = (1 + sum(value >= observed for value in null_means)) / (replicates + 1)
    return BootstrapEvidence(
        observed_mean=observed,
        lower=ordered[lower_rank - 1],
        upper=ordered[upper_rank - 1],
        raw_p=raw_p,
        block_length=block_length,
        replicates=replicates,
    )


def holm_adjust(raw: Mapping[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in raw.values()):
        raise ValueError("Holm p-values must be finite and in [0,1]")
    ordered = sorted(raw.items(), key=lambda item: (item[1], item[0][0], item[0][1]))
    adjusted: dict[tuple[str, str], float] = {}
    prefix_max = 0.0
    count = len(ordered)
    for rank, (key, value) in enumerate(ordered, start=1):
        prefix_max = max(prefix_max, (count - rank + 1) * value)
        adjusted[key] = min(1.0, prefix_max)
    return adjusted


def classify_model(
    *,
    opened: bool,
    integrity_passed: bool,
    games: Mapping[str, Mapping[str, float | bool]],
    delta: float = PRACTICAL_SKILL_DELTA,
    alpha: float = 0.05,
) -> str:
    if not opened:
        return "not_opened"
    if not integrity_passed:
        return "rejected"
    if not games:
        raise ValueError("opened model classification requires game evidence")
    shadow = all(
        bool(row["non_bootstrap_gates_passed"])
        and float(row["lower"]) > delta
        and float(row["holm_adjusted_p"]) <= alpha
        for row in games.values()
    )
    if shadow:
        return "shadow_candidate"
    uncertain = any(
        bool(row["non_bootstrap_gates_passed"])
        and float(row["lower"]) <= delta < float(row["upper"])
        for row in games.values()
    )
    return "indeterminate" if uncertain else "archived"


def summarize_phase(classifications: Sequence[str]) -> str:
    if "shadow_candidate" in classifications:
        return "shadow_candidate"
    if "indeterminate" in classifications:
        return "indeterminate"
    return "no_shadow_candidate"
