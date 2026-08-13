from __future__ import annotations

from pathlib import Path
from typing import Any

from ..alerts import audit_user_scheduler
from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..ledger import AppendOnlyLedger
from ..scheduler import build_schedule_release, tick_schedule
from ..serialization import canonical_sha256, load_json, sha256_file
from ..storage import AdvisoryFileLock, resolve_inside, safe_relative_path, validate_runtime_root, write_once_json


def _project_file(root: Path, supplied: Path) -> Path:
    candidate = supplied.resolve() if supplied.is_absolute() else resolve_inside(root, safe_relative_path(supplied.as_posix()))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("schedule input is outside the installed project") from exc
    return candidate


def schedule_build(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, Path(args.runtime_root))
    calendar_path = _project_file(root, Path(args.calendar))
    release = build_schedule_release(
        load_json(calendar_path, reject_floats=True),
        schedule_release_id=args.schedule_id, contract_id=args.contract_id,
    )
    relative = f"artifacts/phase-4-runtime/{runtime.name}/schedule-releases/{release['schedule_release_id']}/schedule.json"
    provenance = producer_provenance(root, relative)
    with AdvisoryFileLock(resolve_inside(runtime, ".schedule-release.lock")):
        path = resolve_inside(runtime, f"schedule-releases/{release['schedule_release_id']}/schedule.json")
        if path.is_file():
            if load_json(path, reject_floats=True) != release:
                raise ValueError("schedule release identity reuse mismatch")
            idempotent = True
        else:
            write_once_json(path, release)
            idempotent = False
        ledger = AppendOnlyLedger(runtime, "schedule-releases")
        state = ledger.validate()
        view = load_json(ledger.current_view_path, reject_floats=True) if state["event_count"] else {"objects":{}}
        if release["schedule_release_id"] not in view["objects"]:
            event = ledger.append_event(
                object_id=release["schedule_release_id"], event_type="schedule_release_published",
                event_at_utc=parse_clock(args.clock),
                payload={"schedule_release_id":release["schedule_release_id"],"sha256":sha256_file(path),"calendar_release_id":release["calendar_release_id"]},
                producer_provenance=provenance, expected_head_sha256=state["head_sha256"],
            )
            head = event["event_sha256"]
        else:
            head = state["head_sha256"]
    return {"status":"PASS","terminal":"PASS","schedule_release_id":release["schedule_release_id"],"schedule_sha256":sha256_file(path),"ledger_head_sha256":head,"idempotent_resume":idempotent,"exit_code":0}


def schedule_tick(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, Path(args.runtime_root))
    schedule_path = _project_file(root, Path(args.schedule))
    provenance = producer_provenance(root, f"artifacts/phase-4-runtime/{runtime.name}/scheduler")
    receipt = tick_schedule(
        root, runtime, load_json(schedule_path, reject_floats=True),
        clock=parse_clock(args.clock), provenance=provenance,
    )
    return {"status":"PASS","terminal":"PASS",**receipt,"exit_code":0}


def schedule_audit(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    release = Path(args.release_root).resolve(strict=False)
    runtime = Path(args.runtime_root).resolve(strict=False)
    output = _project_file(root, Path(args.output))
    audit = audit_user_scheduler(release, runtime)
    audit["audit_sha256"] = canonical_sha256(audit)
    provenance = producer_provenance(root, output.relative_to(root).as_posix())
    audit["provenance"] = provenance
    write_once_json(output, audit)
    code = 0 if audit["status"] == "PASS" else 20
    return {"status":audit["status"],"terminal":audit["terminal"],"audit_sha256":sha256_file(output),"observed":audit["observed"],"exit_code":code}


def register(registry: ProviderRegistry) -> None:
    registry.register("schedule", "build", schedule_build)
    registry.register("schedule", "tick", schedule_tick)
    registry.register("schedule", "audit", schedule_audit)
