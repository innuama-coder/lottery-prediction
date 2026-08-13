from __future__ import annotations

from typing import Any

from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..identity import validate_stable_id
from ..label_capability import unlock_result_label
from ..storage import validate_runtime_root


def result_unlock(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    result = unlock_result_label(
        runtime,
        forecast_id=validate_stable_id(args.forecast_id, "forecast identity"),
        result_revision_id=validate_stable_id(args.result_revision_id, "result revision identity"),
        label_unlocked_at=parse_clock(args.clock),
        contract_id=args.contract_id,
        producer_provenance=producer_provenance(root, runtime.relative_to(root).as_posix()),
    )
    return {
        "status": "PASS", "terminal": "PASS",
        "unlock_eligibility_id": result["eligibility"]["unlock_eligibility_id"],
        "idempotent_resume": result["idempotent_resume"], "exit_code": 0,
    }


def register(registry: ProviderRegistry) -> None:
    registry.register("result", "unlock", result_unlock)
