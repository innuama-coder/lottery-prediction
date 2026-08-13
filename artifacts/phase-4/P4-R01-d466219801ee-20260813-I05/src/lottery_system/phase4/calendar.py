from __future__ import annotations

import importlib.metadata
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError

from .identity import content_id, validate_stable_id, verify_content_id
from .serialization import load_json, sha256_file


TIMEZONE = "Asia/Shanghai"
CALENDAR_CONTRACT_ID = "phase4-calendar-v1"
RULE_BY_GAME = {"ssq": "ssq-ns-33c6-16c1-v1", "dlt": "dlt-ns-35c5-12c2-v1"}
ACTION_FACTS = {
    "prepare": ("previous_business_day_12:00", "previous_local_calendar_date", time(12, 0), "04:00:00Z"),
    "predict_lock": ("draw_business_day_17:00", "draw_business_date", time(17, 0), "09:00:00Z"),
    "hard_deadline": ("draw_business_day_18:00", "draw_business_date", time(18, 0), "10:00:00Z"),
    "result_probe_primary": ("draw_business_day_22:30", "draw_business_date", time(22, 30), "14:30:00Z"),
    "result_probe_compensation": ("next_business_day_08:30", "next_local_calendar_date", time(8, 30), "00:30:00Z"),
}


class CalendarAmbiguous(ValueError):
    exit_code = 20
    terminal = "HOLD_CALENDAR_AMBIGUOUS"


def tzdata_identity() -> str:
    relative = Path("Asia/Shanghai")
    for root in TZPATH:
        candidate = Path(root) / relative
        if candidate.is_file():
            return f"zoneinfo:{TIMEZONE}:sha256:{sha256_file(candidate)}:python:{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    try:
        version = importlib.metadata.version("tzdata")
    except importlib.metadata.PackageNotFoundError:
        if sys.platform == "win32":
            return f"iana-asia-shanghai-post-1991-fixed-utc+08:python:{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        raise CalendarAmbiguous("Asia/Shanghai tzdata identity cannot be resolved")
    return f"tzdata-package:{TIMEZONE}:{version}:python:{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _canonical_date(value: object) -> date:
    if not isinstance(value, str):
        raise CalendarAmbiguous("calendar business date must be a string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarAmbiguous("calendar business date is not ISO date") from exc
    if parsed.isoformat() != value:
        raise CalendarAmbiguous("calendar business date is not canonical")
    return parsed


def _local_date(draw_date: date, relation: str) -> date:
    if relation == "previous_local_calendar_date":
        return draw_date - timedelta(days=1)
    if relation == "next_local_calendar_date":
        return draw_date + timedelta(days=1)
    if relation == "draw_business_date":
        return draw_date
    raise CalendarAmbiguous("calendar date relation is not registered")


def _validate_action_mapping(policy: Mapping[str, Any], draw_dates: Iterable[date]) -> None:
    dates = tuple(draw_dates)
    try:
        zone = ZoneInfo(TIMEZONE)
    except ZoneInfoNotFoundError:
        if sys.platform != "win32" or any(item < date(1992, 1, 1) for item in dates):
            raise CalendarAmbiguous("Asia/Shanghai tzdata identity cannot be resolved")
        zone = timezone(timedelta(hours=8), TIMEZONE)
    actions = policy["actions"]
    if set(actions) != {*ACTION_FACTS, "unlock_score_research"}:
        raise CalendarAmbiguous("calendar policy action set mismatch")
    dynamic = actions["unlock_score_research"]
    if dynamic != {"date_relation": "dynamic_after_verified_result", "trigger": "verified_result_revision", "idempotent": True}:
        raise CalendarAmbiguous("dynamic calendar action policy mismatch")
    for action, (expression, relation, wall_time, expected_utc_time) in ACTION_FACTS.items():
        supplied = actions[action]
        required = {"contract_expression", "date_relation", "planned_at_local", "planned_at_utc_time"}
        if action == "hard_deadline":
            required |= {"late_terminal", "late_forecast_lock_allowed"}
        if set(supplied) != required or supplied["contract_expression"] != expression or supplied["date_relation"] != relation:
            raise CalendarAmbiguous(f"calendar action mapping mismatch: {action}")
        if supplied["planned_at_local"] != wall_time.isoformat() + "+08:00" or supplied["planned_at_utc_time"] != expected_utc_time:
            raise CalendarAmbiguous(f"calendar action declares an invalid CST/UTC wall-time pair: {action}")
        if action == "hard_deadline" and (supplied["late_terminal"] != "missed_deadline" or supplied["late_forecast_lock_allowed"] is not False):
            raise CalendarAmbiguous("calendar hard-deadline policy mismatch")
        for draw_date in dates:
            local = datetime.combine(_local_date(draw_date, relation), wall_time, tzinfo=zone)
            observed = local.astimezone(timezone.utc)
            if local.utcoffset() != timedelta(hours=8) or observed.strftime("%H:%M:%SZ") != expected_utc_time:
                raise CalendarAmbiguous(f"zoneinfo conversion disagrees with calendar policy: {action}")


def load_calendar_policy(path: Path, *, draw_dates: Iterable[date] = ()) -> dict[str, Any]:
    policy = load_json(path, reject_floats=True)
    required = {
        "schema_version", "artifact_type", "policy_id", "timezone", "timezone_conversion",
        "entries_are_explicit", "issue_inference_forbidden", "actions",
        "calendar_entry_required_fields", "rule_by_game", "validation_rules", "plan_key", "provenance",
    }
    if set(policy) != required or policy["schema_version"] != "1.0.0" or policy["artifact_type"] != "phase4_calendar_policy":
        raise CalendarAmbiguous("calendar policy shape mismatch")
    if policy["timezone"] != TIMEZONE or policy["entries_are_explicit"] is not True or policy["issue_inference_forbidden"] is not True:
        raise CalendarAmbiguous("calendar policy permits implicit issue or timezone behavior")
    if "zoneinfo.ZoneInfo" not in policy["timezone_conversion"] or policy["rule_by_game"] != RULE_BY_GAME:
        raise CalendarAmbiguous("calendar policy timezone or rule mapping mismatch")
    if policy["calendar_entry_required_fields"] != ["game", "target_issue", "draw_business_date", "rule_id"]:
        raise CalendarAmbiguous("calendar entry field contract mismatch")
    rules = policy["validation_rules"]
    if set(rules) != {
        "source_policy_id", "calendar_entries_must_be_source_reviewed",
        "target_issue_strictly_increasing_within_game", "duplicate_game_issue_forbidden",
        "duplicate_or_conflicting_mapping_terminal", "target_issue_rollback_terminal",
        "official_state_conflict_terminal", "weekday_or_recurrence_issue_guessing_allowed",
        "server_local_timezone_allowed", "utc_offset_hardcoding_as_conversion_allowed",
    } or rules["source_policy_id"] != "p4-source-policy-v1-20260811-i01":
        raise CalendarAmbiguous("calendar validation-rule set or source-policy binding mismatch")
    required_true = {
        "calendar_entries_must_be_source_reviewed", "target_issue_strictly_increasing_within_game",
        "duplicate_game_issue_forbidden",
    }
    if any(rules.get(key) is not True for key in required_true):
        raise CalendarAmbiguous("calendar policy weakens explicit source-reviewed ordering")
    if any(rules.get(key) is not False for key in (
        "weekday_or_recurrence_issue_guessing_allowed", "server_local_timezone_allowed",
        "utc_offset_hardcoding_as_conversion_allowed",
    )):
        raise CalendarAmbiguous("calendar policy permits inferred or server-local conversion")
    if any(rules.get(key) != "HOLD_CALENDAR_AMBIGUOUS" for key in (
        "duplicate_or_conflicting_mapping_terminal", "target_issue_rollback_terminal",
        "official_state_conflict_terminal",
    )):
        raise CalendarAmbiguous("calendar ambiguity terminal mismatch")
    if policy["plan_key"] != ["game", "target_issue", "action", "planned_at_utc", "schedule_release_id"]:
        raise CalendarAmbiguous("calendar plan key mismatch")
    _validate_action_mapping(policy, draw_dates)
    return policy


def canonical_entries(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    previous_by_game: dict[str, int] = {}
    for supplied in entries:
        if set(supplied) != {"game", "target_issue", "draw_business_date", "rule_id"}:
            raise CalendarAmbiguous("calendar entry fields mismatch")
        game = supplied["game"]
        if game not in RULE_BY_GAME or supplied["rule_id"] != RULE_BY_GAME[game]:
            raise CalendarAmbiguous("calendar entry game/rule mismatch")
        issue = validate_stable_id(supplied["target_issue"], "calendar issue identity")
        if not issue.isdigit() or len(issue) != 7 or not issue.startswith("20"):
            raise CalendarAmbiguous("calendar issue identity is not canonical")
        key = (game, issue)
        if key in seen:
            raise CalendarAmbiguous("duplicate calendar game/issue mapping")
        issue_number = int(issue)
        if issue_number <= previous_by_game.get(game, -1):
            raise CalendarAmbiguous("calendar target issue is not strictly increasing within game")
        parsed_date = _canonical_date(supplied["draw_business_date"])
        result.append({
            "game": game,
            "target_issue": issue,
            "draw_business_date": parsed_date.isoformat(),
            "rule_id": supplied["rule_id"],
        })
        seen.add(key)
        previous_by_game[game] = issue_number
    if not result:
        raise CalendarAmbiguous("calendar release must contain explicit entries")
    return result


def build_calendar_release(
    policy_path: Path,
    entries: Iterable[Mapping[str, Any]],
    *,
    calendar_release_id: str,
) -> dict[str, Any]:
    canonical = canonical_entries(entries)
    load_calendar_policy(policy_path, draw_dates=(_canonical_date(row["draw_business_date"]) for row in canonical))
    body = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_calendar_release",
        "timezone": TIMEZONE,
        "entries": canonical,
        "tzdata_identity": tzdata_identity(),
    }
    expected = content_id("calendar-release", body)
    if calendar_release_id != expected:
        raise CalendarAmbiguous(f"calendar release identity is not content-derived: expected {expected}")
    body["calendar_release_id"] = calendar_release_id
    return body


def derive_calendar_release_id(policy_path: Path, entries: Iterable[Mapping[str, Any]]) -> str:
    canonical = canonical_entries(entries)
    load_calendar_policy(policy_path, draw_dates=(_canonical_date(row["draw_business_date"]) for row in canonical))
    body = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_calendar_release",
        "timezone": TIMEZONE,
        "entries": canonical,
        "tzdata_identity": tzdata_identity(),
    }
    return content_id("calendar-release", body)


def validate_calendar_release(release: Mapping[str, Any], *, contract_id: str) -> dict[str, Any]:
    if contract_id != CALENDAR_CONTRACT_ID:
        raise CalendarAmbiguous("calendar contract identity mismatch")
    required = {"schema_version", "artifact_type", "calendar_release_id", "timezone", "entries", "tzdata_identity"}
    if set(release) != required or release["schema_version"] != "1.0.0" or release["artifact_type"] != "phase4_calendar_release":
        raise CalendarAmbiguous("calendar release shape mismatch")
    if release["timezone"] != TIMEZONE or release["tzdata_identity"] != tzdata_identity():
        raise CalendarAmbiguous("calendar release timezone database identity mismatch")
    canonical = canonical_entries(release["entries"])
    if canonical != release["entries"]:
        raise CalendarAmbiguous("calendar release entries are not canonical")
    verify_content_id(
        release["calendar_release_id"], "calendar-release", release,
        excluded_fields=("calendar_release_id",),
    )
    return dict(release)


def load_calendar_build_fixture(project_root: Path, path: Path) -> tuple[Path, list[dict[str, Any]]]:
    fixture = load_json(path, reject_floats=True)
    required = {
        "schema_version", "artifact_type", "calendar_policy_path", "calendar_policy_sha256",
        "source_review_receipt_path", "source_review_receipt_sha256", "entries",
    }
    if set(fixture) != required or fixture["schema_version"] != "1.0.0" or fixture["artifact_type"] != "phase4_calendar_build_fixture":
        raise CalendarAmbiguous("calendar build input must be an explicit fixture")
    root = project_root.resolve()
    policy_path = (root / fixture["calendar_policy_path"]).resolve()
    review_path = (root / fixture["source_review_receipt_path"]).resolve()
    policy_path.relative_to(root)
    review_path.relative_to(root)
    if sha256_file(policy_path) != fixture["calendar_policy_sha256"] or sha256_file(review_path) != fixture["source_review_receipt_sha256"]:
        raise CalendarAmbiguous("calendar fixture policy or source-review receipt hash mismatch")
    review = load_json(review_path, reject_floats=True)
    if review.get("status") != "PASS":
        raise CalendarAmbiguous("calendar entries are not bound to a PASS source review")
    return policy_path, list(fixture["entries"])
