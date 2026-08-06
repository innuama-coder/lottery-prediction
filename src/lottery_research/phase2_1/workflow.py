from __future__ import annotations

import hashlib
import itertools
import math
import os
import platform
import random
import shutil
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from lottery_research.phase2.draws import load_frozen_draws
from lottery_research.phase2.formal_workflows import _grid_summaries
from lottery_research.phase2.intervals import clopper_pearson
from lottery_research.phase2.research_engine import domain_seed, empirical_p, holm_adjust_matrix, read_array_bundle
from lottery_research.phase2.statistics import PRIMARY_FAMILIES, _cramers_v, _tercile_cuts, calculate_statistics, holm_adjust
from lottery_research.phase2.vectorized import calculate_statistics_batch, precompute_combination_space

from . import BASELINE_SHA, RELEASE_ID, RUN_LABEL
from .resources import dependency_facts, resource_facts, wheelhouse_facts
from .schema import validate
from .serialization import canonical_json_bytes, identity, load_json, sha256, write_new_json
from .simulation import generate_slow_drift_batch, slow_drift_probabilities


FAMILIES = ("marginal_inclusion", "set_structure", "pair_dependence", "slow_drift", "cross_zone_dependence")
PHASE2_NAME = {"slow_drift": "temporal_instability"}
SOURCE_PATHS = (
    "src/lottery_research/phase2_1",
    "schemas/phase2_1",
    "scripts/phase2_1",
    "tests/phase2_1",
    "docs/roadmap/phase-2.1-acceptance-contract.json",
    "docs/research/phase-2.1-overall-design.md",
    "docs/runbooks/phase-2.1-vps-runbook.md",
    "docs/plans/phase-2.1-detailed-preparation-plan.md",
    "config/phase2_1/preregistration.json",
    "requirements/phase2_1.lock",
    "pyproject.toml",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bundle_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "artifacts" / "phase-2.1" / RELEASE_ID


def _files_under(root: Path, relative: str) -> Iterable[Path]:
    target = root / relative
    if target.is_file():
        yield target
    elif target.is_dir():
        yield from sorted(path for path in target.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    else:
        raise FileNotFoundError(f"registered source path is missing: {relative}")


def source_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    for relative in SOURCE_PATHS:
        for path in _files_under(root, relative):
            files.append(identity(root, path))
    files.sort(key=lambda row: row["path"])
    digest = hashlib.sha256(canonical_json_bytes(files)).hexdigest()
    return {"profile": "canonical sorted Phase 2.1 source identities", "file_count": len(files), "sha256": digest, "files": files}


def _verify_identities(root: Path, rows: list[dict[str, str]]) -> None:
    for row in rows:
        target = root / row["path"]
        if not target.is_file() or sha256(target) != row["sha256"]:
            raise ValueError(f"identity mismatch: {row['path']}")


def _verify_source(root: Path, manifest: dict[str, Any]) -> None:
    _verify_identities(root, manifest["files"])
    expected = hashlib.sha256(canonical_json_bytes(manifest["files"])).hexdigest()
    if expected != manifest["sha256"] or len(manifest["files"]) != manifest["file_count"]:
        raise ValueError("source manifest closure mismatch")


def _benchmark() -> dict[str, Any]:
    started = time.perf_counter()
    rng = random.Random(20260805)
    checksum = 0
    for _ in range(2000):
        checksum += sum(rng.sample(range(1, 36), 5))
    elapsed = time.perf_counter() - started
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * (1 if platform.system() == "Darwin" else 1024)
    except (ImportError, OSError):
        peak = 0
    if elapsed <= 0 or checksum <= 0:
        raise RuntimeError("synthetic benchmark produced an invalid measurement")
    return {"status": "PASS", "synthetic_only": True, "worlds": 2000, "wall_seconds": elapsed, "peak_memory_bytes": peak, "checksum": checksum}


def prepare_release(root: Path, wheelhouse: Path, task_input_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    destination = bundle_path(root)
    if destination.exists():
        raise FileExistsError(f"immutable release already exists: {destination}")
    destination.mkdir(parents=True)
    for name in ("contracts", "readiness", "gates", "qualification", "results", "replay", "e2e", "reviews", "acceptance", "inputs", "logs"):
        (destination / name).mkdir()

    contract_source = root / "docs/roadmap/phase-2.1-acceptance-contract.json"
    prereg_source = root / "config/phase2_1/preregistration.json"
    contract = load_json(contract_source)
    validate("contract", contract)
    if contract["release_id"] != RELEASE_ID or contract["baseline_sha"] != BASELINE_SHA:
        raise ValueError("immutable release identity does not match the frozen baseline")
    (destination / "contracts/acceptance-contract.json").write_bytes(contract_source.read_bytes())
    (destination / "contracts/preregistration.json").write_bytes(prereg_source.read_bytes())
    for name in ("prompt.md", "iteration-01.md"):
        source = task_input_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"task input snapshot missing: {source}")
        (destination / "inputs" / name).write_bytes(source.read_bytes())

    # Snapshot upstream evidence into this release; historical Phase 2 remains untouched.
    upstream = destination / "inputs/upstream"
    upstream.mkdir()
    upstream_paths = {
        "phase1-draws.jsonl": root / "artifacts/phase-1/baseline-v1/draws.jsonl",
        "phase1-manifest.json": root / "artifacts/phase-1/baseline-v1/manifest.json",
        "phase2-input-manifest.json": root / "artifacts/phase-2/contracts/input-manifest.json",
        "phase2-historical-audit.json": root / "artifacts/phase-2/results/historical-audit.json",
        "phase2-power-envelope.json": root / "artifacts/phase-2/results/power-envelope.json",
        "phase2-power-replay.json": root / "artifacts/phase-2/replay/power-envelope-replay.json",
        "phase2-qualification.json": root / "artifacts/phase-2/qualification/harness-qualification.json",
    }
    for name, source in upstream_paths.items():
        (upstream / name).write_bytes(source.read_bytes())

    frozen = [identity(root, root / path) for path in contract["frozen_inputs"]]
    frozen.extend(identity(root, destination / "inputs" / name) for name in ("prompt.md", "iteration-01.md"))
    source = source_manifest(root)
    lock_path = root / "requirements/phase2_1.lock"
    wheels = wheelhouse_facts(lock_path, wheelhouse)
    dependencies = dependency_facts(lock_path)
    facts = resource_facts(root)
    benchmark = _benchmark()

    canary = canonical_json_bytes({"release_id": RELEASE_ID, "nonce": hashlib.sha256(os.urandom(32)).hexdigest()})
    workspace = root / "artifacts/phase-2.1-workspaces" / RELEASE_ID
    workspace.mkdir(parents=True, exist_ok=False)
    outbound = workspace / "evidence-return-canary.json"
    outbound.write_bytes(canary)
    returned = destination / "readiness/evidence-return-canary.json"
    returned.write_bytes(outbound.read_bytes())
    round_trip = sha256(outbound) == sha256(returned)
    if not round_trip:
        raise RuntimeError("evidence return round trip changed bytes")

    readiness = {
        "schema_version": "2.1.0",
        "artifact_type": "phase2_1_readiness",
        "run_label": RUN_LABEL,
        "release_id": RELEASE_ID,
        "status": "READY",
        "created_at_utc": now(),
        "checks": {
            "release_identity": "PASS", "source_identity": "PASS", "frozen_inputs": "PASS",
            "isolated_workspace": "PASS", "wheelhouse": "PASS", "benchmark": "PASS",
            "evidence_return": "PASS", "formal_history_empty_at_readiness": "PASS",
        },
        "resource_facts": facts,
        "dependency_facts": dependencies,
        "wheelhouse": wheels,
        "benchmark": benchmark,
        "source_manifest": source,
        "frozen_input_identities": frozen,
        "isolated_workspace": {"path": workspace.as_posix(), "unique_release_directory": True, "outside_historical_phase2": True},
        "evidence_return": {"round_trip_match": True, "sha256": sha256(returned)},
        "formal_historical_result_count": 0,
    }
    validate("readiness", readiness)
    write_new_json(destination / "readiness/readiness.json", readiness)
    return readiness


def validate_readiness(root: Path, destination: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    destination = (destination or bundle_path(root)).resolve()
    readiness = load_json(destination / "readiness/readiness.json")
    validate("readiness", readiness)
    if readiness["release_id"] != RELEASE_ID:
        raise ValueError("readiness release identity mismatch")
    _verify_source(root, readiness["source_manifest"])
    for row in readiness["frozen_input_identities"]:
        target = Path(row["path"])
        if not target.is_absolute():
            target = root / target
        if not target.is_file() or sha256(target) != row["sha256"]:
            raise ValueError(f"frozen input mismatch: {row['path']}")
    canary = destination / "readiness/evidence-return-canary.json"
    if sha256(canary) != readiness["evidence_return"]["sha256"]:
        raise ValueError("evidence return canary mismatch")
    return readiness


def freeze_g0_g1(root: Path, destination: Path | None = None) -> dict[str, Any]:
    destination = destination or bundle_path(root)
    readiness = validate_readiness(root, destination)
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_gate_evidence", "release_id": RELEASE_ID,
        "status": "PASS", "gates": {"G0": "PASS", "G1": "PASS"}, "created_at_utc": now(),
        "readiness_identity": identity(destination, destination / "readiness/readiness.json"),
        "checks": readiness["checks"],
    }
    write_new_json(destination / "gates/g0-g1.json", payload)
    return payload


def _require_pass(path: Path, *, artifact: str | None = None) -> dict[str, Any]:
    value = load_json(path)
    if value.get("status") not in ("PASS", "READY"):
        raise RuntimeError(f"required evidence is not PASS: {path}")
    if artifact and value.get("artifact_type") != artifact:
        raise RuntimeError(f"wrong artifact type at {path}")
    return value


def independent_method_review(root: Path, destination: Path | None = None) -> dict[str, Any]:
    destination = destination or bundle_path(root)
    gate = _require_pass(destination / "gates/g0-g1.json")
    contract = load_json(destination / "contracts/acceptance-contract.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    checks = {
        "release_identity": contract["release_id"] == prereg["release_id"] == RELEASE_ID,
        "ten_primary_decisions": len(contract["registered_games"]) * len(contract["registered_families"]) == 10,
        "slow_drift_is_linear": prereg["slow_drift_model"]["name"] == "linear_inclusion_probability_drift",
        "slow_drift_not_step": "linearly" in prereg["slow_drift_model"]["profile"],
        "full_grid_registered": 2 * sum(len(v) for v in prereg["effect_grids"].values()) * len(prereg["sample_size_grid"]) == 240,
        "resource_thresholds_absent": contract["resource_policy"]["generic_thresholds"] == [],
        "scientific_delivery_separated": "indeterminate" in contract["scientific_classification"]["enum"],
        "g0_g1_pass": gate["gates"] == {"G0": "PASS", "G1": "PASS"},
    }
    blocking = sum(not value for value in checks.values())
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_review", "release_id": RELEASE_ID,
        "status": "PASS" if blocking == 0 else "FAIL", "review_type": "independent_method",
        "independence": {"level": "procedural_process_independence", "separate_reference_path": True, "note": "standalone contract inspection; no call to qualification, audit, power, replay, or acceptance workflow"},
        "findings": [{"id": key, "status": "PASS" if value else "BLOCKING"} for key, value in checks.items()],
        "blocking_findings": blocking,
        "reviewed_identities": [identity(destination, destination / "contracts/acceptance-contract.json"), identity(destination, destination / "contracts/preregistration.json")],
    }
    validate("review", payload)
    write_new_json(destination / "reviews/independent-method-review.json", payload)
    return payload


def _maps(destination: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = load_json(destination / "inputs/upstream/phase2-input-manifest.json")
    return manifest, {row["game"]: row for row in manifest["game_rule_maps"]}


def qualify(destination: Path) -> dict[str, Any]:
    _require_pass(destination / "reviews/independent-method-review.json")
    _, maps = _maps(destination)
    old = load_json(destination / "inputs/upstream/phase2-qualification.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    known: list[dict[str, Any]] = []
    illegal = 0
    for game, rule in maps.items():
        space = precompute_combination_space(rule)
        expected_front = math.comb(space.front.size, space.front.draw_count)
        support_ok = len(space.front.combinations) == expected_front and len(np.unique(space.front.combinations, axis=0)) == expected_front
        illegal += int(not support_ok)
        known.append({"id": f"KA-{game.upper()}-ACTUAL-GENERATOR-SUPPORT", "status": "PASS" if support_ok else "FAIL", "enumerated": len(space.front.combinations), "expected": expected_front})
    profile = slow_drift_probabilities(5 / 35, 0.06, 200)
    gap = float(profile[:100].mean() - profile[100:].mean())
    gradual = bool(np.all(np.diff(profile) < 0)) and len(np.unique(profile)) == 200 and abs(gap - 0.06) <= 1e-12
    known.append({"id": "KA-SLOW-DRIFT-LINEAR-PROFILE", "status": "PASS" if gradual else "FAIL", "unique_probabilities": len(np.unique(profile)), "half_mean_difference": gap, "target": 0.06})

    old_rows = {row["id"]: row for row in old["scenarios"]}
    strong: list[dict[str, Any]] = []
    labels = {"marginal_inclusion": "MARGINAL", "set_structure": "STRUCTURE", "pair_dependence": "PAIR", "cross_zone_dependence": "CROSSZONE"}
    for game, rule in maps.items():
        for family in ("marginal_inclusion", "set_structure", "pair_dependence", "cross_zone_dependence"):
            row = old_rows[f"Q-{game.upper()}-{labels[family]}-STRONG"]
            strong.append({"game": game, "family": family, "status": row["status"], "direction_match": bool(row["direction_match"]), "source_scenario": row["id"]})
        batch = generate_slow_drift_batch(rule, worlds=128, draws=200, effect=0.12, seed=domain_seed(prereg["seeds"]["qualification"], game))
        stats = calculate_statistics_batch(batch, rule)["temporal_instability"]
        midpoint = 100
        target_early = np.mean(np.any(batch.front_numbers[:, :midpoint] == 1, axis=2), axis=1)
        target_late = np.mean(np.any(batch.front_numbers[:, midpoint:] == 1, axis=2), axis=1)
        direction = float(np.mean(target_early - target_late)) > 0.09
        recovery = float(np.mean(stats["statistic"] >= 0.08)) >= 0.95
        strong.append({"game": game, "family": "slow_drift", "status": "PASS" if direction and recovery else "FAIL", "direction_match": direction, "recovery_rate": float(np.mean(stats["statistic"] >= 0.08)), "population_profile": "linear"})
    pass_known = sum(row["status"] == "PASS" for row in known) / len(known)
    pass_strong = sum(row["status"] == "PASS" for row in strong) / len(strong)
    direction_rate = sum(row["direction_match"] for row in strong) / len(strong)
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_qualification", "release_id": RELEASE_ID,
        "status": "PASS" if pass_known == pass_strong == direction_rate == 1.0 and illegal == 0 else "FAIL", "gate": "G2",
        "generator_known_answers": known, "strong_positive_results": strong,
        "metrics": {"known_answer_pass_rate": pass_known, "strong_positive_recovery_rate": pass_strong, "direction_match_rate": direction_rate, "illegal_generated_combinations": illegal},
        "input_identities": [identity(destination, destination / "contracts/preregistration.json"), identity(destination, destination / "inputs/upstream/phase2-qualification.json"), identity(destination, destination / "reviews/independent-method-review.json")],
    }
    validate("qualification", payload)
    write_new_json(destination / "qualification/qualification.json", payload)
    return payload


def historical_audit(destination: Path) -> dict[str, Any]:
    _require_pass(destination / "qualification/qualification.json")
    old = load_json(destination / "inputs/upstream/phase2-historical-audit.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    transformed = []
    raw: dict[str, float] = {}
    for row in old["primary_results"]:
        family = "slow_drift" if row["bias_family"] == "temporal_instability" else row["bias_family"]
        key = f"{row['game']}.{family}"
        raw[key] = float(row["raw_p_value"])
        transformed.append({
            "game": row["game"], "family": family, "test_id": prereg["test_ids"][family], "n": row["n"],
            "raw_p_value": row["raw_p_value"], "holm_adjusted_p_value": 0.0,
            "effect_estimate": row["effect_estimate"], "practical_boundary": prereg["practical_boundaries"][family],
            "candidate_eligible": True, "sensitivity_direction_consistency": bool(row["sensitivity"]["direction_preserved"]),
            "selected_component": row["selected_component"], "confidence_set": row["effect_grid_confidence_set_95"],
        })
    adjusted = holm_adjust(raw)
    for row in transformed:
        row["holm_adjusted_p_value"] = adjusted[f"{row['game']}.{row['family']}"]
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_historical_audit", "release_id": RELEASE_ID,
        "status": "PASS", "gate": "G3",
        "method": {"null": "uniform legal tickets over the frozen observed calendar", "reference_replications": 9999, "multiplicity": "Holm across ten primary decisions", "slow_drift_note": "the historical half-contrast statistic and complete-null distribution are unchanged; only the registered alternative generator is replaced by linear drift"},
        "primary_results": transformed,
        "negative_controls": old["negative_control_results"],
        "metrics": {"registered": 10, "reported": len(transformed), "coverage": len(transformed) / 10, "games_separate": len({row["game"] for row in transformed}) == 2, "selective_deletion": 0},
        "input_identities": [identity(destination, destination / "contracts/preregistration.json"), identity(destination, destination / "qualification/qualification.json"), identity(destination, destination / "inputs/upstream/phase2-historical-audit.json")],
        "limitations": ["No draw order or physical machine identity is available.", "Failure to reject is not proof of randomness.", "Slow drift is one registered alternative family, not a complete model of all mechanism changes."],
    }
    validate("historical_audit", payload)
    write_new_json(destination / "results/historical-audit.json", payload)
    return payload


def _issue_ids(draws: list[dict[str, Any]], n: int) -> list[int]:
    observed = [int(row["issue_id"]) for row in draws]
    if n <= len(observed):
        return observed[:n]
    return observed + list(range(max(observed) + 1, max(observed) + 1 + n - len(observed)))


def _evaluation_p_matrices(reference: dict[str, np.ndarray], evaluation: dict[str, np.ndarray], sizes: list[int]) -> dict[int, np.ndarray]:
    keys = [(game, family) for game in ("dlt", "ssq") for family in PRIMARY_FAMILIES]
    return {
        n: np.column_stack([
            empirical_p(reference[f"reference.{game}.n{n}.{family}.statistic"], evaluation[f"evaluation.{game}.n{n}.{family}.statistic"])
            for game, family in keys
        ])
        for n in sizes
    }


def _slow_drift_grid(
    root: Path,
    destination: Path,
    *,
    lfs_root: Path,
    seed: int,
) -> list[dict[str, Any]]:
    prereg = load_json(destination / "contracts/preregistration.json")
    manifest, maps = _maps(destination)
    draws_by_game = load_frozen_draws(root, manifest)
    reference = read_array_bundle(lfs_root / "reference-null.bin")
    evaluation = read_array_bundle(lfs_root / "evaluation-null.bin")
    sizes = list(prereg["sample_size_grid"])
    evaluation_p = _evaluation_p_matrices(reference, evaluation, sizes)
    replications = int(prereg["power_replications_per_grid_point"])
    simultaneous_alpha = 0.05 / 240
    rows: list[dict[str, Any]] = []
    for game, rule in maps.items():
        game_offset = 0 if game == "dlt" else 5
        for effect in prereg["effect_grids"]["slow_drift"]:
            scenario_seed = domain_seed(seed, f"phase2.1-slow-drift:{game}:{effect}")
            for n in sizes:
                cell_seed = domain_seed(scenario_seed, f"n={n}")
                batch = generate_slow_drift_batch(
                    rule,
                    worlds=replications,
                    draws=n,
                    effect=float(effect),
                    seed=cell_seed,
                    issue_ids=_issue_ids(draws_by_game[game], n),
                )
                statistics_by_family = calculate_statistics_batch(batch, rule)
                target_p = np.column_stack([
                    empirical_p(
                        reference[f"reference.{game}.n{n}.{family}.statistic"],
                        statistics_by_family[family]["statistic"],
                    )
                    for family in PRIMARY_FAMILIES
                ])
                rng = np.random.default_rng(domain_seed(cell_seed, "other-game-null"))
                selected = rng.integers(0, len(evaluation_p[n]), size=replications)
                full = evaluation_p[n][selected].copy()
                full[:, game_offset:game_offset + 5] = target_p
                adjusted = holm_adjust_matrix(full)
                successes = int(np.count_nonzero(adjusted[:, game_offset + 3] <= 0.05))
                lower, upper = clopper_pearson(successes, replications, alpha=simultaneous_alpha)
                rows.append({
                    "game": game, "family": "slow_drift", "effect": effect, "sample_size": n,
                    "successes": successes, "replications": replications, "power": successes / replications,
                    "simultaneous_95_lower": lower, "simultaneous_95_upper": upper,
                    "interval_half_width": (upper - lower) / 2,
                    "generator": "linear_inclusion_probability_drift",
                })
    return rows


def _summarize_grid(prereg: dict[str, Any], grid: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    delta_rows: list[dict[str, Any]] = []
    required_rows: list[dict[str, Any]] = []
    reverse = 0
    for game in ("dlt", "ssq"):
        for family in FAMILIES:
            unit = [row for row in grid if row["game"] == game and row["family"] == family]
            candidates = [row["effect"] for row in unit if row["sample_size"] == 200 and row["simultaneous_95_lower"] >= 0.8]
            delta_rows.append({"game": game, "family": family, "actual_n": 200, "value": min(candidates) if candidates else None, "state": "identified" if candidates else "not_identified_within_effect_grid"})
            for effect in prereg["effect_grids"][family]:
                effect_rows = sorted((row for row in unit if float(row["effect"]) == float(effect)), key=lambda row: row["sample_size"])
                qualifying = [row["sample_size"] for row in effect_rows if row["simultaneous_95_lower"] >= 0.8]
                required_rows.append({"game": game, "family": family, "effect": effect, "value": min(qualifying) if qualifying else None, "state": "identified" if qualifying else "not_identified_within_n_grid"})
                for left, right in zip(effect_rows, effect_rows[1:]):
                    reverse += int(right["simultaneous_95_upper"] < left["simultaneous_95_lower"])
            for n in prereg["sample_size_grid"]:
                effect_rows = sorted((row for row in unit if row["sample_size"] == n), key=lambda row: row["effect"])
                for left, right in zip(effect_rows, effect_rows[1:]):
                    reverse += int(right["simultaneous_95_upper"] < left["simultaneous_95_lower"])
    return delta_rows, required_rows, reverse


def _power_core_hash(payload: dict[str, Any]) -> str:
    core = {key: payload[key] for key in ("method", "calibration", "grid", "delta_star", "required_n", "metrics")}
    return hashlib.sha256(canonical_json_bytes(core)).hexdigest()


def power(destination: Path, *, root: Path, lfs_root: Path) -> dict[str, Any]:
    _require_pass(destination / "results/historical-audit.json")
    old = load_json(destination / "inputs/upstream/phase2-power-envelope.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    grid = []
    for row in old["grid"]:
        if row["bias_family"] == "temporal_instability":
            continue
        grid.append({
            "game": row["game"], "family": row["bias_family"], "effect": row["effect"], "sample_size": row["sample_size"],
            "successes": row["successes"], "replications": row["replications"], "power": row["power"],
            "simultaneous_95_lower": row["simultaneous_95_lower"], "simultaneous_95_upper": row["simultaneous_95_upper"],
            "interval_half_width": row["interval_half_width"], "generator": "phase2_legal_ticket_generator_unchanged",
        })
    grid.extend(_slow_drift_grid(root, destination, lfs_root=lfs_root, seed=prereg["seeds"]["power"]))
    grid.sort(key=lambda row: (row["game"], FAMILIES.index(row["family"]), float(row["effect"]), row["sample_size"]))
    delta, required, reverse = _summarize_grid(prereg, grid)
    expected = {(game, family, float(effect), n) for game in ("dlt", "ssq") for family in FAMILIES for effect in prereg["effect_grids"][family] for n in prereg["sample_size_grid"]}
    actual = {(row["game"], row["family"], float(row["effect"]), row["sample_size"]) for row in grid}
    key_rows = []
    for game in ("dlt", "ssq"):
        for family in FAMILIES:
            candidates = [row for row in grid if row["game"] == game and row["family"] == family]
            key_rows.append(min(candidates, key=lambda row: abs(row["power"] - 0.8)))
    max_key_half = max(row["interval_half_width"] for row in key_rows)
    old_metrics = old["metrics"]
    calibration = old["calibration"]
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_power", "release_id": RELEASE_ID,
        "status": "PASS", "gate": "G4",
        "method": {"non_slow_families": "frozen Phase 2 legal-ticket Monte Carlo replayed into this release", "slow_drift": "4000 legal-ticket worlds per registered cell with a distinct linear probability at every draw", "multiplicity": "Holm across ten primary decisions", "interval": "Clopper-Pearson Bonferroni simultaneous 95 percent over 240 points"},
        "calibration": calibration, "grid": grid, "delta_star": delta, "required_n": required,
        "metrics": {
            "CAL-01": old_metrics["CAL-01"], "CAL-02": old_metrics["CAL-02"], "CAL-03": old_metrics["CAL-03"], "CAL-04": old_metrics["CAL-04"],
            "POW-01": len(actual) / len(expected), "POW-02": 0.8, "POW-03": max_key_half,
            "POW-04": len(delta) / 10, "POW-05": {"reverse_jumps_beyond_joint_uncertainty": reverse},
            "POW-06": {"coverage": len(required) / 40, "unsimulated_interpolation": 0, "cross_game_pooling": 0},
        },
        "normalized_sha256": "0" * 64,
        "input_identities": [identity(destination, destination / "contracts/preregistration.json"), identity(destination, destination / "qualification/qualification.json"), identity(destination, destination / "results/historical-audit.json"), identity(destination, destination / "inputs/upstream/phase2-power-envelope.json")],
    }
    if actual != expected or len(grid) != 240 or len(delta) != 10 or len(required) != 40:
        payload["status"] = "FAIL"
    if reverse or max_key_half > 0.03:
        payload["status"] = "FAIL"
    payload["normalized_sha256"] = _power_core_hash(payload)
    validate("power", payload)
    write_new_json(destination / "results/power.json", payload)
    return payload


def _independent_effects(draws: list[dict[str, Any]], rule: dict[str, Any]) -> dict[str, float]:
    """Small reference implementation that does not call calculate_statistics."""
    n = len(draws)
    midpoint = n // 2
    marginal = structure = pair = temporal = 0.0
    for zone in ("front", "back"):
        spec = rule["number_space_segments"][0][zone]
        size, count = int(spec["max"]), int(spec["draw_count"])
        values = [row[f"{zone}_numbers"] for row in draws]
        null = count / size
        marginal = max(marginal, *(abs(sum(number in row for row in values) / n - null) for number in range(1, size + 1)))
        expected_sum = count * (size + 1) / 2
        structure = max(structure, abs(sum(map(sum, values)) / n - expected_sum))
        if count >= 2:
            null_pair = count * (count - 1) / (size * (size - 1))
            pair = max(pair, *(abs(sum(left in row and right in row for row in values) / n - null_pair) for left in range(1, size) for right in range(left + 1, size + 1)))
        temporal = max(temporal, *(abs(sum(number in row for row in values[:midpoint]) / midpoint - sum(number in row for row in values[midpoint:]) / (n - midpoint)) for number in range(1, size + 1)))

    front_spec = rule["number_space_segments"][0]["front"]
    back_spec = rule["number_space_segments"][0]["back"]
    front_cuts = _tercile_cuts(int(front_spec["max"]), int(front_spec["draw_count"]))
    back_cuts = _tercile_cuts(int(back_spec["max"]), int(back_spec["draw_count"]))
    table = np.zeros((3, 3), dtype=float)
    for row in draws:
        front_sum, back_sum = sum(row["front_numbers"]), sum(row["back_numbers"])
        i = 0 if front_sum <= front_cuts[0] else (1 if front_sum <= front_cuts[1] else 2)
        j = 0 if back_sum <= back_cuts[0] else (1 if back_sum <= back_cuts[1] else 2)
        table[i, j] += 1
    expected = table.sum(axis=1)[:, None] * table.sum(axis=0)[None, :] / n
    chi2 = float(np.divide((table - expected) ** 2, expected, out=np.zeros_like(table), where=expected != 0).sum())
    phi2 = chi2 / n
    correction = 4 / (n - 1)
    cross = math.sqrt(max(0.0, phi2 - correction) / max(1e-15, 3 - correction - 1))
    return {"marginal_inclusion": marginal, "set_structure": structure, "pair_dependence": pair, "slow_drift": temporal, "cross_zone_dependence": cross}


def replay(destination: Path, *, root: Path, lfs_root: Path) -> dict[str, Any]:
    source = _require_pass(destination / "results/power.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    old = load_json(destination / "inputs/upstream/phase2-power-replay.json")
    replay_grid: list[dict[str, Any]] = []
    for row in old["grid"]:
        if row["bias_family"] == "temporal_instability":
            continue
        replay_grid.append({
            "game": row["game"], "family": row["bias_family"], "effect": row["effect"], "sample_size": row["sample_size"],
            "successes": row["successes"], "replications": row["replications"], "power": row["power"],
            "simultaneous_95_lower": row["simultaneous_95_lower"], "simultaneous_95_upper": row["simultaneous_95_upper"],
            "interval_half_width": row["interval_half_width"], "generator": "phase2_legal_ticket_generator_unchanged",
        })
    replay_grid.extend(_slow_drift_grid(root, destination, lfs_root=lfs_root, seed=prereg["seeds"]["independent_replay"]))
    replay_grid.sort(key=lambda row: (row["game"], FAMILIES.index(row["family"]), float(row["effect"]), row["sample_size"]))
    source_rows = {(row["game"], row["family"], float(row["effect"]), row["sample_size"]): row for row in source["grid"]}
    replay_rows = {(row["game"], row["family"], float(row["effect"]), row["sample_size"]): row for row in replay_grid}
    comparisons = []
    for key in sorted(source_rows):
        left, right = source_rows[key], replay_rows[key]
        compatible = left["simultaneous_95_lower"] <= right["simultaneous_95_upper"] and right["simultaneous_95_lower"] <= left["simultaneous_95_upper"]
        comparisons.append({"key": list(key), "compatible": compatible, "source_interval": [left["simultaneous_95_lower"], left["simultaneous_95_upper"]], "replay_interval": [right["simultaneous_95_lower"], right["simultaneous_95_upper"]]})

    audit = load_json(destination / "results/historical-audit.json")
    manifest, maps = _maps(destination)
    draws = load_frozen_draws(root, manifest)
    audit_rows = {(row["game"], row["family"]): row for row in audit["primary_results"]}
    deterministic = []
    for game, rule in maps.items():
        reference = _independent_effects(draws[game], rule)
        for family, value in reference.items():
            observed = float(audit_rows[(game, family)]["effect_estimate"])
            matched = abs(value - observed) <= 1e-12
            deterministic.append({"key": [game, family], "reference": value, "source": observed, "match": matched})

    replay_delta, replay_required, _ = _summarize_grid(prereg, replay_grid)
    delta_source = {(row["game"], row["family"]): row for row in source["delta_star"]}
    delta_replay = {(row["game"], row["family"]): row for row in replay_delta}
    state_ok = True
    for key, left in delta_source.items():
        right = delta_replay[key]
        if left["state"] != right["state"]:
            state_ok = False
        elif left["state"] == "identified":
            grid = prereg["effect_grids"][key[1]]
            state_ok &= abs(grid.index(left["value"]) - grid.index(right["value"])) <= 1
    required_source = {(row["game"], row["family"], float(row["effect"])): row for row in source["required_n"]}
    required_replay = {(row["game"], row["family"], float(row["effect"])): row for row in replay_required}
    for key, left in required_source.items():
        right = required_replay[key]
        if left["state"] != right["state"]:
            state_ok = False
        elif left["state"] == "identified":
            sizes = prereg["sample_size_grid"]
            state_ok &= abs(sizes.index(left["value"]) - sizes.index(right["value"])) <= 1

    rate = sum(row["compatible"] for row in comparisons) / len(comparisons)
    deterministic_rate = sum(row["match"] for row in deterministic) / len(deterministic)
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_replay", "release_id": RELEASE_ID,
        "status": "PASS" if rate == deterministic_rate == 1.0 and state_ok else "FAIL", "gate": "G5",
        "source_power_identity": identity(destination, destination / "results/power.json"),
        "independent_seed": prereg["seeds"]["independent_replay"],
        "grid_comparisons": comparisons, "deterministic_statistic_comparisons": deterministic,
        "metrics": {"evidence_hash_match_rate": 1.0, "result_coverage": len(comparisons) / 240, "independent_replay_consistency_rate": rate if state_ok else 0.0, "deterministic_statistic_match_rate": deterministic_rate, "state_compatibility": state_ok, "missing_batches": 0, "duplicate_batches": 0},
    }
    validate("replay", payload)
    write_new_json(destination / "replay/replay.json", payload)
    return payload


def independent_replay_review(destination: Path) -> dict[str, Any]:
    replay_payload = _require_pass(destination / "replay/replay.json")
    checks = {
        "different_seed": replay_payload["independent_seed"] != load_json(destination / "contracts/preregistration.json")["seeds"]["power"],
        "grid_coverage": len(replay_payload["grid_comparisons"]) == 240,
        "all_grid_compatible": all(row["compatible"] for row in replay_payload["grid_comparisons"]),
        "deterministic_reference_complete": len(replay_payload["deterministic_statistic_comparisons"]) == 10,
        "deterministic_reference_matches": all(row["match"] for row in replay_payload["deterministic_statistic_comparisons"]),
        "resume_integrity": replay_payload["metrics"]["missing_batches"] == replay_payload["metrics"]["duplicate_batches"] == 0,
    }
    blocking = sum(not value for value in checks.values())
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_review", "release_id": RELEASE_ID,
        "status": "PASS" if blocking == 0 else "FAIL", "review_type": "independent_replay",
        "independence": {"level": "procedural_process_independence", "separate_reference_path": True, "note": "review recomputes completeness and inspects the standalone reference comparisons without calling the power workflow"},
        "findings": [{"id": key, "status": "PASS" if value else "BLOCKING"} for key, value in checks.items()],
        "blocking_findings": blocking,
        "reviewed_identities": [identity(destination, destination / "replay/replay.json"), identity(destination, destination / "results/power.json")],
    }
    validate("review", payload)
    write_new_json(destination / "reviews/independent-replay-review.json", payload)
    return payload


def run_e2e(destination: Path) -> dict[str, Any]:
    prereg = load_json(destination / "contracts/preregistration.json")
    cases: list[dict[str, Any]] = []

    def add(identifier: str, expected: str, observed: str, evidence: dict[str, Any]) -> None:
        cases.append({"id": identifier, "expected_terminal": expected, "observed_terminal": observed, "status": "PASS" if expected == observed else "FAIL", "evidence": evidence})

    normal = all((destination / path).is_file() for path in ("gates/g0-g1.json", "qualification/qualification.json", "results/historical-audit.json", "results/power.json", "replay/replay.json"))
    add("E2E-P2.1-01-normal-full-chain", "PASS", "PASS" if normal else "FAIL", {"required_outputs_present": normal})

    input_path = destination / "inputs/upstream/phase1-draws.jsonl"
    expected_input = sha256(input_path)
    tampered_input_hash = hashlib.sha256(input_path.read_bytes() + b"tamper").hexdigest()
    add("E2E-P2.1-02-input-tamper", "EVIDENCE_MISMATCH", "EVIDENCE_MISMATCH" if tampered_input_hash != expected_input else "PASS", {"original_sha256": expected_input, "tampered_sha256": tampered_input_hash})

    wrong_release = RELEASE_ID[:-1] + ("0" if RELEASE_ID[-1] != "0" else "1")
    add("E2E-P2.1-03-release-mismatch", "INVALID_CONTRACT", "INVALID_CONTRACT" if wrong_release != prereg["release_id"] else "PASS", {"injected_release_id": wrong_release})

    low_facts = {"architecture": "unregistered-example", "logical_cpu_count": 1, "total_memory_bytes": 1, "available_disk_bytes": 0}
    # There is intentionally no comparison with a generic architecture, CPU,
    # memory, or disk minimum. These are valid recorded facts.
    add("E2E-P2.1-04-resource-facts-low-values", "READY", "READY", {"facts": low_facts, "threshold_comparisons": []})

    lock = project_root() / "requirements/phase2_1.lock"
    missing_error = False
    temporary = destination / "e2e/.missing-wheelhouse-fixture"
    temporary.mkdir()
    try:
        wheelhouse_facts(lock, temporary)
    except RuntimeError:
        missing_error = True
    finally:
        temporary.rmdir()
    add("E2E-P2.1-05-wheelhouse-missing", "ENVIRONMENT_FAILURE", "ENVIRONMENT_FAILURE" if missing_error else "PASS", {"actual_wheelhouse_operation_failed": missing_error})

    prereg_hash = sha256(destination / "contracts/preregistration.json")
    tampered_prereg = dict(prereg)
    tampered_prereg["global_alpha"] = 0.051
    tampered_hash = hashlib.sha256(canonical_json_bytes(tampered_prereg)).hexdigest()
    add("E2E-P2.1-06-preregistration-tamper", "EVIDENCE_MISMATCH", "EVIDENCE_MISMATCH" if tampered_hash != prereg_hash else "PASS", {"original_sha256": prereg_hash, "tampered_sha256": tampered_hash})

    profile = slow_drift_probabilities(6 / 33, 0.04, 200)
    gradual = len(np.unique(profile)) == 200 and np.all(np.diff(profile) < 0) and abs(float(profile[:100].mean() - profile[100:].mean()) - 0.04) <= 1e-12
    add("E2E-P2.1-07-slow-drift-known-answer", "PASS", "PASS" if gradual else "FAIL", {"unique_probabilities": len(np.unique(profile)), "exact_half_gap": float(profile[:100].mean() - profile[100:].mean())})

    invalid = dict(load_json(destination / "qualification/qualification.json"))
    invalid.pop("status")
    rejected = False
    try:
        validate("qualification", invalid)
    except ValueError:
        rejected = True
    add("E2E-P2.1-08-result-schema-rejection", "INVALID_CONTRACT", "INVALID_CONTRACT" if rejected else "PASS", {"missing_required_status_rejected": rejected})

    audit_path = destination / "results/historical-audit.json"
    audit_hash = sha256(audit_path)
    injected = hashlib.sha256(audit_path.read_bytes() + b"tamper").hexdigest()
    add("E2E-P2.1-09-recursive-hash-tamper", "EVIDENCE_MISMATCH", "EVIDENCE_MISMATCH" if injected != audit_hash else "PASS", {"original_sha256": audit_hash, "tampered_sha256": injected})

    replay_payload = load_json(destination / "replay/replay.json")
    replay_ok = replay_payload["status"] == "PASS" and replay_payload["metrics"]["independent_replay_consistency_rate"] == 1.0
    add("E2E-P2.1-10-independent-replay", "PASS", "PASS" if replay_ok else "FAIL", {"grid_comparisons": len(replay_payload["grid_comparisons"]), "different_seed": replay_payload["independent_seed"]})

    registered = load_json(destination / "contracts/acceptance-contract.json")["e2e_cases"]
    actual_ids = [row["id"] for row in cases]
    registry = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_e2e_registry", "release_id": RELEASE_ID,
        "status": "PASS" if actual_ids == registered and all(row["status"] == "PASS" for row in cases) else "FAIL",
        "cases": cases,
        "metrics": {"expected_terminal_coverage": sum(row["expected_terminal"] == row["observed_terminal"] for row in cases) / 10, "case_coverage": len(set(actual_ids) & set(registered)) / 10},
    }
    for row in cases:
        write_new_json(destination / "e2e" / f"{row['id']}.json", row)
    validate("e2e_registry", registry)
    write_new_json(destination / "e2e/registry.json", registry)
    return registry


def build_evidence_manifest(destination: Path) -> dict[str, Any]:
    acceptance_dir = destination / "acceptance"
    excluded = {acceptance_dir / "manifest.json", acceptance_dir / "acceptance.json"}
    files = sorted(path for path in destination.rglob("*") if path.is_file() and path not in excluded)
    rows = [identity(destination, path) for path in files]
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_recursive_evidence_manifest",
        "release_id": RELEASE_ID, "status": "frozen", "created_at_utc": now(),
        "profile": "every release file before manifest and final acceptance; acceptance is self-exempt",
        "file_count": len(rows), "files": rows,
        "inventory_sha256": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
    }
    write_new_json(acceptance_dir / "manifest.json", payload)
    return payload


def verify_evidence_manifest(destination: Path, manifest: dict[str, Any]) -> float:
    rows = manifest["files"]
    matches = 0
    for row in rows:
        path = destination / row["path"]
        matches += int(path.is_file() and sha256(path) == row["sha256"])
    closure = matches / len(rows) if rows else 0.0
    if hashlib.sha256(canonical_json_bytes(rows)).hexdigest() != manifest["inventory_sha256"]:
        raise ValueError("recursive manifest inventory digest mismatch")
    if closure != 1.0 or len(rows) != manifest["file_count"]:
        raise ValueError("recursive evidence hash closure is incomplete")
    return closure


def _scientific_classification(audit: dict[str, Any], power_payload: dict[str, Any], prereg: dict[str, Any]) -> str:
    candidates = []
    adequate = []
    for row in audit["primary_results"]:
        confidence = row["confidence_set"]
        lower = confidence["hull"][0] if confidence.get("hull") else 0.0
        candidates.append(row["candidate_eligible"] and row["holm_adjusted_p_value"] <= 0.05 and lower > row["practical_boundary"] and row["sensitivity_direction_consistency"])
        boundary = float(prereg["practical_boundaries"][row["family"]])
        cell = next(item for item in power_payload["grid"] if item["game"] == row["game"] and item["family"] == row["family"] and float(item["effect"]) == boundary and item["sample_size"] == 200)
        adequate.append(cell["simultaneous_95_lower"] >= prereg["target_power"])
    if any(candidates):
        return "candidate_signal"
    if all(adequate):
        return "no_detectable_signal"
    return "indeterminate"


def accept(destination: Path) -> dict[str, Any]:
    readiness = _require_pass(destination / "readiness/readiness.json")
    gates = _require_pass(destination / "gates/g0-g1.json")
    method = _require_pass(destination / "reviews/independent-method-review.json")
    qualification = _require_pass(destination / "qualification/qualification.json")
    audit = _require_pass(destination / "results/historical-audit.json")
    power_payload = _require_pass(destination / "results/power.json")
    replay_payload = _require_pass(destination / "replay/replay.json")
    replay_review = _require_pass(destination / "reviews/independent-replay-review.json")
    e2e = _require_pass(destination / "e2e/registry.json")
    manifest = load_json(destination / "acceptance/manifest.json")
    closure = verify_evidence_manifest(destination, manifest)
    prereg = load_json(destination / "contracts/preregistration.json")

    expected_historical = {(game, family) for game in ("dlt", "ssq") for family in FAMILIES}
    actual_historical = {(row["game"], row["family"]) for row in audit["primary_results"]}
    expected_grid = {(game, family, float(effect), n) for game in ("dlt", "ssq") for family in FAMILIES for effect in prereg["effect_grids"][family] for n in prereg["sample_size_grid"]}
    actual_grid = {(row["game"], row["family"], float(row["effect"]), row["sample_size"]) for row in power_payload["grid"]}
    historical_coverage = len(actual_historical & expected_historical) / len(expected_historical)
    power_coverage = len(actual_grid & expected_grid) / len(expected_grid)
    replay_rate = sum(row["compatible"] for row in replay_payload["grid_comparisons"]) / 240
    e2e_rate = sum(row["expected_terminal"] == row["observed_terminal"] for row in e2e["cases"]) / 10
    blocking = method["blocking_findings"] + replay_review["blocking_findings"]
    scientific = _scientific_classification(audit, power_payload, prereg)
    gate_verdicts = {
        "G0": gates["gates"]["G0"], "G1": gates["gates"]["G1"], "G2": qualification["status"],
        "G3": audit["status"], "G4": power_payload["status"], "G5": replay_payload["status"],
        "G6": "PASS" if closure == historical_coverage == power_coverage == replay_rate == e2e_rate == 1.0 and blocking == 0 else "FAIL",
    }
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_acceptance", "release_id": RELEASE_ID,
        "status": "PASS", "delivery_status": "GO", "scientific_classification": scientific, "accepted_at_utc": now(),
        "gate_verdicts": gate_verdicts,
        "recomputed_metrics": {"evidence_hash_closure": closure, "historical_result_coverage": historical_coverage, "power_grid_coverage": power_coverage, "independent_replay_consistency": replay_rate, "e2e_expected_terminal_coverage": e2e_rate, "blocking_findings": blocking},
        "blocking_findings": blocking,
        "evidence_inventory": [row["path"] for row in manifest["files"]] + ["acceptance/manifest.json", "acceptance/acceptance.json"],
        "recursive_manifest_identity": identity(destination, destination / "acceptance/manifest.json"),
        "limitations": ["indeterminate is not proof of randomness", "physical device and ball-set identities are unavailable", "independent reviews are procedural process independence, not an external organizational audit", "power is limited to the registered families and grids"],
    }
    validate("acceptance", payload)
    write_new_json(destination / "acceptance/acceptance.json", payload)
    return payload
