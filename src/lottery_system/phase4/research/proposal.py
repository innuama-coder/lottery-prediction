from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..identity import content_id, validate_stable_id
from .registry import FAMILIES, ResearchRegistryViolation


ZERO_REASONS = {"no_eligible_hypothesis", "budget_exhausted", "guard_hold", "scheduled_no_change"}


def build_experiment(
    *,
    game: str,
    decision_id: str,
    hypothesis_family: str,
    alpha_ordinal: int,
    alpha_spent: str,
    parent_config_id: str,
    canonical_diff: list[dict[str, Any]],
    registered_p0_id: str,
    registered_p1_id: str,
    terminal: str,
) -> dict[str, Any]:
    if game not in {"ssq", "dlt"} or hypothesis_family not in FAMILIES:
        raise ResearchRegistryViolation("experiment game or family is invalid")
    for label, value in (("decision", decision_id), ("parent config", parent_config_id), ("p0", registered_p0_id), ("p1", registered_p1_id)):
        validate_stable_id(value, label)
    if isinstance(alpha_ordinal, bool) or not isinstance(alpha_ordinal, int) or alpha_ordinal < 1:
        raise ResearchRegistryViolation("alpha ordinal is invalid")
    if terminal not in {"shadow_candidate", "rejected", "archived", "failed", "timeout", "budget_exhausted"}:
        raise ResearchRegistryViolation("experiment terminal is invalid")
    body: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact_type": "phase4_experiment", "game": game,
        "decision_id": decision_id, "hypothesis_family": hypothesis_family,
        "alpha_ordinal": alpha_ordinal, "alpha_spent": alpha_spent,
        "parent_config_id": parent_config_id, "canonical_diff": canonical_diff,
        "registered_p0_id": registered_p0_id, "registered_p1_id": registered_p1_id,
        "minimum_look": 30, "maximum_look": 150, "terminal": terminal,
    }
    body["experiment_id"] = content_id("experiment", body)
    return body


def build_decision(
    *,
    decision_id: str,
    game: str,
    target_issue: str,
    result_revision_id: str,
    trigger: str,
    experiment_ids: list[str],
    terminal: str,
    zero_experiment_reason: str | None,
) -> dict[str, Any]:
    for label, value in (("decision", decision_id), ("target issue", target_issue), ("result revision", result_revision_id)):
        validate_stable_id(value, label)
    if game not in {"ssq", "dlt"} or trigger not in {"new_verified_result", "official_result_revision"}:
        raise ResearchRegistryViolation("decision game or trigger is invalid")
    if not isinstance(experiment_ids, list) or len(experiment_ids) > 1:
        raise ResearchRegistryViolation("at most one experiment is allowed per decision cycle")
    if experiment_ids:
        validate_stable_id(experiment_ids[0], "experiment identity")
        if zero_experiment_reason is not None:
            raise ResearchRegistryViolation("an experiment decision cannot carry a zero-experiment reason")
    elif zero_experiment_reason not in ZERO_REASONS:
        raise ResearchRegistryViolation("zero-experiment decisions require a registered reason")
    allowed = {"no_change", "rejected", "archived", "shadow_candidate_proposal", "remediation_completed", "failed"}
    if terminal not in allowed:
        raise ResearchRegistryViolation("decision terminal is invalid")
    return {
        "schema_version": "1.0.0", "artifact_type": "phase4_decision", "decision_id": decision_id,
        "game": game, "target_issue": target_issue, "result_revision_id": result_revision_id,
        "decision_contract_id": "phase4-decision-v1", "trigger": trigger,
        "experiment_count": len(experiment_ids), "experiment_ids": experiment_ids,
        "terminal": terminal, "zero_experiment_reason": zero_experiment_reason,
    }


def zero_experiment_decision(specification: Mapping[str, Any]) -> dict[str, Any]:
    return build_decision(
        decision_id=specification["decision_id"], game=specification["game"],
        target_issue=specification["target_issue"], result_revision_id=specification["result_revision_id"],
        trigger=specification.get("trigger", "new_verified_result"), experiment_ids=[], terminal="no_change",
        zero_experiment_reason=specification["zero_experiment_reason"],
    )
