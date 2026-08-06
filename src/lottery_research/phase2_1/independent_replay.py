"""Independent Phase 2.1 replay engine.

This module intentionally does not import ``phase2_1.workflow``.  It rebuilds
the simulation grid from frozen inputs through a separately maintained call
graph so replay independence is more than a seed change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import numpy as np

from lottery_research.phase2.draws import load_frozen_draws
from lottery_research.phase2.intervals import clopper_pearson
from lottery_research.phase2.research_engine import (
    domain_seed,
    empirical_p,
    holm_adjust_matrix,
    read_array_bundle,
    scenario_generator_effect,
    simulate_prefix_statistics,
)
from lottery_research.phase2.statistics import PRIMARY_FAMILIES
from lottery_research.phase2.vectorized import calculate_statistics_batch

from .serialization import load_json, sha256
from .simulation import generate_slow_drift_batch


FAMILIES = ("marginal_inclusion", "set_structure", "pair_dependence", "slow_drift", "cross_zone_dependence")
PHASE2_NAME = {"slow_drift": "temporal_instability"}
ENGINE_ID = "phase2_1_independent_replay_v1"


def _issue_calendar(draws: list[dict[str, Any]], sample_size: int) -> list[int]:
    values = [int(row["issue_id"]) for row in draws]
    if sample_size <= len(values):
        return values[:sample_size]
    return values + list(range(max(values) + 1, max(values) + 1 + sample_size - len(values)))


def _load_maps(destination: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = load_json(destination / "inputs/upstream/phase2-input-manifest.json")
    return manifest, {row["game"]: row for row in manifest["game_rule_maps"]}


def _load_corpora(destination: Path, lfs_root: Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    frozen = destination / "inputs/upstream"
    for name in ("reference-null.bin", "evaluation-null.bin"):
        external = lfs_root / name
        if not external.is_file() or sha256(external) != sha256(frozen / name):
            raise ValueError(f"independent replay corpus identity mismatch: {name}")
    return read_array_bundle(frozen / "reference-null.bin"), read_array_bundle(frozen / "evaluation-null.bin")


def _null_p_values(
    reference: dict[str, np.ndarray], evaluation: dict[str, np.ndarray], sample_sizes: Iterable[int]
) -> dict[int, np.ndarray]:
    columns = [(game, family) for game in ("dlt", "ssq") for family in PRIMARY_FAMILIES]
    return {
        n: np.column_stack(
            [
                empirical_p(
                    reference[f"reference.{game}.n{n}.{family}.statistic"],
                    evaluation[f"evaluation.{game}.n{n}.{family}.statistic"],
                )
                for game, family in columns
            ]
        )
        for n in sample_sizes
    }


def _cross_zone_mapping(destination: Path, game: str) -> dict[float, float]:
    calibration = load_json(destination / "inputs/upstream/phase2-effect-interval-calibration.json")
    return {
        float(row["target_v"]): float(row["mixture_q"])
        for row in calibration["cross_zone_mappings"]
        if row["game"] == game
    }


def independent_replay_grid(
    root: Path,
    destination: Path,
    *,
    lfs_root: Path,
    seed: int,
    selected_keys: set[tuple[str, str, float, int]] | None = None,
) -> list[dict[str, Any]]:
    """Recompute replay cells without calling the power workflow implementation."""
    prereg = load_json(destination / "contracts/preregistration.json")
    manifest, maps = _load_maps(destination)
    draws = load_frozen_draws(root, manifest)
    reference, evaluation = _load_corpora(destination, lfs_root)
    sizes = list(prereg["sample_size_grid"])
    null_p = _null_p_values(reference, evaluation, sizes)
    replications = int(prereg["power_replications_per_grid_point"])
    interval_alpha = 0.05 / 240
    rows: list[dict[str, Any]] = []

    for game, rule in maps.items():
        offset = 0 if game == "dlt" else 5
        cross_mapping = _cross_zone_mapping(destination, game)
        for family_index, family in enumerate(FAMILIES):
            for raw_effect in prereg["effect_grids"][family]:
                effect = float(raw_effect)
                wanted_sizes = [
                    n for n in sizes
                    if selected_keys is None or (game, family, effect, n) in selected_keys
                ]
                if not wanted_sizes:
                    continue
                scenario_seed = domain_seed(seed, f"phase2.1-independent-replay:{game}:{family}:{effect}")
                generated: dict[int, dict[str, dict[str, np.ndarray]]]
                if family == "slow_drift":
                    generated = {}
                    for n in wanted_sizes:
                        cell_seed = domain_seed(scenario_seed, f"n={n}")
                        batch = generate_slow_drift_batch(
                            rule,
                            worlds=replications,
                            draws=n,
                            effect=effect,
                            seed=cell_seed,
                            issue_ids=_issue_calendar(draws[game], n),
                        )
                        generated[n] = calculate_statistics_batch(batch, rule)
                else:
                    phase2_family = PHASE2_NAME.get(family, family)
                    generated = simulate_prefix_statistics(
                        rule,
                        worlds=replications,
                        sample_sizes=sizes,
                        family=phase2_family,
                        effect=scenario_generator_effect(phase2_family, effect, cross_mapping),
                        seed=scenario_seed,
                        issue_ids_by_n={n: _issue_calendar(draws[game], n) for n in sizes},
                    )
                for n in wanted_sizes:
                    target = np.column_stack(
                        [
                            empirical_p(
                                reference[f"reference.{game}.n{n}.{name}.statistic"],
                                generated[n][name]["statistic"],
                            )
                            for name in PRIMARY_FAMILIES
                        ]
                    )
                    cell_seed = domain_seed(scenario_seed, f"n={n}")
                    rng = np.random.default_rng(domain_seed(cell_seed, "independent-other-game-null"))
                    selected = rng.integers(0, len(null_p[n]), size=replications)
                    combined = null_p[n][selected].copy()
                    combined[:, offset:offset + 5] = target
                    adjusted = holm_adjust_matrix(combined)
                    successes = int(np.count_nonzero(adjusted[:, offset + family_index] <= 0.05))
                    lower, upper = clopper_pearson(successes, replications, alpha=interval_alpha)
                    rows.append(
                        {
                            "game": game,
                            "family": family,
                            "effect": raw_effect,
                            "sample_size": n,
                            "successes": successes,
                            "replications": replications,
                            "power": successes / replications,
                            "simultaneous_95_lower": lower,
                            "simultaneous_95_upper": upper,
                            "seed": cell_seed,
                        }
                    )
    return rows
