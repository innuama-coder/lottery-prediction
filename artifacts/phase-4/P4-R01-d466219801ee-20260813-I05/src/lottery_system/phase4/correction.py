from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import content_id, validate_stable_id
from .metrics import MetricViolation
from .serialization import load_json, sha256_file


class CorrectionViolation(MetricViolation):
    terminal = "HOLD_CORRECTION_INCOMPLETE"
    exit_code = 20


CORRECTION_POLICY_VERSION = "correction-policy-v1"
CORRECTION_POLICY_SHA256 = "c544b5242d4b4fd6b4e065f857d89b22ccd60b281ab1e5e304452ebb73a10a06"
FROZEN_CORRECTION_POLICY = {
    "schema_version": "1.0.0",
    "artifact_type": "phase4_correction_policy",
    "correction_policy_version": "correction-policy-v1",
    "idempotence_key": ["game", "issue_id", "new_result_revision_id", "correction_policy_version"],
    "score_side": ["new_data_release", "current_view_replacement", "corrected_scores", "corrected_aggregates", "score_correction_impact"],
    "research_side": ["remediation_decision", "archive_pending_requalification", "replay_or_requalify", "alpha_history_unchanged_proof"],
    "closure_requires_both_sides": True,
    "preserve": ["old_results", "old_scores", "old_aggregates", "old_decisions", "executed_experiments", "alpha_spending", "locked_forecasts"],
    "alpha_refund": False,
    "duplicate_observation_credit": False,
    "partial_terminal": "HOLD_CORRECTION_INCOMPLETE",
}


def validate_correction_policy_object(policy: Mapping[str, Any]) -> dict[str, Any]:
    if dict(policy) != FROZEN_CORRECTION_POLICY:
        raise CorrectionViolation("correction policy object differs from the frozen contract")
    return dict(policy)


def validate_correction_policy(
    path: Path,
    *,
    expected_sha256: str,
    expected_version: str,
) -> dict[str, Any]:
    """Validate an explicitly supplied installed contract; never infer a source tree."""
    if type(expected_sha256) is not str or expected_sha256 != CORRECTION_POLICY_SHA256:
        raise CorrectionViolation("correction policy expected SHA is not the frozen identity")
    if type(expected_version) is not str or expected_version != CORRECTION_POLICY_VERSION:
        raise CorrectionViolation("correction policy expected version is not registered")
    resolved = Path(path).resolve(strict=False)
    if not resolved.is_absolute() or not resolved.is_file() or sha256_file(resolved) != expected_sha256:
        raise CorrectionViolation("correction policy byte identity mismatch")
    policy = validate_correction_policy_object(load_json(resolved, reject_floats=True))
    if policy["correction_policy_version"] != expected_version:
        raise CorrectionViolation("correction policy version identity mismatch")
    return policy


def _unique_ids(values: Sequence[object], label: str) -> list[str]:
    if type(values) not in {list, tuple}:
        raise CorrectionViolation(f"{label} must be a sequence")
    result = [validate_stable_id(value, label) for value in values]
    if len(result) != len(set(result)):
        raise CorrectionViolation(f"{label} contains a duplicate")
    return result


def build_score_correction_impact(*, canonical_graph: Mapping[str, Any],
                                  correction_policy_version: str = CORRECTION_POLICY_VERSION) -> dict[str, Any]:
    fields = {
        "game", "issue_id", "old_result_revision_id", "new_result_revision_id", "new_supersedes_revision_id",
        "new_data_release_id", "new_data_release_result_revision_ids", "current_scores", "current_aggregates",
        "score_replacements", "aggregate_replacements", "pending_research_object_ids", "alpha_event_ids_before",
    }
    if not isinstance(canonical_graph, Mapping) or set(canonical_graph) != fields:
        raise CorrectionViolation("canonical correction graph shape mismatch")
    game = canonical_graph["game"]
    issue_id = canonical_graph["issue_id"]
    old_result_revision_id = canonical_graph["old_result_revision_id"]
    new_result_revision_id = canonical_graph["new_result_revision_id"]
    if game not in {"ssq", "dlt"}:
        raise CorrectionViolation("correction game is not registered")
    issue = validate_stable_id(issue_id, "issue identity")
    old = validate_stable_id(old_result_revision_id, "old result revision identity")
    new = validate_stable_id(new_result_revision_id, "new result revision identity")
    if old == new:
        raise CorrectionViolation("correction must change the result revision")
    if canonical_graph["new_supersedes_revision_id"] != old:
        raise CorrectionViolation("new result revision does not directly supersede the old current revision")
    releases = _unique_ids(canonical_graph["new_data_release_result_revision_ids"], "data release result revision identity")
    if new not in releases or old in releases:
        raise CorrectionViolation("new data release does not replace the corrected result revision")
    validate_stable_id(canonical_graph["new_data_release_id"], "new data release identity")
    if correction_policy_version != CORRECTION_POLICY_VERSION:
        raise CorrectionViolation("correction policy version is not registered")
    current_scores, current_aggregates = canonical_graph["current_scores"], canonical_graph["current_aggregates"]
    score_replacements, aggregate_replacements = canonical_graph["score_replacements"], canonical_graph["aggregate_replacements"]
    apply_current_replacements(
        current_scores=current_scores, current_aggregates=current_aggregates,
        score_replacements=score_replacements, aggregate_replacements=aggregate_replacements,
        expected_old_score_ids=list(current_scores.values()), expected_old_aggregate_ids=list(current_aggregates.values()),
    )
    scores = _unique_ids(list(score_replacements.values()), "corrected score identity")
    aggregates = _unique_ids(list(aggregate_replacements.values()), "corrected aggregate identity")
    pending = _unique_ids(canonical_graph["pending_research_object_ids"], "pending research object identity")
    alpha = _unique_ids(canonical_graph["alpha_event_ids_before"], "alpha event identity")
    if not scores:
        raise CorrectionViolation("correction omitted all affected scores")
    return {
        "schema_version": "1.0.0", "artifact_type": "phase4_score_correction_impact",
        "correction_key": [game, issue, new, correction_policy_version],
        "old_result_revision_id": old, "new_result_revision_id": new,
        "corrected_score_ids": scores, "corrected_aggregate_ids": aggregates,
        "pending_research_object_ids": pending, "alpha_event_ids_before": alpha,
        "score_side_complete": True,
    }


def correction_impact_id(impact: Mapping[str, Any]) -> str:
    return content_id("score-correction-impact", impact)


def apply_current_replacements(*, current_scores: Mapping[str, str], current_aggregates: Mapping[str, str],
                               score_replacements: Mapping[str, str], aggregate_replacements: Mapping[str, str],
                               expected_old_score_ids: Sequence[str], expected_old_aggregate_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Pure CAS reducer: preserve old objects and replace every affected current pointer once."""
    if not isinstance(current_scores, Mapping) or not isinstance(current_aggregates, Mapping):
        raise CorrectionViolation("canonical current projections are missing")
    derived_scores = list(current_scores.values())
    derived_aggregates = list(current_aggregates.values())
    if len(derived_scores) != len(set(derived_scores)) or len(derived_aggregates) != len(set(derived_aggregates)):
        raise CorrectionViolation("canonical current projections contain duplicate object identities")
    if (set(score_replacements) != set(derived_scores) or set(aggregate_replacements) != set(derived_aggregates)
            or set(expected_old_score_ids) != set(derived_scores) or set(expected_old_aggregate_ids) != set(derived_aggregates)):
        raise CorrectionViolation("correction replacement set is incomplete")
    scores, aggregates = dict(current_scores), dict(current_aggregates)
    for old in derived_scores:
        matches = [key for key, value in scores.items() if value == old]
        if len(matches) != 1:
            raise CorrectionViolation("old score is not uniquely current")
        replacement = validate_stable_id(score_replacements[old], "replacement score identity")
        if replacement in scores.values():
            raise CorrectionViolation("replacement score is already current")
        scores[matches[0]] = replacement
    for old in derived_aggregates:
        matches = [key for key, value in aggregates.items() if value == old]
        if len(matches) != 1:
            raise CorrectionViolation("old aggregate is not uniquely current")
        replacement = validate_stable_id(aggregate_replacements[old], "replacement aggregate identity")
        if replacement in aggregates.values():
            raise CorrectionViolation("replacement aggregate is already current")
        aggregates[matches[0]] = replacement
    return {"scores": scores, "aggregates": aggregates}
