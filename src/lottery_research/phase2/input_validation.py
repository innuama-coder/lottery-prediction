from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import EvidenceMismatch, InvalidContract, Rejected
from .schema import load_json, validate_payload


REQUIRED_ASSERTIONS = {
    "ASSERT-P2-IN-01",
    "ASSERT-P2-IN-02",
    "ASSERT-P2-IN-04",
    "ASSERT-P2-IN-05",
    "ASSERT-P2-MECH-01",
}

EXPECTED_STATISTICAL_UNIT = "one DrawRecord is one draw; SourceObservation rows are provenance only and never increase n"
EXPECTED_FORBIDDEN_FIELDS = {
    "evidence_links", "revision_id", "supersedes_revision_id", "core_fact_sha256",
    "source_observation_count", "future_draw_numbers",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(base: Path, raw: str) -> Path:
    value = Path(raw)
    return value if value.is_absolute() else base / value


def _check_identity(base: Path, identity: dict[str, Any]) -> None:
    path = _resolve(base, identity["path"])
    if not path.is_file():
        raise EvidenceMismatch(f"evidence path does not exist: {identity['path']}")
    if sha256(path) != identity["sha256"]:
        raise EvidenceMismatch(f"evidence hash mismatch: {identity['path']}")


def _segments(issue: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["issue_start"] <= issue <= row["issue_end"]]


def _validate_draws(project_root: Path, manifest: dict[str, Any]) -> tuple[int, dict[str, int]]:
    draws_identity = manifest["upstream"]["draws"]
    _check_identity(project_root, draws_identity)
    path = _resolve(project_root, draws_identity["path"])
    maps = {item["game"]: item for item in manifest["game_rule_maps"]}
    seen: set[tuple[str, str]] = set()
    counts = {"dlt": 0, "ssq": 0}
    joined = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                draw = json.loads(line)
                game = draw["game"]
                issue_text = str(draw["issue_id"])
                issue = int(issue_text)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise Rejected(f"invalid DrawRecord at line {line_number}: {exc}") from exc
            key = (game, issue_text)
            if key in seen:
                raise Rejected(f"duplicate DrawRecord: {game}/{issue_text}")
            seen.add(key)
            if game not in maps:
                raise Rejected(f"DrawRecord has no game rule map: {game}")
            counts[game] += 1
            if draw.get("knowledge_class") != "retrospective_current_view" or draw.get("available_at_utc") is not None:
                raise Rejected(f"point-in-time contract violation: {game}/{issue_text}")
            rule_map = maps[game]
            spaces = _segments(issue, rule_map["number_space_segments"])
            processes = _segments(issue, rule_map["documented_draw_process_segments"])
            prizes = _segments(issue, rule_map["prize_rule_segments"])
            if (len(spaces), len(processes), len(prizes)) != (1, 1, 1):
                raise Rejected(
                    f"rule mapping cardinality for {game}/{issue_text}: "
                    f"number_space={len(spaces)}, process={len(processes)}, prize={len(prizes)}"
                )
            space = spaces[0]
            for zone_name, numbers_key in (("front", "front_numbers"), ("back", "back_numbers")):
                spec = space[zone_name]
                numbers = draw[numbers_key]
                if (
                    len(numbers) != spec["draw_count"]
                    or len(set(numbers)) != len(numbers)
                    or not all(spec["min"] <= value <= spec["max"] for value in numbers)
                ):
                    raise Rejected(f"illegal {zone_name} numbers for {game}/{issue_text}")
            joined += 1
    if joined != 400 or counts != {"dlt": 200, "ssq": 200}:
        raise Rejected(f"unexpected statistical-unit counts: total={joined}, by_game={counts}")
    if draws_identity["count"] != joined or draws_identity["count_by_game"] != counts:
        raise EvidenceMismatch("declared draw counts do not match recomputation")
    return joined, counts


def _validate_preregistration(manifest: dict[str, Any], preregistration: dict[str, Any]) -> None:
    expected_grid_tolerance = {
        "expected_point_count": 240,
        "key": ["game", "bias_family", "effect", "sample_size"],
        "rule": "source and independent-seed simultaneous Clopper-Pearson intervals must overlap at every frozen grid point",
        "required_match_rate": 1.0,
        "per_run_familywise_alpha": 0.05,
    }
    if preregistration["replay_grid_tolerance"] != expected_grid_tolerance:
        raise Rejected("independent-seed replay grid tolerance differs from the implemented joint comparison")
    expected_seed_domains = [
        "reference-null:{game}", "evaluation-null:{game}",
        "interval-reference:{game}:{family}:{effect}", "interval-evaluation:{game}:{family}:{effect}",
        "power-grid:{game}:{family}:{effect}", "cross-zone-map:{game}:{effect}",
        "common-random-numbers", "qualification:{game}:{family}", "resume", "n={sample_size}",
        "checkpoint-batch={batch_index}", "other-null:n={sample_size}",
    ]
    if preregistration["seed_derivation"]["domains"] != expected_seed_domains:
        raise Rejected("seed derivation domain registry differs from the executable hierarchical domains")
    expected_replay_profile = {
        "id": "power-core-v1",
        "source_seed_role": "power_grid",
        "included_sections": ["calibration", "power_method", "grid", "delta_star", "required_n", "key_power_rows", "metrics"],
        "checkpoint_projection": "sorted scenario and aggregate_sha256 pairs",
    }
    if preregistration["replay_artifact_profile"] != expected_replay_profile:
        raise Rejected("normalized replay artifact profile differs from the implemented power-core-v1 profile")
    expected_prefix_policy = {
        "non_temporal": "generate_once_at_max_n_and_evaluate_registered_prefixes",
        "temporal_instability": "generate_independently_at_each_n_with_domain_separated_n_seed",
    }
    if preregistration["power_prefix_policy"] != expected_prefix_policy:
        raise Rejected("power prefix policy differs from the implemented estimand-preserving policy")
    seed_values = list(preregistration["seed_registry"].values())
    if len(seed_values) != len(set(seed_values)):
        raise Rejected("every registered seed purpose must use a distinct scalar seed")
    tests = {item["id"]: item for item in preregistration["test_registry"]}
    if len(tests) != len(preregistration["test_registry"]):
        raise Rejected("duplicate test id in preregistration")
    candidate_ids = {key for key, item in tests.items() if item["candidate_eligible"]}
    generation = {
        item["game"]: item["documented_draw_process_segments"][0]["id"]
        for item in manifest["game_rule_maps"]
        if len(item["documented_draw_process_segments"]) == 1
    }
    covered: set[tuple[str, str]] = set()
    keys: set[tuple[str, str, str, str]] = set()
    for entry in preregistration["practical_effect_registry"]:
        key = tuple(entry[field] for field in ("game", "generation_segment", "bias_family", "effect_parameter"))
        if key in keys:
            raise Rejected(f"duplicate practical-effect key: {key}")
        keys.add(key)
        if generation.get(entry["game"]) != entry["generation_segment"]:
            raise Rejected(f"unknown generation segment in practical-effect registry: {key}")
        if not (entry["practical_null_lower"] <= entry["null_value"] <= entry["practical_null_upper"]):
            raise Rejected(f"practical-null interval excludes null: {key}")
        registered_grid = preregistration["effect_grids"].get(entry["bias_family"])
        if registered_grid is None or entry["effect_grid"] != registered_grid:
            raise Rejected(f"practical-effect grid differs from the executable family grid: {key}")
        for test_id in entry["applicable_test_ids"]:
            if test_id not in tests:
                raise Rejected(f"unknown applicable test id: {test_id}")
            if test_id in candidate_ids:
                covered.add((entry["game"], test_id))
    expected = {(game, test_id) for game in manifest["active_games"] for test_id in candidate_ids}
    if covered != expected:
        raise Rejected(f"practical-effect coverage mismatch: missing={sorted(expected - covered)}")


def _validate_roles(contract: dict[str, Any], assignment: dict[str, Any]) -> None:
    rows = assignment["assignments"]
    by_role = {row["role"]: row for row in rows}
    expected = set(contract["role_separation"]["roles"])
    if set(by_role) != expected or len(rows) != len(expected):
        raise Rejected("reviewer assignment does not cover each required role exactly once")
    for left, right in contract["role_separation"]["conflicts_forbidden"]:
        if by_role[left]["identity"] == by_role[right]["identity"]:
            raise Rejected(f"forbidden reviewer conflict: {left}/{right}")
    if any(not row["signature"]["signed"] for row in rows):
        raise Rejected("every formal role assignment must be signed")


def validate_formal_inputs(
    *,
    contract_path: Path,
    input_rule_contract_path: Path,
    input_manifest_path: Path,
    preregistration_path: Path,
    reviewer_assignment_path: Path,
    schema_root: Path | None = None,
    require_no_formal_results: bool = True,
) -> dict[str, Any]:
    contract = load_json(contract_path)
    if contract.get("contract_version") != "1.3.0" or contract.get("status") != "planned_not_executed":
        raise InvalidContract("unsupported or unexpectedly executed Phase 2 acceptance contract")
    project_root = contract_path.resolve().parents[2]
    readiness = load_json(project_root / contract["pre_gate_readiness"]["readiness_path"])
    manifest = load_json(input_manifest_path)
    preregistration = load_json(preregistration_path)
    assignment = load_json(reviewer_assignment_path)
    amendment_path = project_root / "artifacts/phase-2/contracts/pre-g0-contract-amendment.json"
    amendment = load_json(amendment_path)
    validate_payload("input_manifest", manifest, schema_root)
    validate_payload("preregistration", preregistration, schema_root)
    validate_payload("reviewer_assignment", assignment, schema_root)
    if amendment.get("status") != "frozen_before_g0" or amendment.get("amendment_id") != "P2-A01":
        raise InvalidContract("P2-A01 pre-G0 amendment is not frozen")
    if amendment["base_contract"] != {"path": contract_path.resolve().relative_to(project_root).as_posix(), "version": contract["contract_version"], "sha256": sha256(contract_path)}:
        raise EvidenceMismatch("P2-A01 base contract identity mismatch")
    if datetime.fromisoformat(preregistration["frozen_at_utc"].replace("Z", "+00:00")) <= datetime.fromisoformat(amendment["frozen_at_utc"].replace("Z", "+00:00")):
        raise Rejected("preregistration must be frozen after the effective pre-G0 amendment")

    try:
        document = input_rule_contract_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidContract(f"cannot read input rule contract: {exc}") from exc
    missing_assertions = sorted(assertion for assertion in REQUIRED_ASSERTIONS if assertion not in document)
    if missing_assertions:
        raise InvalidContract(f"input rule contract missing assertions: {missing_assertions}")

    expected_upstream = {
        "phase1_contract": (contract["upstream"]["phase1_contract_ref"], contract["upstream"]["phase1_contract_sha256"]),
        "phase1_final": (contract["upstream"]["phase1_final_ref"], contract["upstream"]["phase1_final_sha256"]),
        "draws": (contract["upstream"]["baseline"]["draws_ref"], contract["upstream"]["baseline"]["draws_sha256"]),
        "observations": (contract["upstream"]["baseline"]["observations_ref"], contract["upstream"]["baseline"]["observations_sha256"]),
        "manifest": (contract["upstream"]["baseline"]["manifest_ref"], contract["upstream"]["baseline"]["manifest_sha256"]),
    }
    for name, (path, digest) in expected_upstream.items():
        identity = manifest["upstream"][name]
        if (identity["path"], identity["sha256"]) != (path, digest):
            raise EvidenceMismatch(f"{name} identity differs from acceptance contract")
        _check_identity(project_root, identity)
    if manifest["upstream"]["observations"]["count"] != contract["upstream"]["baseline"]["observation_count"]:
        raise EvidenceMismatch("declared observation count differs from acceptance contract")
    if manifest["statistical_unit"] != EXPECTED_STATISTICAL_UNIT:
        raise Rejected("SourceObservation sample inflation or statistical-unit contract change")
    if set(manifest["forbidden_statistical_matrix_fields"]) != EXPECTED_FORBIDDEN_FIELDS:
        raise Rejected("point-in-time leakage guard field set was changed")
    point_policy = manifest["point_in_time_policy"]
    if point_policy["knowledge_class"] != "retrospective_current_view" or not point_policy["available_at_utc_is_null"]:
        raise Rejected("point-in-time use contract was changed")
    expected_schema_freeze = next(
        item for item in readiness["upstream_identities"] if item["path"].endswith("spec-bundle-freeze.json")
    )
    if manifest["upstream"]["schema_freeze"] != expected_schema_freeze:
        raise EvidenceMismatch("Phase 1 schema-freeze identity differs from P2-00A readiness")
    _check_identity(project_root, manifest["upstream"]["schema_freeze"])
    expected_r2 = readiness["R2_source_path_and_sha256"]
    if {key: manifest["R2_source"][key] for key in ("path", "sha256")} != expected_r2:
        raise EvidenceMismatch("R2 identity differs from P2-00A readiness")
    _check_identity(project_root, expected_r2)
    if any(item["generation_null_blockers"] for item in manifest["game_rule_maps"]):
        raise Rejected("generation-null blockers must be empty before freeze")
    if any(not item["mechanism_metadata_status"].startswith("unknown_") for item in manifest["game_rule_maps"]):
        raise Rejected("unknown physical mechanism metadata must remain explicit")
    evidence_ids = {item["id"] for item in manifest["rule_evidence"]}
    for rule_map in manifest["game_rule_maps"]:
        for group in ("documented_draw_process_segments", "prize_rule_segments", "promotions"):
            for segment in rule_map[group]:
                if set(segment.get("evidence_ref_ids", [])) - evidence_ids:
                    raise Rejected(f"rule segment references unknown evidence: {segment['id']}")
                if group in {"prize_rule_segments", "promotions"} and segment.get("generation_split") is not False:
                    raise Rejected(f"prize or promotion segment cannot create a generation split: {segment['id']}")
    joined, counts = _validate_draws(project_root, manifest)
    _validate_preregistration(manifest, preregistration)
    _validate_roles(contract, assignment)
    formal_result_count = sum((project_root / path).exists() for path in (
        "artifacts/phase-2/results/historical-audit.json",
        "artifacts/phase-2/results/power-envelope.json",
    ))
    if require_no_formal_results and formal_result_count:
        raise EvidenceMismatch("formal historical result exists before G0/G1/G2")
    return {
        "contract_version": contract["contract_version"],
        "draw_count": joined,
        "draw_count_by_game": counts,
        "generation_rule_join_rate": 1.0,
        "practical_effect_registry_entries": len(preregistration["practical_effect_registry"]),
        "required_role_count": len(assignment["assignments"]),
        "formal_historical_result_count": formal_result_count,
        "effective_contract_amendment": "P2-A01",
    }
