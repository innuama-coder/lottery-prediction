from __future__ import annotations

import math
from typing import Any


def independent_reference_statistics(draws: list[dict[str, Any]], rule_map: dict[str, Any]) -> dict[str, float]:
    """Small independent path for four critical deterministic effects.

    It intentionally does not import or call statistics.calculate_statistics.
    """
    n = len(draws)
    space = rule_map["number_space_segments"][0]
    result: dict[str, float] = {}
    for zone in ("front", "back"):
        maximum = space[zone]["max"]
        count = space[zone]["draw_count"]
        key = f"{zone}_numbers"
        frequencies = {value: 0 for value in range(1, maximum + 1)}
        total_sum = 0
        for row in draws:
            total_sum += sum(row[key])
            for value in row[key]:
                frequencies[value] += 1
        expected_rate = count / maximum
        result[f"{zone}_marginal_effect"] = max(abs(frequencies[value] / n - expected_rate) for value in frequencies)
        structure_effect = abs(total_sum / n - count * (maximum + 1) / 2.0)
        population_variance = (maximum * maximum - 1) / 12.0
        per_draw_variance = count * (maximum - count) / (maximum - 1) * population_variance
        result[f"{zone}_structure_effect"] = structure_effect
        result[f"{zone}_structure_statistic"] = structure_effect / math.sqrt(per_draw_variance / n)
    result["marginal_inclusion"] = max(result["front_marginal_effect"], result["back_marginal_effect"])
    structure_winner = max(("front", "back"), key=lambda zone: result[f"{zone}_structure_statistic"])
    result["set_structure"] = result[f"{structure_winner}_structure_effect"]
    return result
