from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry, producer_provenance, project_root
from ..provider_registry import complete_registry
from ..identity import verify_content_id
from ..serialization import canonical_sha256, load_json, sha256_file
from ..state_projection import project_runtime_state
from ..storage import resolve_inside, safe_relative_path, validate_runtime_root, write_once_json


STATE_CONTRACT_ID = "phase4-state-v1"
STATE_CONTRACT_PATH = "config/phase4/state-contract.json"
STATE_CONTRACT_SHA256 = "4119cdd274446135ca8e180f745509db68b8455dba6e2ab1fc1fe878e2c04012"


def _write_same(path: Path, value: dict[str, Any]) -> bool:
    if path.is_file():
        if load_json(path, reject_floats=True) != value:
            raise ContractEvidenceMismatch("state projection identity reuse mismatch")
        return True
    write_once_json(path, value)
    return False


def state_project(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, Path(args.runtime_root))
    contract_path = resolve_inside(root, STATE_CONTRACT_PATH)
    if sha256_file(contract_path) != STATE_CONTRACT_SHA256:
        raise ContractEvidenceMismatch("installed state contract hash mismatch")
    contract = load_json(contract_path, reject_floats=True)
    projection = project_runtime_state(runtime, contract)
    object_relative = f"state-projections/objects/{projection['projection_id']}.json"
    object_path = resolve_inside(runtime, object_relative)
    output_argument = Path(args.output)
    if output_argument.is_absolute():
        try:
            output_relative = output_argument.resolve(strict=False).relative_to(root).as_posix()
        except ValueError as exc:
            raise ContractEvidenceMismatch("state projection output is outside the installed project") from exc
    else:
        output_relative = safe_relative_path(output_argument.as_posix())
    output_root = resolve_inside(root, output_relative)
    receipt_path = output_root / "state-projection.json"
    provenance = producer_provenance(root, receipt_path.relative_to(root).as_posix())
    idempotent = _write_same(object_path, projection)
    receipt = {
        "schema_version": "1.0.0", "artifact_type": "phase4_state_projection_receipt",
        "projection_id": projection["projection_id"], "projection_sha256": canonical_sha256(projection),
        "object_path": object_path.relative_to(root).as_posix(), "contract_id": STATE_CONTRACT_ID,
        "ledger_head_sha256": projection["ledger_head_sha256"], "producer_provenance": provenance,
    }
    receipt_idempotent = _write_same(receipt_path, receipt)
    return {
        "status": "PASS", "terminal": "PASS", "projection_id": projection["projection_id"],
        "projection_sha256": receipt["projection_sha256"], "engineering_status": projection["engineering_status"]["status"],
        "model_status_count": len(projection["model_status"]), "top_k_status_count": len(projection["top_k_status"]),
        "idempotent_resume": idempotent and receipt_idempotent, "exit_code": 0,
    }


def state_show(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, Path(args.runtime_root))
    object_id = safe_relative_path(args.object_id)
    path = resolve_inside(runtime, f"state-projections/objects/{object_id}.json")
    if not path.is_file():
        raise ContractEvidenceMismatch("explicit state projection object does not exist")
    projection = load_json(path, reject_floats=True)
    if projection.get("projection_id") != object_id:
        raise ContractEvidenceMismatch("state projection object identity mismatch")
    verify_content_id(object_id, "state-projection", projection, excluded_fields=("projection_id",))
    return {"status": "PASS", "terminal": "PASS", "projection": projection, "exit_code": 0}


def register(registry: ProviderRegistry) -> None:
    registry.register("state", "project", state_project)
    registry.register("state", "show", state_show)
    complete_registry(registry, project_root())
