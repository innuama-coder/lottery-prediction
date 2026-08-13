from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


TIME_CLASSES = frozenset({
    "retrospective_sequence_safe",
    "external_point_in_time",
    "official_result_label",
})


class TimeContractViolation(ValueError):
    exit_code = 6
    terminal = "FAIL_CAUSALITY_OR_TAMPER"


class MixedTimeClass(TimeContractViolation):
    terminal = "FAIL_TIME_CLASS_MIXED"


def parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TimeContractViolation(f"{label} must be canonical UTC with Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TimeContractViolation(f"{label} is not RFC3339") from exc
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        raise TimeContractViolation(f"{label} is not canonical UTC")
    return parsed


def _issue(value: object, label: str) -> int:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("20") or not value.isdigit():
        raise TimeContractViolation(f"{label} is not a canonical issue identity")
    return int(value)


def require_single_time_class(rows: Iterable[Mapping[str, Any]], expected: str) -> None:
    if expected not in TIME_CLASSES:
        raise MixedTimeClass("unregistered time class")
    observed = {row.get("time_class") for row in rows}
    if observed != {expected}:
        raise MixedTimeClass(f"expected only {expected}, observed {sorted(map(str, observed))}")


def validate_retrospective_sequence_safe(
    rows: Iterable[Mapping[str, Any]], *, target_issue: str
) -> tuple[dict[str, Any], ...]:
    supplied = tuple(dict(row) for row in rows)
    require_single_time_class(supplied, "retrospective_sequence_safe")
    target = _issue(target_issue, "target issue")
    result: list[dict[str, Any]] = []
    for row in supplied:
        required = {"time_class", "source_issue", "numbers"}
        if set(row) != required:
            if "available_at_utc" in row:
                raise TimeContractViolation("historical available_at_utc must not be fabricated")
            raise TimeContractViolation("retrospective feature shape mismatch")
        if _issue(row["source_issue"], "source issue") >= target:
            raise TimeContractViolation("retrospective feature is not strictly earlier than target")
        numbers = row["numbers"]
        if not isinstance(numbers, Mapping) or set(numbers) != {"front", "back"}:
            raise TimeContractViolation("historical numbers shape mismatch")
        result.append(row)
    if not result:
        raise TimeContractViolation("strict historical prefix is empty")
    return tuple(result)


def validate_external_point_in_time(
    rows: Iterable[Mapping[str, Any]], *, prediction_locked_at: str
) -> tuple[dict[str, Any], ...]:
    supplied = tuple(dict(row) for row in rows)
    if not supplied:
        parse_utc(prediction_locked_at, "prediction lock time")
        return ()
    require_single_time_class(supplied, "external_point_in_time")
    locked = parse_utc(prediction_locked_at, "prediction lock time")
    result: list[dict[str, Any]] = []
    for row in supplied:
        required = {
            "time_class", "feature_id", "value", "available_at_utc",
            "availability_evidence_sha256", "availability_evidence_kind",
        }
        if set(row) != required:
            raise TimeContractViolation("external point-in-time feature shape mismatch")
        if row["availability_evidence_kind"] not in {"publisher_timestamp", "signed_fixture_timestamp"}:
            raise TimeContractViolation("external availability evidence is inferred or unregistered")
        digest = row["availability_evidence_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise TimeContractViolation("external availability evidence hash is invalid")
        if parse_utc(row["available_at_utc"], "external available time") >= locked:
            raise TimeContractViolation("external feature was not truly available before prediction lock")
        result.append(row)
    return tuple(result)


def validate_official_result_label_times(
    *, prediction_locked_at: str, result_verified_at: str, label_unlocked_at: str
) -> None:
    locked = parse_utc(prediction_locked_at, "prediction lock time")
    verified = parse_utc(result_verified_at, "result verification time")
    unlocked = parse_utc(label_unlocked_at, "label unlock time")
    if not locked < verified <= unlocked:
        raise TimeContractViolation(
            "official result label requires prediction_locked_at < result_verified_at <= label_unlocked_at"
        )


def require_before_deadline(*, prediction_locked_at: str, hard_deadline_at: str) -> None:
    locked = parse_utc(prediction_locked_at, "prediction lock time")
    deadline = parse_utc(hard_deadline_at, "forecast hard deadline")
    if locked >= deadline:
        raise TimeContractViolation("forecast lock is at or after the hard deadline")
