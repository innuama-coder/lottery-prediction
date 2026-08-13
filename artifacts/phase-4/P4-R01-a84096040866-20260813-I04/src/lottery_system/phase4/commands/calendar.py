from __future__ import annotations

from typing import Any

from ..calendar import (
    CalendarAmbiguous,
    build_calendar_release,
    load_calendar_build_fixture,
    validate_calendar_release,
)
from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..ledger import AppendOnlyLedger
from ..serialization import load_json, sha256_file
from ..storage import AdvisoryFileLock, resolve_inside, validate_runtime_root, write_once_json


def calendar_build(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    try:
        policy_path, entries = load_calendar_build_fixture(root, args.calendar_policy.resolve())
        release = build_calendar_release(policy_path, entries, calendar_release_id=args.calendar_id)
        provenance = producer_provenance(root, runtime.relative_to(root).as_posix())
        with AdvisoryFileLock(resolve_inside(runtime, ".calendar-release.lock")):
            path = resolve_inside(runtime, f"calendar-releases/{release['calendar_release_id']}/calendar.json")
            if path.exists():
                if load_json(path, reject_floats=True) != release:
                    raise CalendarAmbiguous("calendar release identity reuse mismatch")
                idempotent = True
            else:
                write_once_json(path, release)
                idempotent = False
            ledger = AppendOnlyLedger(runtime, "calendar-releases")
            validation = ledger.validate()
            view = load_json(ledger.current_view_path, reject_floats=True) if validation["event_count"] else {"objects": {}}
            if release["calendar_release_id"] not in view["objects"]:
                event = ledger.append_event(
                    object_id=release["calendar_release_id"],
                    event_type="calendar_release_published",
                    event_at_utc=parse_clock(args.clock),
                    payload={"calendar_release_id": release["calendar_release_id"], "sha256": sha256_file(path)},
                    producer_provenance=provenance,
                    expected_head_sha256=validation["head_sha256"],
                )
                head_sha256 = event["event_sha256"]
            else:
                head_sha256 = validation["head_sha256"]
    except CalendarAmbiguous as exc:
        return {"status": "HOLD", "terminal": exc.terminal, "error": str(exc), "exit_code": 20}
    return {
        "status": "PASS", "terminal": "PASS", "calendar_release_id": release["calendar_release_id"],
        "ledger_head_sha256": head_sha256, "idempotent_resume": idempotent, "exit_code": 0,
    }


def calendar_validate(args: Any) -> dict[str, Any]:
    try:
        release = validate_calendar_release(load_json(args.calendar, reject_floats=True), contract_id=args.contract_id)
    except CalendarAmbiguous as exc:
        return {"status": "HOLD", "terminal": exc.terminal, "error": str(exc), "exit_code": 20}
    return {"status": "PASS", "terminal": "PASS", "calendar_release_id": release["calendar_release_id"], "exit_code": 0}


def register(registry: ProviderRegistry) -> None:
    registry.register("calendar", "build", calendar_build)
    registry.register("calendar", "validate", calendar_validate)
