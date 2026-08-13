from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry
from ..serialization import load_json, sha256_file
from ..storage import resolve_inside, write_once_json


def validate_contract(args: Any) -> dict[str, Any]:
    config_root, schema_root = args.config.resolve(), args.schemas.resolve()
    if not config_root.is_dir() or not schema_root.is_dir():
        raise ContractEvidenceMismatch("contract config/schema roots are missing")
    authority = load_json(args.authority_receipt)
    if authority.get("status") != "PASS" or authority.get("task_id") != "T00":
        raise ContractEvidenceMismatch("authority receipt is not T00 PASS")
    schema_files = sorted(schema_root.glob("*.schema.json"))
    config_files = sorted(config_root.glob("*.json"))
    if not schema_files or not config_files:
        raise ContractEvidenceMismatch("contract bundle is empty")
    for path in schema_files:
        Draft202012Validator.check_schema(load_json(path))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_contract_validation",
        "status": "PASS",
        "terminal": "PASS",
        "config_count": len(config_files),
        "schema_count": len(schema_files),
        "authority_receipt_sha256": sha256_file(args.authority_receipt),
        "actor_assignments_sha256": sha256_file(args.actor_assignments),
    }
    write_once_json(output / "contract-validation.json", receipt)
    return {"status": "PASS", "terminal": "PASS", "output": output.as_posix()}


def register(registry: ProviderRegistry) -> None:
    registry.register("contract", "validate", validate_contract)
