from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .serialization import load_json


SCHEMA_FILES = {
    "input_manifest": "input-manifest.schema.json",
    "preregistration": "preregistration.schema.json",
    "model_registry": "model-registry.schema.json",
    "feature_registry": "feature-registry.schema.json",
    "fold": "fold.schema.json",
    "forecast": "forecast.schema.json",
    "metric": "metric.schema.json",
    "experiment_ledger": "experiment-ledger.schema.json",
    "replay": "replay.schema.json",
    "review": "review.schema.json",
    "manifest": "manifest.schema.json",
    "acceptance": "acceptance.schema.json",
    "actor_assignment": "actor-assignment.schema.json",
    "handoff": "handoff.schema.json",
    "work_item_receipt": "work-item-receipt.schema.json",
}


def validate_payload(root: Path, kind: str, payload: Any) -> None:
    if kind not in SCHEMA_FILES:
        raise ValueError(f"unknown Phase 3 schema kind: {kind}")
    schema = load_json(root / "schemas/phase3" / SCHEMA_FILES[kind])
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(map(str, first.absolute_path)) or "$"
        raise ValueError(f"{kind} schema violation at {location}: {first.message}")
    if kind == "review":
        reviewer = payload["reviewer_id"]
        if reviewer in {payload["implementation_author_id"], payload["classification_approver_id"]}:
            raise ValueError("review schema violation: reviewer identity conflicts with implementation or approval")
        if payload["implementation_author_id"] == payload["classification_approver_id"]:
            raise ValueError("review schema violation: implementation author cannot approve classification")
    if kind == "work_item_receipt":
        expected = {"PASS": 0, "HOLD": 20}
        if payload["status"] in expected and payload["process_exit_code"] != expected[payload["status"]]:
            raise ValueError("work item receipt exit code does not match status")
    if kind == "acceptance" and payload["implementation_author_id"] == payload["classification_approver_id"]:
        raise ValueError("acceptance schema violation: implementation author cannot approve classification")
    if kind == "actor_assignment":
        assignments = payload["assignments"]
        roles = [row["role"] for row in assignments]
        if len(roles) != len(set(roles)):
            raise ValueError("actor assignment contains duplicate roles")
        required = {"data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer"}
        if payload["assignment_stage"] == "formal_before_W07":
            required |= {"run_operator", "independent_reviewer", "acceptance_engineer", "classification_approver", "release_controller"}
            if payload["parent_assignment_sha256"] is None:
                raise ValueError("formal actor assignment must bind its preparation parent")
        elif payload["parent_assignment_sha256"] is not None:
            raise ValueError("preparation actor assignment cannot have a parent")
        if not required.issubset(set(roles)):
            raise ValueError("actor assignment required role coverage is incomplete")
        for row in assignments:
            if row["assigned_by"] != payload["controller_id"]:
                raise ValueError("actor assignment assigned_by must match controller_id")
        by_role = {row["role"]: row for row in assignments}
        if by_role["independent_method_reviewer"]["actor_id"] in {by_role["implementation_author"]["actor_id"], by_role["statistical_owner"]["actor_id"]}:
            raise ValueError("independent method reviewer conflicts with implementation or statistical owner")
        if payload["assignment_stage"] == "formal_before_W07":
            reviewer_id = by_role["independent_reviewer"]["actor_id"]
            author_id = by_role["implementation_author"]["actor_id"]
            approver_id = by_role["classification_approver"]["actor_id"]
            if reviewer_id in {author_id, approver_id} or author_id == approver_id:
                raise ValueError("formal actor assignment violates review/approval independence")
