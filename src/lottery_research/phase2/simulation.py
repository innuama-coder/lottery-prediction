from __future__ import annotations

import random
from typing import Any

from .statistics import calculate_statistics


def generate_null_draws(rule_map: dict[str, Any], issues: list[str], rng: random.Random) -> list[dict[str, Any]]:
    space = rule_map["number_space_segments"][0]
    return [
        {
            "issue_id": issue,
            "front_numbers": sorted(rng.sample(range(space["front"]["min"], space["front"]["max"] + 1), space["front"]["draw_count"])),
            "back_numbers": sorted(rng.sample(range(space["back"]["min"], space["back"]["max"] + 1), space["back"]["draw_count"])),
        }
        for issue in issues
    ]


def generate_strong_positive(rule_map: dict[str, Any], issues: list[str], family: str, rng: random.Random) -> list[dict[str, Any]]:
    rows = generate_null_draws(rule_map, issues, rng)
    space = rule_map["number_space_segments"][0]
    if family == "marginal_inclusion":
        for row in rows:
            values = set(row["front_numbers"])
            values.add(1)
            while len(values) > space["front"]["draw_count"]:
                values.remove(max(values))
            row["front_numbers"] = sorted(values)
    elif family == "set_structure":
        top = list(range(space["front"]["max"] - space["front"]["draw_count"] + 1, space["front"]["max"] + 1))
        for row in rows:
            row["front_numbers"] = top
    elif family == "pair_dependence":
        for row in rows:
            values = set(row["front_numbers"])
            values.update((1, 2))
            for value in sorted(values, reverse=True):
                if len(values) <= space["front"]["draw_count"]:
                    break
                if value not in (1, 2):
                    values.remove(value)
            row["front_numbers"] = sorted(values)
    elif family == "temporal_instability":
        midpoint = len(rows) // 2
        for index, row in enumerate(rows):
            forced = 1 if index < midpoint else space["front"]["max"]
            values = set(row["front_numbers"])
            values.add(forced)
            for value in sorted(values, reverse=index < midpoint):
                if len(values) <= space["front"]["draw_count"]:
                    break
                if value != forced:
                    values.remove(value)
            row["front_numbers"] = sorted(values)
    elif family == "cross_zone_dependence":
        back_max = space["back"]["max"]
        back_count = space["back"]["draw_count"]
        midpoint = space["front"]["draw_count"] * (space["front"]["max"] + 1) / 2
        for row in rows:
            high = sum(row["front_numbers"]) >= midpoint
            pool = range(max(1, back_max - back_count + 1), back_max + 1) if high else range(1, back_count + 1)
            row["back_numbers"] = sorted(pool)
    else:
        raise ValueError(f"unknown family: {family}")
    return rows


def simulate_null_statistics(rule_map: dict[str, Any], issues: list[str], replications: int, seed: int) -> list[dict[str, dict[str, float]]]:
    rng = random.Random(seed)
    return [calculate_statistics(generate_null_draws(rule_map, issues, rng), rule_map) for _ in range(replications)]

