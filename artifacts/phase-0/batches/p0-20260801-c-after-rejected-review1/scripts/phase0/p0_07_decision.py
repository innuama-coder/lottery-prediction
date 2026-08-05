"""Pure Phase 0 per-game gate and decision derivation.

This module has no CLI and writes no artifacts.  Review and handoff layers must
supply explicit clean-replay and consumer facts; neither fact is inferred here.
The local artifact-shape checks below are decision-projection preconditions, not
terminal truth proofs.  A full review integration must rerun the strict schema,
provenance, parser, time, exact-24, revision, and recovery semantic validators
and bind their receipts before these gates may enter an acceptance report.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from phase0lib import ValidationError


GATE_IDS = (
    "G-SCOPE", "G-AUTHORITY", "G-COMPLIANCE", "G-SCHEMA", "G-PROVENANCE",
    "G-RULES", "G-TIME", "G-PARSE", "G-CORRECTNESS", "G-COVERAGE",
    "G-REVISION", "G-RECOVERY", "G-REPRODUCIBILITY", "G-HANDOFF",
)
PASS_OUTCOMES = {"PASS_FULL", "PASS_LIMITED"}
INTEGRATION_CONSTRAINT = (
    "Terminal use is forbidden until strict full-review semantic validator receipts are bound "
    "and every gate is recomputed from those receipts."
)


def _gate(
    gate_id: str,
    passed: bool,
    refs: list[str],
    pass_code: str,
    fail_code: str,
    reason: str,
    *,
    fail_remediation: str,
) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "outcome": "PASS" if passed else "FAIL",
        "remediation_status": "not_applicable" if passed else fail_remediation,
        "reason_code": pass_code if passed else fail_code,
        "evidence_refs": refs,
        "reason": reason,
    }


def _failure_remediation(game_input: dict[str, Any], gate_id: str) -> str:
    if gate_id in {"G-REPRODUCIBILITY", "G-HANDOFF"}:
        return "concrete_compliant_action_available"
    if game_input["compliant_corrective_action_available"]:
        return "concrete_compliant_action_available"
    if game_input["alternatives_exhausted_no_evidentiary_path"]:
        return "alternatives_exhausted_no_evidentiary_path"
    raise ValidationError(f"{gate_id}: failed gate has no controlled remediation classification")


def build_per_game_gate_results(
    game: str,
    *,
    gate_inputs: dict[str, Any],
    coverage: dict[str, Any],
    revision: dict[str, Any],
    exact24: list[dict[str, Any]],
    source_catalog: dict[str, Any],
    contract: dict[str, Any],
    repair_evidence: dict[str, Any],
    clean_replay_match: bool,
    handoff_consumer_match: bool,
) -> list[dict[str, Any]]:
    """Mechanically evaluate one game's fourteen contract gates."""
    if game not in {"dlt", "ssq"}:
        raise ValidationError(f"unsupported game: {game}")
    game_input = next((item for item in gate_inputs["games"] if item["game"] == game), None)
    game_coverage = next((item for item in coverage["games"] if item["game"] == game), None)
    game_source = next((item for item in source_catalog["games"] if item["game"] == game), None)
    if game_input is None or game_coverage is None or game_source is None:
        raise ValidationError(f"{game}: required per-game input is missing")
    contract_gate_ids = tuple(item["id"] for item in contract["hard_gates"])
    exact_ids = [item.get("request_id") for item in exact24]
    exact24_valid = (
        len(exact24) == 24 and len(exact_ids) == len(set(exact_ids))
        and Counter(item.get("game") for item in exact24) == Counter({"dlt": 12, "ssq": 12})
    )
    sources = [game_source["authoritative_primary"], *game_source["official_corroborators"]]
    scope_ok = (
        contract_gate_ids == GATE_IDS
        and "P0-07-05" in {item.get("id") for item in repair_evidence.get("root_causes", [])}
        and any("acceptance-contract hard gates" in item for item in repair_evidence.get("forbidden_change_classes", []))
    )
    authority_ok = (
        game_source["authoritative_primary"].get("role") == "authoritative_primary"
        and game_source["authoritative_primary"].get("authority_tier") == "A"
        and game_source.get("channel_availability_assessed") is True
        and bool(game_source.get("shared_upstream_assessment"))
    )
    compliance_ok = all(
        source["observed_access"].get("access_controls_bypassed") is False
        and source.get("approved_use") in {"blocked", "hold_pending", "scheduled_low_rate_fetch"}
        and bool(source.get("compliance_evidence"))
        for source in sources
    )
    schema_ok = (
        gate_inputs.get("artifact_type") == "p0_07_gate_inputs"
        and coverage.get("artifact_type") == "coverage_report"
        and revision.get("artifact_type") == "revision_report"
        and len(gate_inputs.get("games", [])) == 2 and len(coverage.get("games", [])) == 2
    )
    provenance_ok = revision.get("append_only_verified") is True and all(
        isinstance(item.get("evidence_ref"), str) and item["evidence_ref"]
        for item in revision.get("events", []) if item.get("record_id", "").startswith(f"{game}-")
    )
    rules_ok = (
        isinstance(game_coverage.get("rule_boundary"), list)
        and "artifacts/phase-0/rule-bundles.json" in game_coverage.get("evidence_refs", [])
    )
    time_ok = exact24_valid and game_input.get("soak_request_count") == 12
    parse_ok = isinstance(revision.get("history_sha256"), str) and len(revision["history_sha256"]) == 64
    correctness_ok = game_input.get("unresolved_conflicts") == 0
    coverage_ok = (
        game_coverage.get("coverage_tier") in {"target", "minimum_viable"}
        and game_input.get("coverage_tier") == game_coverage.get("coverage_tier")
    )
    revision_ok = (
        revision.get("append_only_verified") is True
        and revision.get("synthetic_correction_replay", {}).get("reconstructed") is True
        and revision.get("synthetic_correction_replay", {}).get("before_hash")
        != revision.get("synthetic_correction_replay", {}).get("after_hash")
    )
    facts = {
        "G-SCOPE": scope_ok, "G-AUTHORITY": authority_ok, "G-COMPLIANCE": compliance_ok,
        "G-SCHEMA": schema_ok, "G-PROVENANCE": provenance_ok, "G-RULES": rules_ok,
        "G-TIME": time_ok, "G-PARSE": parse_ok, "G-CORRECTNESS": correctness_ok,
        "G-COVERAGE": coverage_ok, "G-REVISION": revision_ok, "G-RECOVERY": exact24_valid,
        "G-REPRODUCIBILITY": clean_replay_match is True, "G-HANDOFF": handoff_consumer_match is True,
    }
    refs = {
        "G-SCOPE": ["docs/roadmap/phase-0-acceptance-contract.json", "artifacts/phase-0/repair-manifest-p0-20260801-c-draft.json"],
        "G-AUTHORITY": ["artifacts/phase-0/source-catalog.json"],
        "G-COMPLIANCE": ["artifacts/phase-0/source-catalog.json"],
        "G-SCHEMA": ["derived/p0-07-gate-inputs.json", "derived/coverage-report.json", "derived/revision-report.json"],
        "G-PROVENANCE": ["derived/revision-report.json"], "G-RULES": ["derived/coverage-report.json"],
        "G-TIME": ["artifacts/phase-0/soak-run-log.jsonl"], "G-PARSE": ["derived/revision-report.json"],
        "G-CORRECTNESS": ["derived/p0-07-gate-inputs.json"], "G-COVERAGE": ["derived/coverage-report.json"],
        "G-REVISION": ["derived/revision-report.json"], "G-RECOVERY": ["artifacts/phase-0/soak-run-log.jsonl"],
        "G-REPRODUCIBILITY": ["fact:clean-replay-match"], "G-HANDOFF": ["fact:stage1-consumer-match"],
    }
    results = []
    for gate_id in GATE_IDS:
        remediation = (
            "concrete_compliant_action_available" if facts[gate_id]
            else _failure_remediation(game_input, gate_id)
        )
        results.append(_gate(
            gate_id, facts[gate_id], refs[gate_id], f"{gate_id[2:].lower().replace('-', '_')}_verified",
            f"{gate_id[2:].lower().replace('-', '_')}_not_verified",
            f"{game} {gate_id} was mechanically evaluated from declared Phase 0 evidence.",
            fail_remediation=remediation,
        ))
    validate_gate_results(results, contract=contract)
    return results


def validate_gate_results(
    gate_results: list[dict[str, Any]],
    *,
    contract: dict[str, Any],
    expected: list[dict[str, Any]] | None = None,
) -> None:
    expected_ids = tuple(item["id"] for item in contract["hard_gates"])
    actual_ids = tuple(item.get("gate_id") for item in gate_results)
    if len(gate_results) != 14 or actual_ids != expected_ids or len(set(actual_ids)) != 14:
        raise ValidationError("gate results must contain each of the fourteen contract gates exactly once in contract order")
    for gate in gate_results:
        if gate.get("outcome") not in {"PASS", "FAIL"}:
            raise ValidationError(f"{gate.get('gate_id')}: invalid gate outcome")
        remediation = gate.get("remediation_status")
        if gate["outcome"] == "PASS" and remediation != "not_applicable":
            raise ValidationError(f"{gate['gate_id']}: PASS must use not_applicable remediation")
        if gate["outcome"] == "FAIL" and remediation not in {
            "concrete_compliant_action_available", "alternatives_exhausted_no_evidentiary_path",
        }:
            raise ValidationError(f"{gate['gate_id']}: FAIL requires a controlled remediation status")
        if not gate.get("reason_code") or not gate.get("reason"):
            raise ValidationError(f"{gate['gate_id']}: reason is missing")
        refs = gate.get("evidence_refs")
        if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
            raise ValidationError(f"{gate['gate_id']}: evidence refs are missing or duplicated")
    if expected is not None and gate_results != expected:
        raise ValidationError("gate results differ from mechanical derivation")


def derive_per_game_outcome(gate_results: list[dict[str, Any]], coverage_tier: str) -> str:
    by_id = {item["gate_id"]: item for item in gate_results}
    if set(by_id) != set(GATE_IDS) or len(gate_results) != 14:
        raise ValidationError("cannot derive per-game outcome from incomplete or duplicate gates")
    noncoverage_pass = all(item["outcome"] == "PASS" for gate_id, item in by_id.items() if gate_id != "G-COVERAGE")
    if noncoverage_pass and by_id["G-COVERAGE"]["outcome"] == "PASS" and coverage_tier == "target":
        return "PASS_FULL"
    if noncoverage_pass and by_id["G-COVERAGE"]["outcome"] == "PASS" and coverage_tier == "minimum_viable":
        return "PASS_LIMITED"
    failures = [item for item in gate_results if item["outcome"] == "FAIL"]
    if any(item["remediation_status"] == "alternatives_exhausted_no_evidentiary_path" for item in failures):
        return "STOP"
    if any(item["remediation_status"] == "concrete_compliant_action_available" for item in failures):
        return "HOLD"
    raise ValidationError("gate classification matches no contract per-game outcome")


def derive_project_decision(game_results: list[dict[str, Any]]) -> str:
    if len(game_results) != 2 or {item.get("game") for item in game_results} != {"dlt", "ssq"}:
        raise ValidationError("project decision requires exactly one result for each game")
    outcomes = [item["per_game_outcome"] for item in game_results]
    if all(outcome == "PASS_FULL" for outcome in outcomes):
        return "GO"
    if any(outcome in PASS_OUTCOMES for outcome in outcomes):
        return "LIMITED_GO"
    if any(outcome == "HOLD" for outcome in outcomes):
        return "HOLD"
    if all(outcome == "STOP" for outcome in outcomes):
        return "STOP"
    raise ValidationError("per-game outcomes match no project decision")


def validate_game_and_project_results(
    game_results: list[dict[str, Any]],
    project_decision: str,
    *,
    contract: dict[str, Any],
) -> None:
    for result in game_results:
        validate_gate_results(result["gate_results"], contract=contract)
        expected = derive_per_game_outcome(result["gate_results"], result["coverage_tier"])
        if result.get("per_game_outcome") != expected:
            raise ValidationError(f"{result.get('game')}: per-game outcome differs from gate derivation")
    if project_decision != derive_project_decision(game_results):
        raise ValidationError("project decision differs from per-game derivation")
