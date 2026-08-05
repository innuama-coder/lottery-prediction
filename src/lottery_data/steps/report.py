from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lottery_data.models import validate_object


@dataclass
class RunCounters:
    planned: int = 0
    started: int = 0
    succeeded: int = 0
    failed: int = 0
    parsed: int = 0
    valid: int = 0
    invalid: int = 0
    missing: int = 0
    duplicate: int = 0
    conflict: int = 0
    candidates: int = 0
    eligible: int = 0
    unresolved: int = 0
    added: int = 0
    revised: int = 0
    unchanged: int = 0
    manual_core_edit: int = 0
    artifact_hashes: dict[str, str] = field(default_factory=dict)


def build_run_result(
    *,
    run_id: str,
    status: str,
    exit_code: int,
    started_at_utc: str,
    completed_at_utc: str,
    release_id: str | None,
    manifest_ref: str,
    events_ref: str,
    quality_report_ref: str,
    error_refs: list[str],
    counters: RunCounters,
    mode: str = "bootstrap",
) -> dict[str, Any]:
    result = {
        "result_schema_version": "1.0.0",
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "request_stats": {
            "planned": counters.planned,
            "started": counters.started,
            "succeeded": counters.succeeded,
            "failed": counters.failed,
            "not_started": counters.planned - counters.started,
        },
        "observation_stats": {
            "parsed": counters.parsed,
            "valid": counters.valid,
            "invalid": counters.invalid,
            "missing": counters.missing,
            "duplicate": counters.duplicate,
            "conflict": counters.conflict,
        },
        "candidate_stats": {
            "observed": counters.candidates,
            "eligible": counters.eligible,
            "unresolved": counters.unresolved,
        },
        "change_stats": {
            "added": counters.added,
            "revised": counters.revised,
            "unchanged": counters.unchanged,
            "conflict": counters.conflict,
            "invalid": counters.invalid,
            "duplicate": counters.duplicate,
            "manual_core_edit": counters.manual_core_edit,
        },
        "exit_code": exit_code,
        "release_id": release_id,
        "manifest_ref": manifest_ref,
        "events_ref": events_ref,
        "quality_report_ref": quality_report_ref,
        "error_refs": sorted(set(error_refs)),
        "deterministic_artifact_hashes": dict(sorted(counters.artifact_hashes.items())),
    }
    validate_object("RunResult", result)
    return result
