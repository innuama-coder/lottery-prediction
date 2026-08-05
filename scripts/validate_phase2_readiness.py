#!/usr/bin/env python3
"""Read-only validator for the Phase 2 P2-00A readiness handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

READY = 0
INVALID_CONTRACT = 4
EVIDENCE_MISMATCH = 5
HOLD = 20

EFFECT_FIELDS = {
    "game",
    "generation_segment",
    "bias_family",
    "effect_parameter",
    "parameter_definition",
    "unit",
    "direction",
    "null_value",
    "practical_null_lower",
    "practical_null_upper",
    "grid_transform",
    "threshold_rationale",
    "applicable_test_ids",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def project_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return root / path


def identity_mismatches(root: Path, identities: list[dict[str, str]]) -> list[str]:
    failures: list[str] = []
    for identity in identities:
        path = project_path(root, identity["path"])
        if not path.is_file():
            failures.append(f"missing:{identity['path']}")
        elif sha256(path) != identity["sha256"]:
            failures.append(f"sha256:{identity['path']}")
    return failures


def matching_segments(issue: int, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        segment
        for segment in segments
        if int(segment["issue_start"]) <= issue <= int(segment["issue_end"])
    ]


def validate_input_draft(root: Path, draft: dict[str, Any]) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    required = {
        "schema_version",
        "artifact_type",
        "status",
        "statistical_unit",
        "active_games",
        "upstream",
        "R2_source",
        "point_in_time_policy",
        "forbidden_statistical_matrix_fields",
        "game_rule_maps",
        "rule_evidence",
    }
    if not required.issubset(draft):
        failures.append("input_draft_missing_required_fields")
        return 0, 1, failures

    maps = {entry.get("game"): entry for entry in draft["game_rule_maps"]}
    evidence_ids = {entry.get("id") for entry in draft["rule_evidence"]}
    if set(draft["active_games"]) != {"dlt", "ssq"} or set(maps) != {"dlt", "ssq"}:
        failures.append("active_game_or_rule_map_set")

    for evidence in draft["rule_evidence"]:
        if not evidence.get("url", "").startswith("https://") or not evidence.get("supports"):
            failures.append(f"invalid_rule_evidence:{evidence.get('id')}")

    blockers = 0
    for game, rule_map in maps.items():
        blockers += len(rule_map.get("generation_null_blockers", []))
        if not rule_map.get("mechanism_metadata_status", "").startswith("unknown_"):
            failures.append(f"mechanism_unknown_not_explicit:{game}")
        for group in ("documented_draw_process_segments", "prize_rule_segments"):
            for segment in rule_map.get(group, []):
                unknown = set(segment.get("evidence_ref_ids", [])) - evidence_ids
                if unknown:
                    failures.append(f"unknown_evidence_ref:{game}:{segment.get('id')}")

    draws_identity = draft["upstream"].get("draws", {})
    draws_path = project_path(root, draws_identity.get("path", ""))
    if not draws_path.is_file():
        failures.append("draws_missing")
        return 0, blockers, failures

    joined = 0
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {"dlt": 0, "ssq": 0}
    with draws_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                draw = json.loads(line)
                game = draw["game"]
                issue_text = str(draw["issue_id"])
                issue = int(issue_text)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                failures.append(f"invalid_draw_line:{line_number}")
                continue
            key = (game, issue_text)
            if key in seen:
                failures.append(f"duplicate_draw:{game}:{issue_text}")
            seen.add(key)
            if game not in maps:
                failures.append(f"unmapped_game:{game}")
                continue
            counts[game] += 1
            rule_map = maps[game]
            ns = matching_segments(issue, rule_map["number_space_segments"])
            process = matching_segments(issue, rule_map["documented_draw_process_segments"])
            prize = matching_segments(issue, rule_map["prize_rule_segments"])
            if len(ns) != 1 or len(process) != 1 or len(prize) != 1:
                failures.append(
                    f"rule_join_cardinality:{game}:{issue_text}:ns={len(ns)}:process={len(process)}:prize={len(prize)}"
                )
                continue
            space = ns[0]
            front = draw.get("front_numbers", [])
            back = draw.get("back_numbers", [])
            front_spec, back_spec = space["front"], space["back"]
            legal = (
                len(front) == front_spec["draw_count"]
                and len(set(front)) == len(front)
                and all(front_spec["min"] <= value <= front_spec["max"] for value in front)
                and len(back) == back_spec["draw_count"]
                and len(set(back)) == len(back)
                and all(back_spec["min"] <= value <= back_spec["max"] for value in back)
            )
            if not legal:
                failures.append(f"illegal_draw_for_number_space:{game}:{issue_text}")
                continue
            joined += 1

    if counts != {"dlt": 200, "ssq": 200}:
        failures.append(f"draw_count_by_game:{counts}")
    if len(seen) != 400:
        failures.append(f"unique_draw_count:{len(seen)}")
    if draft["point_in_time_policy"].get("available_at_utc_is_null") is not True:
        failures.append("point_in_time_null_status_not_declared")
    if not draft["forbidden_statistical_matrix_fields"]:
        failures.append("forbidden_field_registry_empty")
    return joined, blockers, failures


def validate_preregistration(draft: dict[str, Any], generation_segments: dict[str, str]) -> list[str]:
    failures: list[str] = []
    required = {
        "scope_boundary",
        "joint_null",
        "calendar_policy",
        "global_alpha",
        "multiplicity_family",
        "test_registry",
        "practical_effect_registry",
        "sample_size_grid",
        "monte_carlo_design",
        "qualification_scenarios",
        "freeze_rule",
    }
    if not required.issubset(draft):
        return ["preregistration_missing_required_fields"]
    if draft["global_alpha"] != 0.05 or 200 not in draft["sample_size_grid"]:
        failures.append("alpha_or_sample_size_grid")
    tests = {test["id"]: test for test in draft["test_registry"]}
    candidate_ids = {test_id for test_id, test in tests.items() if test.get("candidate_eligible")}
    covered: set[tuple[str, str]] = set()
    keys: set[tuple[str, str, str, str]] = set()
    for entry in draft["practical_effect_registry"]:
        if not EFFECT_FIELDS.issubset(entry):
            failures.append("effect_registry_missing_fields")
            continue
        key = tuple(entry[name] for name in ("game", "generation_segment", "bias_family", "effect_parameter"))
        if key in keys:
            failures.append(f"duplicate_effect_key:{key}")
        keys.add(key)
        if entry["practical_null_lower"] > entry["null_value"] or entry["practical_null_upper"] < entry["null_value"]:
            failures.append(f"effect_interval_excludes_null:{key}")
        for test_id in entry["applicable_test_ids"]:
            if test_id not in tests:
                failures.append(f"unknown_applicable_test:{test_id}")
            if test_id in candidate_ids:
                covered.add((entry["game"], test_id))
        if generation_segments.get(entry["game"]) != entry["generation_segment"]:
            failures.append(f"effect_generation_segment:{key}")
    expected = {(game, test_id) for game in ("dlt", "ssq") for test_id in candidate_ids}
    if covered != expected:
        failures.append(f"candidate_effect_coverage:missing={sorted(expected-covered)}:extra={sorted(covered-expected)}")
    if draft["monte_carlo_design"].get("formal_historical_results_allowed") is not False:
        failures.append("formal_results_not_forbidden")
    if not any(item.get("label") == "qualification_positive" for item in draft["qualification_scenarios"]):
        failures.append("qualification_positive_missing")
    return failures


def validate_roles(draft: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    required_fields = {"role", "candidate_id", "assignment_status", "responsibility", "authored_formal_phase2_artifacts"}
    roles = draft.get("required_roles", [])
    if any(not required_fields.issubset(role) for role in roles):
        failures.append("role_entry_missing_fields")
    actual = {role.get("role") for role in roles}
    expected = set(contract["role_separation"]["roles"])
    if actual != expected or len(roles) != len(expected):
        failures.append(f"role_set:missing={sorted(expected-actual)}:extra={sorted(actual-expected)}")
    if draft.get("minimum_independence_level") != contract["role_separation"]["minimum_phase_level"]:
        failures.append("minimum_independence_level")
    declared = {tuple(pair) for pair in draft.get("forbidden_conflicts", [])}
    expected_conflicts = {tuple(pair) for pair in contract["role_separation"]["conflicts_forbidden"]}
    if declared != expected_conflicts:
        failures.append("forbidden_conflict_registry")
    return failures


def formal_path_counts(root: Path, contract: dict[str, Any]) -> tuple[int, int]:
    occupied = 0
    historical = 0
    for deliverable in contract["deliverables"]:
        for raw in deliverable["paths"]:
            if project_path(root, raw).exists():
                occupied += 1
                if deliverable["id"] in {"D2-08", "D2-09"}:
                    historical += 1
    return occupied, historical


def evaluate(contract_path: Path, readiness_path: Path) -> tuple[int, dict[str, Any]]:
    try:
        contract = load_json(contract_path)
        readiness = load_json(readiness_path)
        root = contract_path.resolve().parents[2]
        pre_gate = contract["pre_gate_readiness"]
        schema_path = project_path(root, pre_gate["schema_path"])
        schema = load_json(schema_path)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(readiness)
        if contract.get("contract_version") != "1.3.0" or pre_gate.get("id") != "P2-R00":
            raise ValueError("unsupported Phase 2 readiness contract")
    except Exception as exc:  # parse/schema/contract shape errors share the fixed code 4
        return INVALID_CONTRACT, {"terminal": "INVALID_CONTRACT", "errors": [str(exc)]}

    evidence_failures: list[str] = []
    claimed_contract = readiness["contract_identity"]
    if Path(claimed_contract["path"]).as_posix() != Path(pre_gate_path(contract_path, root)).as_posix():
        evidence_failures.append("contract_path_identity")
    evidence_failures.extend(identity_mismatches(root, [claimed_contract]))
    evidence_failures.extend(identity_mismatches(root, readiness["upstream_identities"]))
    evidence_failures.extend(identity_mismatches(root, [readiness["R2_source_path_and_sha256"]]))
    evidence_failures.extend(identity_mismatches(root, readiness["draft_path_sha256_inventory"]))
    evidence_failures.extend(identity_mismatches(root, [readiness["validator_identity"]]))
    if readiness["validator_identity"]["path"] != pre_gate["validator_path"]:
        evidence_failures.append("validator_identity_path")

    expected_drafts = set(pre_gate["draft_paths"])
    actual_drafts = {item["path"] for item in readiness["draft_path_sha256_inventory"]}
    if actual_drafts != expected_drafts:
        evidence_failures.append("draft_inventory_path_set")
    expected_upstream = {
        contract["upstream"]["phase1_contract_ref"]: contract["upstream"]["phase1_contract_sha256"],
        contract["upstream"]["phase1_final_ref"]: contract["upstream"]["phase1_final_sha256"],
        contract["upstream"]["baseline"]["draws_ref"]: contract["upstream"]["baseline"]["draws_sha256"],
        contract["upstream"]["baseline"]["observations_ref"]: contract["upstream"]["baseline"]["observations_sha256"],
        contract["upstream"]["baseline"]["manifest_ref"]: contract["upstream"]["baseline"]["manifest_sha256"],
        "tests/phase1/fixtures/spec/spec-bundle-freeze.json": "56d443d76075acdc8914da3fa8a0b7318325aff91f222bcd181c7390f14a2c6e",
    }
    actual_upstream = {item["path"]: item["sha256"] for item in readiness["upstream_identities"]}
    if actual_upstream != expected_upstream:
        evidence_failures.append("upstream_identity_path_set")
    if evidence_failures:
        return EVIDENCE_MISMATCH, {"terminal": "EVIDENCE_MISMATCH", "errors": sorted(set(evidence_failures))}

    draft_by_name = {
        Path(item["path"]).name: load_json(project_path(root, item["path"]))
        for item in readiness["draft_path_sha256_inventory"]
        if item["path"].endswith(".json")
    }
    input_draft = draft_by_name["input-manifest.draft.json"]
    if input_draft.get("R2_source") != {
        **readiness["R2_source_path_and_sha256"],
        "objective": input_draft.get("R2_source", {}).get("objective"),
    }:
        draft_failures = ["R2_source_identity_crosscheck"]
    else:
        draft_failures = []
    joined, blockers, input_failures = validate_input_draft(root, input_draft)
    generation_segments = {
        item["game"]: item["documented_draw_process_segments"][0]["id"]
        for item in input_draft.get("game_rule_maps", [])
        if len(item.get("documented_draw_process_segments", [])) == 1
    }
    draft_failures += input_failures
    draft_failures += validate_preregistration(draft_by_name["preregistration.draft.json"], generation_segments)
    draft_failures += validate_roles(draft_by_name["reviewer-assignment.draft.json"], contract)
    markdown_path = project_path(root, next(path for path in expected_drafts if path.endswith(".md")))
    markdown = markdown_path.read_text(encoding="utf-8")
    for assertion in ("ASSERT-P2-IN-01", "ASSERT-P2-IN-02", "ASSERT-P2-IN-04", "ASSERT-P2-IN-05", "ASSERT-P2-MECH-01"):
        if assertion not in markdown:
            draft_failures.append(f"missing_markdown_assertion:{assertion}")

    occupied, historical = formal_path_counts(root, contract)
    coverage = 0.0 if draft_failures else 1.0
    actual = {
        "generation_rule_join_count": joined,
        "unresolved_generation_null_blockers": blockers,
        "required_draft_field_coverage": coverage,
        "formal_D2_path_occupancy_count": occupied,
        "formal_historical_result_count": historical,
    }
    claim_mismatches = [key for key, value in actual.items() if readiness[key] != value]
    ready_conditions_met = (
        joined == 400
        and blockers == 0
        and coverage == 1.0
        and occupied == 0
        and historical == 0
        and not claim_mismatches
    )
    terminal_expected = "P2-00A-READY" if ready_conditions_met else "HOLD"
    if readiness["terminal"] != terminal_expected:
        claim_mismatches.append("terminal")
        ready_conditions_met = False
    result = {
        "terminal": "P2-00A-READY" if ready_conditions_met else "HOLD",
        "actual": actual,
        "draft_errors": sorted(set(draft_failures)),
        "claim_mismatches": sorted(set(claim_mismatches)),
        "note": "P2-00A readiness is not G0/G1 acceptance and authorizes only P2-01 startup.",
    }
    return (READY if ready_conditions_met else HOLD), result


def pre_gate_path(contract_path: Path, root: Path) -> str:
    return contract_path.resolve().relative_to(root.resolve()).as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--readiness", required=True, type=Path)
    args = parser.parse_args(argv)
    code, result = evaluate(args.contract, args.readiness)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
