from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..identity import validate_stable_id
from ..ledger import AppendOnlyLedger
from ..official_adapter import (
    ProtectedRootMutation,
    RetryableSourceError,
    SourcePolicyError,
    SourceReadinessError,
    run_readonly_canary,
)
from ..serialization import load_json, sha256_file
from ..storage import AdvisoryFileLock, resolve_inside, validate_runtime_root, write_once_json
from ..verification import SourceVerificationError, deduplicate_facts, verify_result_revision


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def data_ingest(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    output = args.output.resolve()
    provenance = producer_provenance(root, output.relative_to(root).as_posix())
    try:
        result = run_readonly_canary(
            project_root=root,
            source_policy_path=args.source_policy.resolve(),
            staging_root=args.staging_root.resolve(),
            output_root=output,
            mode=args.mode,
            observed_at_utc=_now(),
            producer_provenance=provenance,
        )
    except (ProtectedRootMutation, RetryableSourceError, SourcePolicyError, SourceReadinessError, SourceVerificationError) as exc:
        code = getattr(exc, "exit_code", 20)
        return {
            "status": "FAIL" if code == 6 else "HOLD",
            "terminal": getattr(exc, "terminal", "HOLD_SOURCE_READINESS"),
            "error": str(exc),
            "exit_code": code,
        }
    return {"status": "PASS", "terminal": "PASS", "summary": result, "exit_code": 0}


def _runtime_facts(runtime: Path) -> list[dict[str, Any]]:
    root = resolve_inside(runtime, "parsed-source-facts")
    if not root.is_dir():
        raise SourceVerificationError("runtime has no parsed source facts")
    rows = [load_json(path, reject_floats=True) for path in sorted(root.iterdir()) if path.is_file() and path.suffix == ".json"]
    return deduplicate_facts(rows)


def data_verify(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    validate_stable_id(args.contract_id, "source verification contract identity")
    observation_id = validate_stable_id(args.observation_id, "observation identity")
    try:
        facts = _runtime_facts(runtime)
        selected = [row for row in facts if row["observation_id"] == observation_id]
        if len(selected) != 1:
            raise SourceVerificationError("observation identity must select exactly one parsed issue")
        target = selected[0]
        pair = [row for row in facts if row["game"] == target["game"] and row["issue_id"] == target["issue_id"]]
        if len(pair) != 2:
            raise SourceVerificationError("verified result requires exactly two registered source facts")
        revision = verify_result_revision(pair[0], pair[1], verified_at_utc=parse_clock(args.clock))
        provenance = producer_provenance(root, runtime.relative_to(root).as_posix())
        with AdvisoryFileLock(resolve_inside(runtime, ".result-revision.lock")):
            path = resolve_inside(runtime, f"result-revisions/{revision['result_revision_id']}.json")
            if path.exists():
                if load_json(path, reject_floats=True) != revision:
                    raise SourceVerificationError("result revision identity reuse mismatch")
            else:
                write_once_json(path, revision)
            ledger = AppendOnlyLedger(runtime, "result-revisions")
            validation = ledger.validate()
            view = load_json(ledger.current_view_path, reject_floats=True) if validation["event_count"] else {"objects": {}}
            if revision["result_revision_id"] not in view["objects"]:
                ledger.append_event(
                    object_id=revision["result_revision_id"],
                    event_type="result_revision_verified",
                    event_at_utc=parse_clock(args.clock),
                    payload={"result_revision_id": revision["result_revision_id"], "sha256": sha256_file(path)},
                    producer_provenance=provenance,
                    expected_head_sha256=validation["head_sha256"],
                )
    except SourceVerificationError as exc:
        return {"status": "HOLD", "terminal": exc.terminal, "error": str(exc), "exit_code": 20}
    return {"status": "PASS", "terminal": "PASS", "result_revision_id": revision["result_revision_id"], "exit_code": 0}


def register(registry: ProviderRegistry) -> None:
    registry.register("data", "ingest", data_ingest)
    registry.register("data", "verify", data_verify)
