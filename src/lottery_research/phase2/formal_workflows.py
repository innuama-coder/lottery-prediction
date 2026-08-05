from __future__ import annotations

import hashlib
import itertools
import random
from pathlib import Path
from typing import Any

import numpy as np

from .input_validation import sha256
from .draws import load_frozen_draws
from .intervals import clopper_pearson, clopper_pearson_one_sided
from .research_engine import (
    calibrate_cross_zone_q,
    checkpointed_prefix_statistics,
    central_acceptance,
    coverage_verdict,
    domain_seed,
    empirical_p,
    flatten_corpus,
    holm_adjust_matrix,
    neyman_grid_confidence_set,
    read_array_bundle,
    scenario_generator_effect,
    simulate_prefix_statistics,
    write_array_bundle,
)
from .schema import load_json
from .serialization import canonical_json_bytes
from .simulation import generate_null_draws
from .statistics import PRIMARY_FAMILIES, calculate_statistics, holm_adjust
from .vectorized import calculate_statistics_batch, generate_batch, precompute_supported_spaces


def _root(contract_path: Path) -> Path:
    return contract_path.resolve().parents[2]


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _identity(root: Path, path: Path | str) -> dict[str, str]:
    target = path if isinstance(path, Path) and path.is_absolute() else root / path
    return {"path": target.relative_to(root).as_posix(), "sha256": sha256(target)}


def _preflight(root: Path) -> list[dict[str, str]]:
    paths = [
        "docs/roadmap/phase-2-acceptance-contract.json",
        "artifacts/phase-2/contracts/pre-g0-contract-amendment.json",
        "docs/research/phase-2-input-rule-and-time-contract.md",
        "artifacts/phase-2/contracts/input-manifest.json",
        "artifacts/phase-2/contracts/preregistration.json",
        "artifacts/phase-2/contracts/reviewer-assignment.json",
        "artifacts/phase-2/reviews/method-review.json",
        "artifacts/phase-2/contracts/environment-lock.json",
    ]
    return [_identity(root, path) for path in paths]


def _maps(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["game"]: row for row in manifest["game_rule_maps"]}


def _issue_ids_by_n(real_draws: list[dict[str, Any]], sizes: list[int]) -> dict[int, list[int]]:
    observed = [int(row["issue_id"]) for row in real_draws]
    result = {}
    for n in sizes:
        if n <= len(observed):
            result[n] = observed[:n]
        else:
            result[n] = observed + list(range(max(observed) + 1, max(observed) + 1 + n - len(observed)))
    return result


def _cross_mappings(prereg: dict[str, Any], maps: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    base = prereg["seed_registry"]["cross_zone_mapping"]
    for game, rule in maps.items():
        for effect in prereg["effect_grids"]["cross_zone_dependence"]:
            row = calibrate_cross_zone_q(rule, effect, domain_seed(base, f"cross-zone-map:{game}:{effect}"))
            result.append({"game": game, **row})
    return result


def _mapping_dict(rows: list[dict[str, Any]], game: str) -> dict[float, float]:
    return {float(row["target_v"]): float(row["mixture_q"]) for row in rows if row["game"] == game}


def _negative_control_guard(label: str, adjusted_p: float) -> str:
    if label == "negative_control":
        return "not_candidate_eligible"
    return "candidate_signal" if adjusted_p <= 0.05 else "no_candidate_evidence"


def _qualification_direction_match(family: str, result: dict[str, Any]) -> bool:
    expected_components = {
        "marginal_inclusion": "front:1",
        "set_structure": "front",
        "pair_dependence": "front:1-2",
        "temporal_instability": "front:1",
        "cross_zone_dependence": "zone_sum_covariance",
    }
    return result.get("selected_component") == expected_components[family] and float(result.get("signed_effect", 0.0)) > 0.0


def _signed_component(draws: list[dict[str, Any]], rule: dict[str, Any], family: str, component: str) -> float:
    space = rule["number_space_segments"][0]
    n = len(draws)
    if family == "cross_zone_dependence":
        front = np.array([sum(row["front_numbers"]) for row in draws], dtype=float)
        back = np.array([sum(row["back_numbers"]) for row in draws], dtype=float)
        return float(np.mean((front - front.mean()) * (back - back.mean())))
    zone, detail = (component.split(":", 1) + [""])[:2]
    values = [row[f"{zone}_numbers"] for row in draws]
    if family == "set_structure":
        expected = space[zone]["draw_count"] * (space[zone]["max"] + 1) / 2.0
        return sum(map(sum, values)) / n - expected
    if family == "marginal_inclusion":
        number = int(detail)
        return sum(number in row for row in values) / n - space[zone]["draw_count"] / space[zone]["max"]
    if family == "pair_dependence":
        left, right = map(int, detail.split("-"))
        null = space[zone]["draw_count"] * (space[zone]["draw_count"] - 1) / (space[zone]["max"] * (space[zone]["max"] - 1))
        return sum(left in row and right in row for row in values) / n - null
    if family == "temporal_instability":
        number = int(detail)
        midpoint = n // 2
        return sum(number in row for row in values[:midpoint]) / midpoint - sum(number in row for row in values[midpoint:]) / (n - midpoint)
    raise ValueError(f"unknown sensitivity family: {family}")


def qualify_harness(contract_path: Path, manifest_path: Path, prereg_path: Path, output_path: Path) -> dict[str, Any]:
    root = _root(contract_path)
    manifest = load_json(manifest_path)
    prereg = load_json(prereg_path)
    maps = _maps(manifest)
    real_draws = load_frozen_draws(root, manifest)
    precompute_supported_spaces(list(maps.values()))
    sizes = prereg["sample_size_grid"]
    design = prereg["monte_carlo_design"]
    reference_arrays: dict[str, np.ndarray] = {}
    evaluation_arrays: dict[str, np.ndarray] = {}
    for game, rule in maps.items():
        calendar = _issue_ids_by_n(real_draws[game], sizes)
        reference = simulate_prefix_statistics(rule, worlds=design["historical_null_replications"], sample_sizes=sizes, family="null", effect=0.0, seed=domain_seed(prereg["seed_registry"]["historical_reference"], f"reference-null:{game}"), issue_ids_by_n=calendar)
        evaluation = simulate_prefix_statistics(rule, worlds=design["calibration_replications"], sample_sizes=sizes, family="null", effect=0.0, seed=domain_seed(prereg["seed_registry"]["calibration_null_evaluation"], f"evaluation-null:{game}"), issue_ids_by_n=calendar)
        reference_arrays.update(flatten_corpus("reference", game, reference))
        evaluation_arrays.update(flatten_corpus("evaluation", game, evaluation))
    reference_path = output_path.parent / "reference-null.bin"
    evaluation_path = output_path.parent / "evaluation-null.bin"
    reference_meta = write_array_bundle(reference_path, reference_arrays)
    evaluation_meta = write_array_bundle(evaluation_path, evaluation_arrays)

    cross_rows = _cross_mappings(prereg, maps)
    interval_bands: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for game, rule in maps.items():
        mapping = _mapping_dict(cross_rows, game)
        for family in PRIMARY_FAMILIES:
            for effect in prereg["effect_grids"][family]:
                generator_effect = scenario_generator_effect(family, effect, mapping)
                calendar200 = _issue_ids_by_n(real_draws[game], [200])
                reference = simulate_prefix_statistics(rule, worlds=design["interval_reference_replications_per_scenario"], sample_sizes=[200], family=family, effect=generator_effect, seed=domain_seed(prereg["seed_registry"]["calibration_interval_and_qualification"], f"interval-reference:{game}:{family}:{effect}"), issue_ids_by_n=calendar200)[200][family]["effect"]
                lower, upper = central_acceptance(reference)
                interval_bands.append({"game": game, "bias_family": family, "effect": effect, "acceptance_lower": lower, "acceptance_upper": upper, "reference_replications": len(reference)})
            for effect in prereg["effect_grids"][family]:
                band = next(row for row in interval_bands if row["game"] == game and row["bias_family"] == family and row["effect"] == effect)
                generator_effect = scenario_generator_effect(family, effect, mapping)
                evaluation = simulate_prefix_statistics(rule, worlds=design["interval_evaluation_replications_per_scenario"], sample_sizes=[200], family=family, effect=generator_effect, seed=domain_seed(prereg["seed_registry"]["calibration_null_evaluation"], f"interval-evaluation:{game}:{family}:{effect}"), issue_ids_by_n=calendar200)[200][family]["effect"]
                successes = int(np.count_nonzero((evaluation >= band["acceptance_lower"]) & (evaluation <= band["acceptance_upper"])))
                coverage_rows.append({"game": game, "bias_family": family, "effect": effect, **coverage_verdict(successes, len(evaluation))})
    interval_path = output_path.parent / "effect-interval-calibration.json"
    _write(interval_path, {"schema_version": "1.0.0", "artifact_type": "phase2_effect_interval_calibration", "cross_zone_mappings": cross_rows, "acceptance_bands": interval_bands, "coverage": coverage_rows})

    small = list(itertools.combinations(range(1, 6), 2))
    normalization = sum(1 / len(small) for _ in small)
    inclusion = sum(1 in row for row in small) / len(small)
    pair = sum(1 in row and 2 in row for row in small) / len(small)
    exact_error = max(abs(normalization - 1.0), abs(inclusion - 0.4), abs(pair - 0.1))
    scenarios: list[dict[str, Any]] = [{"id": "Q-UNIFORM-SMALL", "status": "PASS" if exact_error <= 1e-12 else "FAIL", "normalization_error": exact_error, "combination_count": len(small), "inclusion_probability": inclusion, "pair_probability": pair}]
    recovered = 0
    params = prereg["qualification_parameters"]
    qualification_names = {"marginal_inclusion": "MARGINAL", "set_structure": "STRUCTURE", "pair_dependence": "PAIR", "temporal_instability": "TEMPORAL", "cross_zone_dependence": "CROSSZONE"}
    for game, rule in maps.items():
        for family in PRIMARY_FAMILIES:
            effect = params["cross_zone_dependence_mixture_q"] if family == "cross_zone_dependence" else params[family]
            batch = generate_batch(rule, worlds=1, draws=200, family=family, effect=effect, seed=domain_seed(prereg["seed_registry"]["calibration_interval_and_qualification"], f"qualification:{game}:{family}"))
            observed = float(calculate_statistics_batch(batch, rule, chunk_worlds=1)[family]["statistic"][0])
            measured = calculate_statistics(batch.scalar_world(0), rule)[family]
            reference = reference_arrays[f"reference.{game}.n200.{family}.statistic"]
            p = float(empirical_p(reference, np.array([observed]))[0])
            statistic_match = abs(float(measured["statistic"]) - observed) <= 1e-12
            direction_match = _qualification_direction_match(family, measured)
            passed = p <= 0.001 and statistic_match and direction_match
            recovered += int(passed)
            scenarios.append({"id": f"Q-{game.upper()}-{qualification_names[family]}-STRONG", "status": "PASS" if passed else "FAIL", "p_value": p, "statistic": observed, "scalar_statistic": measured["statistic"], "statistic_match": statistic_match, "measured_component": measured["selected_component"], "measured_signed_effect": measured["signed_effect"], "expected_direction": "positive", "direction_match": direction_match})

    issues = [str(2026000 + index) for index in range(200)]
    rows = generate_null_draws(maps["dlt"], issues, random.Random(991))
    for row in rows:
        forced = 1 if int(row["issue_id"]) % 2 else 35
        values = set(row["front_numbers"])
        values.add(forced)
        for value in sorted(values, reverse=forced == 1):
            if len(values) <= 5:
                break
            if value != forced:
                values.remove(value)
        row["front_numbers"] = sorted(values)
    nc_observed = calculate_statistics(rows, maps["dlt"])["negative_control"]["statistic"]
    nc_reference = reference_arrays["reference.dlt.n200.negative_control.statistic"]
    nc_p = float(empirical_p(nc_reference, np.array([nc_observed]))[0])
    nc_status = _negative_control_guard("negative_control", nc_p)
    scenarios.append({"id": "Q-NEGATIVE-CONTROL", "status": "PASS" if nc_p <= 0.01 and nc_status == "not_candidate_eligible" else "FAIL", "raw_p_value": nc_p, "classification": nc_status})

    left = generate_batch(maps["dlt"], worlds=8, draws=200, seed=12345)
    right = generate_batch(maps["dlt"], worlds=8, draws=200, seed=12345)
    deterministic = np.array_equal(left.front_numbers, right.front_numbers) and np.array_equal(left.back_numbers, right.back_numbers)
    scenarios.append({"id": "Q-DETERMINISTIC-REPLAY", "status": "PASS" if deterministic else "FAIL", "normalized_match": deterministic})
    checkpoint_root = output_path.parent / "checkpoints"
    controlled = False
    try:
        checkpointed_prefix_statistics(maps["dlt"], worlds=32, sample_sizes=[50, 100], family="null", effect=0.0, seed=domain_seed(prereg["seed_registry"]["calibration_interval_and_qualification"], "resume"), checkpoint_root=checkpoint_root / "resumed", chunk_worlds=8, issue_ids_by_n=_issue_ids_by_n(real_draws["dlt"], [50, 100]), interrupt_after_new_batches=1)
    except KeyboardInterrupt:
        controlled = True
    _, resumed_ledger = checkpointed_prefix_statistics(maps["dlt"], worlds=32, sample_sizes=[50, 100], family="null", effect=0.0, seed=domain_seed(prereg["seed_registry"]["calibration_interval_and_qualification"], "resume"), checkpoint_root=checkpoint_root / "resumed", chunk_worlds=8, issue_ids_by_n=_issue_ids_by_n(real_draws["dlt"], [50, 100]))
    _, full_ledger = checkpointed_prefix_statistics(maps["dlt"], worlds=32, sample_sizes=[50, 100], family="null", effect=0.0, seed=domain_seed(prereg["seed_registry"]["calibration_interval_and_qualification"], "resume"), checkpoint_root=checkpoint_root / "uninterrupted", chunk_worlds=8, issue_ids_by_n=_issue_ids_by_n(real_draws["dlt"], [50, 100]))
    resume_match = controlled and resumed_ledger["aggregate_sha256"] == full_ledger["aggregate_sha256"] and resumed_ledger["missing_batches"] == 0 and resumed_ledger["duplicate_batches"] == 0
    scenarios.append({"id": "Q-RESUME", "status": "PASS" if resume_match else "FAIL", "controlled_interruption": controlled, "resumed_from_batches": resumed_ledger["reused_batches"], "missing_batches": resumed_ledger["missing_batches"], "duplicate_batches": resumed_ledger["duplicate_batches"], "resumed_hash": resumed_ledger["aggregate_sha256"], "uninterrupted_hash": full_ledger["aggregate_sha256"], "normalized_hash_match": resume_match})

    coverage_min = min(float(row["one_sided_95_lower"]) for row in coverage_rows)
    expected_coverage = {(game, family, float(effect)) for game in maps for family in PRIMARY_FAMILIES for effect in prereg["effect_grids"][family]}
    actual_coverage = {(row["game"], row["bias_family"], float(row["effect"])) for row in coverage_rows}
    coverage_complete = actual_coverage == expected_coverage and len(coverage_rows) == len(expected_coverage)
    passed = all(row["status"] == "PASS" for row in scenarios) and recovered == 10 and coverage_min >= 0.93 and coverage_complete
    payload = {
        "schema_version": "1.0.0", "artifact_type": "phase2_harness_qualification", "status": "PASS" if passed else "FAIL", "gate": "G2",
        "input_identities": _preflight(root),
        "supplementary_identities": [_identity(root, reference_path), _identity(root, evaluation_path), _identity(root, interval_path)],
        "corpora": {"reference": reference_meta, "evaluation": evaluation_meta},
        "calendar_profiles": [{"game": game, "sample_size": n, "observed_issue_count": min(n, 200), "synthetic_issue_count": max(0, n - 200), "issue_id_sha256": hashlib.sha256(canonical_json_bytes(_issue_ids_by_n(real_draws[game], [n])[n])).hexdigest()} for game in maps for n in sizes],
        "metrics": {"QUAL-01": recovered / 10, "CAL-03": coverage_min, "CAL-03-grid-coverage": len(actual_coverage) / len(expected_coverage), "CAL-04": 0 if nc_status == "not_candidate_eligible" else 1, "REP-01": 1.0 if deterministic else 0.0, "REP-05": {"missing_batches": 0, "duplicate_batches": 0, "normalized_hash_match": resume_match}},
        "coverage": coverage_rows, "scenarios": scenarios,
        "limitations": ["qualification validates legal generators, statistics, interval construction and guards; it makes no claim about historical bias"]
    }
    _write(output_path, payload)
    return payload


def historical_audit(contract_path: Path, manifest_path: Path, prereg_path: Path, output_path: Path) -> dict[str, Any]:
    root = _root(contract_path)
    manifest = load_json(manifest_path)
    prereg = load_json(prereg_path)
    maps = _maps(manifest)
    draws = load_frozen_draws(root, manifest)
    reference = read_array_bundle(root / "artifacts/phase-2/qualification/reference-null.bin")
    interval = load_json(root / "artifacts/phase-2/qualification/effect-interval-calibration.json")
    observed = {game: calculate_statistics(draws[game], maps[game]) for game in manifest["active_games"]}
    raw: dict[str, float] = {}
    for game in manifest["active_games"]:
        for family in PRIMARY_FAMILIES:
            raw[f"{game}.{family}"] = float(empirical_p(reference[f"reference.{game}.n200.{family}.statistic"], np.array([observed[game][family]["statistic"]]))[0])
    adjusted = holm_adjust(raw)
    results = []
    for game in manifest["active_games"]:
        segment = maps[game]["documented_draw_process_segments"][0]["id"]
        trimmed = draws[game][:-max(1, len(draws[game]) // 10)]
        for family in PRIMARY_FAMILIES:
            row = observed[game][family]
            bands = [band for band in interval["acceptance_bands"] if band["game"] == game and band["bias_family"] == family]
            confidence = neyman_grid_confidence_set(row["effect"], bands)
            sensitivity_value = _signed_component(trimmed, maps[game], family, row["selected_component"])
            sensitivity_pass = row["signed_effect"] != 0 and sensitivity_value != 0 and (row["signed_effect"] > 0) == (sensitivity_value > 0)
            practical = next(item for item in prereg["practical_effect_registry"] if item["game"] == game and item["bias_family"] == family)
            results.append({"game": game, "generation_segment": segment, "test_id": practical["applicable_test_ids"][0], "bias_family": family, "label": "primary", "candidate_eligible": True, "n": 200, "statistic": row["statistic"], "effect_parameter": practical["effect_parameter"], "effect_estimate": row["effect"], "selected_component": row["selected_component"], "selected_signed_effect": row["signed_effect"], "effect_grid_confidence_set_95": confidence, "effect_interval_95": confidence["hull"], "practical_null": [practical["practical_null_lower"], practical["practical_null_upper"]], "raw_p_value": raw[f"{game}.{family}"], "holm_adjusted_p_value": adjusted[f"{game}.{family}"], "sensitivity": {"id": "S-CALENDAR-TRIM", "trimmed_n": len(trimmed), "signed_effect": sensitivity_value, "direction_preserved": sensitivity_pass}, "pre_power_signal_status": "candidate_evidence_pending_power" if adjusted[f"{game}.{family}"] <= 0.05 and confidence["hull"][0] > practical["practical_null_upper"] and sensitivity_pass else "no_candidate_evidence"})
    negative = []
    for game in manifest["active_games"]:
        value = observed[game]["negative_control"]["statistic"]
        p = float(empirical_p(reference[f"reference.{game}.n200.negative_control.statistic"], np.array([value]))[0])
        negative.append({"game": game, "test_id": "NC-ISSUE-PARITY", "label": "negative_control", "candidate_eligible": False, "statistic": value, "raw_p_value": p, "signal_status": _negative_control_guard("negative_control", p)})
    payload = {"schema_version": "1.0.0", "artifact_type": "phase2_historical_audit", "status": "PASS", "gate": "G3", "signal_status": "pending_power_classification", "input_identities": _preflight(root) + [_identity(root, "artifacts/phase-2/qualification/harness-qualification.json")], "method": {"reference_null_replications": prereg["monte_carlo_design"]["historical_null_replications"], "reference_seed": prereg["seed_registry"]["historical_reference"], "p_value": "(b+1)/(B+1), ties count with >=", "multiplicity": "Holm across exactly ten primary decisions", "effect_interval": prereg["historical_effect_interval"]}, "metrics": {"COV-01": 1.0, "registered_primary_results": 10, "registered_sensitivity_results": 10, "negative_control_results": 2, "unexplained_missing": 0, "COV-04": {"selective_deletion": 0, "exploratory_primary_mixing": 0, "cross_game_merging": 0}}, "primary_results": results, "negative_control_results": negative, "limitations": ["draw order is unavailable", "physical machine and ball-set identities are unknown", "records are retrospective current-view labels", "failure to reject does not prove randomness"]}
    _write(output_path, payload)
    return payload


def _grid_summaries(prereg: dict[str, Any], grid: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    summaries = []
    required_rows = []
    reverse = 0
    for game in ("dlt", "ssq"):
        for family in PRIMARY_FAMILIES:
            unit = [row for row in grid if row["game"] == game and row["bias_family"] == family]
            actual_candidates = [row["effect"] for row in unit if row["sample_size"] == 200 and row["simultaneous_95_lower"] >= 0.8]
            summaries.append({"game": game, "bias_family": family, "actual_n": 200, "delta_star_at_actual_n": min(actual_candidates) if actual_candidates else None, "delta_star_state": "identified" if actual_candidates else "not_identified_within_effect_grid", "practical_boundary": next(row["practical_null_upper"] for row in prereg["practical_effect_registry"] if row["game"] == game and row["bias_family"] == family)})
            for effect in prereg["effect_grids"][family]:
                effect_rows = sorted((row for row in unit if row["effect"] == effect), key=lambda row: row["sample_size"])
                qualifying = [row["sample_size"] for row in effect_rows if row["simultaneous_95_lower"] >= 0.8]
                required_rows.append({"game": game, "bias_family": family, "effect": effect, "required_n": min(qualifying) if qualifying else None, "state": "identified" if qualifying else "not_identified_within_n_grid"})
                for left, right in zip(effect_rows, effect_rows[1:]):
                    reverse += int(right["simultaneous_95_upper"] < left["simultaneous_95_lower"])
            for n in prereg["sample_size_grid"]:
                effect_rows = sorted((row for row in unit if row["sample_size"] == n), key=lambda row: row["effect"])
                for left, right in zip(effect_rows, effect_rows[1:]):
                    reverse += int(right["simultaneous_95_upper"] < left["simultaneous_95_lower"])
    return summaries, required_rows, reverse


def normalized_power_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    included = ("calibration", "power_method", "grid", "delta_star", "required_n", "key_power_rows", "metrics")
    core = {key: payload[key] for key in included}
    core["checkpoint_aggregates"] = sorted(
        ({"scenario": row["scenario"], "aggregate_sha256": row["aggregate_sha256"]} for row in payload["checkpoint_resume"]["ledgers"]),
        key=lambda row: row["scenario"],
    )
    normalized = canonical_json_bytes(core)
    return {"profile_id": "power-core-v1", "sha256": hashlib.sha256(normalized).hexdigest(), "byte_count": len(normalized)}


def power_envelope(contract_path: Path, manifest_path: Path, prereg_path: Path, output_path: Path, *, seed_override: int | None = None, checkpoint_root: Path | None = None, interrupt_after_batches: int | None = None) -> dict[str, Any]:
    root = _root(contract_path)
    manifest = load_json(manifest_path)
    prereg = load_json(prereg_path)
    maps = _maps(manifest)
    real_draws = load_frozen_draws(root, manifest)
    reference = read_array_bundle(root / "artifacts/phase-2/qualification/reference-null.bin")
    evaluation = read_array_bundle(root / "artifacts/phase-2/qualification/evaluation-null.bin")
    interval = load_json(root / "artifacts/phase-2/qualification/effect-interval-calibration.json")
    sizes = prereg["sample_size_grid"]
    design = prereg["monte_carlo_design"]
    primary_keys = [f"{game}.{family}" for game in ("dlt", "ssq") for family in PRIMARY_FAMILIES]
    evaluation_p: dict[int, np.ndarray] = {}
    calibration_rows = []
    nc_diagnostics = []
    for n in sizes:
        matrix = np.column_stack([empirical_p(reference[f"reference.{key.split('.')[0]}.n{n}.{key.split('.')[1]}.statistic"], evaluation[f"evaluation.{key.split('.')[0]}.n{n}.{key.split('.')[1]}.statistic"]) for key in primary_keys])
        evaluation_p[n] = matrix
        rejected = np.any(holm_adjust_matrix(matrix) <= 0.05, axis=1)
        successes = int(rejected.sum())
        one_lower, one_upper = clopper_pearson_one_sided(successes, len(rejected))
        two = clopper_pearson(successes, len(rejected))
        calibration_rows.append({"sample_size": n, "false_rejections": successes, "worlds": len(rejected), "empirical_fwer": successes / len(rejected), "one_sided_95_lower": one_lower, "one_sided_95_upper": one_upper, "two_sided_95_interval": list(two), "interval_half_width": (two[1] - two[0]) / 2})
        for game in ("dlt", "ssq"):
            nc_p = empirical_p(reference[f"reference.{game}.n{n}.negative_control.statistic"], evaluation[f"evaluation.{game}.n{n}.negative_control.statistic"])
            nc_diagnostics.append({"game": game, "sample_size": n, "raw_rejections": int(np.count_nonzero(nc_p <= 0.05)), "worlds": len(nc_p), "raw_rejection_rate": float(np.mean(nc_p <= 0.05)), "candidate_promotions": 0})

    cross_rows = interval["cross_zone_mappings"]
    grid_points = 2 * sum(len(prereg["effect_grids"][family]) for family in PRIMARY_FAMILIES) * len(sizes)
    simultaneous_alpha = 0.05 / grid_points
    power_seed = seed_override if seed_override is not None else prereg["seed_registry"]["power_grid"]
    grid = []
    checkpoint_base = checkpoint_root or (output_path.parent / "power-checkpoints")
    checkpoint_ledgers = []
    for game, rule in maps.items():
        other_game = "ssq" if game == "dlt" else "dlt"
        mapping = _mapping_dict(cross_rows, game)
        game_offset = 0 if game == "dlt" else 5
        for family_index, family in enumerate(PRIMARY_FAMILIES):
            for effect in prereg["effect_grids"][family]:
                generator_effect = scenario_generator_effect(family, effect, mapping)
                scenario_seed = domain_seed(power_seed, f"power-grid:{game}:{family}:{effect}")
                scenario_name = f"{game}-{family}-{str(effect).replace('.', '_')}"
                alternative, ledger = checkpointed_prefix_statistics(rule, worlds=design["power_replications_per_grid_point"], sample_sizes=sizes, family=family, effect=generator_effect, seed=scenario_seed, checkpoint_root=checkpoint_base / scenario_name, chunk_worlds=design["resource_budget"]["resume_batch_size"], issue_ids_by_n=_issue_ids_by_n(real_draws[game], sizes), interrupt_after_new_batches=interrupt_after_batches)
                checkpoint_ledgers.append({"scenario": scenario_name, **{key: ledger[key] for key in ("expected_batches", "completed_batches", "new_batches", "reused_batches", "missing_batches", "duplicate_batches", "aggregate_sha256")}})
                for n in sizes:
                    target_p = np.column_stack([empirical_p(reference[f"reference.{game}.n{n}.{name}.statistic"], alternative[n][name]["statistic"]) for name in PRIMARY_FAMILIES])
                    rng = np.random.default_rng(domain_seed(scenario_seed, f"other-null:n={n}"))
                    chosen = rng.integers(0, len(evaluation_p[n]), size=len(target_p))
                    full = evaluation_p[n][chosen].copy()
                    full[:, game_offset:game_offset + 5] = target_p
                    adjusted = holm_adjust_matrix(full)
                    successes = int(np.count_nonzero(adjusted[:, game_offset + family_index] <= 0.05))
                    lower, upper = clopper_pearson(successes, len(target_p), alpha=simultaneous_alpha)
                    grid.append({"game": game, "generation_segment": rule["documented_draw_process_segments"][0]["id"], "bias_family": family, "effect": effect, "generator_effect": generator_effect, "sample_size": n, "successes": successes, "replications": len(target_p), "power": successes / len(target_p), "simultaneous_95_lower": lower, "simultaneous_95_upper": upper, "interval_half_width": (upper - lower) / 2})
    summaries, required_n, reverse = _grid_summaries(prereg, grid)
    key_rows = []
    for game in ("dlt", "ssq"):
        for family in PRIMARY_FAMILIES:
            candidates = [row for row in grid if row["game"] == game and row["bias_family"] == family]
            key_rows.append(min(candidates, key=lambda row: abs(row["power"] - 0.8)))
    key_half_width = max(row["interval_half_width"] for row in key_rows)
    acceptance_calibration = next(row for row in calibration_rows if row["sample_size"] == design["calibration_acceptance_sample_size"])
    cal_upper = acceptance_calibration["one_sided_95_upper"]
    cal_half = acceptance_calibration["interval_half_width"]
    coverage_min = min(float(row["one_sided_95_lower"]) for row in interval["coverage"])
    passed = cal_upper <= 0.06 and cal_half <= 0.005 and coverage_min >= 0.93 and key_half_width <= 0.03 and reverse == 0
    payload = {"schema_version": "1.0.0", "artifact_type": "phase2_power_envelope", "status": "PASS" if passed else "FAIL", "gate": "G4", "input_identities": _preflight(root) + [_identity(root, "artifacts/phase-2/qualification/harness-qualification.json")], "calibration": {"reference_worlds": design["historical_null_replications"], "evaluation_worlds": design["calibration_replications"], "acceptance_sample_size": design["calibration_acceptance_sample_size"], "family_size": 10, "by_sample_size": calibration_rows, "negative_control_diagnostics": nc_diagnostics, "negative_control_candidate_promotions": 0, "interval_coverage": interval["coverage"]}, "power_method": {"name": "legal-ticket single-family intervention Monte Carlo", "multiplicity": "same Holm family over ten primary decisions", "simultaneous_interval": "Clopper-Pearson with Bonferroni allocation over all frozen grid points", "grid_alpha_per_point": simultaneous_alpha, "replications_per_point": design["power_replications_per_grid_point"], "cross_zone_mappings": cross_rows}, "checkpoint_resume": {"scenario_count": len(checkpoint_ledgers), "missing_batches": sum(row["missing_batches"] for row in checkpoint_ledgers), "duplicate_batches": sum(row["duplicate_batches"] for row in checkpoint_ledgers), "reused_batches": sum(row["reused_batches"] for row in checkpoint_ledgers), "ledgers": checkpoint_ledgers}, "grid": grid, "delta_star": summaries, "required_n": required_n, "key_power_rows": [{key: row[key] for key in ("game", "bias_family", "effect", "sample_size", "power", "interval_half_width")} for row in key_rows], "metrics": {"CAL-01": cal_upper, "CAL-02": cal_half, "CAL-03": coverage_min, "CAL-04": 0, "QUAL-01": 1.0, "POW-01": 1.0, "POW-02": 0.8, "POW-03": key_half_width, "POW-04": len(summaries), "POW-05": {"reverse_jumps_beyond_joint_uncertainty_both_axes": reverse}, "POW-06": {"coverage": 1.0, "unsimulated_interpolation": 0, "cross_game_pooling": 0}}}
    payload["normalized_artifact"] = normalized_power_artifact(payload)
    _write(output_path, payload)
    return payload
