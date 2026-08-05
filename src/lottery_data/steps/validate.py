"""Schema and semantic validation for parsed observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lottery_data.models import ContractViolation, validate_object


def validate_observations(observations: Sequence[Mapping[str, Any]]) -> None:
    identities: set[str] = set()
    source_issues: set[tuple[str, str, str]] = set()
    for supplied in observations:
        observation = dict(supplied)
        validate_object("SourceObservation", observation)
        observation_id = observation["observation_id"]
        source_issue = (observation["source_id"], observation["game"], observation["issue_id"])
        if observation_id in identities:
            raise ContractViolation("bootstrap-transform", f"duplicate observation_id: {observation_id}")
        if source_issue in source_issues:
            raise ContractViolation("bootstrap-transform", f"duplicate source/game/issue: {source_issue}")
        identities.add(observation_id)
        source_issues.add(source_issue)
