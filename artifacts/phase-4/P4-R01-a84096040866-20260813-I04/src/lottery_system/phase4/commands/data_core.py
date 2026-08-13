from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..data_chain import append_data_release, create_genesis, current_data_release
from ..identity import validate_stable_id
from ..storage import validate_runtime_root


def _runtime(args: Any) -> tuple[Path, Path]:
    root = project_root().resolve()
    return root, validate_runtime_root(root, args.runtime_root)


def data_genesis(args: Any) -> dict[str, Any]:
    root, runtime = _runtime(args)
    result = create_genesis(
        root,
        runtime,
        args.genesis.resolve(),
        clock=parse_clock(args.clock),
        producer_provenance=producer_provenance(root, runtime.relative_to(root).as_posix()),
    )
    return {"status": "PASS", "terminal": "PASS", "data_release_id": result["release"]["data_release_id"], "idempotent_resume": result["idempotent_resume"]}


def data_release(args: Any) -> dict[str, Any]:
    root, runtime = _runtime(args)
    validate_stable_id(args.contract_id, "contract identity")
    current = current_data_release(runtime, load_current_id(runtime))["requested_release"]
    result = append_data_release(
        root,
        runtime,
        data_release_id=args.data_release_id,
        previous_phase4_release_id=current["data_release_id"],
        result_revision_ids=args.result_revision_id,
        clock=parse_clock(args.clock),
        producer_provenance=producer_provenance(root, runtime.relative_to(root).as_posix()),
    )
    return {"status": "PASS", "terminal": "PASS", "data_release_id": result["release"]["data_release_id"], "idempotent_resume": result["idempotent_resume"]}


def load_current_id(runtime: Path) -> str:
    from ..serialization import load_json

    value = load_json(runtime / "data-releases/current-view.json", reject_floats=True)
    return value["data_release_id"]


def data_current(args: Any) -> dict[str, Any]:
    _, runtime = _runtime(args)
    result = current_data_release(runtime, args.data_release_id)
    return {"status": "PASS", "terminal": "PASS", **result}


def register(registry: ProviderRegistry) -> None:
    registry.register("data", "genesis", data_genesis)
    registry.register("data", "release", data_release)
    registry.register("data", "current", data_current)
