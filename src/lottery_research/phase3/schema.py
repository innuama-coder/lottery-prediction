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
