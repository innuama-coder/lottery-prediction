from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from .statistics import PRIMARY_FAMILIES, _tercile_cuts


UIntTickets = NDArray[np.uint8]
FloatVector = NDArray[np.float64]


@dataclass(frozen=True)
class ZoneCombinationSpace:
    """An exhaustive, immutable representation of one legal lottery zone."""

    size: int
    draw_count: int
    combinations: UIntTickets
    sums: NDArray[np.int16]
    terciles: NDArray[np.uint8]
    includes_one: NDArray[np.bool_]
    includes_pair_12: NDArray[np.bool_]

    @property
    def null_inclusion_probability(self) -> float:
        return self.draw_count / self.size

    @property
    def null_pair_probability(self) -> float:
        if self.draw_count < 2:
            return 0.0
        return self.draw_count * (self.draw_count - 1) / (self.size * (self.size - 1))

    @property
    def expected_sum(self) -> float:
        return self.draw_count * (self.size + 1) / 2.0


@dataclass(frozen=True)
class GameCombinationSpace:
    game: str
    front: ZoneCombinationSpace
    back: ZoneCombinationSpace


@dataclass(frozen=True)
class BatchDraws:
    """Legal draws with arrays shaped ``(world, draw, numbers_in_zone)``."""

    front_numbers: UIntTickets
    back_numbers: UIntTickets
    issue_ids: NDArray[np.int64]

    def __post_init__(self) -> None:
        front = np.asarray(self.front_numbers)
        back = np.asarray(self.back_numbers)
        issues = np.asarray(self.issue_ids)
        if front.ndim != 3 or back.ndim != 3:
            raise ValueError("ticket arrays must have shape (world, draw, number)")
        if front.shape[:2] != back.shape[:2]:
            raise ValueError("front and back arrays must contain the same worlds and draws")
        if issues.ndim != 1 or issues.shape[0] != front.shape[1]:
            raise ValueError("issue_ids must contain one value for each draw")

    @property
    def world_count(self) -> int:
        return int(self.front_numbers.shape[0])

    @property
    def draw_count(self) -> int:
        return int(self.front_numbers.shape[1])

    def scalar_world(self, index: int) -> list[dict[str, Any]]:
        return [
            {
                "issue_id": str(int(self.issue_ids[draw_index])),
                "front_numbers": self.front_numbers[index, draw_index].astype(int).tolist(),
                "back_numbers": self.back_numbers[index, draw_index].astype(int).tolist(),
            }
            for draw_index in range(self.draw_count)
        ]


def _rule_dimensions(rule_map: Mapping[str, Any]) -> tuple[str, int, int, int, int]:
    segment = rule_map["number_space_segments"][0]
    for zone in ("front", "back"):
        if int(segment[zone]["min"]) != 1:
            raise ValueError("the phase-2 engine requires one-based contiguous number spaces")
    return (
        str(rule_map["game"]),
        int(segment["front"]["max"]),
        int(segment["front"]["draw_count"]),
        int(segment["back"]["max"]),
        int(segment["back"]["draw_count"]),
    )


@lru_cache(maxsize=8)
def _combination_space(size: int, draw_count: int) -> ZoneCombinationSpace:
    total = math.comb(size, draw_count)
    flattened = np.fromiter(
        itertools.chain.from_iterable(itertools.combinations(range(1, size + 1), draw_count)),
        dtype=np.uint8,
        count=total * draw_count,
    )
    combinations = flattened.reshape(total, draw_count)
    sums = combinations.sum(axis=1, dtype=np.int16)
    cuts = _tercile_cuts(size, draw_count)
    terciles = np.where(sums <= cuts[0], 0, np.where(sums <= cuts[1], 1, 2)).astype(np.uint8)
    includes_one = combinations[:, 0] == 1
    includes_pair = (
        np.logical_and(includes_one, np.any(combinations == 2, axis=1))
        if draw_count >= 2
        else np.zeros(total, dtype=np.bool_)
    )
    for array in (combinations, sums, terciles, includes_one, includes_pair):
        array.flags.writeable = False
    return ZoneCombinationSpace(
        size=size,
        draw_count=draw_count,
        combinations=combinations,
        sums=sums,
        terciles=terciles,
        includes_one=includes_one,
        includes_pair_12=includes_pair,
    )


@lru_cache(maxsize=4)
def _game_space(game: str, front_size: int, front_count: int, back_size: int, back_count: int) -> GameCombinationSpace:
    return GameCombinationSpace(
        game=game,
        front=_combination_space(front_size, front_count),
        back=_combination_space(back_size, back_count),
    )


def precompute_combination_space(rule_map: Mapping[str, Any]) -> GameCombinationSpace:
    """Enumerate and cache every legal front/back combination for DLT or SSQ."""

    game, front_size, front_count, back_size, back_count = _rule_dimensions(rule_map)
    if (game, front_size, front_count, back_size, back_count) not in {
        ("dlt", 35, 5, 12, 2),
        ("ssq", 33, 6, 16, 1),
    }:
        raise ValueError(f"unsupported phase-2 game rule: {game}")
    return _game_space(game, front_size, front_count, back_size, back_count)


def precompute_supported_spaces(rule_maps: Sequence[Mapping[str, Any]]) -> dict[str, GameCombinationSpace]:
    """Materialize both frozen games before a long simulation starts."""

    spaces = {str(rule["game"]): precompute_combination_space(rule) for rule in rule_maps}
    if set(spaces) != {"dlt", "ssq"}:
        raise ValueError("the frozen phase-2 workload requires exactly DLT and SSQ")
    return spaces


def _conditional_indices(mask: NDArray[np.bool_], desired: bool) -> NDArray[np.int64]:
    indices = np.flatnonzero(mask if desired else ~mask)
    if not len(indices):
        raise ValueError("requested conditioning event has no legal combinations")
    return indices


def _sample_conditioned(
    space: ZoneCombinationSpace,
    shape: tuple[int, int],
    event_mask: NDArray[np.bool_],
    event_probability: float | FloatVector,
    rng: np.random.Generator,
) -> UIntTickets:
    probabilities = np.asarray(event_probability, dtype=np.float64)
    if np.any((probabilities < 0.0) | (probabilities > 1.0)):
        raise ValueError("the requested effect implies a probability outside [0, 1]")
    occurs = rng.random(shape) < probabilities
    positive = _conditional_indices(event_mask, True)
    negative = _conditional_indices(event_mask, False)
    sampled = np.empty(shape, dtype=np.int64)
    positive_count = int(occurs.sum())
    sampled[occurs] = positive[rng.integers(0, len(positive), size=positive_count)]
    sampled[~occurs] = negative[rng.integers(0, len(negative), size=sampled.size - positive_count)]
    return space.combinations[sampled]


def _sample_uniform(space: ZoneCombinationSpace, shape: tuple[int, int], rng: np.random.Generator) -> UIntTickets:
    return space.combinations[rng.integers(0, len(space.combinations), size=shape)]


def _generate_chunk(
    space: GameCombinationSpace,
    worlds: int,
    draws: int,
    family: str,
    effect: float,
    issue_ids: NDArray[np.int64],
    rng: np.random.Generator,
) -> BatchDraws:
    shape = (worlds, draws)
    front = _sample_uniform(space.front, shape, rng)
    back = _sample_uniform(space.back, shape, rng)

    if family == "null":
        pass
    elif family == "marginal_inclusion":
        probability = space.front.null_inclusion_probability + effect
        front = _sample_conditioned(space.front, shape, space.front.includes_one, probability, rng)
    elif family == "set_structure":
        maximum_sum = int(space.front.sums.max())
        available_shift = maximum_sum - space.front.expected_sum
        mixture = effect / available_shift
        if not 0.0 <= mixture <= 1.0:
            raise ValueError(f"set_structure effect must be between 0 and {available_shift:g}")
        injected = rng.random(shape) < mixture
        highest = np.flatnonzero(space.front.sums == maximum_sum)
        front[injected] = space.front.combinations[highest[rng.integers(0, len(highest), size=int(injected.sum()))]]
    elif family == "pair_dependence":
        probability = space.front.null_pair_probability + effect
        front = _sample_conditioned(space.front, shape, space.front.includes_pair_12, probability, rng)
    elif family == "temporal_instability":
        midpoint = draws // 2
        if midpoint == 0:
            raise ValueError("temporal generation requires at least two draws")
        base = space.front.null_inclusion_probability
        probabilities = np.empty(draws, dtype=np.float64)
        probabilities[:midpoint] = base + effect / 2.0
        probabilities[midpoint:] = base - effect / 2.0
        front = _sample_conditioned(space.front, shape, space.front.includes_one, probabilities[None, :], rng)
    elif family == "cross_zone_dependence":
        if not 0.0 <= effect <= 1.0:
            raise ValueError("cross_zone_dependence q must be between 0 and 1")
        coupled = rng.random(shape) < effect
        if np.any(coupled):
            # The sampled rows are immutable combination values, so recover only
            # their sum bin; this avoids a large reverse-index dictionary.
            front_sums = front.sum(axis=2, dtype=np.int16)
            front_cuts = _tercile_cuts(space.front.size, space.front.draw_count)
            front_bins = np.where(front_sums <= front_cuts[0], 0, np.where(front_sums <= front_cuts[1], 1, 2))
            for bin_id in range(3):
                target = coupled & (front_bins == bin_id)
                count = int(target.sum())
                if count:
                    eligible = np.flatnonzero(space.back.terciles == bin_id)
                    back[target] = space.back.combinations[eligible[rng.integers(0, len(eligible), size=count)]]
    else:
        raise ValueError(f"unknown bias family: {family}")
    return BatchDraws(front_numbers=front, back_numbers=back, issue_ids=issue_ids)


def iter_generated_batches(
    rule_map: Mapping[str, Any],
    *,
    worlds: int,
    draws: int,
    family: str = "null",
    effect: float = 0.0,
    seed: int,
    chunk_worlds: int = 128,
    issue_ids: Sequence[int | str] | None = None,
) -> Iterator[BatchDraws]:
    """Yield deterministic legal worlds without materializing the whole workload."""

    if worlds <= 0 or draws < 2 or chunk_worlds <= 0:
        raise ValueError("worlds/chunk_worlds must be positive and draws must be at least two")
    if family != "null" and family not in PRIMARY_FAMILIES:
        raise ValueError(f"unknown bias family: {family}")
    issues = np.arange(draws, dtype=np.int64) if issue_ids is None else np.asarray(issue_ids, dtype=np.int64)
    if issues.shape != (draws,):
        raise ValueError("issue_ids must contain exactly draws entries")
    rng = np.random.default_rng(seed)
    space = precompute_combination_space(rule_map)
    remaining = worlds
    while remaining:
        current = min(chunk_worlds, remaining)
        yield _generate_chunk(space, current, draws, family, float(effect), issues, rng)
        remaining -= current


def generate_batch(
    rule_map: Mapping[str, Any],
    *,
    worlds: int,
    draws: int,
    family: str = "null",
    effect: float = 0.0,
    seed: int,
    issue_ids: Sequence[int | str] | None = None,
) -> BatchDraws:
    """Convenience API for workloads that fit comfortably in memory."""

    return next(
        iter_generated_batches(
            rule_map,
            worlds=worlds,
            draws=draws,
            family=family,
            effect=effect,
            seed=seed,
            chunk_worlds=worlds,
            issue_ids=issue_ids,
        )
    )


def _ticket_mask(values: UIntTickets, size: int) -> NDArray[np.uint8]:
    worlds, draws, positions = values.shape
    mask = np.zeros((worlds, draws, size), dtype=np.uint8)
    world_index = np.arange(worlds)[:, None]
    draw_index = np.arange(draws)[None, :]
    for position in range(positions):
        mask[world_index, draw_index, values[:, :, position] - 1] = 1
    return mask


def _cross_zone_effect(front: UIntTickets, back: UIntTickets, front_space: ZoneCombinationSpace, back_space: ZoneCombinationSpace) -> FloatVector:
    worlds, draws, _ = front.shape
    front_cuts = _tercile_cuts(front_space.size, front_space.draw_count)
    back_cuts = _tercile_cuts(back_space.size, back_space.draw_count)
    front_sums = front.sum(axis=2, dtype=np.int16)
    back_sums = back.sum(axis=2, dtype=np.int16)
    front_bins = np.where(front_sums <= front_cuts[0], 0, np.where(front_sums <= front_cuts[1], 1, 2))
    back_bins = np.where(back_sums <= back_cuts[0], 0, np.where(back_sums <= back_cuts[1], 1, 2))
    encoded = front_bins * 3 + back_bins
    table = np.stack([(encoded == cell).sum(axis=1) for cell in range(9)], axis=1).reshape(worlds, 3, 3)
    row_totals = table.sum(axis=2)
    col_totals = table.sum(axis=1)
    expected = row_totals[:, :, None] * col_totals[:, None, :] / draws
    contribution = np.divide(
        (table - expected) ** 2,
        expected,
        out=np.zeros_like(expected, dtype=np.float64),
        where=expected != 0,
    )
    phi2 = contribution.sum(axis=(1, 2)) / draws
    correction = 4.0 / (draws - 1) if draws > 1 else 0.0
    corrected_dimension = 3.0 - 4.0 / (draws - 1) if draws > 1 else 1.0
    denominator = max(1e-15, corrected_dimension - 1.0)
    return np.sqrt(np.maximum(0.0, phi2 - correction) / denominator)


def _calculate_chunk(batch: BatchDraws, rule_map: Mapping[str, Any]) -> dict[str, dict[str, FloatVector]]:
    if batch.draw_count < 2:
        raise ValueError("at least two draws are required")
    space = precompute_combination_space(rule_map)
    worlds = batch.world_count
    draws = batch.draw_count
    midpoint = draws // 2
    late_count = draws - midpoint
    marginal = np.zeros(worlds, dtype=np.float64)
    pair = np.zeros(worlds, dtype=np.float64)
    temporal = np.zeros(worlds, dtype=np.float64)
    negative = np.zeros(worlds, dtype=np.float64)
    structure_statistic = np.full(worlds, -np.inf, dtype=np.float64)
    structure_effect = np.zeros(worlds, dtype=np.float64)
    odd_selector = batch.issue_ids % 2 == 1
    even_selector = ~odd_selector

    for values, zone in ((batch.front_numbers, space.front), (batch.back_numbers, space.back)):
        mask = _ticket_mask(values, zone.size)
        rates = mask.sum(axis=1) / draws
        marginal = np.maximum(marginal, np.max(np.abs(rates - zone.null_inclusion_probability), axis=1))

        sums = values.sum(axis=2, dtype=np.int16)
        shift = np.abs(sums.mean(axis=1) - zone.expected_sum)
        population_variance = (zone.size * zone.size - 1) / 12.0
        per_draw_variance = zone.draw_count * (zone.size - zone.draw_count) / (zone.size - 1) * population_variance
        standardized = shift / math.sqrt(per_draw_variance / draws)
        wins = standardized > structure_statistic
        structure_statistic[wins] = standardized[wins]
        structure_effect[wins] = shift[wins]

        if zone.draw_count >= 2:
            cooccurrence = np.einsum("wna,wnb->wab", mask, mask, dtype=np.int32, optimize=True) / draws
            upper = np.triu_indices(zone.size, 1)
            zone_pair = np.max(np.abs(cooccurrence[:, upper[0], upper[1]] - zone.null_pair_probability), axis=1)
            pair = np.maximum(pair, zone_pair)

        early = mask[:, :midpoint].sum(axis=1) / midpoint
        late = mask[:, midpoint:].sum(axis=1) / late_count
        temporal = np.maximum(temporal, np.max(np.abs(early - late), axis=1))

        if np.any(odd_selector) and np.any(even_selector):
            odd = mask[:, odd_selector].sum(axis=1) / int(odd_selector.sum())
            even = mask[:, even_selector].sum(axis=1) / int(even_selector.sum())
            negative = np.maximum(negative, np.max(np.abs(odd - even), axis=1))

    cross = _cross_zone_effect(batch.front_numbers, batch.back_numbers, space.front, space.back)
    return {
        "marginal_inclusion": {"statistic": marginal, "effect": marginal.copy()},
        "set_structure": {"statistic": structure_statistic, "effect": structure_effect},
        "pair_dependence": {"statistic": pair, "effect": pair.copy()},
        "temporal_instability": {"statistic": temporal, "effect": temporal.copy()},
        "cross_zone_dependence": {"statistic": cross, "effect": cross.copy()},
        "negative_control": {"statistic": negative, "effect": negative.copy()},
    }


def calculate_statistics_batch(
    batch: BatchDraws,
    rule_map: Mapping[str, Any],
    *,
    chunk_worlds: int = 128,
) -> dict[str, dict[str, FloatVector]]:
    """Calculate the frozen six statistics, bounded by ``chunk_worlds`` memory."""

    if chunk_worlds <= 0:
        raise ValueError("chunk_worlds must be positive")
    parts: dict[str, dict[str, list[FloatVector]]] = {
        family: {"statistic": [], "effect": []} for family in (*PRIMARY_FAMILIES, "negative_control")
    }
    for start in range(0, batch.world_count, chunk_worlds):
        stop = min(batch.world_count, start + chunk_worlds)
        current = BatchDraws(
            front_numbers=batch.front_numbers[start:stop],
            back_numbers=batch.back_numbers[start:stop],
            issue_ids=batch.issue_ids,
        )
        result = _calculate_chunk(current, rule_map)
        for family in parts:
            for field in ("statistic", "effect"):
                parts[family][field].append(result[family][field])
    return {
        family: {field: np.concatenate(vectors) for field, vectors in fields.items()}
        for family, fields in parts.items()
    }
