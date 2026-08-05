"""Offline-testable P0-06 scheduler runtime. Network use is opt-in at execution time."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from p0_04_http import AcquisitionError, ClockCheck, PublicHttpCollector, run_windows_clock_check
from p0_04_pipeline import dlt_issue_url, load_existing_environment, process_capture, write_run_artifacts
from phase0lib import ValidationError, canonical_json_bytes, canonical_sha256, load_json, load_jsonl, validate_json_file, validate_jsonl_file


REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "phase-0"
SOAK_LOG_NAME = "soak-run-log.jsonl"
PLAN_NAME = "p0-06-runtime-plan.json"
PLAN_HASH_NAME = "p0-06-runtime-plan.json.sha256"
GENERATED_AT = "2026-08-01T06:00:00Z"
DLT_ISSUES = {"2026-08-03": "2026087", "2026-08-05": "2026088", "2026-08-08": "2026089", "2026-08-10": "2026090", "2026-08-12": "2026091", "2026-08-15": "2026092"}
SSQ_ISSUES = {"2026-08-02": "2026088", "2026-08-04": "2026089", "2026-08-06": "2026090", "2026-08-09": "2026091", "2026-08-11": "2026092", "2026-08-13": "2026093"}


class RuntimeHold(RuntimeError):
    """Raised before any acquisition when runtime policy cannot authorize execution."""


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_runtime_plan(observation: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    games = {item["game"]: item for item in catalog["games"]}
    requests: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    for scheduled in observation["request_schedule"]:
        game = scheduled["game"]
        date = scheduled["scheduled_at_utc"][:10]
        issue_id = (DLT_ISSUES if game == "dlt" else SSQ_ISSUES)[date]
        if scheduled["source_slot"] == "authoritative_primary":
            source = games[game]["authoritative_primary"]
        else:
            corroborators = games[game]["official_corroborators"]
            if len(corroborators) != 1:
                raise ValidationError(f"{game}: runtime requires exactly one frozen corroborator")
            source = corroborators[0]
        approved = source["approved_use"]
        policy = {"blocked": "policy_blocked", "hold_pending": "compliance_hold", "scheduled_low_rate_fetch": "network_attempted"}[approved]
        if game == "ssq" and policy == "network_attempted":
            raise ValidationError("SSQ conditional issue mapping cannot authorize collection")
        request_url = dlt_issue_url(issue_id) if game == "dlt" and policy == "network_attempted" else None
        requests.append({
            "request_id": scheduled["request_id"], "sequence": scheduled["sequence"], "game": game,
            "planned_at_utc": scheduled["scheduled_at_utc"], "source_slot": scheduled["source_slot"],
            "source_id": source["source_id"], "approved_use": approved, "execution_policy": policy,
            "scheduled_issue_id": issue_id,
            "mapping_status": "expected_from_anchor_not_verified" if game == "dlt" else "conditional_hold",
            "request_url": request_url,
        })
        local = _parse_utc(scheduled["scheduled_at_utc"]).astimezone().isoformat(timespec="seconds")
        # Freeze Asia/Shanghai explicitly; do not depend on the executing machine's timezone.
        local = scheduled["scheduled_at_utc"][:10] + "T" + ("22:00:00+08:00" if scheduled["scheduled_at_utc"][11:16] == "14:00" else "22:03:00+08:00")
        triggers.append({"request_id": scheduled["request_id"], "planned_at_utc": scheduled["scheduled_at_utc"], "local_at": local})
    authorized_ids = [item["request_id"] for item in requests if item["game"] == "dlt" and item["execution_policy"] == "network_attempted"]
    if len(authorized_ids) != 6:
        raise ValidationError("P0-06 authorization must cover exactly six frozen DLT corroborator requests")
    return {
        "schema_version": "1.0.0", "artifact_type": "p0_06_runtime_plan", "contract_version": "1.3",
        "generated_at_utc": GENERATED_AT, "status": "prepared_not_started",
        "soak_log_path": "artifacts/phase-0/soak-run-log.jsonl",
        "plan_hash_sidecar": "artifacts/phase-0/p0-06-runtime-plan.json.sha256",
        "frozen_input_canonical_sha256": {"observation_plan": canonical_sha256(observation), "source_catalog": canonical_sha256(catalog)},
        "request_schedule_sha256": observation["request_schedule_sha256"],
        "acceptance_cutoff_utc": observation["acceptance_cutoff_utc"], "acceptance_cutoff_local": "2026-08-16T01:00:00+08:00",
        "issue_mapping": {"algorithm_version": "calendar-cadence-anchor-v1", "dlt_anchor_issue_id": "2026050", "dlt_anchor_draw_date": "2026-05-09", "dlt_rule": "increment_issue_by_one_for_each_Mon_Wed_Sat_draw_after_anchor", "dlt_page_mismatch_action": "fail_closed_do_not_auto_remap", "ssq_rule": "conditional_2026088_through_2026093_without_local_primary_anchor", "independent_verification_required": True, "observation_rule_mapping_scope": "outside_frozen_historical_mapping_after_2026050", "future_rule_inference": "forbidden"},
        "requests": requests,
        "scheduler": {"task_name": "AutoresearchLotte-P0-06", "scope": "current_user", "start_when_available": True, "execution_time_limit_minutes": 15, "multiple_instances": "IgnoreNew", "trigger_count": 24, "triggers": triggers, "default_installer_action": "verify"},
        "network_authorization": {"status": "authorized_by_user_for_frozen_p0_06_schedule", "authorized_request_count": 6, "authorized_request_ids": authorized_ids, "scope": "exact_dlt_scheduled_corroborator_requests_only"},
    }


def write_runtime_plan(artifacts: Path, plan: dict[str, Any]) -> str:
    raw = canonical_json_bytes(plan) + b"\n"
    digest = hashlib.sha256(raw).hexdigest()
    (artifacts / PLAN_NAME).write_bytes(raw)
    (artifacts / PLAN_HASH_NAME).write_bytes((digest + "\n").encode("ascii"))
    return digest


def load_validated_execution_plan(artifacts: Path, expected_plan_sha256: str | None) -> dict[str, Any]:
    if expected_plan_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256) is None:
        raise RuntimeHold("execution requires --expected-plan-sha256")
    plan_path = artifacts / PLAN_NAME
    schema_dir = artifacts / "schemas"
    plan = validate_json_file(plan_path, schema_dir / "p0-06-runtime-plan.schema.json")
    observation = validate_json_file(artifacts / "observation-plan.json", schema_dir / "observation-plan.schema.json")
    catalog = validate_json_file(artifacts / "source-catalog.json", schema_dir / "source-catalog.schema.json")
    raw = plan_path.read_bytes()
    if raw != canonical_json_bytes(plan) + b"\n":
        raise RuntimeHold("runtime plan is not canonical JSON")
    actual = hashlib.sha256(raw).hexdigest()
    sidecar_path = artifacts / PLAN_HASH_NAME
    try:
        sidecar_raw = sidecar_path.read_bytes()
    except OSError as exc:
        raise RuntimeHold("runtime plan hash sidecar is missing") from exc
    if sidecar_raw != (actual + "\n").encode("ascii"):
        raise RuntimeHold("runtime plan hash sidecar mismatch")
    if expected_plan_sha256 != actual:
        raise RuntimeHold("runtime plan does not match expected SHA-256")
    try:
        rebuilt = build_runtime_plan(observation, catalog)
    except (ValidationError, KeyError, ValueError) as exc:
        raise RuntimeHold("frozen observation/source policy cannot rebuild runtime plan") from exc
    if plan != rebuilt:
        raise RuntimeHold("runtime plan does not replay from frozen observation/source policy")
    return plan


def append_soak_entry(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json_bytes(entry) + b"\n"
    existing: dict[str, bytes] = {}
    if path.exists():
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise AcquisitionError("soak log lacks trailing newline")
        for number, old_line in enumerate(raw.splitlines(keepends=True), 1):
            try:
                value = json.loads(old_line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise AcquisitionError(f"invalid soak log line {number}") from exc
            if old_line != canonical_json_bytes(value) + b"\n":
                raise AcquisitionError(f"non-canonical soak log line {number}")
            request_id = value.get("request_id")
            if not isinstance(request_id, str) or request_id in existing:
                raise AcquisitionError(f"duplicate/invalid soak request_id at line {number}")
            existing[request_id] = old_line
    prior = existing.get(entry["request_id"])
    if prior is not None:
        if prior != line:
            raise AcquisitionError(f"request_id conflict with different soak content: {entry['request_id']}")
        return entry
    mode = "ab" if path.exists() else "xb"
    with path.open(mode) as handle:
        handle.write(line); handle.flush(); os.fsync(handle.fileno())
    return entry


def _existing_soak(path: Path, request_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    matches = [item for item in load_jsonl(path) if item.get("request_id") == request_id]
    if len(matches) > 1:
        raise AcquisitionError(f"duplicate soak request_id: {request_id}")
    return matches[0] if matches else None


def _base_entry(request: dict[str, Any], schedule_hash: str, now_text: str) -> dict[str, Any]:
    return {"schema_version": "1.1.0", "artifact_type": "soak_log_entry", "request_id": request["request_id"], "game": request["game"], "planned_at_utc": request["planned_at_utc"], "started_at_utc": now_text, "completed_at_utc": now_text, "source_slot": request["source_slot"], "source_id": request["source_id"], "scheduled_issue_id": request["scheduled_issue_id"], "request_schedule_sha256": schedule_hash, "execution_disposition": request["execution_policy"], "attempts": 0, "network_used": False, "clock_check_at_utc": None, "clock_offset_seconds": None, "result": request["execution_policy"], "classification_reason": "", "failure_injection": "none", "evidence_ref": None, "raw_payload_ref": None}


def _completed_text(started: datetime, utcnow_fn: Callable[[], datetime]) -> str:
    completed = utcnow_fn()
    if completed < started:
        completed = started
    return _utc_text(completed)


def _rate_limit_from_history(artifacts: Path, now: datetime, sleeper: Callable[[float], None]) -> None:
    attempts: list[datetime] = []
    manifest = artifacts / "evidence-manifest.jsonl"
    if manifest.exists():
        attempts.extend(_parse_utc(item["retrieved_at"]) for item in load_jsonl(manifest) if item.get("retrieved_at"))
    soak = artifacts / SOAK_LOG_NAME
    if soak.exists():
        attempts.extend(
            _parse_utc(item.get("completed_at_utc") or item["started_at_utc"])
            for item in load_jsonl(soak) if item.get("network_used") is True
        )
    if not attempts: return
    remaining = 60.0 - (now - max(attempts)).total_seconds()
    if remaining > 0: sleeper(remaining)


def _execute_one_validated(
    plan: dict[str, Any], request_id: str, *, artifacts: Path = ARTIFACTS,
    allow_network: bool = False,
    clock_fn: Callable[[int], ClockCheck] = run_windows_clock_check,
    collector_factory: Callable[[], PublicHttpCollector] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    failure_injection: str = "none",
    utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    requests = [item for item in plan["requests"] if item["request_id"] == request_id]
    if len(requests) != 1: raise RuntimeHold(f"unknown or duplicate request_id: {request_id}")
    request = requests[0]; started = utcnow_fn(); now_text = _utc_text(started)
    if started < _parse_utc(request["planned_at_utc"]): raise RuntimeHold("request is not due")
    soak_path = artifacts / SOAK_LOG_NAME
    prior = _existing_soak(soak_path, request_id)
    if prior is not None: return prior
    entry = _base_entry(request, plan["request_schedule_sha256"], now_text)
    if request["execution_policy"] == "policy_blocked":
        entry["classification_reason"] = "source_approved_use_blocked"
        entry["completed_at_utc"] = _completed_text(started, utcnow_fn)
        return append_soak_entry(soak_path, entry)
    if request["execution_policy"] == "compliance_hold":
        entry["classification_reason"] = "source_compliance_hold_no_collection"
        entry["completed_at_utc"] = _completed_text(started, utcnow_fn)
        return append_soak_entry(soak_path, entry)
    if started > _parse_utc(plan["acceptance_cutoff_utc"]):
        entry.update({"execution_disposition": "policy_blocked", "result": "invalid", "classification_reason": "acceptance_cutoff_passed_no_collection"})
        entry["completed_at_utc"] = _completed_text(started, utcnow_fn)
        return append_soak_entry(soak_path, entry)
    authorization = plan.get("network_authorization", {})
    authorized_ids = authorization.get("authorized_request_ids", [])
    if authorization.get("status") != "authorized_by_user_for_frozen_p0_06_schedule" or request_id not in authorized_ids:
        raise RuntimeHold("request is not authorized by the frozen P0-06 plan")
    if not allow_network: raise RuntimeHold("scheduled acquisition also requires explicit --allow-network")
    clock = clock_fn(5)
    if not clock.passed or abs(clock.offset_seconds) > 5:
        entry.update({"execution_disposition": "policy_blocked", "result": "invalid", "classification_reason": "fresh_clock_check_failed", "failure_injection": "clock_failure", "clock_check_at_utc": clock.checked_at_utc, "clock_offset_seconds": clock.offset_seconds})
        entry["completed_at_utc"] = _completed_text(started, utcnow_fn)
        return append_soak_entry(soak_path, entry)
    entry.update({"clock_check_at_utc": clock.checked_at_utc, "clock_offset_seconds": clock.offset_seconds, "attempts": 1, "network_used": True, "execution_disposition": "network_attempted", "result": "invalid", "failure_injection": failure_injection})
    _rate_limit_from_history(artifacts, started, sleeper)
    collector = collector_factory() if collector_factory else PublicHttpCollector(minimum_interval_seconds=60, timeout_seconds=20)
    try:
        fetch = collector.fetch(request["request_url"], clock_check=clock)
        outcome = process_capture(game=request["game"], issue_id=request["scheduled_issue_id"], fetch=fetch, clock_check=clock, output_root=artifacts, environment_lock=load_existing_environment(artifacts), evidence_prefix="p0-06")
        write_run_artifacts(artifacts, [outcome])
        entry["evidence_ref"] = f"artifacts/phase-0/evidence-manifest.jsonl#{outcome.evidence['evidence_id']}"
        entry["raw_payload_ref"] = outcome.evidence["stored_payload_path"]
        if outcome.parse_result is not None and outcome.evidence["status"] == "unverified":
            entry.update({"result": "unverified", "classification_reason": "captured_unverified_pending_independent_corroboration"})
        elif outcome.evidence.get("field_parsing_succeeded") and getattr(outcome, "normalized", None) is None:
            entry["classification_reason"] = "captured_raw_core_fields_parsed_rule_mapping_unavailable"
        else:
            entry["classification_reason"] = "capture_invalid_parse_or_rule_mapping"
    except (AcquisitionError, OSError, ValueError) as exc:
        entry["classification_reason"] = f"network_or_capture_failure:{type(exc).__name__}"
    entry["completed_at_utc"] = _completed_text(started, utcnow_fn)
    return append_soak_entry(soak_path, entry)


def execute_one(
    plan: dict[str, Any], request_id: str, *, artifacts: Path = ARTIFACTS,
    expected_plan_sha256: str | None = None, **kwargs: Any,
) -> dict[str, Any]:
    validated = load_validated_execution_plan(artifacts, expected_plan_sha256)
    if plan != validated:
        raise RuntimeHold("supplied runtime plan differs from validated on-disk plan")
    return _execute_one_validated(validated, request_id, artifacts=artifacts, **kwargs)


def execute_due(
    plan: dict[str, Any], *, artifacts: Path = ARTIFACTS,
    expected_plan_sha256: str | None = None,
    utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    **kwargs: Any,
) -> list[dict[str, Any]]:
    validated = load_validated_execution_plan(artifacts, expected_plan_sha256)
    if plan != validated:
        raise RuntimeHold("supplied runtime plan differs from validated on-disk plan")
    evaluated_at = utcnow_fn()
    return [
        _execute_one_validated(validated, item["request_id"], artifacts=artifacts, utcnow_fn=utcnow_fn, **kwargs)
        for item in validated["requests"] if _parse_utc(item["planned_at_utc"]) <= evaluated_at
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", choices=("build-plan", "verify-plan", "execute-one", "execute-due"), default="verify-plan"); parser.add_argument("--artifacts", type=Path, default=ARTIFACTS); parser.add_argument("--request-id"); parser.add_argument("--allow-network", action="store_true"); parser.add_argument("--expected-plan-sha256"); args = parser.parse_args(argv)
    try:
        if args.action == "build-plan":
            plan = build_runtime_plan(load_json(args.artifacts / "observation-plan.json"), load_json(args.artifacts / "source-catalog.json")); write_runtime_plan(args.artifacts, plan); (args.artifacts / SOAK_LOG_NAME).touch(exist_ok=True)
        elif args.action == "verify-plan":
            expected = args.expected_plan_sha256
            if expected is None:
                expected = (args.artifacts / PLAN_HASH_NAME).read_text(encoding="ascii").strip()
            plan = load_validated_execution_plan(args.artifacts, expected)
        else:
            plan = load_validated_execution_plan(args.artifacts, args.expected_plan_sha256)
        if args.action == "execute-one":
            if not args.request_id: raise RuntimeHold("--request-id is required")
            execute_one(plan, args.request_id, artifacts=args.artifacts, expected_plan_sha256=args.expected_plan_sha256, allow_network=args.allow_network)
        elif args.action == "execute-due": execute_due(plan, artifacts=args.artifacts, expected_plan_sha256=args.expected_plan_sha256, allow_network=args.allow_network)
        print(json.dumps({"status": "PASS", "action": args.action, "network_switch_enabled": args.allow_network and args.action.startswith("execute")}, separators=(",", ":"))); return 0
    except (RuntimeHold, ValidationError, AcquisitionError, OSError, KeyError) as exc:
        print(json.dumps({"status": "HOLD", "action": args.action, "error": str(exc), "network_used": False}, separators=(",", ":")), file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
