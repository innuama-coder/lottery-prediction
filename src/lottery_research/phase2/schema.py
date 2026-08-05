from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from .errors import InvalidContract


SCHEMA_FILES = {
    "input_manifest": "input-manifest.schema.json",
    "preregistration": "preregistration.schema.json",
    "reviewer_assignment": "reviewer-assignment.schema.json",
    "run_request": "run-request.schema.json",
    "run_result": "run-result.schema.json",
    "replay_review": "replay-review.schema.json",
    "acceptance": "acceptance.schema.json",
    "environment_lock": "environment-lock.schema.json",
    "method_review": "method-review.schema.json",
    "e2e_receipt": "e2e-receipt.schema.json",
    "e2e_registry": "e2e-registry.schema.json",
    "final_evidence_manifest": "final-evidence-manifest.schema.json",
}


def default_schema_root() -> Path:
    source_root = Path(__file__).resolve().parents[3] / "schemas" / "phase2"
    installed_root = Path(sys.prefix) / "share" / "autoresearch-lotte" / "schemas" / "phase2"
    for candidate in (source_root, installed_root):
        if candidate.is_dir():
            return candidate
    raise InvalidContract("Phase 2 schema root not found")


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise InvalidContract(f"required JSON file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidContract(f"cannot load JSON file {path}: {exc}") from exc


def load_schema(kind: str, schema_root: Path | None = None) -> dict[str, Any]:
    if kind not in SCHEMA_FILES:
        raise InvalidContract(f"unknown schema kind: {kind}")
    root = schema_root or default_schema_root()
    schema = load_json(root / SCHEMA_FILES[kind])
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise InvalidContract(f"invalid {kind} schema: {exc.message}") from exc
    return schema


def validate_payload(kind: str, payload: Any, schema_root: Path | None = None) -> None:
    validator = Draft202012Validator(load_schema(kind, schema_root), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if errors:
        error: ValidationError = errors[0]
        location = "/".join(str(item) for item in error.absolute_path) or "$"
        raise InvalidContract(f"{kind} schema violation at {location}: {error.message}")
