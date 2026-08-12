from __future__ import annotations

import hashlib
import heapq
import re
from decimal import Decimal, localcontext
from math import comb
from typing import Iterable, Mapping, Sequence

from .identity import validate_stable_id
from .probability import DECIMAL_PRECISION, P4E1Distribution, ZoneDistribution, decimal_probability
from .rules import RuleViolation, canonical_ticket


class RankingViolation(RuleViolation):
    pass


def probability_order_key(score: int) -> str:
    if type(score) is not int or score < -28672 or score > 28672:
        raise RankingViolation("probability order score is outside [-28672,28672]")
    return f"P4Q1024-{score + 28672:05d}"


def tie_key(model_contract_id: str, order_key: str) -> str:
    try:
        validate_stable_id(model_contract_id, "model contract identity")
    except ValueError as exc:
        raise RankingViolation(str(exc)) from exc
    if type(order_key) is not str or not re.fullmatch(r"P4Q1024-\d{5}", order_key):
        raise RankingViolation("probability order key is not canonical")
    if order_key != probability_order_key(int(order_key[-5:]) - 28672):
        raise RankingViolation("probability order key is not canonical")
    return hashlib.sha256(f"{model_contract_id}|{order_key}".encode("utf-8")).hexdigest()


def tie_group_id(forecast_id: str, key: str) -> str:
    try:
        validate_stable_id(forecast_id, "forecast identity")
    except ValueError as exc:
        raise RankingViolation(str(exc)) from exc
    if type(key) is not str or not re.fullmatch(r"[0-9a-f]{64}", key):
        raise RankingViolation("tie-group identity inputs are invalid")
    return hashlib.sha256(f"{forecast_id}|{key}".encode("utf-8")).hexdigest()


def zone_histogram(ticks: Sequence[int], k: int) -> dict[int, int]:
    if type(k) is not int or not ticks or k <= 0 or k > len(ticks):
        raise RankingViolation("histogram cardinality must be a positive integer within a nonempty tick vector")
    if any(type(tick) is not int for tick in ticks):
        raise RankingViolation("histogram ticks must be integers")
    states: list[dict[int, int]] = [{0: 1}] + [{} for _ in range(k)]
    for position, tick in enumerate(ticks, start=1):
        for chosen in range(min(position, k), 0, -1):
            for previous, count in tuple(states[chosen - 1].items()):
                score = previous + tick
                states[chosen][score] = states[chosen].get(score, 0) + count
    if not states[k]:
        raise RankingViolation("zone histogram is empty")
    return dict(sorted(states[k].items()))


def rank_histogram(model: P4E1Distribution) -> dict[int, int]:
    front = zone_histogram(model.front.ticks, model.rule.front_k)
    back = zone_histogram(model.back.ticks, model.rule.back_k)
    joint: dict[int, int] = {}
    for front_score, front_count in front.items():
        for back_score, back_count in back.items():
            score = front_score + back_score
            joint[score] = joint.get(score, 0) + front_count * back_count
    if sum(joint.values()) != model.rule.space_size:
        raise RankingViolation("joint histogram count differs from the legal full space")
    return dict(sorted(joint.items()))


def rank_bands(histogram: Mapping[int, int]) -> dict[int, tuple[int, int, str]]:
    if not histogram or any(type(score) is not int or type(count) is not int or count <= 0 for score, count in histogram.items()):
        raise RankingViolation("rank histogram is invalid")
    result: dict[int, tuple[int, int, str]] = {}
    before = 0
    for score in sorted(histogram, reverse=True):
        lower = before + 1
        upper = before + histogram[score]
        numerator = lower + upper
        midrank = str(numerator // 2) if numerator % 2 == 0 else f"{numerator // 2}.5"
        result[score] = (lower, upper, midrank)
        before = upper
    return result


def top_zone_combinations(zone: ZoneDistribution, limit: int) -> list[tuple[int, tuple[int, ...]]]:
    if type(limit) is not int or limit <= 0:
        raise RankingViolation("Top-K limit must be positive")
    dp: list[list[tuple[int, tuple[int, ...]]]] = [[(0, ())]] + [[] for _ in range(zone.k)]
    for number, tick in enumerate(zone.ticks, start=1):
        for chosen in range(min(number, zone.k), 0, -1):
            included = [(score + tick, ticket + (number,)) for score, ticket in dp[chosen - 1]]
            candidates = dp[chosen] + included
            candidates.sort(key=lambda row: (-row[0], row[1]))
            dp[chosen] = candidates[:limit]
    expected = min(limit, comb(len(zone.ticks), zone.k))
    if len(dp[zone.k]) != expected:
        raise RankingViolation("zone Top-K construction is incomplete")
    return dp[zone.k]


def _joint_top(
    front: Sequence[tuple[int, tuple[int, ...]]],
    back: Sequence[tuple[int, tuple[int, ...]]],
    limit: int,
) -> list[tuple[int, tuple[int, ...], tuple[int, ...]]]:
    if not front or not back:
        raise RankingViolation("joint Top-K requires both zone prefixes")
    heap: list[tuple[int, tuple[int, ...], tuple[int, ...], int, int]] = []
    visited: set[tuple[int, int]] = set()

    def push(i: int, j: int) -> None:
        if i >= len(front) or j >= len(back) or (i, j) in visited:
            return
        visited.add((i, j))
        front_score, front_ticket = front[i]
        back_score, back_ticket = back[j]
        heapq.heappush(heap, (-(front_score + back_score), front_ticket, back_ticket, i, j))

    push(0, 0)
    rows: list[tuple[int, tuple[int, ...], tuple[int, ...]]] = []
    while heap and len(rows) < limit:
        negative_score, front_ticket, back_ticket, i, j = heapq.heappop(heap)
        rows.append((-negative_score, front_ticket, back_ticket))
        push(i + 1, j)
        push(i, j + 1)
    if len(rows) != limit:
        raise RankingViolation("joint Top-K did not produce the requested number of tickets")
    return rows


def top1000(
    model: P4E1Distribution,
    *,
    forecast_id: str | None = None,
    limit: int = 1000,
) -> list[dict[str, object]]:
    if limit != 1000:
        raise RankingViolation("Phase 4 product forecast size must be exactly 1000")
    front = top_zone_combinations(model.front, limit)
    back = top_zone_combinations(model.back, limit)
    tickets = _joint_top(front, back, limit)
    histogram = rank_histogram(model)
    bands = rank_bands(histogram)
    tie_digest_preimages: dict[str, tuple[str, str]] = {}
    group_digest_preimages: dict[str, tuple[str, str, str]] = {}
    rows: list[dict[str, object]] = []
    for position, (score, front_ticket, back_ticket) in enumerate(tickets, start=1):
        lower, upper, midrank = bands[score]
        order_key = probability_order_key(score)
        row: dict[str, object] = {
            "front": list(front_ticket),
            "back": list(back_ticket),
            "display_position": position,
            "joint_tick_score": score,
            "probability": decimal_probability(model.probability_for_score(score)),
            "probability_order_key": order_key,
            "tie_group_size": histogram[score],
            "tie_midrank": midrank,
            "tie_rank_lower": lower,
            "tie_rank_upper": upper,
        }
        if forecast_id is not None:
            key = tie_key(model.model_contract_id, order_key)
            tie_preimage = (model.model_contract_id, order_key)
            if key in tie_digest_preimages and tie_digest_preimages[key] != tie_preimage:
                raise RankingViolation("tie-key digest collision across distinct exact order keys")
            tie_digest_preimages[key] = tie_preimage
            group = tie_group_id(forecast_id, key)
            group_preimage = (forecast_id, model.model_contract_id, order_key)
            if group in group_digest_preimages and group_digest_preimages[group] != group_preimage:
                raise RankingViolation("tie-group digest collision across distinct exact order keys")
            group_digest_preimages[group] = group_preimage
            row["tie_key"] = key
            row["tie_group_id"] = group
        rows.append(row)
    if len({(tuple(row["front"]), tuple(row["back"])) for row in rows}) != limit:
        raise RankingViolation("Top-1000 contains duplicate tickets")
    if rows != sorted(rows, key=lambda row: (-row["joint_tick_score"], tuple(row["front"]), tuple(row["back"]))):  # type: ignore[operator]
        raise RankingViolation("Top-1000 order is not canonical")
    return rows


def zone_top_rows(zone: ZoneDistribution, *, limit: int) -> list[dict[str, object]]:
    histogram = zone_histogram(zone.ticks, zone.k)
    bands = rank_bands(histogram)
    rows: list[dict[str, object]] = []
    for position, (score, ticket) in enumerate(top_zone_combinations(zone, limit), start=1):
        lower, upper, midrank = bands[score]
        rows.append({
            "display_position": position,
            "joint_tick_score": score,
            "probability": decimal_probability(zone.probability_for_score(score)),
            "probability_order_key": probability_order_key(score),
            "ticket": list(ticket),
            "tie_group_size": histogram[score],
            "tie_midrank": midrank,
            "tie_rank_lower": lower,
            "tie_rank_upper": upper,
        })
    return rows


def top_k_coverage(
    model: P4E1Distribution,
    rows: Sequence[Mapping[str, object]],
    k_values: Iterable[int],
    *,
    forecast_id: str | None = None,
) -> dict[str, str]:
    if not isinstance(rows, Sequence) or len(rows) != 1000 or any(not isinstance(row, Mapping) for row in rows):
        raise RankingViolation("coverage requires a complete cached deterministic Top-1000 prefix")
    histogram = rank_histogram(model)
    bands = rank_bands(histogram)
    required = {
        "front", "back", "display_position", "joint_tick_score", "probability", "probability_order_key",
        "tie_group_size", "tie_midrank", "tie_rank_lower", "tie_rank_upper",
    }
    if forecast_id is not None:
        required |= {"tie_key", "tie_group_id"}
    seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    previous_order: tuple[int, tuple[int, ...], tuple[int, ...]] | None = None
    for position, row in enumerate(rows, start=1):
        if set(row) != required:
            raise RankingViolation("cached Top-1000 row shape mismatch")
        integer_fields = ("display_position", "joint_tick_score", "tie_group_size", "tie_rank_lower", "tie_rank_upper")
        if any(type(row[field]) is not int for field in integer_fields) or row["display_position"] != position:
            raise RankingViolation("cached Top-1000 integer identity is noncanonical")
        if type(row["front"]) is not list or type(row["back"]) is not list:
            raise RankingViolation("cached Top-1000 ticket zones must be canonical arrays")
        front, back = canonical_ticket(model.rule.game, row["front"], row["back"])
        ticket = (front, back)
        if ticket in seen:
            raise RankingViolation("cached Top-1000 contains a duplicate ticket")
        seen.add(ticket)
        score = model.joint_score(front, back)
        if row["joint_tick_score"] != score:
            raise RankingViolation("cached Top-1000 score does not match its legal ticket")
        order_key = probability_order_key(score)
        if type(row["probability_order_key"]) is not str or row["probability_order_key"] != order_key:
            raise RankingViolation("cached Top-1000 order key mismatch")
        probability = decimal_probability(model.probability_for_score(score))
        if type(row["probability"]) is not str or row["probability"] != probability:
            raise RankingViolation("cached Top-1000 probability mismatch")
        lower, upper, midrank = bands[score]
        if (
            row["tie_group_size"] != histogram[score]
            or row["tie_rank_lower"] != lower
            or row["tie_rank_upper"] != upper
            or type(row["tie_midrank"]) is not str
            or row["tie_midrank"] != midrank
        ):
            raise RankingViolation("cached Top-1000 exact tie/rank mismatch")
        if forecast_id is not None:
            key = tie_key(model.model_contract_id, order_key)
            group = tie_group_id(forecast_id, key)
            if type(row["tie_key"]) is not str or type(row["tie_group_id"]) is not str or row["tie_key"] != key or row["tie_group_id"] != group:
                raise RankingViolation("cached Top-1000 tie digest identity mismatch")
        current_order = (-score, front, back)
        if previous_order is not None and current_order < previous_order:
            raise RankingViolation("cached Top-1000 canonical order mismatch")
        previous_order = current_order
    expected = top1000(model, forecast_id=forecast_id)
    if list(rows) != expected:
        raise RankingViolation("cached Top-1000 row identity, legality, rank, tie, order, or uniqueness mismatch")
    result: dict[str, str] = {}
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for k in k_values:
            if type(k) is not int or k <= 0 or k > len(rows):
                raise RankingViolation("coverage K is outside the supplied deterministic prefix")
            total = sum((model.probability_for_score(row["joint_tick_score"]) for row in rows[:k]), Decimal(0))  # type: ignore[arg-type]
            result[str(k)] = format(+total, "f")
    return result
