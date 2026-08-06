from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .serialization import load_json


SCHEMAS = {
    "contract": "contract.schema.json",
    "readiness": "readiness.schema.json",
    "qualification": "qualification.schema.json",
    "historical_audit": "historical-audit.schema.json",
    "power": "power.schema.json",
    "replay": "replay.schema.json",
    "review": "review.schema.json",
    "e2e_registry": "e2e-registry.schema.json",
    "acceptance": "acceptance.schema.json",
    "verification_receipt": "verification-receipt.schema.json",
}


def schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas" / "phase2_1"


def validate(kind: str, payload: Any) -> None:
    if kind not in SCHEMAS:
        raise ValueError(f"unknown Phase 2.1 schema kind: {kind}")
    schema = load_json(schema_root() / SCHEMAS[kind])
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(map(str, first.absolute_path)) or "$"
        raise ValueError(f"{kind} schema violation at {location}: {first.message}")
