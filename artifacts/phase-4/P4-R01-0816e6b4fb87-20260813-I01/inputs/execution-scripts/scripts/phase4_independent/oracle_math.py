from __future__ import annotations

import hashlib
import heapq
import itertools
import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from math import comb
from pathlib import Path
from typing import Any, Iterable, Sequence


DECIMAL_PRECISION = 80
SCALE = 1024
PROBABILITY_QUANTUM = Decimal("1e-50")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decimal_string(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        value = +value
        if not value.is_finite():
            raise ValueError("non-finite Decimal")
        if value == 0:
            return "0"
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered


def probability_string(value: Decimal) -> str:
    if not value.is_finite() or value <= 0:
        raise ValueError("probability must be finite and strictly positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        quantized = value.quantize(PROBABILITY_QUANTUM, rounding=ROUND_HALF_EVEN)
    if quantized <= 0:
        raise ValueError("probability serialized to zero")
    return format(quantized, ".50f")


def normalize_ticks(raw_ticks: Sequence[int], *, bound: int = 4096) -> tuple[int, ...]:
    if not raw_ticks:
        raise ValueError("ticks must not be empty")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_ticks):
        raise TypeError("ticks must be integers")
    anchor = raw_ticks[0]
    normalized = tuple(value - anchor for value in raw_ticks)
    if normalized[0] != 0 or any(value < -bound or value > bound for value in normalized):
        raise ValueError("normalized ticks outside frozen bounds")
    return normalized


def combinations_with_scores(n: int, k: int, ticks: Sequence[int]) -> list[tuple[int, tuple[int, ...]]]:
    if len(ticks) != n or not 0 < k <= n:
        raise ValueError("invalid fixed-cardinality zone")
    return [
        (sum(ticks[number - 1] for number in ticket), ticket)
        for ticket in itertools.combinations(range(1, n + 1), k)
    ]


def zone_histogram_direct(n: int, k: int, ticks: Sequence[int]) -> dict[int, int]:
    histogram = Counter(score for score, _ in combinations_with_scores(n, k, ticks))
    if sum(histogram.values()) != comb(n, k):
        raise AssertionError("direct zone histogram count mismatch")
    return dict(sorted(histogram.items()))


def zone_histogram_dp(n: int, k: int, ticks: Sequence[int]) -> dict[int, int]:
    states: list[Counter[int]] = [Counter() for _ in range(k + 1)]
    states[0][0] = 1
    for tick in ticks:
        for chosen in range(k, 0, -1):
            for score, count in list(states[chosen - 1].items()):
                states[chosen][score + tick] += count
    histogram = dict(sorted(states[k].items()))
    if sum(histogram.values()) != comb(n, k):
        raise AssertionError("DP zone histogram count mismatch")
    return histogram


def joint_histogram(front: dict[int, int], back: dict[int, int]) -> dict[int, int]:
    result: Counter[int] = Counter()
    for front_score, front_count in front.items():
        for back_score, back_count in back.items():
            result[front_score + back_score] += front_count * back_count
    return dict(sorted(result.items()))


def partition_direct(rows: Iterable[tuple[int, tuple[int, ...]]], *, scale: int = SCALE) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        denominator = Decimal(scale)
        return sum((Decimal(score) / denominator).exp() for score, _ in rows)


def partition_from_histogram(histogram: dict[int, int], *, scale: int = SCALE) -> Decimal:
    """Sum direct-enumeration multiplicities without repeating equal Decimal exponentials."""
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        denominator = Decimal(scale)
        return sum(Decimal(count) * (Decimal(score) / denominator).exp() for score, count in histogram.items())


def partition_dp(ticks: Sequence[int], k: int, *, scale: int = SCALE) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        states = [Decimal(0)] * (k + 1)
        states[0] = Decimal(1)
        denominator = Decimal(scale)
        for tick in ticks:
            weight = (Decimal(tick) / denominator).exp()
            for chosen in range(k, 0, -1):
                states[chosen] += states[chosen - 1] * weight
        return +states[k]


def sorted_zone_rows(n: int, k: int, ticks: Sequence[int], *, limit: int = 1000) -> list[tuple[int, tuple[int, ...]]]:
    rows = combinations_with_scores(n, k, ticks)
    rows.sort(key=lambda row: (-row[0], row[1]))
    return rows[:limit]


def top_joint_rows(
    front_rows: Sequence[tuple[int, tuple[int, ...]]],
    back_rows: Sequence[tuple[int, tuple[int, ...]]],
    *,
    limit: int = 1000,
) -> list[tuple[int, tuple[int, ...], tuple[int, ...]]]:
    if not front_rows or not back_rows:
        raise ValueError("empty zone rows")
    row_count = min(limit, len(front_rows))
    column_count = min(limit, len(back_rows))
    heap: list[tuple[int, tuple[int, ...], int, int]] = []
    for front_index in range(row_count):
        score = front_rows[front_index][0] + back_rows[0][0]
        ticket = front_rows[front_index][1] + back_rows[0][1]
        heapq.heappush(heap, (-score, ticket, front_index, 0))
    output: list[tuple[int, tuple[int, ...], tuple[int, ...]]] = []
    while heap and len(output) < limit:
        negative_score, _, front_index, back_index = heapq.heappop(heap)
        front_ticket = front_rows[front_index][1]
        back_ticket = back_rows[back_index][1]
        output.append((-negative_score, front_ticket, back_ticket))
        next_back = back_index + 1
        if next_back < column_count:
            score = front_rows[front_index][0] + back_rows[next_back][0]
            ticket = front_ticket + back_rows[next_back][1]
            heapq.heappush(heap, (-score, ticket, front_index, next_back))
    if len(output) != limit or len({front + back for _, front, back in output}) != limit:
        raise AssertionError("Top-K generation did not produce the requested unique count")
    return output


def rank_bounds(histogram: dict[int, int], score: int) -> tuple[int, int, Decimal]:
    if score not in histogram:
        raise ValueError("score absent from histogram")
    lower = 1 + sum(count for current, count in histogram.items() if current > score)
    upper = sum(count for current, count in histogram.items() if current >= score)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return lower, upper, +((Decimal(lower) + Decimal(upper)) / Decimal(2))


def order_key(score: int) -> str:
    shifted = score + 28672
    if shifted < 0 or shifted > 57344:
        raise ValueError("joint score outside frozen order-key domain")
    return f"P4Q1024-{shifted:05d}"


def zone_inclusion_probabilities(
    n: int,
    k: int,
    ticks: Sequence[int],
) -> list[Decimal]:
    rows = combinations_with_scores(n, k, ticks)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        denominator = Decimal(SCALE)
        weights = [((Decimal(score) / denominator).exp(), ticket) for score, ticket in rows]
        partition = sum(weight for weight, _ in weights)
        inclusions: list[Decimal] = []
        for number in range(1, n + 1):
            numerator = sum(weight for weight, ticket in weights if number in ticket)
            inclusions.append(+(numerator / partition))
        return inclusions


def full_rule_ticks(n: int) -> tuple[int, ...]:
    if n < 8:
        raise ValueError("full-rule fixture requires N >= 8")
    raw = [0] * n
    raw[0:4] = [32, 24, 16, 8]
    raw[n - 4 : n] = [-8, -16, -24, -32]
    return normalize_ticks(raw)


def effect_ticks(q: int, *, sign: int = 1) -> tuple[int, ...]:
    vector = [1, 1, 1, 0, 0, 0, 0, -1, -1, -1]
    return normalize_ticks([sign * q * value for value in vector])


def full_rule_oracle(game: str, rule: dict[str, Any], top_k: Sequence[int]) -> dict[str, Any]:
    front_ticks = full_rule_ticks(rule["front_n"])
    back_ticks = full_rule_ticks(rule["back_n"])
    front_all = combinations_with_scores(rule["front_n"], rule["front_k"], front_ticks)
    back_all = combinations_with_scores(rule["back_n"], rule["back_k"], back_ticks)
    front_hist_direct = dict(sorted(Counter(score for score, _ in front_all).items()))
    back_hist_direct = dict(sorted(Counter(score for score, _ in back_all).items()))
    front_hist_dp = zone_histogram_dp(rule["front_n"], rule["front_k"], front_ticks)
    back_hist_dp = zone_histogram_dp(rule["back_n"], rule["back_k"], back_ticks)
    if front_hist_direct != front_hist_dp or back_hist_direct != back_hist_dp:
        raise AssertionError("independent enumeration and DP histograms differ")
    histogram = joint_histogram(front_hist_direct, back_hist_direct)
    if sum(histogram.values()) != rule["space_size"]:
        raise AssertionError("full-space histogram mismatch")

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        front_z_direct = partition_from_histogram(front_hist_direct)
        back_z_direct = partition_from_histogram(back_hist_direct)
        front_z_dp = partition_dp(front_ticks, rule["front_k"])
        back_z_dp = partition_dp(back_ticks, rule["back_k"])
        z_direct = front_z_direct * back_z_direct
        z_dp = front_z_dp * back_z_dp
        partition_difference = abs(z_direct - z_dp)
        if partition_difference > Decimal("1e-60") * max(abs(z_direct), Decimal(1)):
            raise AssertionError("independent partition paths differ")

        front_sorted = sorted(front_all, key=lambda row: (-row[0], row[1]))[:1000]
        back_sorted = sorted(back_all, key=lambda row: (-row[0], row[1]))[:1000]
        top = top_joint_rows(front_sorted, back_sorted, limit=1000)
        top_rows: list[dict[str, Any]] = []
        cumulative = Decimal(0)
        coverage: dict[int, Decimal] = {}
        for position, (score, front, back) in enumerate(top, start=1):
            probability = (Decimal(score) / Decimal(SCALE)).exp() / z_direct
            cumulative += probability
            lower, upper, midrank = rank_bounds(histogram, score)
            top_rows.append(
                {
                    "display_position": position,
                    "front": list(front),
                    "back": list(back),
                    "joint_tick_score": score,
                    "probability": probability_string(probability),
                    "probability_order_key": order_key(score),
                    "tie_group_size": histogram[score],
                    "tie_rank_lower": lower,
                    "tie_rank_upper": upper,
                    "tie_midrank": decimal_string(midrank),
                }
            )
            if position in top_k:
                coverage[position] = +cumulative

        cells = []
        for k in top_k:
            m0 = Decimal(k) / Decimal(rule["space_size"])
            candidate = coverage[k]
            cells.append(
                {
                    "game": game,
                    "K": k,
                    "m0_coverage": decimal_string(m0),
                    "candidate_coverage": decimal_string(candidate),
                    "difference": decimal_string(candidate - m0),
                    "strictly_better": candidate > m0,
                    "absolute_error_bound": "1e-60",
                }
            )
        top_hash = sha256_bytes(canonical_bytes(top_rows))
        return {
            "game": game,
            "rule_id": rule["rule_id"],
            "space_size": rule["space_size"],
            "front_ticks": list(front_ticks),
            "back_ticks": list(back_ticks),
            "front_combination_count": len(front_all),
            "back_combination_count": len(back_all),
            "front_reachable_scores": len(front_hist_direct),
            "back_reachable_scores": len(back_hist_direct),
            "joint_reachable_scores": len(histogram),
            "histogram_total": sum(histogram.values()),
            "histogram_sha256": sha256_bytes(canonical_bytes([[score, histogram[score]] for score in sorted(histogram)])),
            "top1000_sha256": top_hash,
            "top1000": top_rows,
            "partition_direct": decimal_string(z_direct),
            "partition_dp": decimal_string(z_dp),
            "partition_absolute_difference": decimal_string(partition_difference),
            "normalization_absolute_error_bound": "1e-60",
            "cells": cells,
        }


def m0_real_rule_oracle(game: str, rule: dict[str, Any], top_k: Sequence[int]) -> dict[str, Any]:
    space_size = rule["space_size"]
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        probability = +(Decimal(1) / Decimal(space_size))
        normalization = +(probability * Decimal(space_size))
        normalization_residual = +abs(normalization - Decimal(1))
        if normalization_residual > Decimal("1e-45"):
            raise AssertionError("M0 Decimal80 normalization exceeds 1e-45")
        midrank = +((Decimal(space_size) + Decimal(1)) / Decimal(2))
        coverages = {
            str(k): +(Decimal(k) / Decimal(space_size))
            for k in top_k
        }
    front_iter = itertools.combinations(range(1, rule["front_n"] + 1), rule["front_k"])
    rows: list[dict[str, Any]] = []
    forecast_id = f"oracle-m0-{game}-fixture"
    probability_order_key = order_key(0)
    tie_key = sha256_bytes(f"M0|{probability_order_key}".encode("utf-8"))
    tie_group_id = sha256_bytes(f"{forecast_id}|{tie_key}".encode("utf-8"))
    position = 0
    for front in front_iter:
        for back in itertools.combinations(range(1, rule["back_n"] + 1), rule["back_k"]):
            position += 1
            rows.append(
                {
                    "display_position": position,
                    "front": list(front),
                    "back": list(back),
                    "joint_tick_score": 0,
                    "probability": probability_string(probability),
                    "probability_order_key": probability_order_key,
                    "tie_key": tie_key,
                    "tie_group_id": tie_group_id,
                    "tie_group_size": space_size,
                    "tie_rank_lower": 1,
                    "tie_rank_upper": space_size,
                    "tie_midrank": decimal_string(midrank),
                }
            )
            if position == 1000:
                break
        if position == 1000:
            break
    if len(rows) != 1000:
        raise AssertionError("M0 real-rule Top-1000 incomplete")
    return {
        "game": game,
        "rule_id": rule["rule_id"],
        "model_id": "M0",
        "space_size": space_size,
        "histogram": [[0, space_size]],
        "histogram_total": space_size,
        "full_space_tie_group_count": 1,
        "joint_probability": decimal_string(probability),
        "joint_probability_serialized_50_places": probability_string(probability),
        "normalization": decimal_string(normalization),
        "normalization_absolute_residual": decimal_string(normalization_residual),
        "normalization_tolerance": "1e-45",
        "top_k_coverage": {key: decimal_string(value) for key, value in coverages.items()},
        "top1000": rows,
        "top1000_sha256": sha256_bytes(canonical_bytes(rows)),
    }


def guard_vectors() -> dict[str, Any]:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        minimum_lower_bound = Decimal(-56).exp() / Decimal(21425712)
    tie_fixture = compact_fixture_vector("cross-top-k-tie", [0, 0, 0, 0, 1, 1, 1, 1, 2, 2])
    crossing = []
    for k in (10, 100):
        row = tie_fixture["top_rows"][k - 1]
        crossing.append(
            {
                "K": k,
                "score": row["joint_tick_score"],
                "tie_rank_lower": row["tie_rank_lower"],
                "tie_rank_upper": row["tie_rank_upper"],
                "tie_crosses_cutoff": row["tie_rank_lower"] <= k < row["tie_rank_upper"],
            }
        )
    original = compact_fixture_vector("permutation-original", [0, 4, 1, -2, 3, 0, -1, 2, -3, 1])
    permuted_input = list(reversed(combinations_with_scores(10, 3, (0, 4, 1, -2, 3, 0, -1, 2, -3, 1))))
    permuted_input.sort(key=lambda row: (-row[0], row[1]))
    permuted_top = []
    histogram = zone_histogram_direct(10, 3, (0, 4, 1, -2, 3, 0, -1, 2, -3, 1))
    z = partition_from_histogram(histogram)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for position, (score, ticket) in enumerate(permuted_input[:100], start=1):
            lower, upper, midrank = rank_bounds(histogram, score)
            probability = (Decimal(score) / Decimal(SCALE)).exp() / z
            permuted_top.append({
                "display_position": position,
                "ticket": list(ticket),
                "joint_tick_score": score,
                "probability": probability_string(probability),
                "probability_order_key": order_key(score),
                "tie_group_size": histogram[score],
                "tie_rank_lower": lower,
                "tie_rank_upper": upper,
                "tie_midrank": decimal_string(midrank),
            })
    return {
        "theoretical_minimum_probability": decimal_string(minimum_lower_bound),
        "theoretical_minimum_gt_1e_32": minimum_lower_bound > Decimal("1e-32"),
        "theoretical_minimum_serialized_50_places": probability_string(minimum_lower_bound),
        "cross_top_k_ties": crossing,
        "input_permutation": {
            "canonical_top_sha256": original["top_rows_sha256"],
            "permuted_input_top_sha256": sha256_bytes(canonical_bytes(permuted_top)),
            "stable": original["top_rows_sha256"] == sha256_bytes(canonical_bytes(permuted_top)),
        },
        "nontransitive_approximation": {
            "a": "1",
            "b": "1.0000000000000000000000000000000000000001",
            "c": "1.0000000000000000000000000000000000000002",
            "absolute_pair_gap": "1e-40",
            "absolute_endpoint_gap": "2e-40",
            "exact_key_equivalence_required": True,
        },
    }


def compact_fixture_vector(name: str, ticks: Sequence[int], *, n: int = 10, k: int = 3) -> dict[str, Any]:
    normalized = normalize_ticks(ticks)
    direct = zone_histogram_direct(n, k, normalized)
    dynamic = zone_histogram_dp(n, k, normalized)
    if direct != dynamic:
        raise AssertionError("fixture histogram mismatch")
    all_rows = combinations_with_scores(n, k, normalized)
    ordered = sorted(all_rows, key=lambda row: (-row[0], row[1]))
    z_direct = partition_from_histogram(direct)
    z_dp = partition_dp(normalized, k)
    top_limit = min(100, len(ordered))
    top = []
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for position, (score, ticket) in enumerate(ordered[:top_limit], start=1):
            lower, upper, midrank = rank_bounds(direct, score)
            probability = (Decimal(score) / Decimal(SCALE)).exp() / z_direct
            top.append(
                {
                    "display_position": position,
                    "ticket": list(ticket),
                    "joint_tick_score": score,
                    "probability": probability_string(probability),
                    "probability_order_key": order_key(score),
                    "tie_group_size": direct[score],
                    "tie_rank_lower": lower,
                    "tie_rank_upper": upper,
                    "tie_midrank": decimal_string(midrank),
                }
            )
    return {
        "fixture_id": name,
        "N": n,
        "k": k,
        "ticks": list(normalized),
        "space_size": comb(n, k),
        "histogram": [[score, direct[score]] for score in sorted(direct)],
        "histogram_sha256": sha256_bytes(canonical_bytes([[score, direct[score]] for score in sorted(direct)])),
        "partition_direct": decimal_string(z_direct),
        "partition_dp": decimal_string(z_dp),
        "partition_absolute_difference": decimal_string(abs(z_direct - z_dp)),
        "top_rows": top,
        "top_rows_sha256": sha256_bytes(canonical_bytes(top)),
    }
