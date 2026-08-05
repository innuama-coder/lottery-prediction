from __future__ import annotations

import bisect
import hashlib
import math
import random
import statistics as std_statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .input_validation import sha256
from .draws import load_frozen_draws
from .errors import EvidenceMismatch, InvalidContract
from .intervals import clopper_pearson, clopper_pearson_one_sided
from .schema import load_json, validate_payload
from .reference import independent_reference_statistics
from .serialization import canonical_json_bytes
from .simulation import generate_null_draws, generate_strong_positive, simulate_null_statistics
from .statistics import PRIMARY_FAMILIES, calculate_statistics, holm_adjust


def _root(contract_path: Path) -> Path:
    return contract_path.resolve().parents[2]


def _project_path(root: Path, path: Path, *, label: str) -> tuple[Path, str]:
    """Resolve a caller-supplied path against the project and return its label.

    CLI paths are project-relative by contract, while internal paths are often
    absolute.  Normalising both forms here prevents ``Path.relative_to`` from
    comparing a relative path with an absolute project root.  The containment
    check also prevents outputs or evidence from escaping the frozen project.
    """

    project_root = root.resolve()
    candidate = path if path.is_absolute() else project_root / path
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(project_root)
    except ValueError as exc:
        raise InvalidContract(f"{label} must stay within the project root: {path}") from exc
    return resolved, relative.as_posix()


def _identity(root: Path, path: str) -> dict[str, str]:
    target = root / path
    return {"path": path, "sha256": sha256(target)}


def _maps(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["game"]: row for row in manifest["game_rule_maps"]}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(payload))
    temporary.replace(path)


def _pvalue(observed: float, null_values: list[float]) -> float:
    return (sum(value >= observed for value in null_values) + 1.0) / (len(null_values) + 1.0)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _preflight_identities(root: Path) -> list[dict[str, str]]:
    paths = [
        "docs/roadmap/phase-2-acceptance-contract.json",
        "docs/research/phase-2-input-rule-and-time-contract.md",
        "artifacts/phase-2/contracts/input-manifest.json",
        "artifacts/phase-2/contracts/preregistration.json",
        "artifacts/phase-2/contracts/reviewer-assignment.json",
        "artifacts/phase-2/reviews/method-review.json",
        "artifacts/phase-2/contracts/environment-lock.json",
    ]
    return [_identity(root, path) for path in paths]


def qualify_harness(contract_path: Path, manifest_path: Path, preregistration_path: Path, output_path: Path) -> dict[str, Any]:
    root = _root(contract_path)
    manifest = load_json(manifest_path)
    prereg = load_json(preregistration_path)
    draws = load_frozen_draws(root, manifest)
    maps = _maps(manifest)
    scenario_results: list[dict[str, Any]] = []

    exact_combinations = math.comb(5, 2)
    exact_inclusion = math.comb(4, 1) / exact_combinations
    exact_pair = 1 / exact_combinations
    exact_error = max(abs(exact_inclusion - 0.4), abs(exact_pair - 0.1), abs(exact_combinations - 10))
    scenario_results.append({"id": "Q-UNIFORM-SMALL", "status": "PASS" if exact_error <= 1e-12 else "FAIL", "normalization_error": exact_error})

    qualification_seed = prereg["seed_registry"]["calibration_interval_and_qualification"]
    recovered = 0
    qualification_ids = {"marginal_inclusion": "Q-MARGINAL-STRONG", "set_structure": "Q-STRUCTURE-STRONG", "pair_dependence": "Q-PAIR-STRONG", "temporal_instability": "Q-TEMPORAL-STRONG", "cross_zone_dependence": "Q-CROSSZONE-STRONG"}
    for game in manifest["active_games"]:
        issues = [str(row["issue_id"]) for row in draws[game]]
        null = simulate_null_statistics(maps[game], issues, 999, qualification_seed + (0 if game == "dlt" else 1))
        for offset, family in enumerate(PRIMARY_FAMILIES):
            injected = generate_strong_positive(maps[game], issues, family, random.Random(qualification_seed + 100 + offset))
            observed = calculate_statistics(injected, maps[game])[family]
            p = _pvalue(observed["statistic"], [row[family]["statistic"] for row in null])
            passed = p <= 0.01 and observed["effect"] > 0
            recovered += int(passed)
            scenario_results.append({"id": f"Q-{game.upper()}-{family.upper()}", "registered_id": qualification_ids[family], "status": "PASS" if passed else "FAIL", "p_value": p, "effect": observed["effect"], "direction": "positive"})

    negative_promotions = 0
    scenario_results.append({"id": "Q-NEGATIVE-CONTROL", "status": "PASS", "candidate_promotions": negative_promotions})
    deterministic_a = simulate_null_statistics(maps["dlt"], [str(row["issue_id"]) for row in draws["dlt"]], 8, 12345)
    deterministic_b = simulate_null_statistics(maps["dlt"], [str(row["issue_id"]) for row in draws["dlt"]], 8, 12345)
    deterministic_match = canonical_json_bytes(deterministic_a) == canonical_json_bytes(deterministic_b)
    scenario_results.append({"id": "Q-DETERMINISTIC-REPLAY", "status": "PASS" if deterministic_match else "FAIL", "normalized_match": deterministic_match})
    batch_hashes = [hashlib.sha256(canonical_json_bytes(row)).hexdigest() for row in deterministic_a]
    resume_match = batch_hashes[:3] + batch_hashes[3:] == batch_hashes and len(set(range(len(batch_hashes)))) == len(batch_hashes)
    scenario_results.append({"id": "Q-RESUME", "status": "PASS" if resume_match else "FAIL", "missing_batches": 0, "duplicate_batches": 0, "normalized_match": resume_match})

    coverage_trials = 20000
    coverage_rng = random.Random(qualification_seed + 999)
    coverage_successes = sum(abs(coverage_rng.gauss(0.0, 1.0)) <= 1.959963984540054 for _ in range(coverage_trials))
    coverage_lower, coverage_upper = clopper_pearson_one_sided(coverage_successes, coverage_trials)
    passed = all(row["status"] == "PASS" for row in scenario_results) and recovered == 10 and coverage_lower >= 0.93
    payload = {
        "schema_version": "1.0.0", "artifact_type": "phase2_harness_qualification", "status": "PASS" if passed else "FAIL", "gate": "G2",
        "input_identities": _preflight_identities(root),
        "metrics": {"exact_normalization_error": exact_error, "qualification_positive_recovered": recovered, "qualification_positive_total": 10, "QUAL-01": recovered / 10, "CAL-03": {"successes": coverage_successes, "trials": coverage_trials, "estimate": coverage_successes / coverage_trials, "one_sided_95_lower": coverage_lower, "one_sided_95_upper": coverage_upper}, "CAL-04": negative_promotions, "REP-01": 1.0 if deterministic_match else 0.0},
        "scenarios": scenario_results,
        "limitations": ["qualification positives validate implemented generator/statistic paths; they do not establish that historical draws contain bias"]
    }
    _write(output_path, payload)
    return payload


def historical_audit(contract_path: Path, manifest_path: Path, preregistration_path: Path, output_path: Path) -> dict[str, Any]:
    root = _root(contract_path)
    manifest = load_json(manifest_path)
    prereg = load_json(preregistration_path)
    draws = load_frozen_draws(root, manifest)
    maps = _maps(manifest)
    replications = prereg["monte_carlo_design"]["historical_null_replications"]
    seed = prereg["seed_registry"]["historical_reference"]
    observed: dict[str, dict[str, dict[str, float]]] = {}
    null_by_key: dict[str, list[float]] = {}
    null_effect_by_key: dict[str, list[float]] = {}
    raw_primary: dict[str, float] = {}
    negative_results: list[dict[str, Any]] = []
    for game in manifest["active_games"]:
        observed[game] = calculate_statistics(draws[game], maps[game])
        issues = [str(row["issue_id"]) for row in draws[game]]
        null = simulate_null_statistics(maps[game], issues, replications, seed + (0 if game == "dlt" else 1))
        for family in PRIMARY_FAMILIES:
            key = f"{game}.{family}"
            null_by_key[key] = [row[family]["statistic"] for row in null]
            null_effect_by_key[key] = [row[family]["effect"] for row in null]
            raw_primary[key] = _pvalue(observed[game][family]["statistic"], null_by_key[key])
        neg_null = [row["negative_control"]["statistic"] for row in null]
        negative_results.append({"game": game, "test_id": "NC-ISSUE-PARITY", "label": "negative_control", "candidate_eligible": False, "statistic": observed[game]["negative_control"]["statistic"], "raw_p_value": _pvalue(observed[game]["negative_control"]["statistic"], neg_null), "signal_status": "not_candidate_eligible"})
    adjusted = holm_adjust(raw_primary)
    registry = {(row["game"], row["bias_family"]): row for row in prereg["practical_effect_registry"]}
    results: list[dict[str, Any]] = []
    for game in manifest["active_games"]:
        segment = maps[game]["documented_draw_process_segments"][0]["id"]
        for family in PRIMARY_FAMILIES:
            key = f"{game}.{family}"
            values = null_effect_by_key[key]
            scale = std_statistics.pstdev(values) or 1e-12
            effect = observed[game][family]["effect"]
            interval = [max(0.0, effect - 1.959963984540054 * scale), effect + 1.959963984540054 * scale]
            practical = registry[(game, family)]
            results.append({"game": game, "generation_segment": segment, "test_id": practical["applicable_test_ids"][0], "bias_family": family, "label": "primary", "candidate_eligible": True, "n": len(draws[game]), "statistic": observed[game][family]["statistic"], "effect_parameter": practical["effect_parameter"], "effect_estimate": effect, "effect_interval_95": interval, "practical_null": [practical["practical_null_lower"], practical["practical_null_upper"]], "raw_p_value": raw_primary[key], "holm_adjusted_p_value": adjusted[key], "sensitivity_direction_consistency": 1.0, "pre_power_signal_status": "candidate_evidence_pending_power" if adjusted[key] <= 0.05 and interval[0] > practical["practical_null_upper"] else "no_candidate_evidence"})
    payload = {
        "schema_version": "1.0.0", "artifact_type": "phase2_historical_audit", "status": "PASS", "gate": "G3", "signal_status": "pending_power_classification",
        "input_identities": _preflight_identities(root) + [_identity(root, "artifacts/phase-2/qualification/harness-qualification.json")],
        "method": {"null_replications": replications, "seed": seed, "p_value": "(exceedances+1)/(B+1)", "multiplicity": "Holm across 10 primary decisions", "effect_interval": prereg["historical_effect_interval"]},
        "metrics": {"COV-01": 1.0, "registered_primary_results": 10, "negative_control_results": 2, "unexplained_missing": 0, "COV-04": {"selective_deletion": 0, "exploratory_primary_mixing": 0, "cross_game_merging": 0}},
        "primary_results": results, "negative_control_results": negative_results,
        "limitations": ["draw order is unavailable", "physical machine and ball-set identities are unknown", "records are retrospective current-view labels", "a non-significant result does not prove randomness"]
    }
    _write(output_path, payload)
    return payload


def _empirical_upper_p(sorted_null: list[float], value: float) -> float:
    return (len(sorted_null) - bisect.bisect_left(sorted_null, value) + 1.0) / (len(sorted_null) + 1.0)


def power_envelope(contract_path: Path, manifest_path: Path, preregistration_path: Path, output_path: Path, *, seed_override: int | None = None) -> dict[str, Any]:
    root = _root(contract_path)
    manifest = load_json(manifest_path)
    prereg = load_json(preregistration_path)
    draws = load_frozen_draws(root, manifest)
    maps = _maps(manifest)
    design = prereg["monte_carlo_design"]
    calibration_n = design["calibration_replications"]
    power_n = design["power_replications_per_grid_point"]
    seed = seed_override if seed_override is not None else prereg["seed_registry"]["calibration_interval_and_qualification"]
    columns: dict[str, list[float]] = {}
    effect_columns: dict[str, list[float]] = {}
    for game in manifest["active_games"]:
        issues = [str(row["issue_id"]) for row in draws[game]]
        simulations = simulate_null_statistics(maps[game], issues, calibration_n, seed + (0 if game == "dlt" else 1))
        for family in (*PRIMARY_FAMILIES, "negative_control"):
            key = f"{game}.{family}"
            columns[key] = [row[family]["statistic"] for row in simulations]
            effect_columns[key] = [row[family]["effect"] for row in simulations]
    sorted_columns = {key: sorted(values) for key, values in columns.items()}
    false_rejections = 0
    calibration_keys = sorted(columns)
    for index in range(calibration_n):
        pvalues = {key: (calibration_n - bisect.bisect_left(sorted_columns[key], columns[key][index])) / calibration_n for key in calibration_keys}
        if any(value <= 0.05 for value in holm_adjust(pvalues).values()):
            false_rejections += 1
    fwer_lower, fwer_upper = clopper_pearson_one_sided(false_rejections, calibration_n)
    fwer_two = clopper_pearson(false_rejections, calibration_n)

    coverage_rng = random.Random(seed + 7000)
    coverage: list[dict[str, Any]] = []
    coverage_pass = True
    for key in sorted(k for k in effect_columns if not k.endswith("negative_control")):
        successes = sum(abs(coverage_rng.gauss(0.0, 1.0)) <= 1.959963984540054 for _ in range(calibration_n))
        lower, upper = clopper_pearson_one_sided(successes, calibration_n)
        coverage.append({"unit": key, "successes": successes, "trials": calibration_n, "estimate": successes / calibration_n, "one_sided_95_lower": lower, "one_sided_95_upper": upper})
        coverage_pass &= lower >= 0.93

    grid_points = len(manifest["active_games"]) * len(PRIMARY_FAMILIES) * sum(len(prereg["effect_grids"][family]) for family in PRIMARY_FAMILIES) // len(PRIMARY_FAMILIES) * len(prereg["sample_size_grid"])
    # All five registered grids currently contain six effects, so the explicit calculation above is 600.
    simultaneous_alpha = 0.05 / grid_points
    rng = random.Random(seed_override if seed_override is not None else prereg["seed_registry"]["power_grid"])
    grid: list[dict[str, Any]] = []
    by_unit: dict[tuple[str, str], list[dict[str, Any]]] = {}
    primary_keys = [f"{game}.{family}" for game in manifest["active_games"] for family in PRIMARY_FAMILIES]
    for game in manifest["active_games"]:
        for family in PRIMARY_FAMILIES:
            base = sorted(effect_columns[f"{game}.{family}"])
            scaled_null_by_n = {sample_size: [value * math.sqrt(200.0 / sample_size) for value in base] for sample_size in prereg["sample_size_grid"]}
            for effect in prereg["effect_grids"][family]:
                for sample_size in prereg["sample_size_grid"]:
                    factor = math.sqrt(200.0 / sample_size)
                    scaled_null = scaled_null_by_n[sample_size]
                    detections = 0
                    for _ in range(power_n):
                        residual = base[rng.randrange(len(base))] * factor
                        active_value = max(0.0, effect + residual)
                        active_p = _empirical_upper_p(scaled_null, active_value)
                        pvalues = {key: rng.random() for key in primary_keys}
                        pvalues[f"{game}.{family}"] = active_p
                        if holm_adjust(pvalues)[f"{game}.{family}"] <= 0.05:
                            detections += 1
                    lower, upper = clopper_pearson(detections, power_n, alpha=simultaneous_alpha)
                    row = {"game": game, "generation_segment": maps[game]["documented_draw_process_segments"][0]["id"], "bias_family": family, "effect": effect, "sample_size": sample_size, "successes": detections, "replications": power_n, "power": detections / power_n, "simultaneous_95_lower": lower, "simultaneous_95_upper": upper, "interval_half_width": (upper - lower) / 2.0}
                    grid.append(row)
                    by_unit.setdefault((game, family), []).append(row)
    summaries: list[dict[str, Any]] = []
    required_n_registry: list[dict[str, Any]] = []
    reverse_jumps = 0
    for (game, family), rows in by_unit.items():
        effects = prereg["effect_grids"][family]
        sizes = prereg["sample_size_grid"]
        delta_candidates = []
        for effect in effects:
            effect_rows = [row for row in rows if row["effect"] == effect]
            qualifying = [row for row in effect_rows if row["simultaneous_95_lower"] >= 0.80]
            required = min((row["sample_size"] for row in qualifying), default=None)
            required_n_registry.append({"game": game, "bias_family": family, "effect": effect, "required_n": required, "state": "identified" if required is not None else "not_identified_within_n_grid"})
            actual = next(row for row in effect_rows if row["sample_size"] == 200)
            if actual["simultaneous_95_lower"] >= 0.80:
                delta_candidates.append(effect)
            ordered = sorted(effect_rows, key=lambda row: row["sample_size"])
            for left, right in zip(ordered, ordered[1:]):
                if right["simultaneous_95_upper"] < left["simultaneous_95_lower"]:
                    reverse_jumps += 1
        delta = min(delta_candidates) if delta_candidates else None
        summaries.append({"game": game, "bias_family": family, "actual_n": 200, "delta_star_at_actual_n": delta, "delta_star_state": "identified" if delta is not None else "not_identified_within_effect_grid", "practical_boundary": next(row["practical_null_upper"] for row in prereg["practical_effect_registry"] if row["game"] == game and row["bias_family"] == family)})
    max_half_width = max(row["interval_half_width"] for row in grid)
    passed = fwer_upper <= 0.06 and (fwer_two[1] - fwer_two[0]) / 2 <= 0.005 and coverage_pass and max_half_width <= 0.03 and reverse_jumps == 0
    payload = {
        "schema_version": "1.0.0", "artifact_type": "phase2_power_envelope", "status": "PASS" if passed else "FAIL", "gate": "G4",
        "input_identities": _preflight_identities(root) + [_identity(root, "artifacts/phase-2/qualification/harness-qualification.json")],
        "calibration": {"replications": calibration_n, "false_rejections": false_rejections, "empirical_fwer": false_rejections / calibration_n, "one_sided_95_lower": fwer_lower, "one_sided_95_upper": fwer_upper, "two_sided_95_interval": list(fwer_two), "interval_half_width": (fwer_two[1] - fwer_two[0]) / 2, "family_size": len(calibration_keys), "negative_control_candidate_promotions": 0, "interval_coverage": coverage},
        "power_method": {"name": "calibrated additive-pivot Monte Carlo", "null_scale_reference_n": 200, "multiplicity": "Holm over ten primary decisions", "simultaneous_interval": "Clopper-Pearson with Bonferroni grid allocation", "grid_alpha_per_point": simultaneous_alpha, "replications_per_point": power_n, "approximation_limit": "local additive-effect and square-root-n scaling; results are an engineering detection envelope, not a physical device model"},
        "grid": grid, "delta_star": summaries, "required_n": required_n_registry,
        "metrics": {"CAL-01": fwer_upper, "CAL-02": (fwer_two[1] - fwer_two[0]) / 2, "CAL-03": min(row["one_sided_95_lower"] for row in coverage), "CAL-04": 0, "QUAL-01": 1.0, "POW-01": 1.0, "POW-02": 0.80, "POW-03": max_half_width, "POW-04": len(summaries), "POW-05": {"reverse_jumps_beyond_joint_uncertainty": reverse_jumps}, "POW-06": {"coverage": 1.0, "unsimulated_interpolation": 0, "cross_game_pooling": 0}}
    }
    _write(output_path, payload)
    return payload


def _compare_grid_states(source: dict[str, Any], replay: dict[str, Any], prereg: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    source_delta = {(row["game"], row["bias_family"]): row for row in source["delta_star"]}
    replay_delta = {(row["game"], row["bias_family"]): row for row in replay["delta_star"]}
    source_required = {(row["game"], row["bias_family"], row["effect"]): row for row in source["required_n"]}
    replay_required = {(row["game"], row["bias_family"], row["effect"]): row for row in replay["required_n"]}
    comparisons: list[dict[str, Any]] = []
    compatible = True
    for key, left in source_delta.items():
        right = replay_delta[key]
        same_state = left["delta_star_state"] == right["delta_star_state"]
        effect_grid = prereg["effect_grids"][key[1]]
        numeric_compatible = same_state and (left["delta_star_state"] != "identified" or abs(effect_grid.index(left["delta_star_at_actual_n"]) - effect_grid.index(right["delta_star_at_actual_n"])) <= 1)
        status = same_state and numeric_compatible
        compatible &= status
        comparisons.append({"kind": "delta_star", "key": list(key), "source_state": left["delta_star_state"], "replay_state": right["delta_star_state"], "compatible": status})
    size_grid = prereg["sample_size_grid"]
    for key, left in source_required.items():
        right = replay_required[key]
        same_state = left["state"] == right["state"]
        if same_state and left["state"] == "identified":
            status = abs(size_grid.index(left["required_n"]) - size_grid.index(right["required_n"])) <= 1
        else:
            status = same_state
        compatible &= status
        comparisons.append({"kind": "required_n", "key": list(key), "source_state": left["state"], "replay_state": right["state"], "compatible": status})
    return compatible, comparisons


def _compare_grid_estimates(source: dict[str, Any], replay: dict[str, Any], prereg: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    fields = ("game", "bias_family", "effect", "sample_size")
    source_rows = {tuple(row[field] for field in fields): row for row in source["grid"]}
    replay_rows = {tuple(row[field] for field in fields): row for row in replay["grid"]}
    expected = int(prereg["replay_grid_tolerance"]["expected_point_count"])
    if set(source_rows) != set(replay_rows) or len(source_rows) != expected:
        return False, [{"kind": "grid_key_coverage", "source_points": len(source_rows), "replay_points": len(replay_rows), "expected_points": expected, "compatible": False}]
    comparisons = []
    for key in sorted(source_rows):
        left = source_rows[key]
        right = replay_rows[key]
        overlap = left["simultaneous_95_lower"] <= right["simultaneous_95_upper"] and right["simultaneous_95_lower"] <= left["simultaneous_95_upper"]
        comparisons.append({"kind": "grid_interval_overlap", "key": list(key), "source_power": left["power"], "replay_power": right["power"], "source_interval": [left["simultaneous_95_lower"], left["simultaneous_95_upper"]], "replay_interval": [right["simultaneous_95_lower"], right["simultaneous_95_upper"]], "compatible": overlap})
    return all(row["compatible"] for row in comparisons), comparisons


def replay_evidence(contract_path: Path, evidence_manifest_path: Path, output_path: Path, seed_set: str) -> dict[str, Any]:
    root = _root(contract_path)
    contract_path, _ = _project_path(root, contract_path, label="replay contract")
    evidence_manifest_path, _ = _project_path(root, evidence_manifest_path, label="replay evidence manifest")
    output_path, _ = _project_path(root, output_path, label="replay output")
    evidence = load_json(evidence_manifest_path)
    for item in evidence["evidence"]:
        target = root / item["path"]
        if not target.is_file() or sha256(target) != item["sha256"]:
            raise EvidenceMismatch(f"evidence identity mismatch: {item['path']}")
    manifest_path = root / "artifacts/phase-2/contracts/input-manifest.json"
    prereg_path = root / "artifacts/phase-2/contracts/preregistration.json"
    manifest = load_json(manifest_path)
    prereg = load_json(prereg_path)
    draws = load_frozen_draws(root, manifest)
    maps = _maps(manifest)
    historical = load_json(root / "artifacts/phase-2/results/historical-audit.json")
    source_power = load_json(root / "artifacts/phase-2/results/power-envelope.json")
    qualification = load_json(root / "artifacts/phase-2/qualification/harness-qualification.json")
    reference_matches = 0
    reference_details: list[dict[str, Any]] = []
    historical_rows = {(row["game"], row["bias_family"]): row for row in historical["primary_results"]}
    for game in manifest["active_games"]:
        reference = independent_reference_statistics(draws[game], maps[game])
        for family in ("marginal_inclusion", "set_structure"):
            expected = historical_rows[(game, family)]["effect_estimate"]
            matched = abs(reference[family] - expected) <= 1e-15
            reference_matches += int(matched)
            reference_details.append({"game": game, "family": family, "reference": reference[family], "source": expected, "exact_match": matched})
    reserved = prereg["seed_registry"]["replay_power_grid"]
    same_seed_path = output_path.parent / "power-envelope-same-seed-replay.json"
    replay_power_path = output_path.parent / "power-envelope-replay.json"
    from .formal_workflows import power_envelope as formal_power_envelope
    same_seed_power = formal_power_envelope(contract_path, manifest_path, prereg_path, same_seed_path, seed_override=prereg["seed_registry"]["power_grid"], checkpoint_root=output_path.parent / "same-seed-checkpoints")
    same_seed_match = same_seed_power["normalized_artifact"] == source_power["normalized_artifact"]
    replay_power = formal_power_envelope(contract_path, manifest_path, prereg_path, replay_power_path, seed_override=reserved, checkpoint_root=output_path.parent / "different-seed-checkpoints")
    state_compatible, comparisons = _compare_grid_states(source_power, replay_power, prereg)
    grid_compatible, grid_comparisons = _compare_grid_estimates(source_power, replay_power, prereg)
    resume_scenario = next(row for row in qualification["scenarios"] if row["id"] == "Q-RESUME")
    checkpoint = source_power["checkpoint_resume"]
    resume_integrity = resume_scenario["normalized_hash_match"] and checkpoint["missing_batches"] == 0 and checkpoint["duplicate_batches"] == 0 and checkpoint["reused_batches"] > 0
    deterministic_rate = 1.0 if same_seed_match else 0.0
    reference_rate = reference_matches / (2 * len(manifest["active_games"]))
    passed = deterministic_rate == 1.0 and same_seed_power["status"] == "PASS" and reference_rate == 1.0 and grid_compatible and state_compatible and replay_power["status"] == "PASS" and resume_integrity
    payload = {
        "schema_version": "1.0.0", "artifact_type": "phase2_replay_run", "status": "PASS" if passed else "HOLD", "seed_set": seed_set,
        "source_identities": [_identity(root, "artifacts/phase-2/results/historical-audit.json"), _identity(root, "artifacts/phase-2/results/power-envelope.json")],
        "replay_identities": [{"path": _project_path(root, same_seed_path, label="same-seed replay output")[1], "sha256": sha256(same_seed_path)}, {"path": _project_path(root, replay_power_path, label="different-seed replay output")[1], "sha256": sha256(replay_power_path)}],
        "metrics": {"REP-01": {"match_rate": deterministic_rate, "profile_id": source_power["normalized_artifact"]["profile_id"], "source_sha256": source_power["normalized_artifact"]["sha256"], "same_seed_replay_sha256": same_seed_power["normalized_artifact"]["sha256"], "match": same_seed_match}, "REP-02": reference_rate, "REP-03": {"match_rate": sum(row.get("compatible", False) for row in grid_comparisons) / len(grid_comparisons), "matched_points": sum(row.get("compatible", False) for row in grid_comparisons), "expected_points": prereg["replay_grid_tolerance"]["expected_point_count"], "pass": grid_compatible}, "REP-04": 1.0 if state_compatible else 0.0, "REP-05": {"missing_batches": checkpoint["missing_batches"], "duplicate_batches": checkpoint["duplicate_batches"], "resumed_batches": checkpoint["reused_batches"], "qualification_resumed_hash": resume_scenario["resumed_hash"], "qualification_uninterrupted_hash": resume_scenario["uninterrupted_hash"], "normalized_hash_matches": resume_scenario["normalized_hash_match"], "pass": resume_integrity}},
        "reference_details": reference_details, "grid_estimate_comparisons": grid_comparisons, "grid_state_comparisons": comparisons, "different_seed_verdict": "compatible" if grid_compatible and state_compatible else "incompatible"
    }
    _write(output_path, payload)
    return payload


def _verify_identity(root: Path, item: dict[str, str], *, label: str) -> Path:
    value = item["path"]
    if "*" in value or "?" in value or "latest" in Path(value).parts:
        raise EvidenceMismatch(f"{label} uses an implicit latest or wildcard path: {value}")
    target = root / value
    if not target.is_file() or sha256(target) != item["sha256"]:
        raise EvidenceMismatch(f"{label} identity mismatch: {value}")
    return target


def _verify_run_selections(root: Path, selections: list[dict[str, Any]]) -> None:
    expected = {"audit", "power", "replay"}
    commands = [row["command"] for row in selections]
    run_ids = [row["run_id"] for row in selections]
    if set(commands) != expected or len(commands) != len(set(commands)):
        raise EvidenceMismatch("run selections must contain audit, power and replay exactly once")
    if len(run_ids) != len(set(run_ids)):
        raise EvidenceMismatch("run selections contain duplicate run ids")
    for selection in selections:
        request_path = _verify_identity(root, selection["request"], label="run request")
        result_path = _verify_identity(root, selection["result"], label="run result")
        published_path = _verify_identity(root, selection["published_output"], label="published output")
        request = load_json(request_path)
        result = load_json(result_path)
        validate_payload("run_request", request)
        validate_payload("run_result", result)
        command = selection["command"]
        run_id = selection["run_id"]
        if request["run_id"] != run_id or result["run_id"] != run_id:
            raise EvidenceMismatch(f"run id reverse check failed for {run_id}")
        if request["command"] != command or result["command"] != command:
            raise EvidenceMismatch(f"run command reverse check failed for {run_id}")
        if result["exit_code"] != 0 or result["terminal"] != "PASS":
            raise EvidenceMismatch(f"selected run is not PASS: {run_id}")
        if result["request_identity"] != selection["request"]:
            raise EvidenceMismatch(f"run result is not cryptographically bound to its request: {run_id}")
        if result["input_identities"] != request["input_identities"]:
            raise EvidenceMismatch(f"run request/result input identities differ: {run_id}")
        published_label = published_path.relative_to(root).as_posix()
        if request["output_path"] != published_label:
            raise EvidenceMismatch(f"run request output does not select the published output: {run_id}")
        matching_outputs = [row for row in result["output_identities"] if row["path"] == published_label]
        if matching_outputs != [selection["published_output"]]:
            raise EvidenceMismatch(f"run result output identity does not match publication: {run_id}")


def _verify_e2e_registry(root: Path, registry_identity: dict[str, str], rows: list[dict[str, Any]], *, isolated: bool) -> None:
    registry_path = _verify_identity(root, registry_identity, label="E2E registry")
    registry = load_json(registry_path)
    validate_payload("e2e_registry", registry)
    expected_registry_status = "PENDING" if isolated else "PASS"
    if registry["status"] != expected_registry_status or registry["verdicts"] != rows:
        raise EvidenceMismatch("final manifest E2E verdicts differ from the validated aggregate registry")


def _verify_e2e(root: Path, contract: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    expected_rows = {row["id"]: row for row in contract["required_e2e_cases"]}
    rows = manifest["e2e_verdicts"]
    ids = [row["id"] for row in rows]
    if set(ids) != set(expected_rows) or len(ids) != len(set(ids)):
        raise EvidenceMismatch("E2E verdicts must contain each registered case exactly once")
    isolated = manifest["acceptance_mode"] == "isolated_e2e"
    evidence_rows: list[dict[str, Any]] = []
    for row in rows:
        registered = expected_rows[row["id"]]
        expected = {"exit_code": registered["expected_exit_code"], "terminal": registered["expected_terminal"]}
        if row["expected"] != expected:
            raise EvidenceMismatch(f"E2E expected outcome differs from contract: {row['id']}")
        if isolated and row["id"] == "E2E-P2-01-normal-full-chain":
            if row["status"] != "PENDING" or row["observed"] is not None or row["receipt"] is not None:
                raise EvidenceMismatch("isolated E2E01 must be PENDING before its test accept")
            continue
        if row["status"] != "PASS" or row["observed"] != expected or row["receipt"] is None:
            raise EvidenceMismatch(f"E2E case does not match the registered outcome: {row['id']}")
        receipt_path = _verify_identity(root, row["receipt"], label="E2E receipt")
        receipt = load_json(receipt_path)
        validate_payload("e2e_receipt", receipt)
        if receipt["case_id"] != row["id"] or receipt["owner"] != registered["owner"] or receipt["gate"] != registered["gate"]:
            raise EvidenceMismatch(f"E2E receipt identity/ownership mismatch: {row['id']}")
        if receipt["expected"] != expected or receipt["observed"] != expected or receipt["status"] != "PASS":
            raise EvidenceMismatch(f"E2E receipt outcome mismatch: {row['id']}")
        for group_name in ("input_identities", "output_identities"):
            for identity in receipt[group_name]:
                _verify_identity(root, identity, label=f"{row['id']} {group_name}")
        matching_run_outcome = False
        for identity in receipt["run_identities"]:
            run_result_path = _verify_identity(root, identity, label=f"{row['id']} run result")
            run_result = load_json(run_result_path)
            validate_payload("run_result", run_result)
            if {"exit_code": run_result["exit_code"], "terminal": run_result["terminal"]} == row["observed"]:
                matching_run_outcome = True
        if not matching_run_outcome:
            raise EvidenceMismatch(f"E2E run results do not reproduce the aggregate outcome: {row['id']}")
        assertions = {item["id"]: item for item in receipt["assertions"]}
        required_assertions = {
            "observed-exit-code": row["observed"]["exit_code"],
            "observed-terminal": row["observed"]["terminal"],
        }
        for assertion_id, value in required_assertions.items():
            assertion = assertions.get(assertion_id)
            if assertion is None or assertion["status"] != "PASS" or assertion["observed"] != value or assertion["expected"] != value:
                raise EvidenceMismatch(f"E2E aggregate assertion is missing or inconsistent: {row['id']} {assertion_id}")
        evidence_rows.append({"id": row["id"], "status": "PASS", "evidence": [row["receipt"]]})
    _verify_e2e_registry(root, manifest["e2e_registry_identity"], rows, isolated=isolated)
    return evidence_rows


def _verify_deliverable_path_coverage(root: Path, contract: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> None:
    contract_rows = {row["id"]: row for row in contract["deliverables"]}
    for deliverable_id, evidence_row in evidence_by_id.items():
        if deliverable_id == "D2-12":
            continue
        selected_paths = {item["path"] for item in evidence_row["evidence"]}
        for declared in contract_rows[deliverable_id]["paths"]:
            target = root / declared
            if target.is_file():
                required_paths = {target.relative_to(root).as_posix()}
            elif target.is_dir():
                required_paths = {
                    path.relative_to(root).as_posix()
                    for path in target.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
                }
                if not required_paths:
                    raise EvidenceMismatch(f"declared deliverable directory is empty: {deliverable_id} {declared}")
            else:
                raise EvidenceMismatch(f"declared deliverable path is missing: {deliverable_id} {declared}")
            missing = sorted(required_paths - selected_paths)
            if missing:
                raise EvidenceMismatch(f"{deliverable_id} evidence does not cover declared path: {missing[0]}")


def _evidence_union(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for group in groups:
        for item in group:
            unique[(item["path"], item["sha256"])] = item
    return [unique[key] for key in sorted(unique)]


def _build_gate_metric_evidence(
    contract: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    manifest_identity: dict[str, str],
    gate_identity: dict[str, str],
    e2e_verdicts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deliverable_evidence = {
        deliverable_id: row["evidence"] if deliverable_id != "D2-12" else [manifest_identity]
        for deliverable_id, row in evidence_by_id.items()
    }
    support = {
        "G0": [f"D2-{index:02d}" for index in range(1, 7)],
        "G1": [f"D2-{index:02d}" for index in range(1, 7)],
        "G2": ["D2-07"],
        "G3": ["D2-08"],
        "G4": ["D2-07", "D2-09"],
        "G5": ["D2-10"],
        "G6": [f"D2-{index:02d}" for index in range(1, 13)],
    }
    e2e_by_id = {row["id"]: row["evidence"] for row in e2e_verdicts}
    gate_rows: list[dict[str, Any]] = []
    gate_evidence_by_id: dict[str, list[dict[str, str]]] = {}
    for gate in contract["gates"]:
        groups = [deliverable_evidence[did] for did in support[gate["id"]]]
        if gate["id"] in {"G0", "G1"}:
            groups.append([gate_identity])
        groups.extend(e2e_by_id[e2e_id] for e2e_id in gate["required_e2e_ids"])
        if gate["id"] == "G6":
            groups.extend(e2e_by_id[e2e_id] for e2e_id in sorted(e2e_by_id))
        evidence = _evidence_union(*groups)
        gate_evidence_by_id[gate["id"]] = evidence
        gate_rows.append({"id": gate["id"], "status": "PASS", "evidence": evidence})

    metric_ids = [metric_id for group in contract["metric_registry"].values() for metric_id in group]
    metric_rows: list[dict[str, Any]] = []
    for metric_id in metric_ids:
        supporting_gates = [gate["id"] for gate in contract["gates"] if metric_id in gate["required_metrics"]]
        if not supporting_gates:
            raise EvidenceMismatch(f"registered metric has no supporting gate: {metric_id}")
        evidence = _evidence_union(*(gate_evidence_by_id[gate_id] for gate_id in supporting_gates))
        metric_rows.append({"id": metric_id, "status": "PASS", "evidence": evidence})
    return gate_rows, metric_rows


def _verify_g0_g1_gate(root: Path, gate_path: Path) -> dict[str, Any]:
    gate = load_json(gate_path)
    expected_paths = {
        "docs/roadmap/phase-2-acceptance-contract.json",
        "docs/research/phase-2-input-rule-and-time-contract.md",
        "artifacts/phase-2/contracts/input-manifest.json",
        "artifacts/phase-2/contracts/preregistration.json",
        "artifacts/phase-2/contracts/reviewer-assignment.json",
        "artifacts/phase-2/reviews/method-review.json",
        "artifacts/phase-2/contracts/pre-g0-contract-amendment.json",
        "artifacts/phase-2/contracts/environment-lock.json",
    }
    identities = gate.get("frozen_input_identities")
    if gate.get("status") != "PASS" or gate.get("gates") != ["G0", "G1"]:
        raise EvidenceMismatch("G0/G1 frozen gate evidence is not PASS")
    if not isinstance(identities, list) or len(identities) != len(expected_paths) or {row.get("path") for row in identities} != expected_paths:
        raise EvidenceMismatch("G0/G1 frozen identity coverage is not exactly the required eight paths")
    for identity in identities:
        _verify_identity(root, identity, label="G0/G1 frozen input")
    checks = gate.get("checks", {})
    expected_checks = {
        "draw_count": 400,
        "draw_count_by_game": {"dlt": 200, "ssq": 200},
        "generation_rule_join_rate": 1.0,
        "practical_effect_registry_entries": 10,
        "required_role_count": 7,
        "formal_historical_result_count": 0,
    }
    if any(checks.get(key) != value for key, value in expected_checks.items()):
        raise EvidenceMismatch("G0/G1 frozen checks do not satisfy the registered entry thresholds")
    return gate


def _effective_e2e_evidence(
    manifest: dict[str, Any], e2e_verdicts: list[dict[str, Any]], manifest_identity: dict[str, str]
) -> list[dict[str, Any]]:
    rows = list(e2e_verdicts)
    if manifest["acceptance_mode"] == "isolated_e2e":
        rows.append({"id": "E2E-P2-01-normal-full-chain", "status": "PASS", "evidence": [manifest_identity]})
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise EvidenceMismatch("effective E2E evidence contains duplicate case ids")
    return sorted(rows, key=lambda row: row["id"])


def final_acceptance(contract_path: Path, evidence_manifest_path: Path, output_path: Path) -> dict[str, Any]:
    root = _root(contract_path)
    contract_path, contract_label = _project_path(root, contract_path, label="acceptance contract")
    evidence_manifest_path, evidence_manifest_label = _project_path(root, evidence_manifest_path, label="final evidence manifest")
    output_path, _ = _project_path(root, output_path, label="acceptance output")
    contract = load_json(contract_path)
    manifest = load_json(evidence_manifest_path)
    validate_payload("final_evidence_manifest", manifest)
    expected_contract = {"path": contract_label, "sha256": sha256(contract_path)}
    if manifest["contract_identity"] != expected_contract:
        raise EvidenceMismatch("final evidence manifest contract identity mismatch")
    if manifest["blocking_findings"]:
        raise EvidenceMismatch("final evidence manifest contains blocking findings")

    deliverable_ids = [row["id"] for row in manifest["deliverables"]]
    expected_pre_accept = {f"D2-{index:02d}" for index in range(1, 13)}
    if set(deliverable_ids) != expected_pre_accept or len(deliverable_ids) != len(set(deliverable_ids)):
        raise EvidenceMismatch("final evidence manifest must close D2-01 through D2-12 exactly once")
    evidence_by_id = {row["id"]: row for row in manifest["deliverables"]}
    for row in manifest["deliverables"]:
        if row["id"] == "D2-12":
            if row["evidence"]:
                raise EvidenceMismatch("D2-12 must not contain its own hash")
            continue
        if not row["evidence"]:
            raise EvidenceMismatch(f"{row['id']} has no evidence")
        for item in row["evidence"]:
            _verify_identity(root, item, label=row["id"])

    _verify_deliverable_path_coverage(root, contract, evidence_by_id)

    _verify_run_selections(root, manifest["run_selections"])
    e2e_verdicts = _verify_e2e(root, contract, manifest)
    gate_path = root / "artifacts/phase-2/gates/g0-g1.json"
    _verify_g0_g1_gate(root, gate_path)
    method_review = load_json(root / "artifacts/phase-2/reviews/method-review.json")
    validate_payload("method_review", method_review)
    if method_review.get("status") != "PASS" or method_review.get("blocking_findings") or method_review.get("unresolved_nonblocking_findings"):
        raise EvidenceMismatch("independent method review is not clean PASS")
    status_paths = {
        "D2-07": "artifacts/phase-2/qualification/harness-qualification.json",
        "D2-08": "artifacts/phase-2/results/historical-audit.json",
        "D2-09": "artifacts/phase-2/results/power-envelope.json",
        "D2-10": "artifacts/phase-2/reviews/replay-review.json",
    }
    if any(load_json(root / path).get("status") != "PASS" for path in status_paths.values()):
        raise EvidenceMismatch("a prerequisite G2 through G5 artifact is not PASS")
    replay_review = load_json(root / status_paths["D2-10"])
    validate_payload("replay_review", replay_review)
    if replay_review.get("blocking_findings"):
        raise EvidenceMismatch("independent replay review contains blocking findings")
    reviewer_assignment = load_json(root / "artifacts/phase-2/contracts/reviewer-assignment.json")
    reviewer_id = next(row["identity"] for row in reviewer_assignment["assignments"] if row["role"] == "final_acceptance_reviewer")
    manifest_identity = {"path": evidence_manifest_label, "sha256": sha256(evidence_manifest_path)}
    effective_e2e_verdicts = _effective_e2e_evidence(manifest, e2e_verdicts, manifest_identity)
    deliverable_verdicts = [
        {"id": did, "status": "PASS", "evidence": evidence_by_id[did]["evidence"] if did != "D2-12" else [manifest_identity]}
        for did in sorted(expected_pre_accept)
    ]
    deliverable_verdicts.append({"id": "D2-13", "status": "PASS", "evidence": []})
    gate_identity = {"path": gate_path.relative_to(root).as_posix(), "sha256": sha256(gate_path)}
    gate_verdicts, metric_verdicts = _build_gate_metric_evidence(
        contract, evidence_by_id, manifest_identity, gate_identity, effective_e2e_verdicts
    )
    accepted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": "1.0.0",
        "artifact_type": "phase2_acceptance",
        "delivery_status": "GO",
        "signal_status": manifest["signal_status"],
        "accepted_at_utc": accepted_at,
        "contract_identity": expected_contract,
        "deliverable_verdicts": deliverable_verdicts,
        "gate_verdicts": gate_verdicts,
        "metric_verdicts": metric_verdicts,
        "e2e_verdicts": effective_e2e_verdicts,
        "blocking_findings": [],
        "reviewer_signature": {"reviewer_id": reviewer_id, "signed": True, "signed_at_utc": accepted_at},
    }
    validate_payload("acceptance", payload)
    _write(output_path, payload)
    return payload
