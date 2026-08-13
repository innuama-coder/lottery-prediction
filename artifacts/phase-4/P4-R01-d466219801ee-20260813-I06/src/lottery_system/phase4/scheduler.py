from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .alerts import build_alert, publish_alert
from .identity import content_id, validate_stable_id
from .ledger import AppendOnlyLedger
from .orchestrator import OrchestrationViolation, _commit_terminal, _validate_terminal, execute_plan
from .serialization import canonical_sha256, load_json
from .storage import IdentityReuseError, resolve_inside, write_once_json


class ScheduleViolation(ValueError):
    exit_code = 5


ACTIONS = {"prepare", "predict_lock", "result_probe_primary", "result_probe_compensation", "unlock_score_research"}
PLAN_FIELDS = {"game","target_issue","action","planned_at_local","timezone","planned_at_utc","schedule_release_id"}
ACTION_TIMES = {
    "prepare": (12, 0), "predict_lock": (17, 0),
    "result_probe_primary": (22, 30), "result_probe_compensation": (8, 30),
}
CALENDAR_CONTRACT_ID = "phase4-calendar-v1"
SCHEDULE_CONTRACT_ID = "phase4-schedule-v1"


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ScheduleViolation("schedule time must be RFC3339 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleViolation("schedule time is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise ScheduleViolation("schedule time must include an offset")
    return parsed.astimezone(timezone.utc)


def plan_key(plan: Mapping[str, Any]) -> list[str]:
    if set(plan) != PLAN_FIELDS:
        raise ScheduleViolation("plan fields are invalid")
    if plan["game"] not in {"ssq", "dlt"} or plan["action"] not in ACTIONS or plan["timezone"] != "Asia/Shanghai":
        raise ScheduleViolation("plan game, action, or timezone is invalid")
    for label in ("target_issue", "schedule_release_id"):
        validate_stable_id(plan[label], label)
    if not plan["target_issue"].isdigit() or len(plan["target_issue"]) != 7 or not plan["target_issue"].startswith("20"):
        raise ScheduleViolation("target issue is not canonical")
    utc = _instant(plan["planned_at_utc"])
    if not isinstance(plan["planned_at_local"], str) or not plan["planned_at_local"]:
        raise ScheduleViolation("planned local time is invalid")
    try:
        local = datetime.fromisoformat(plan["planned_at_local"])
    except ValueError as exc:
        raise ScheduleViolation("planned local time is not ISO-8601") from exc
    if local.tzinfo is None or local.utcoffset() != timedelta(hours=8) or local.astimezone(timezone.utc) != utc:
        raise ScheduleViolation("planned local and UTC instants disagree")
    expected = ACTION_TIMES.get(plan["action"])
    if expected is not None and (local.hour, local.minute, local.second) != (*expected, 0):
        raise ScheduleViolation("planned action time differs from the frozen calendar contract")
    return [plan["game"], plan["target_issue"], plan["action"], plan["planned_at_utc"], plan["schedule_release_id"]]


def validate_schedule(schedule: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(schedule) != {"schema_version","artifact_type","schedule_release_id","calendar_release_id","plans"}:
        raise ScheduleViolation("schedule release shape is invalid")
    if schedule.get("schema_version") != "1.0.0" or schedule.get("artifact_type") != "phase4_schedule_release":
        raise ScheduleViolation("schedule release identity is invalid")
    validate_stable_id(schedule["schedule_release_id"], "schedule release")
    validate_stable_id(schedule["calendar_release_id"], "calendar release")
    plans = schedule["plans"]
    if not isinstance(plans, list) or not plans or any(not isinstance(plan, Mapping) for plan in plans):
        raise ScheduleViolation("schedule plans must be a nonempty array")
    keys = []
    normalized = []
    for raw in plans:
        plan = dict(raw)
        if plan["schedule_release_id"] != schedule["schedule_release_id"]:
            raise ScheduleViolation("plan schedule release mismatch")
        key = plan_key(plan)
        if key in keys:
            raise ScheduleViolation("duplicate plan key")
        keys.append(key)
        normalized.append(plan)
    identity_body = {
        "schema_version":schedule["schema_version"], "artifact_type":schedule["artifact_type"],
        "calendar_release_id":schedule["calendar_release_id"],
        "plans":[{key:value for key, value in plan.items() if key != "schedule_release_id"} for plan in normalized],
    }
    expected = content_id("schedule-release", identity_body)
    if schedule["schedule_release_id"] != expected:
        raise ScheduleViolation("schedule release identity is not content-derived")
    ordered = sorted(normalized, key=lambda row: (_instant(row["planned_at_utc"]), row["game"], row["target_issue"], row["action"]))
    if normalized != ordered:
        raise ScheduleViolation("schedule plans are not in canonical order")
    return ordered


def build_schedule_release(
    calendar_release: Mapping[str, Any], *, schedule_release_id: str, contract_id: str,
) -> dict[str, Any]:
    from .calendar import validate_calendar_release

    if contract_id != SCHEDULE_CONTRACT_ID:
        raise ScheduleViolation("schedule contract identity mismatch")
    calendar = validate_calendar_release(calendar_release, contract_id=CALENDAR_CONTRACT_ID)
    zone = ZoneInfo("Asia/Shanghai")
    plan_bodies: list[dict[str, Any]] = []
    actions = (
        ("prepare", -1, 12, 0),
        ("predict_lock", 0, 17, 0),
        ("result_probe_primary", 0, 22, 30),
        ("result_probe_compensation", 1, 8, 30),
    )
    for entry in calendar["entries"]:
        draw_date = datetime.fromisoformat(entry["draw_business_date"]).date()
        for action, offset, hour, minute in actions:
            local = datetime.combine(draw_date + timedelta(days=offset), datetime.min.time(), tzinfo=zone).replace(hour=hour, minute=minute)
            plan_bodies.append({
                "game":entry["game"], "target_issue":entry["target_issue"], "action":action,
                "planned_at_local":local.isoformat(), "timezone":"Asia/Shanghai",
                "planned_at_utc":local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
    identity_body = {
        "schema_version":"1.0.0", "artifact_type":"phase4_schedule_release",
        "calendar_release_id":calendar["calendar_release_id"],
        "plans":sorted(plan_bodies, key=lambda row: (_instant(row["planned_at_utc"]), row["game"], row["target_issue"], row["action"])),
    }
    expected = content_id("schedule-release", identity_body)
    if schedule_release_id != expected:
        raise ScheduleViolation(f"schedule release identity is not content-derived: expected {expected}")
    release = {
        "schema_version":identity_body["schema_version"], "artifact_type":identity_body["artifact_type"],
        "schedule_release_id":schedule_release_id, "calendar_release_id":identity_body["calendar_release_id"],
        "plans":[{**plan,"schedule_release_id":schedule_release_id} for plan in identity_body["plans"]],
    }
    validate_schedule(release)
    return release


def load_tick_fixture(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if value.get("artifact_type") == "phase4_schedule_release":
        return dict(value), []
    if set(value) != {"schema_version","artifact_type","schedule","behaviors"} or value.get("schema_version") != "1.0.0" or value.get("artifact_type") != "phase4_schedule_tick_fixture":
        raise ScheduleViolation("schedule tick fixture shape is invalid")
    if not isinstance(value["schedule"], Mapping) or not isinstance(value["behaviors"], list):
        raise ScheduleViolation("schedule tick fixture members are invalid")
    behaviors = []
    for row in value["behaviors"]:
        if not isinstance(row, Mapping) or set(row) != {"match","behavior"} or not isinstance(row["match"], Mapping) or not isinstance(row["behavior"], Mapping):
            raise ScheduleViolation("scheduler behavior row is invalid")
        if set(row["match"]) != {"game","target_issue","action"}:
            raise ScheduleViolation("scheduler behavior match is invalid")
        behaviors.append({"match":dict(row["match"]), "behavior":dict(row["behavior"])})
    return dict(value["schedule"]), behaviors


def _behavior_for(plan: Mapping[str, Any], behaviors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matches = [row["behavior"] for row in behaviors if all(plan.get(key) == value for key, value in row["match"].items())]
    if len(matches) > 1:
        raise ScheduleViolation("multiple scheduler behaviors match one plan")
    return dict(matches[0]) if matches else {}


def _terminal_exists(runtime_root: Path, key: Sequence[str]) -> bool:
    plan_id = content_id("plan", {"plan_key": list(key)})
    return resolve_inside(runtime_root, f"scheduler/terminals/{plan_id}.json").is_file()


def _check_rollback(runtime_root: Path, plan: Mapping[str, Any], key: Sequence[str]) -> None:
    if _terminal_exists(runtime_root, key):
        return
    path = resolve_inside(runtime_root, f"scheduler/high-water/{plan['game']}-{plan['action']}.json")
    if not path.is_file():
        return
    prior = load_json(path, reject_floats=True)
    if int(plan["target_issue"]) < int(prior["target_issue"]):
        raise ScheduleViolation("target issue rollback is forbidden")
    if _instant(plan["planned_at_utc"]) < _instant(prior["planned_at_utc"]):
        raise ScheduleViolation("issue or plan time rollback is forbidden")


def _update_high_water(runtime_root: Path, plan: Mapping[str, Any]) -> None:
    path = resolve_inside(runtime_root, f"scheduler/high-water/{plan['game']}-{plan['action']}.json")
    value = {"game":plan["game"],"action":plan["action"],"target_issue":plan["target_issue"],"planned_at_utc":plan["planned_at_utc"]}
    if path.is_file():
        prior = load_json(path, reject_floats=True)
        if _instant(prior["planned_at_utc"]) >= _instant(plan["planned_at_utc"]):
            return
        from .storage import atomic_replace_json
        atomic_replace_json(path, value)
    else:
        write_once_json(path, value)


def _missed_terminal(runtime_root: Path, plan: Mapping[str, Any], key: Sequence[str], *, clock: str, provenance: Mapping[str, Any]) -> dict[str, Any]:
    plan_id = content_id("plan", {"plan_key":list(key)})
    run_id = content_id("run", {"plan_key":list(key),"missed":True})
    path = resolve_inside(runtime_root, f"scheduler/terminals/{plan_id}.json")
    if path.is_file():
        existing = load_json(path, reject_floats=True)
        _validate_terminal(runtime_root, existing, plan_id=plan_id, run_id=run_id, plan_key=key)
        _commit_terminal(runtime_root, AppendOnlyLedger(runtime_root, "schedule-runs"), path, existing, event_type="plan_missed_deadline", provenance=provenance)
        return {**existing,"idempotent":True,"terminal":"skipped_idempotent"}
    alert = build_alert(severity="ERROR", game=plan["game"], object_id=plan_id, reason_code="late_or_missed_deadline", first_seen=clock, last_event_id=run_id, runbook_ref="phase4-deadline")
    publish_alert(runtime_root, alert, provenance=provenance)
    terminal = {"schema_version":"1.0.0","artifact_type":"phase4_plan_terminal","plan_id":plan_id,"run_id":run_id,"plan_key":list(key),"terminal":"missed_deadline","completed_at_utc":clock,"effect_ids":[],"correction_closure_id":None,"alert_ids":[alert["alert_id"]],"idempotent":False}
    _validate_terminal(runtime_root, terminal, plan_id=plan_id, run_id=run_id, plan_key=key)
    _commit_terminal(runtime_root, AppendOnlyLedger(runtime_root, "schedule-runs"), path, terminal, event_type="plan_missed_deadline", provenance=provenance)
    return terminal


def tick_schedule(
    project_root: Path, runtime_root: Path, schedule_or_fixture: Mapping[str, Any], *,
    clock: str, provenance: Mapping[str, Any],
) -> dict[str, Any]:
    schedule, behaviors = load_tick_fixture(schedule_or_fixture)
    plans = validate_schedule(schedule)
    now = _instant(clock)
    schedule_sha = canonical_sha256(schedule)
    results = []
    early_count = 0
    for plan in plans:
        planned = _instant(plan["planned_at_utc"])
        if now < planned:
            early_count += 1
            continue
        key = plan_key(plan)
        _check_rollback(runtime_root, plan, key)
        if plan["action"] == "predict_lock" and now > planned + timedelta(hours=1):
            result = _missed_terminal(runtime_root, plan, key, clock=clock, provenance=provenance)
        else:
            result = execute_plan(
                project_root, runtime_root, plan, plan_key=key, schedule_sha256=schedule_sha,
                clock=clock, provenance=provenance, late=now > planned,
                behavior=_behavior_for(plan, behaviors),
            )
        results.append(result)
        if result["terminal"] not in {"blocked"}:
            _update_high_water(runtime_root, plan)
    counts: dict[str, int] = {}
    for result in results:
        counts[result["terminal"]] = counts.get(result["terminal"], 0) + 1
    return {
        "schema_version":"1.0.0", "artifact_type":"phase4_schedule_tick_receipt",
        "schedule_release_id":schedule["schedule_release_id"], "clock":clock,
        "plan_count":len(plans), "due_count":len(results), "early_count":early_count,
        "terminal_counts":counts, "runs":results,
    }
