"""Pure Stage 1 handoff, consumer, and G-HANDOFF fixed-point functions."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from p0_07_decision import (
    PASS_OUTCOMES, derive_per_game_outcome, derive_project_decision, validate_gate_results,
)
from phase0lib import ValidationError, canonical_json_bytes, canonical_sha256, sha256_bytes, validate_schema_instance


GAMES = ("dlt", "ssq")
TIERS = ("corroborated_official", "shared_upstream", "primary_only")
DECISION_EVIDENCE_REF = "derived/p0-07-gate-inputs.json"
PREVIOUS_REFS = (
    "artifacts/phase-0/field-contract.json",
    "artifacts/phase-0/rule-bundles.json",
    "artifacts/phase-0/environment-lock.json",
)
FORBIDDEN_FUTURE_MARKERS = ("replay-report", "reviewer-attestation", "acceptance-report")


def project_handoff_pass(base_game_results: list[dict[str, Any]], *, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Project G-HANDOFF=PASS without mutating or publishing the base results."""
    if len(base_game_results) != 2 or {item.get("game") for item in base_game_results} != set(GAMES):
        raise ValidationError("handoff projection requires exactly one result per game")
    projected = copy.deepcopy(base_game_results)
    for result in projected:
        validate_gate_results(result["gate_results"], contract=contract)
        current = derive_per_game_outcome(result["gate_results"], result["coverage_tier"])
        if result.get("per_game_outcome") != current:
            raise ValidationError(f"{result['game']}: base outcome was manually changed")
        handoff = next(item for item in result["gate_results"] if item["gate_id"] == "G-HANDOFF")
        handoff.update({
            "outcome": "PASS", "remediation_status": "not_applicable",
            "reason_code": "handoff_consumer_verified",
            "evidence_refs": ["fact:stage1-consumer-match"],
            "reason": f"{result['game']} Stage 1 consumer accepted the candidate fixture without hidden transformation.",
        })
        result["per_game_outcome"] = derive_per_game_outcome(result["gate_results"], result["coverage_tier"])
    return sorted(projected, key=lambda item: GAMES.index(item["game"]))


def _accepted_reconciliation(reconciliation: list[dict[str, Any]], evidence_by_id: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    accepted = {game: [] for game in GAMES}
    for row in reconciliation:
        game = row.get("game")
        if game not in accepted:
            raise ValidationError("reconciliation contains an unsupported game")
        refs = [row.get("primary_evidence_ref"), *row.get("corroborating_evidence_refs", [])]
        for reference in refs:
            if reference not in evidence_by_id:
                raise ValidationError(f"{game}: reconciliation has dangling evidence ref {reference}")
            if evidence_by_id[reference].get("game") != game:
                raise ValidationError(f"{game}: reconciliation contains a cross-game evidence ref {reference}")
        is_accepted = (
            row.get("core_fact_match") is True
            and row.get("resolution_status") in {"agreed", "primary_only", "resolved"}
            and row.get("resolved_record_ref") is not None
        )
        if is_accepted:
            accepted[game].append(row)
    return accepted


def build_handoff_fixture(
    projected_game_results: list[dict[str, Any]],
    *,
    reconciliation: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    contract: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    if len(evidence) != len({item.get("evidence_id") for item in evidence}):
        raise ValidationError("handoff evidence IDs must be unique")
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    accepted = _accepted_reconciliation(reconciliation, evidence_by_id)
    if len(projected_game_results) != 2 or {item.get("game") for item in projected_game_results} != set(GAMES):
        raise ValidationError("handoff requires exactly one projected result per game")
    game_results = []
    for result in sorted(projected_game_results, key=lambda item: GAMES.index(item["game"])):
        validate_gate_results(result["gate_results"], contract=contract)
        expected_outcome = derive_per_game_outcome(result["gate_results"], result["coverage_tier"])
        if result.get("per_game_outcome") != expected_outcome:
            raise ValidationError(f"{result['game']}: projected outcome differs from gates")
        rows = accepted[result["game"]]
        counts = Counter(row["corroboration_tier"] for row in rows)
        total = sum(counts.values())
        minimum_tier = (
            "none" if total == 0 else
            "primary_only" if counts["primary_only"] else
            "shared_upstream" if counts["shared_upstream"] else
            "corroborated_official"
        )
        row_refs = sorted({
            reference
            for row in rows
            for reference in [row["primary_evidence_ref"], *row["corroborating_evidence_refs"]]
        })
        game_results.append({
            "game": result["game"], "per_game_outcome": expected_outcome,
            "coverage_tier": result["coverage_tier"], "corroboration_tier": minimum_tier,
            "corroboration_counts": [{"tier": tier, "count": counts[tier]} for tier in TIERS],
            "evidence_ref": [DECISION_EVIDENCE_REF, *row_refs],
        })
    decision = derive_project_decision(projected_game_results)
    fixture = {
        "schema_version": "1.1.0", "artifact_type": "stage1_handoff_fixture", "contract_version": "1.3",
        "project_decision": decision,
        "active_games": [item["game"] for item in game_results if item["per_game_outcome"] in PASS_OUTCOMES],
        "excluded_games": [item["game"] for item in game_results if item["per_game_outcome"] not in PASS_OUTCOMES],
        "game_results": game_results,
        "field_contract_ref": PREVIOUS_REFS[0], "rule_bundles_ref": PREVIOUS_REFS[1],
        "environment_lock_ref": PREVIOUS_REFS[2], "decision_evidence_ref": DECISION_EVIDENCE_REF,
    }
    validate_handoff_fixture(
        fixture, projected_game_results=projected_game_results, reconciliation=reconciliation,
        evidence=evidence, contract=contract, schema=schema,
    )
    return fixture


def validate_handoff_fixture(
    fixture: dict[str, Any],
    *,
    projected_game_results: list[dict[str, Any]],
    reconciliation: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    contract: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    validate_schema_instance(fixture, schema)
    # Rebuild without recursive validation, then compare all declared semantics.
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    accepted = _accepted_reconciliation(reconciliation, evidence_by_id)
    expected_games = []
    ordered = sorted(projected_game_results, key=lambda item: GAMES.index(item["game"]))
    for result in ordered:
        validate_gate_results(result["gate_results"], contract=contract)
        outcome = derive_per_game_outcome(result["gate_results"], result["coverage_tier"])
        if result.get("per_game_outcome") != outcome:
            raise ValidationError(f"{result['game']}: projected outcome differs from gates")
        rows = accepted[result["game"]]
        counts = Counter(row["corroboration_tier"] for row in rows)
        total = sum(counts.values())
        tier = "none" if total == 0 else "primary_only" if counts["primary_only"] else "shared_upstream" if counts["shared_upstream"] else "corroborated_official"
        refs = sorted({ref for row in rows for ref in [row["primary_evidence_ref"], *row["corroborating_evidence_refs"]]})
        expected_games.append({
            "game": result["game"], "per_game_outcome": outcome, "coverage_tier": result["coverage_tier"],
            "corroboration_tier": tier,
            "corroboration_counts": [{"tier": name, "count": counts[name]} for name in TIERS],
            "evidence_ref": [DECISION_EVIDENCE_REF, *refs],
        })
    expected_active = [item["game"] for item in expected_games if item["per_game_outcome"] in PASS_OUTCOMES]
    expected_excluded = [item["game"] for item in expected_games if item["per_game_outcome"] not in PASS_OUTCOMES]
    if fixture["game_results"] != expected_games:
        raise ValidationError("handoff tier/count/ref/outcome fields differ from mechanical derivation")
    if fixture["active_games"] != expected_active or fixture["excluded_games"] != expected_excluded:
        raise ValidationError("handoff active/excluded partition differs from per-game outcomes")
    if set(fixture["active_games"]) & set(fixture["excluded_games"]) or set(fixture["active_games"] + fixture["excluded_games"]) != set(GAMES):
        raise ValidationError("handoff active/excluded partition is not exact")
    if fixture["project_decision"] != derive_project_decision(projected_game_results):
        raise ValidationError("handoff project decision differs from per-game outcomes")


def consume_stage1_fixture(
    fixture: dict[str, Any],
    *,
    fixture_schema: dict[str, Any],
    receipt_schema: dict[str, Any],
    available_refs: dict[str, set[str] | None],
) -> dict[str, Any]:
    """Minimal consumer: validate declared fields, resolve refs, emit a receipt."""
    validate_schema_instance(fixture, fixture_schema)
    for game in fixture["game_results"]:
        outcome = game["per_game_outcome"]
        coverage_tier = game["coverage_tier"]
        if outcome == "PASS_FULL" and coverage_tier != "target":
            raise ValidationError("consumer requires target coverage for PASS_FULL")
        if outcome == "PASS_LIMITED" and coverage_tier != "minimum_viable":
            raise ValidationError("consumer requires minimum_viable coverage for PASS_LIMITED")
        counts = {item["tier"]: item["count"] for item in game["corroboration_counts"]}
        total = sum(counts.values())
        tier = "none" if total == 0 else "primary_only" if counts["primary_only"] else "shared_upstream" if counts["shared_upstream"] else "corroborated_official"
        if game["corroboration_tier"] != tier:
            raise ValidationError("consumer corroboration tier differs from counts")
    derived_project = derive_project_decision(fixture["game_results"])
    derived_active = [item["game"] for item in fixture["game_results"] if item["per_game_outcome"] in PASS_OUTCOMES]
    derived_excluded = [item["game"] for item in fixture["game_results"] if item["per_game_outcome"] not in PASS_OUTCOMES]
    if fixture["project_decision"] != derived_project:
        raise ValidationError("consumer project decision differs from per-game outcomes")
    if fixture["active_games"] != derived_active or fixture["excluded_games"] != derived_excluded:
        raise ValidationError("consumer active/excluded partition differs from per-game outcomes")
    declared_refs = [
        fixture["field_contract_ref"], fixture["rule_bundles_ref"], fixture["environment_lock_ref"],
        fixture["decision_evidence_ref"],
        *[ref for game in fixture["game_results"] for ref in game["evidence_ref"]],
    ]
    for reference in declared_refs:
        if any(marker in reference for marker in FORBIDDEN_FUTURE_MARKERS):
            raise ValidationError(f"consumer rejects future/terminal dependency: {reference}")
        if reference not in available_refs:
            raise ValidationError(f"consumer cannot resolve declared ref: {reference}")
    for game in fixture["game_results"]:
        for reference in game["evidence_ref"]:
            games = available_refs[reference]
            if games is not None and game["game"] not in games:
                raise ValidationError(f"consumer found cross-game ref {reference} in {game['game']}")
    receipt = {
        "schema_version": "1.0.0", "artifact_type": "p0_07_stage1_consumer_receipt", "contract_version": "1.3",
        "fixture_sha256": canonical_sha256(fixture), "project_decision": derived_project,
        "consumed_fixture_file_bytes_sha256": sha256_bytes(canonical_json_bytes(fixture) + b"\n"),
        "active_games": derived_active, "excluded_games": derived_excluded,
        "consumed_game_count": 2, "declared_fields_only": True, "hidden_manual_transformations": False,
        "resolved_evidence_ref_count": len(set(declared_refs)),
    }
    validate_schema_instance(receipt, receipt_schema)
    return receipt


def validate_fixed_point(
    candidate_fixture: dict[str, Any],
    final_fixture: dict[str, Any],
    candidate_results: list[dict[str, Any]],
    final_results: list[dict[str, Any]],
) -> None:
    if candidate_results != final_results:
        raise ValidationError("G-HANDOFF fixed point changed per-game gate/outcome results")
    if derive_project_decision(candidate_results) != derive_project_decision(final_results):
        raise ValidationError("G-HANDOFF fixed point changed project decision")
    if candidate_fixture != final_fixture:
        raise ValidationError("G-HANDOFF fixed point changed the handoff fixture")


def finalize_handoff_fixed_point(
    base_game_results: list[dict[str, Any]],
    *,
    reconciliation: list[dict[str, Any]], evidence: list[dict[str, Any]], contract: dict[str, Any],
    fixture_schema: dict[str, Any], receipt_schema: dict[str, Any], available_refs: dict[str, set[str] | None],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    candidate_results = project_handoff_pass(base_game_results, contract=contract)
    candidate_fixture = build_handoff_fixture(
        candidate_results, reconciliation=reconciliation, evidence=evidence, contract=contract, schema=fixture_schema,
    )
    receipt = consume_stage1_fixture(
        candidate_fixture, fixture_schema=fixture_schema, receipt_schema=receipt_schema, available_refs=available_refs,
    )
    final_results = project_handoff_pass(base_game_results, contract=contract)
    final_fixture = build_handoff_fixture(
        final_results, reconciliation=reconciliation, evidence=evidence, contract=contract, schema=fixture_schema,
    )
    validate_fixed_point(candidate_fixture, final_fixture, candidate_results, final_results)
    return final_results, final_fixture, receipt
