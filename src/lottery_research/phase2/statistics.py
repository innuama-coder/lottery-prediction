from __future__ import annotations

import itertools
import math
from collections import Counter
from functools import lru_cache
from typing import Any, Iterable

PRIMARY_FAMILIES = (
    "marginal_inclusion",
    "set_structure",
    "pair_dependence",
    "temporal_instability",
    "cross_zone_dependence",
)


def _zone_specs(rule_map: dict[str, Any]) -> tuple[tuple[str, int, int], ...]:
    space = rule_map["number_space_segments"][0]
    return tuple((name, space[name]["max"], space[name]["draw_count"]) for name in ("front", "back"))


def _subset_sum_distribution(size: int, draw_count: int) -> Counter[int]:
    states: list[Counter[int]] = [Counter() for _ in range(draw_count + 1)]
    states[0][0] = 1
    for value in range(1, size + 1):
        for chosen in range(min(draw_count, value), 0, -1):
            for total, count in list(states[chosen - 1].items()):
                states[chosen][total + value] += count
    return states[draw_count]


@lru_cache(maxsize=None)
def _tercile_cuts(size: int, draw_count: int) -> tuple[int, int]:
    distribution = _subset_sum_distribution(size, draw_count)
    grand = sum(distribution.values())
    cumulative = 0
    cuts: list[int] = []
    targets = (grand / 3.0, 2.0 * grand / 3.0)
    for total in sorted(distribution):
        cumulative += distribution[total]
        while len(cuts) < 2 and cumulative >= targets[len(cuts)]:
            cuts.append(total)
    return cuts[0], cuts[1]


def _bin(value: int, cuts: tuple[int, int]) -> int:
    return 0 if value <= cuts[0] else (1 if value <= cuts[1] else 2)


def _cramers_v(rows: Iterable[tuple[int, int]], n: int) -> float:
    table = [[0, 0, 0] for _ in range(3)]
    for left, right in rows:
        table[left][right] += 1
    row_totals = [sum(row) for row in table]
    col_totals = [sum(table[i][j] for i in range(3)) for j in range(3)]
    chi2 = 0.0
    for i in range(3):
        for j in range(3):
            expected = row_totals[i] * col_totals[j] / n
            if expected:
                chi2 += (table[i][j] - expected) ** 2 / expected
    phi2 = chi2 / n
    correction = 4.0 / (n - 1) if n > 1 else 0.0
    phi2_corrected = max(0.0, phi2 - correction)
    corrected_dimension = 3.0 - 4.0 / (n - 1) if n > 1 else 1.0
    denominator = max(1e-15, corrected_dimension - 1.0)
    return math.sqrt(phi2_corrected / denominator)


def calculate_statistics(draws: list[dict[str, Any]], rule_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    n = len(draws)
    if n < 2:
        raise ValueError("at least two draws are required")
    zones = _zone_specs(rule_map)
    marginal_effect = 0.0
    pair_effect = 0.0
    temporal_effect = 0.0
    structure_effect = 0.0
    structure_statistic = 0.0
    negative_effect = 0.0
    selected: dict[str, tuple[float, str, float]] = {
        "marginal_inclusion": (-1.0, "", 0.0),
        "set_structure": (-1.0, "", 0.0),
        "pair_dependence": (-1.0, "", 0.0),
        "temporal_instability": (-1.0, "", 0.0),
    }
    midpoint = n // 2
    for name, size, count in zones:
        key = f"{name}_numbers"
        inclusion = [0] * (size + 1)
        early = [0] * (size + 1)
        late = [0] * (size + 1)
        odd = [0] * (size + 1)
        even = [0] * (size + 1)
        odd_n = even_n = 0
        pairs: Counter[tuple[int, int]] = Counter()
        sums: list[int] = []
        for index, draw in enumerate(draws):
            numbers = draw[key]
            sums.append(sum(numbers))
            parity_target = odd if int(draw["issue_id"]) % 2 else even
            if int(draw["issue_id"]) % 2:
                odd_n += 1
            else:
                even_n += 1
            for number in numbers:
                inclusion[number] += 1
                (early if index < midpoint else late)[number] += 1
                parity_target[number] += 1
            pairs.update(itertools.combinations(numbers, 2))
        null_rate = count / size
        for x in range(1, size + 1):
            signed = inclusion[x] / n - null_rate
            if abs(signed) > selected["marginal_inclusion"][0]:
                selected["marginal_inclusion"] = (abs(signed), f"{name}:{x}", signed)
        marginal_effect = selected["marginal_inclusion"][0]
        expected_sum = count * (size + 1) / 2.0
        mean_sum = sum(sums) / n
        shift = abs(mean_sum - expected_sum)
        population_variance = (size * size - 1) / 12.0
        per_draw_variance = count * (size - count) / (size - 1) * population_variance
        zone_statistic = shift / math.sqrt(per_draw_variance / n)
        if zone_statistic > selected["set_structure"][0]:
            selected["set_structure"] = (zone_statistic, name, mean_sum - expected_sum)
            structure_statistic = zone_statistic
            structure_effect = shift
        if count >= 2:
            null_pair = count * (count - 1) / (size * (size - 1))
            for a in range(1, size):
                for b in range(a + 1, size + 1):
                    signed = pairs[(a, b)] / n - null_pair
                    if abs(signed) > selected["pair_dependence"][0]:
                        selected["pair_dependence"] = (abs(signed), f"{name}:{a}-{b}", signed)
            pair_effect = selected["pair_dependence"][0]
        late_n = n - midpoint
        for x in range(1, size + 1):
            signed = early[x] / midpoint - late[x] / late_n
            if abs(signed) > selected["temporal_instability"][0]:
                selected["temporal_instability"] = (abs(signed), f"{name}:{x}", signed)
        temporal_effect = selected["temporal_instability"][0]
        if odd_n and even_n:
            negative_effect = max(negative_effect, max(abs(odd[x] / odd_n - even[x] / even_n) for x in range(1, size + 1)))
    front = zones[0]
    back = zones[1]
    front_cuts = _tercile_cuts(front[1], front[2])
    back_cuts = _tercile_cuts(back[1], back[2])
    cross_effect = _cramers_v(((_bin(sum(row["front_numbers"]), front_cuts), _bin(sum(row["back_numbers"]), back_cuts)) for row in draws), n)
    front_sums = [sum(row["front_numbers"]) for row in draws]
    back_sums = [sum(row["back_numbers"]) for row in draws]
    front_mean = sum(front_sums) / n
    back_mean = sum(back_sums) / n
    cross_signed = sum((left - front_mean) * (right - back_mean) for left, right in zip(front_sums, back_sums)) / n
    return {
        "marginal_inclusion": {"statistic": marginal_effect, "effect": marginal_effect, "selected_component": selected["marginal_inclusion"][1], "signed_effect": selected["marginal_inclusion"][2]},
        "set_structure": {"statistic": structure_statistic, "effect": structure_effect, "selected_component": selected["set_structure"][1], "signed_effect": selected["set_structure"][2]},
        "pair_dependence": {"statistic": pair_effect, "effect": pair_effect, "selected_component": selected["pair_dependence"][1], "signed_effect": selected["pair_dependence"][2]},
        "temporal_instability": {"statistic": temporal_effect, "effect": temporal_effect, "selected_component": selected["temporal_instability"][1], "signed_effect": selected["temporal_instability"][2]},
        "cross_zone_dependence": {"statistic": cross_effect, "effect": cross_effect, "selected_component": "zone_sum_covariance", "signed_effect": cross_signed},
        "negative_control": {"statistic": negative_effect, "effect": negative_effect},
    }


def holm_adjust(raw: dict[str, float]) -> dict[str, float]:
    ordered = sorted(raw.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (key, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * value))
        adjusted[key] = running
    return adjusted
