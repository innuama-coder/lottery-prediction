from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..identity import content_id, validate_stable_id, verify_content_id


class ResearchRegistryViolation(ValueError):
    exit_code = 5


FAMILIES = ("static_parameter", "slow_drift_parameter", "context_feature")
_PARAMETER_PATHS = {
    "/P01/shrinkage": {1, 5, 20, 100},
    "/P02/training_window": {50, 100, 150, "expanding"},
    "/P03/recency_half_life": {26, 52, 104, "none"},
}
_FAMILY_PATHS = {
    "static_parameter": {"/P01/shrinkage", "/P02/training_window", "/P04/tick_group"},
    "slow_drift_parameter": {"/P03/recency_half_life", "/P04/tick_group"},
    "context_feature": {"/F01/enabled", "/F02/config"},
}
_F02_VALUES = {"weekday", "month", "holiday", "draw_gap", "recent_frequency", "recent_omission"}


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchRegistryViolation(f"{label} must be a non-boolean integer")
    return value


def validate_frozen_registries(model_registry: Mapping[str, Any], feature_registry: Mapping[str, Any]) -> None:
    if model_registry.get("artifact_type") != "phase4_model_registry":
        raise ResearchRegistryViolation("model registry identity mismatch")
    if model_registry.get("champion_promotion_surface") is not False:
        raise ResearchRegistryViolation("Champion promotion surface must remain closed")
    families = model_registry.get("parameter_families")
    if not isinstance(families, Mapping) or set(families) != {"P01", "P02", "P03", "P04"}:
        raise ResearchRegistryViolation("parameter registry is incomplete")
    if feature_registry.get("artifact_type") != "phase4_feature_registry":
        raise ResearchRegistryViolation("feature registry identity mismatch")
    if feature_registry.get("unknown_features_allowed") is not False or feature_registry.get("one_family_per_experiment") is not True:
        raise ResearchRegistryViolation("feature registry does not close the experiment surface")
    feature_ids = {row.get("feature_id") for row in feature_registry.get("features", []) if isinstance(row, Mapping)}
    if feature_ids != {"F01", "F02"}:
        raise ResearchRegistryViolation("feature registry is incomplete")


def canonical_diff(
    patches: object,
    hypothesis_family: object,
    *,
    model_registry: Mapping[str, Any],
    feature_registry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    validate_frozen_registries(model_registry, feature_registry)
    if hypothesis_family not in FAMILIES:
        raise ResearchRegistryViolation("hypothesis family is not registered")
    if not isinstance(patches, list) or len(patches) != 1 or not isinstance(patches[0], Mapping):
        raise ResearchRegistryViolation("exactly one canonical parameter or feature patch is required")
    patch = dict(patches[0])
    if set(patch) != {"op", "path", "value"} or patch["op"] != "replace" or not isinstance(patch["path"], str):
        raise ResearchRegistryViolation("canonical diff shape is invalid")
    path = patch["path"]
    if path not in _FAMILY_PATHS[hypothesis_family]:
        raise ResearchRegistryViolation("diff is unregistered or belongs to another hypothesis family")
    value = patch["value"]
    if path in _PARAMETER_PATHS:
        if isinstance(value, bool) or value not in _PARAMETER_PATHS[path]:
            raise ResearchRegistryViolation("registered parameter value is invalid")
    elif path == "/P04/tick_group":
        if not isinstance(value, list) or not 1 <= len(value) <= 8:
            raise ResearchRegistryViolation("tick group must contain one through eight offsets")
        checked = [_strict_int(item, "tick offset") for item in value]
        if len(set(checked)) != len(checked) or any(item < -4096 or item > 4096 for item in checked):
            raise ResearchRegistryViolation("tick group is duplicate or out of range")
        if checked != sorted(checked):
            raise ResearchRegistryViolation("tick group must use canonical ascending order")
    elif path == "/F01/enabled":
        if not isinstance(value, bool):
            raise ResearchRegistryViolation("F01 enabled value must be boolean")
    elif path == "/F02/config":
        if not isinstance(value, list) or not 1 <= len(value) <= 8 or any(not isinstance(item, str) for item in value):
            raise ResearchRegistryViolation("F02 config must be a nonempty string list")
        if len(set(value)) != len(value) or not set(value) <= _F02_VALUES:
            raise ResearchRegistryViolation("F02 config is duplicate or unregistered")
        if value != sorted(value):
            raise ResearchRegistryViolation("F02 config must use canonical lexical order")
    else:
        raise ResearchRegistryViolation("unregistered diff path")
    return [patch]


def candidate_id(candidate: Mapping[str, Any]) -> str:
    return content_id("candidate", candidate, excluded_fields=("candidate_id", "status"))


def build_candidate(
    *,
    game: str,
    parent_model_id: str,
    parent_config_id: str,
    patches: object,
    hypothesis_family: str,
    code_identity: str,
    data_release_id: str,
    feature_snapshot_id: str,
    preregistration_id: str,
    qualification_id: str,
    status: str,
    model_registry: Mapping[str, Any],
    feature_registry: Mapping[str, Any],
) -> dict[str, Any]:
    if game not in {"ssq", "dlt"}:
        raise ResearchRegistryViolation("game is invalid")
    for label, value in (
        ("parent model", parent_model_id), ("parent config", parent_config_id),
        ("code identity", code_identity), ("data release", data_release_id),
        ("feature snapshot", feature_snapshot_id), ("preregistration", preregistration_id),
        ("qualification", qualification_id),
    ):
        validate_stable_id(value, label)
    if parent_model_id != "M0":
        raise ResearchRegistryViolation("Phase 4 candidates must retain M0 as the immutable Champion parent")
    if status not in {"rejected", "archived", "shadow_candidate", "archived_pending_requalification"}:
        raise ResearchRegistryViolation("candidate status is invalid")
    body: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact_type": "phase4_candidate", "game": game,
        "parent_model_id": parent_model_id, "parent_config_id": parent_config_id,
        "canonical_diff": canonical_diff(patches, hypothesis_family, model_registry=model_registry, feature_registry=feature_registry),
        "hypothesis_family": hypothesis_family, "code_identity": code_identity,
        "data_release_id": data_release_id, "feature_snapshot_id": feature_snapshot_id,
        "preregistration_id": preregistration_id, "qualification_id": qualification_id,
        "status": status,
    }
    body["candidate_id"] = candidate_id(body)
    return body


def validate_candidate(candidate: Mapping[str, Any], *, model_registry: Mapping[str, Any], feature_registry: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "artifact_type", "candidate_id", "game", "parent_model_id", "parent_config_id",
        "canonical_diff", "hypothesis_family", "code_identity", "data_release_id", "feature_snapshot_id",
        "preregistration_id", "qualification_id", "status",
    }
    if set(candidate) != required or candidate.get("schema_version") != "1.0.0" or candidate.get("artifact_type") != "phase4_candidate":
        raise ResearchRegistryViolation("candidate shape is invalid")
    canonical_diff(candidate["canonical_diff"], candidate["hypothesis_family"], model_registry=model_registry, feature_registry=feature_registry)
    verify_content_id(candidate["candidate_id"], "candidate", candidate, excluded_fields=("candidate_id", "status"))


def apply_registered_diff(config: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(patches) != 1:
        raise ResearchRegistryViolation("exactly one diff is required")
    result = dict(config)
    path, value = patches[0]["path"], patches[0]["value"]
    key = path.strip("/").replace("/", ".")
    if result.get(key) == value:
        raise ResearchRegistryViolation("config change must change the next shadow output")
    result[key] = value
    return result
