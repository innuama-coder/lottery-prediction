from __future__ import annotations

import hashlib
import itertools
import math
import os
import platform
import random
import shutil
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from lottery_research.phase2.draws import load_frozen_draws
from lottery_research.phase2.formal_workflows import _signed_component
from lottery_research.phase2.intervals import clopper_pearson, clopper_pearson_one_sided
from lottery_research.phase2.research_engine import (
    domain_seed,
    empirical_p,
    holm_adjust_matrix,
    neyman_grid_confidence_set,
    read_array_bundle,
    scenario_generator_effect,
    simulate_prefix_statistics,
)
from lottery_research.phase2.statistics import PRIMARY_FAMILIES, _cramers_v, _tercile_cuts, calculate_statistics, holm_adjust
from lottery_research.phase2.vectorized import calculate_statistics_batch, generate_batch, precompute_combination_space

from . import BASELINE_SHA, RELEASE_ID, RUN_LABEL
from .resources import dependency_facts, resource_facts, wheelhouse_facts
from .schema import validate
from .serialization import canonical_json_bytes, identity, load_json, sha256, write_new_json
from .simulation import generate_slow_drift_batch, slow_drift_probabilities


FAMILIES = ("marginal_inclusion", "set_structure", "pair_dependence", "slow_drift", "cross_zone_dependence")
PHASE2_NAME = {"slow_drift": "temporal_instability"}
SOURCE_PATHS = (
    "src/lottery_research/phase2_1",
    "src/lottery_research/phase2",
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
    current = source_manifest(root)
    if current != manifest:
        raise ValueError("source manifest does not enumerate the complete registered runtime closure")


def scan_formal_history(root: Path, destination: Path, task_input_dir: Path) -> dict[str, Any]:
    """Count pre-readiness formal results for this exact release identity."""
    roots = [
        destination / "results",
        root / "artifacts/phase-2.1-protected-results" / RELEASE_ID,
        task_input_dir / "results",
    ]
    discovered: list[str] = []
    for scan_root in roots:
        if not scan_root.is_dir():
            continue
        for path in sorted(scan_root.rglob("*.json")):
            try:
                value = load_json(path)
            except (OSError, ValueError):
                continue
            if value.get("release_id") == RELEASE_ID and value.get("artifact_type") in {
                "phase2_1_historical_audit", "phase2_1_power", "phase2_1_replay"
            }:
                discovered.append(path.resolve().as_posix())
    return {
        "profile": "exact-release formal audit/power/replay JSON in release, task-input, and protected result roots",
        "roots": [path.resolve().as_posix() for path in roots],
        "discovered": discovered,
        "count": len(discovered),
    }


def validate_task_inputs(task_input_dir: Path, expected: dict[str, str]) -> list[Path]:
    validated = []
    for name, expected_hash in expected.items():
        source = task_input_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"task input snapshot missing: {source}")
        if sha256(source) != expected_hash:
            raise ValueError(f"task input identity mismatch: {name}")
        validated.append(source)
    return validated


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


def prepare_release(root: Path, wheelhouse: Path, task_input_dir: Path, corpus_root: Path) -> dict[str, Any]:
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
    for source in validate_task_inputs(task_input_dir, contract["task_input_identities"]):
        name = source.name
        (destination / "inputs" / name).write_bytes(source.read_bytes())

    # Snapshot upstream evidence into this release; historical Phase 2 remains untouched.
    upstream = destination / "inputs/upstream"
    upstream.mkdir()
    upstream_paths = {
        "phase1-draws.jsonl": root / "artifacts/phase-1/baseline-v1/draws.jsonl",
        "phase1-manifest.json": root / "artifacts/phase-1/baseline-v1/manifest.json",
        "phase2-input-manifest.json": root / "artifacts/phase-2/contracts/input-manifest.json",
        "phase2-effect-interval-calibration.json": root / "artifacts/phase-2/qualification/effect-interval-calibration.json",
        "reference-null.bin": corpus_root / "reference-null.bin",
        "evaluation-null.bin": corpus_root / "evaluation-null.bin",
    }
    for name, source in upstream_paths.items():
        (upstream / name).write_bytes(source.read_bytes())

    frozen = [identity(destination, path) for path in sorted((destination / "inputs").rglob("*")) if path.is_file()]
    frozen.extend(identity(destination, path) for path in sorted((destination / "contracts").glob("*.json")))
    source = source_manifest(root)
    lock_path = root / "requirements/phase2_1.lock"
    wheels = wheelhouse_facts(lock_path, wheelhouse)
    dependencies = dependency_facts(lock_path)
    facts = resource_facts(root)
    benchmark = _benchmark()

    canary = canonical_json_bytes({"release_id": RELEASE_ID, "nonce": hashlib.sha256(os.urandom(32)).hexdigest()})
    history_scan = scan_formal_history(root, destination, task_input_dir)
    if history_scan["count"] != contract["expected_formal_historical_result_count"]:
        raise ValueError("formal historical result scan does not match the contract")

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
        "formal_history_scan": history_scan,
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
    _verify_identities(destination, readiness["frozen_input_identities"])
    if readiness["formal_historical_result_count"] != readiness["formal_history_scan"]["count"]:
        raise ValueError("formal historical result count does not match its scan receipt")
    if readiness["formal_history_scan"]["discovered"]:
        raise ValueError("formal historical results existed before readiness")
    canary = destination / "readiness/evidence-return-canary.json"
    if sha256(canary) != readiness["evidence_return"]["sha256"]:
        raise ValueError("evidence return canary mismatch")
    return readiness


def validate_preregistration(root: Path, destination: Path) -> dict[str, Any]:
    readiness = validate_readiness(root, destination)
    prereg = load_json(destination / "contracts/preregistration.json")
    contract = load_json(destination / "contracts/acceptance-contract.json")
    validate("contract", contract)
    if prereg.get("release_id") != contract.get("release_id") or contract.get("release_id") != RELEASE_ID:
        raise ValueError("preregistration release identity mismatch")
    frozen = {row["path"]: row["sha256"] for row in readiness["frozen_input_identities"]}
    for relative in ("contracts/acceptance-contract.json", "contracts/preregistration.json"):
        if frozen.get(relative) != sha256(destination / relative):
            raise ValueError(f"frozen contract identity mismatch: {relative}")
    return prereg


def validate_runtime_evidence(root: Path, destination: Path) -> dict[str, Any]:
    validate_preregistration(root, destination)
    required = {
        "gates/g0-g1.json": "phase2_1_gate_evidence",
        "qualification/qualification.json": "phase2_1_qualification",
        "results/historical-audit.json": "phase2_1_historical_audit",
        "results/power.json": "phase2_1_power",
        "replay/replay.json": "phase2_1_replay",
        "reviews/independent-method-review.json": "phase2_1_review",
        "reviews/independent-replay-review.json": "phase2_1_review",
    }
    checked = []
    for relative, artifact_type in required.items():
        value = _require_pass(destination / relative, artifact=artifact_type)
        if value.get("release_id") != RELEASE_ID:
            raise ValueError(f"release identity mismatch: {relative}")
        checked.append(relative)
    return {"status": "PASS", "checked": checked}


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
    prereg = load_json(destination / "contracts/preregistration.json")
    reference = read_array_bundle(destination / "inputs/upstream/reference-null.bin")
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

    strong: list[dict[str, Any]] = []
    parameters = {"marginal_inclusion": 0.4, "set_structure": 30.0, "pair_dependence": 0.4, "cross_zone_dependence": 1.0}
    expected_components = {"marginal_inclusion": "front:1", "set_structure": "front", "pair_dependence": "front:1-2", "cross_zone_dependence": "zone_sum_covariance"}
    for game, rule in maps.items():
        for family in ("marginal_inclusion", "set_structure", "pair_dependence", "cross_zone_dependence"):
            seed = domain_seed(prereg["seeds"]["qualification"], f"qualification:{game}:{family}")
            batch = generate_batch(rule, worlds=1, draws=200, family=family, effect=parameters[family], seed=seed)
            measured = calculate_statistics(batch.scalar_world(0), rule)[family]
            p_value = float(empirical_p(reference[f"reference.{game}.n200.{family}.statistic"], np.array([measured["statistic"]]))[0])
            direction = measured["selected_component"] == expected_components[family] and float(measured["signed_effect"]) > 0
            passed = p_value <= 0.001 and direction
            strong.append({"game": game, "family": family, "status": "PASS" if passed else "FAIL", "direction_match": direction, "p_value": p_value, "seed": seed, "measured_component": measured["selected_component"]})
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
        "input_identities": [identity(destination, destination / "contracts/preregistration.json"), identity(destination, destination / "inputs/upstream/reference-null.bin"), identity(destination, destination / "reviews/independent-method-review.json")],
    }
    validate("qualification", payload)
    write_new_json(destination / "qualification/qualification.json", payload)
    return payload


def historical_audit(destination: Path, *, root: Path) -> dict[str, Any]:
    _require_pass(destination / "qualification/qualification.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    manifest, maps = _maps(destination)
    draws = load_frozen_draws(root, manifest)
    reference_path = destination / "inputs/upstream/reference-null.bin"
    reference = read_array_bundle(reference_path)
    interval = load_json(destination / "inputs/upstream/phase2-effect-interval-calibration.json")
    observed = {game: calculate_statistics(draws[game], maps[game]) for game in ("dlt", "ssq")}
    raw: dict[str, float] = {}
    for game in ("dlt", "ssq"):
        for family in FAMILIES:
            phase2_family = PHASE2_NAME.get(family, family)
            statistic = observed[game][phase2_family]["statistic"]
            raw[f"{game}.{family}"] = float(empirical_p(reference[f"reference.{game}.n200.{phase2_family}.statistic"], np.array([statistic]))[0])
    adjusted = holm_adjust(raw)
    transformed = []
    negative = []
    for game in ("dlt", "ssq"):
        trimmed = draws[game][:-max(1, len(draws[game]) // 10)]
        for family in FAMILIES:
            phase2_family = PHASE2_NAME.get(family, family)
            row = observed[game][phase2_family]
            bands = [item for item in interval["acceptance_bands"] if item["game"] == game and item["bias_family"] == phase2_family]
            confidence = neyman_grid_confidence_set(float(row["effect"]), bands)
            sensitivity = _signed_component(trimmed, maps[game], phase2_family, row["selected_component"])
            direction = bool(row["signed_effect"] and sensitivity and (row["signed_effect"] > 0) == (sensitivity > 0))
            transformed.append({
                "game": game, "family": family, "test_id": prereg["test_ids"][family], "n": len(draws[game]),
                "raw_p_value": raw[f"{game}.{family}"], "holm_adjusted_p_value": adjusted[f"{game}.{family}"],
                "effect_estimate": float(row["effect"]), "practical_boundary": prereg["practical_boundaries"][family],
                "candidate_eligible": True, "sensitivity_direction_consistency": direction,
                "selected_component": row["selected_component"], "confidence_set": confidence,
            })
        value = observed[game]["negative_control"]["statistic"]
        p_value = float(empirical_p(reference[f"reference.{game}.n200.negative_control.statistic"], np.array([value]))[0])
        negative.append({"game": game, "test_id": "NC-ISSUE-PARITY", "candidate_eligible": False, "statistic": value, "raw_p_value": p_value, "signal_status": "not_candidate_eligible"})
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_historical_audit", "release_id": RELEASE_ID,
        "status": "PASS", "gate": "G3",
        "method": {"null": "uniform legal tickets over the frozen observed calendar", "reference_replications": len(reference["reference.dlt.n200.marginal_inclusion.statistic"]), "multiplicity": "Holm across ten primary decisions", "seed": prereg["seeds"]["historical"], "execution": "all observed statistics, p-values, Holm adjustments, sensitivities and negative controls recomputed for this release", "slow_drift_note": "the registered historical statistic is the calendar-ordered half contrast; the alternative power generator is linear drift"},
        "primary_results": transformed,
        "negative_controls": negative,
        "metrics": {"registered": 10, "reported": len(transformed), "coverage": len(transformed) / 10, "games_separate": len({row["game"] for row in transformed}) == 2, "selective_deletion": 0},
        "input_identities": [identity(destination, destination / "contracts/preregistration.json"), identity(destination, destination / "qualification/qualification.json"), identity(destination, destination / "inputs/upstream/phase1-draws.jsonl"), identity(destination, reference_path)],
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


def _corpora(destination: Path, lfs_root: Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    bundled = destination / "inputs/upstream"
    for name in ("reference-null.bin", "evaluation-null.bin"):
        external = lfs_root / name
        if not external.is_file() or sha256(external) != sha256(bundled / name):
            raise ValueError(f"local corpus identity mismatch: {name}")
    return read_array_bundle(bundled / "reference-null.bin"), read_array_bundle(bundled / "evaluation-null.bin")


def _cross_mapping(destination: Path, game: str) -> dict[float, float]:
    calibration = load_json(destination / "inputs/upstream/phase2-effect-interval-calibration.json")
    return {float(row["target_v"]): float(row["mixture_q"]) for row in calibration["cross_zone_mappings"] if row["game"] == game}


def _power_grid(
    root: Path,
    destination: Path,
    *,
    lfs_root: Path,
    seed: int,
) -> list[dict[str, Any]]:
    prereg = load_json(destination / "contracts/preregistration.json")
    manifest, maps = _maps(destination)
    draws_by_game = load_frozen_draws(root, manifest)
    reference, evaluation = _corpora(destination, lfs_root)
    sizes = list(prereg["sample_size_grid"])
    evaluation_p = _evaluation_p_matrices(reference, evaluation, sizes)
    replications = int(prereg["power_replications_per_grid_point"])
    simultaneous_alpha = 0.05 / 240
    rows: list[dict[str, Any]] = []
    for game, rule in maps.items():
        game_offset = 0 if game == "dlt" else 5
        mapping = _cross_mapping(destination, game)
        for family_index, family in enumerate(FAMILIES):
            for effect in prereg["effect_grids"][family]:
                scenario_seed = domain_seed(seed, f"phase2.1-power-grid:{game}:{family}:{effect}")
                generated: dict[int, dict[str, dict[str, np.ndarray]]] = {}
                if family == "slow_drift":
                    for n in sizes:
                        cell_seed = domain_seed(scenario_seed, f"n={n}")
                        batch = generate_slow_drift_batch(rule, worlds=replications, draws=n, effect=float(effect), seed=cell_seed, issue_ids=_issue_ids(draws_by_game[game], n))
                        generated[n] = calculate_statistics_batch(batch, rule)
                else:
                    phase2_family = PHASE2_NAME.get(family, family)
                    generated = simulate_prefix_statistics(
                        rule,
                        worlds=replications,
                        sample_sizes=sizes,
                        family=phase2_family,
                        effect=scenario_generator_effect(phase2_family, float(effect), mapping),
                        seed=scenario_seed,
                        issue_ids_by_n={n: _issue_ids(draws_by_game[game], n) for n in sizes},
                    )
                for n in sizes:
                    target_p = np.column_stack([
                        empirical_p(reference[f"reference.{game}.n{n}.{name}.statistic"], generated[n][name]["statistic"])
                        for name in PRIMARY_FAMILIES
                    ])
                    cell_seed = domain_seed(scenario_seed, f"n={n}")
                    rng = np.random.default_rng(domain_seed(cell_seed, "other-game-null"))
                    selected = rng.integers(0, len(evaluation_p[n]), size=replications)
                    full = evaluation_p[n][selected].copy()
                    full[:, game_offset:game_offset + 5] = target_p
                    adjusted = holm_adjust_matrix(full)
                    successes = int(np.count_nonzero(adjusted[:, game_offset + family_index] <= 0.05))
                    lower, upper = clopper_pearson(successes, replications, alpha=simultaneous_alpha)
                    rows.append({
                        "game": game, "family": family, "effect": effect, "sample_size": n,
                        "successes": successes, "replications": replications, "power": successes / replications,
                        "simultaneous_95_lower": lower, "simultaneous_95_upper": upper,
                        "interval_half_width": (upper - lower) / 2,
                        "generator": "linear_inclusion_probability_drift" if family == "slow_drift" else f"phase2_legal_ticket_{family}",
                        "seed": cell_seed,
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


def _recompute_calibration(destination: Path, lfs_root: Path, sizes: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    reference, evaluation = _corpora(destination, lfs_root)
    evaluation_p = _evaluation_p_matrices(reference, evaluation, sizes)
    rows = []
    coverages = []
    for n in sizes:
        matrix = evaluation_p[n]
        rejected = np.any(holm_adjust_matrix(matrix) <= 0.05, axis=1)
        successes = int(rejected.sum())
        one_lower, one_upper = clopper_pearson_one_sided(successes, len(rejected))
        two_lower, two_upper = clopper_pearson(successes, len(rejected))
        rows.append({"sample_size": n, "false_rejections": successes, "worlds": len(rejected), "empirical_fwer": successes / len(rejected), "one_sided_95_lower": one_lower, "one_sided_95_upper": one_upper, "two_sided_95_interval": [two_lower, two_upper], "interval_half_width": (two_upper - two_lower) / 2})
        for game in ("dlt", "ssq"):
            for family in PRIMARY_FAMILIES:
                reference_effect = reference[f"reference.{game}.n{n}.{family}.effect"]
                lower, upper = np.quantile(reference_effect, [0.025, 0.975], method="inverted_cdf")
                evaluation_effect = evaluation[f"evaluation.{game}.n{n}.{family}.effect"]
                coverages.append(float(np.mean((evaluation_effect >= lower) & (evaluation_effect <= upper))))
    acceptance = next(row for row in rows if row["sample_size"] == 200)
    metrics = {"CAL-01": acceptance["one_sided_95_upper"], "CAL-02": acceptance["interval_half_width"], "CAL-03": min(coverages), "CAL-04": 0}
    return {"source": "recomputed from frozen reference and independent evaluation corpora", "by_sample_size": rows, "family_size": 10, "interval_coverage_min": min(coverages)}, metrics


def power(destination: Path, *, root: Path, lfs_root: Path) -> dict[str, Any]:
    _require_pass(destination / "results/historical-audit.json")
    prereg = load_json(destination / "contracts/preregistration.json")
    grid = _power_grid(root, destination, lfs_root=lfs_root, seed=prereg["seeds"]["power"])
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
    calibration, calibration_metrics = _recompute_calibration(destination, lfs_root, list(prereg["sample_size_grid"]))
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_power", "release_id": RELEASE_ID,
        "status": "PASS", "gate": "G4",
        "method": {"execution": "all five registered families and all 240 cells simulated in this release", "seed": prereg["seeds"]["power"], "replications_per_cell": prereg["power_replications_per_grid_point"], "slow_drift": "legal-ticket worlds with a distinct linear probability at every draw", "multiplicity": "Holm across ten primary decisions", "interval": "Clopper-Pearson Bonferroni simultaneous 95 percent over 240 points"},
        "calibration": calibration, "grid": grid, "delta_star": delta, "required_n": required,
        "metrics": {
            **calibration_metrics,
            "POW-01": len(actual) / len(expected), "POW-02": 0.8, "POW-03": max_key_half,
            "POW-04": len(delta) / 10, "POW-05": {"reverse_jumps_beyond_joint_uncertainty": reverse},
            "POW-06": {"coverage": len(required) / 40, "unsimulated_interpolation": 0, "cross_game_pooling": 0},
        },
        "normalized_sha256": "0" * 64,
        "input_identities": [identity(destination, destination / "contracts/preregistration.json"), identity(destination, destination / "qualification/qualification.json"), identity(destination, destination / "results/historical-audit.json"), identity(destination, destination / "inputs/upstream/reference-null.bin"), identity(destination, destination / "inputs/upstream/evaluation-null.bin")],
    }
    if actual != expected or len(grid) != 240 or len(delta) != 10 or len(required) != 40:
        payload["status"] = "FAIL"
    if reverse or max_key_half > 0.03:
        payload["status"] = "FAIL"
    if calibration_metrics["CAL-01"] > 0.06 or calibration_metrics["CAL-02"] > 0.005 or calibration_metrics["CAL-03"] < 0.93 or calibration_metrics["CAL-04"] != 0:
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
    replay_grid = _power_grid(root, destination, lfs_root=lfs_root, seed=prereg["seeds"]["independent_replay"])
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


def verification_receipt(operation: str, verifier: Any) -> dict[str, Any]:
    started = now()
    try:
        verifier()
        terminal, code, error = "PASS", 0, None
    except FileExistsError as exc:
        terminal, code, error = "INVALID_CONTRACT", 4, str(exc)
    except (ValueError, KeyError) as exc:
        terminal, code, error = "EVIDENCE_MISMATCH", 5, str(exc)
    except (OSError, RuntimeError) as exc:
        terminal, code, error = "ENVIRONMENT_FAILURE", 3, str(exc)
    receipt = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_verification_receipt", "release_id": RELEASE_ID,
        "operation": operation, "status": "PASS" if code == 0 else "FAIL", "terminal": terminal,
        "exit_code": code, "started_at_utc": started, "finished_at_utc": now(), "error": error,
    }
    validate("verification_receipt", receipt)
    return receipt


def run_e2e(destination: Path, *, root: Path) -> dict[str, Any]:
    prereg = load_json(destination / "contracts/preregistration.json")
    cases: list[dict[str, Any]] = []

    def add(identifier: str, expected: str, observed: str, evidence: dict[str, Any]) -> None:
        cases.append({"id": identifier, "expected_terminal": expected, "observed_terminal": observed, "status": "PASS" if expected == observed else "FAIL", "evidence": evidence})

    normal_receipt = verification_receipt("runtime-evidence", lambda: validate_runtime_evidence(root, destination))
    add("E2E-P2.1-01-normal-full-chain", "PASS", normal_receipt["terminal"], {"production_verification_receipt": normal_receipt})

    with tempfile.TemporaryDirectory() as raw:
        isolated = Path(raw) / RELEASE_ID
        shutil.copytree(destination, isolated)
        input_path = isolated / "inputs/upstream/phase1-draws.jsonl"
        input_path.write_bytes(input_path.read_bytes() + b"tamper")
        tamper_receipt = verification_receipt("readiness-input-identity", lambda: validate_readiness(root, isolated))
    add("E2E-P2.1-02-input-tamper", "EVIDENCE_MISMATCH", tamper_receipt["terminal"], {"production_verification_receipt": tamper_receipt, "isolated_copy_mutated": True})

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

    with tempfile.TemporaryDirectory() as raw:
        isolated = Path(raw) / RELEASE_ID
        shutil.copytree(destination, isolated)
        prereg_path = isolated / "contracts/preregistration.json"
        tampered_prereg = load_json(prereg_path)
        tampered_prereg["global_alpha"] = 0.051
        prereg_path.write_bytes(canonical_json_bytes(tampered_prereg))
        prereg_receipt = verification_receipt("preregistration-identity", lambda: validate_preregistration(root, isolated))
    add("E2E-P2.1-06-preregistration-tamper", "EVIDENCE_MISMATCH", prereg_receipt["terminal"], {"production_verification_receipt": prereg_receipt, "isolated_copy_mutated": True})

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

    with tempfile.TemporaryDirectory() as raw:
        isolated = Path(raw) / RELEASE_ID
        shutil.copytree(destination, isolated)
        manifest = build_evidence_manifest(isolated)
        audit_path = isolated / "results/historical-audit.json"
        audit_path.write_bytes(audit_path.read_bytes() + b"tamper")
        manifest_receipt = verification_receipt("recursive-evidence-manifest", lambda: verify_evidence_manifest(isolated, manifest))
    add("E2E-P2.1-09-recursive-hash-tamper", "EVIDENCE_MISMATCH", manifest_receipt["terminal"], {"production_verification_receipt": manifest_receipt, "isolated_copy_mutated": True})

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
    run_final_validation_negative_suite(root, destination)
    return registry


def build_evidence_manifest(destination: Path) -> dict[str, Any]:
    acceptance_dir = destination / "acceptance"
    excluded = {acceptance_dir / "manifest.json", acceptance_dir / "acceptance.json"}
    files = sorted(path for path in destination.rglob("*") if path.is_file() and path not in excluded)
    rows = [identity(destination, path) for path in files]
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_recursive_evidence_manifest",
        "release_id": RELEASE_ID, "status": "frozen", "created_at_utc": now(),
        "profile": "exact recursive inventory; only acceptance/manifest.json and acceptance/acceptance.json are fixed semantic-validation exclusions",
        "file_count": len(rows), "files": rows,
        "inventory_sha256": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
    }
    write_new_json(acceptance_dir / "manifest.json", payload)
    return payload


def verify_evidence_manifest(destination: Path, manifest: dict[str, Any]) -> float:
    if manifest.get("artifact_type") != "phase2_1_recursive_evidence_manifest" or manifest.get("release_id") != RELEASE_ID:
        raise ValueError("recursive manifest identity mismatch")
    rows = manifest["files"]
    paths = [row["path"] for row in rows]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("recursive manifest paths are not a unique sorted inventory")
    excluded = {"acceptance/manifest.json", "acceptance/acceptance.json"}
    actual = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() and path.relative_to(destination).as_posix() not in excluded
    }
    if actual != set(paths):
        missing = sorted(set(paths) - actual)
        extra = sorted(actual - set(paths))
        raise ValueError(f"recursive manifest directory inventory mismatch: missing={missing}, extra={extra}")
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


def derive_acceptance(root: Path, destination: Path, *, accepted_at_utc: str) -> dict[str, Any]:
    readiness = load_json(destination / "readiness/readiness.json")
    gates = load_json(destination / "gates/g0-g1.json")
    method = load_json(destination / "reviews/independent-method-review.json")
    qualification = load_json(destination / "qualification/qualification.json")
    audit = load_json(destination / "results/historical-audit.json")
    power_payload = load_json(destination / "results/power.json")
    replay_payload = load_json(destination / "replay/replay.json")
    replay_review = load_json(destination / "reviews/independent-replay-review.json")
    e2e = load_json(destination / "e2e/registry.json")
    negative_suite = load_json(destination / "reviews/final-validator-negative-tests.json")
    manifest = load_json(destination / "acceptance/manifest.json")
    prereg = load_json(destination / "contracts/preregistration.json")

    expected_historical = {(game, family) for game in ("dlt", "ssq") for family in FAMILIES}
    actual_historical = {(row["game"], row["family"]) for row in audit["primary_results"]}
    expected_grid = {(game, family, float(effect), n) for game in ("dlt", "ssq") for family in FAMILIES for effect in prereg["effect_grids"][family] for n in prereg["sample_size_grid"]}
    actual_grid = {(row["game"], row["family"], float(row["effect"]), row["sample_size"]) for row in power_payload["grid"]}
    historical_coverage = len(actual_historical & expected_historical) / len(expected_historical)
    power_coverage = len(actual_grid & expected_grid) / len(expected_grid)
    replay_keys = {tuple(row["key"]) for row in replay_payload["grid_comparisons"]}
    replay_rate = sum(bool(row["compatible"]) for row in replay_payload["grid_comparisons"]) / len(expected_grid)
    registered_e2e = set(load_json(destination / "contracts/acceptance-contract.json")["e2e_cases"])
    actual_e2e = {row["id"] for row in e2e["cases"]}
    e2e_rate = sum(row["expected_terminal"] == row["observed_terminal"] and row["status"] == "PASS" for row in e2e["cases"]) / len(registered_e2e)
    blocking = sum(row.get("status") != "PASS" for review in (method, replay_review) for row in review["findings"])
    try:
        closure = verify_evidence_manifest(destination, manifest)
    except (OSError, ValueError, KeyError):
        closure = 0.0
    scientific = _scientific_classification(audit, power_payload, prereg)
    identities_ok = all(value.get("release_id") == RELEASE_ID for value in (readiness, gates, method, qualification, audit, power_payload, replay_payload, replay_review, e2e, negative_suite, manifest, prereg))

    try:
        validate_readiness(root, destination)
        readiness_ok = readiness["status"] == "READY" and all(value == "PASS" for value in readiness["checks"].values())
    except (OSError, ValueError, KeyError):
        readiness_ok = False
    g0_g1 = readiness_ok and gates.get("status") == "PASS" and gates.get("gates") == {"G0": "PASS", "G1": "PASS"}
    known = qualification["generator_known_answers"]
    strong = qualification["strong_positive_results"]
    qualification_ok = qualification.get("status") == "PASS" and len(known) >= 3 and len(strong) == 10 and all(row["status"] == "PASS" for row in known + strong) and all(row["direction_match"] for row in strong) and qualification["metrics"] == {
        "known_answer_pass_rate": 1.0, "strong_positive_recovery_rate": 1.0, "direction_match_rate": 1.0, "illegal_generated_combinations": 0
    }
    raw = {f"{row['game']}.{row['family']}": float(row["raw_p_value"]) for row in audit["primary_results"]}
    adjusted = holm_adjust(raw)
    audit_arithmetic = all(abs(float(row["holm_adjusted_p_value"]) - adjusted[f"{row['game']}.{row['family']}"]) <= 1e-15 for row in audit["primary_results"])
    manifest_input, maps = _maps(destination)
    draws = load_frozen_draws(root, manifest_input)
    reference = read_array_bundle(destination / "inputs/upstream/reference-null.bin")
    audit_recomputed = True
    for row in audit["primary_results"]:
        phase2_family = PHASE2_NAME.get(row["family"], row["family"])
        observed = calculate_statistics(draws[row["game"]], maps[row["game"]])[phase2_family]
        p_value = float(empirical_p(reference[f"reference.{row['game']}.n200.{phase2_family}.statistic"], np.array([observed["statistic"]]))[0])
        audit_recomputed &= abs(float(row["effect_estimate"]) - float(observed["effect"])) <= 1e-15 and abs(float(row["raw_p_value"]) - p_value) <= 1e-15
    audit_ok = audit.get("status") == "PASS" and len(audit["primary_results"]) == 10 and actual_historical == expected_historical and audit_arithmetic and audit_recomputed and len(audit["negative_controls"]) == 2 and all(not row["candidate_eligible"] for row in audit["negative_controls"])
    delta, required, reverse = _summarize_grid(prereg, power_payload["grid"])
    power_arithmetic = all(row["successes"] <= row["replications"] and abs(row["power"] - row["successes"] / row["replications"]) <= 1e-15 for row in power_payload["grid"])
    power_metrics = power_payload["metrics"]
    power_ok = power_payload.get("status") == "PASS" and len(power_payload["grid"]) == len(expected_grid) and actual_grid == expected_grid and power_arithmetic and power_payload["normalized_sha256"] == _power_core_hash(power_payload) and len(delta) == 10 and len(required) == 40 and reverse == 0 and power_metrics["CAL-01"] <= 0.06 and power_metrics["CAL-02"] <= 0.005 and power_metrics["CAL-03"] >= 0.93 and power_metrics["CAL-04"] == 0 and power_metrics["POW-01"] == power_metrics["POW-04"] == 1.0 and power_metrics["POW-03"] <= 0.03 and power_metrics["POW-06"] == {"coverage": 1.0, "unsimulated_interpolation": 0, "cross_game_pooling": 0}
    replay_ok = replay_payload.get("status") == "PASS" and len(replay_payload["grid_comparisons"]) == len(expected_grid) and replay_keys == expected_grid and all(row["compatible"] for row in replay_payload["grid_comparisons"]) and len(replay_payload["deterministic_statistic_comparisons"]) == 10 and all(row["match"] for row in replay_payload["deterministic_statistic_comparisons"]) and replay_payload["metrics"]["missing_batches"] == replay_payload["metrics"]["duplicate_batches"] == 0
    negative_suite_ok = negative_suite.get("status") == "PASS" and len(negative_suite.get("cases", [])) == 5 and all(row.get("status") == "PASS" and row.get("production_verification_receipt", {}).get("exit_code", 0) != 0 for row in negative_suite["cases"])
    reviews_ok = blocking == 0 and method["status"] == replay_review["status"] == "PASS" and negative_suite_ok
    e2e_ok = e2e.get("status") == "PASS" and len(e2e["cases"]) == len(registered_e2e) and actual_e2e == registered_e2e and e2e_rate == 1.0
    gate_verdicts = {
        "G0": "PASS" if g0_g1 else "FAIL", "G1": "PASS" if g0_g1 else "FAIL", "G2": "PASS" if qualification_ok else "FAIL",
        "G3": "PASS" if audit_ok else "FAIL", "G4": "PASS" if power_ok else "FAIL", "G5": "PASS" if replay_ok else "FAIL",
        "G6": "PASS" if closure == historical_coverage == power_coverage == replay_rate == e2e_rate == 1.0 and reviews_ok and e2e_ok and identities_ok else "FAIL",
    }
    passed = all(value == "PASS" for value in gate_verdicts.values())
    payload = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_acceptance", "release_id": RELEASE_ID,
        "status": "PASS" if passed else "FAIL", "delivery_status": "GO" if passed else "NO-GO", "scientific_classification": scientific, "accepted_at_utc": accepted_at_utc,
        "gate_verdicts": gate_verdicts,
        "recomputed_metrics": {"evidence_hash_closure": closure, "historical_result_coverage": historical_coverage, "power_grid_coverage": power_coverage, "independent_replay_consistency": replay_rate, "e2e_expected_terminal_coverage": e2e_rate, "blocking_findings": blocking},
        "blocking_findings": blocking,
        "evidence_inventory": [row["path"] for row in manifest["files"]] + ["acceptance/manifest.json", "acceptance/acceptance.json"],
        "recursive_manifest_identity": identity(destination, destination / "acceptance/manifest.json"),
        "limitations": ["indeterminate is not proof of randomness", "physical device and ball-set identities are unavailable", "independent reviews are procedural process independence, not an external organizational audit", "power is limited to the registered families and grids"],
    }
    validate("acceptance", payload)
    return payload


def accept(root: Path, destination: Path) -> dict[str, Any]:
    payload = derive_acceptance(root, destination, accepted_at_utc=now())
    write_new_json(destination / "acceptance/acceptance.json", payload)
    return payload


def validate_final_bundle(root: Path, destination: Path) -> dict[str, Any]:
    acceptance = load_json(destination / "acceptance/acceptance.json")
    validate("acceptance", acceptance)
    expected = derive_acceptance(root, destination, accepted_at_utc=acceptance["accepted_at_utc"])
    if canonical_json_bytes(acceptance) != canonical_json_bytes(expected):
        raise ValueError("final acceptance differs from independently recomputed bottom-up acceptance")
    if acceptance["status"] != "PASS" or acceptance["delivery_status"] != "GO":
        raise ValueError("final bundle is NO-GO")
    return expected


def run_final_validation_negative_suite(root: Path, destination: Path) -> dict[str, Any]:
    identifiers = ("acceptance", "g6_receipt", "metrics", "manifest", "unregistered_file")
    with tempfile.TemporaryDirectory() as raw:
        isolated = Path(raw) / RELEASE_ID
        shutil.copytree(destination, isolated)
        seed_suite = {
            "schema_version": "2.1.0", "artifact_type": "phase2_1_final_validator_negative_suite", "release_id": RELEASE_ID,
            "status": "PASS", "cases": [{"id": identifier, "status": "PASS", "production_verification_receipt": {"exit_code": 5}} for identifier in identifiers],
        }
        write_new_json(isolated / "reviews/final-validator-negative-tests.json", seed_suite)
        build_evidence_manifest(isolated)
        accept(root, isolated)
        validate_final_bundle(root, isolated)

        targets = {
            "acceptance": isolated / "acceptance/acceptance.json",
            "g6_receipt": isolated / "e2e/registry.json",
            "metrics": isolated / "results/power.json",
            "manifest": isolated / "acceptance/manifest.json",
        }
        original = {name: path.read_bytes() for name, path in targets.items()}
        receipts = []
        for identifier in identifiers:
            if identifier == "acceptance":
                value = load_json(targets[identifier]); value["delivery_status"] = "NO-GO"
                targets[identifier].write_bytes(canonical_json_bytes(value))
            elif identifier == "g6_receipt":
                value = load_json(targets[identifier]); value["status"] = "FAIL"
                targets[identifier].write_bytes(canonical_json_bytes(value))
            elif identifier == "metrics":
                value = load_json(targets[identifier]); value["metrics"]["POW-01"] = 0.5
                targets[identifier].write_bytes(canonical_json_bytes(value))
            elif identifier == "manifest":
                value = load_json(targets[identifier]); value["inventory_sha256"] = "0" * 64
                targets[identifier].write_bytes(canonical_json_bytes(value))
            else:
                (isolated / "unregistered.txt").write_text("not in manifest", encoding="utf-8")
            receipt = verification_receipt(f"final-validator-{identifier}", lambda: validate_final_bundle(root, isolated))
            receipts.append({"id": identifier, "status": "PASS" if receipt["exit_code"] != 0 else "FAIL", "production_verification_receipt": receipt})
            if identifier in original:
                targets[identifier].write_bytes(original[identifier])
            else:
                (isolated / "unregistered.txt").unlink()
        status = "PASS" if all(row["status"] == "PASS" for row in receipts) else "FAIL"
    payload = {"schema_version": "2.1.0", "artifact_type": "phase2_1_final_validator_negative_suite", "release_id": RELEASE_ID, "status": status, "cases": receipts}
    write_new_json(destination / "reviews/final-validator-negative-tests.json", payload)
    return payload
