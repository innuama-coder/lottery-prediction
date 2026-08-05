"""Strict parser for the Guangdong Lottery ``gameNumber.json`` history."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any


_MAX_TOP_LEVEL_RECORDS = 20_000
_LATEST_DLT_RECORDS = 20
_DLT_GAME_ID = "085"
_KEY = re.compile(r"^(?P<game_id>[0-9]{3})_(?P<draw_id>[0-9]{5})$")
_DLT_NUMBERS = re.compile(
    r"^(?P<f1>[0-9]{2})\+(?P<f2>[0-9]{2})\+(?P<f3>[0-9]{2})\+"
    r"(?P<f4>[0-9]{2})\+(?P<f5>[0-9]{2}) "
    r"(?P<b1>[0-9]{2})\+(?P<b2>[0-9]{2})$"
)
_REQUIRED_FIELDS = frozenset({"id", "gameId", "drawId", "createTime", "kjhm"})
_OPTIONAL_FIELDS = frozenset({"cashTime", "drawNumber"})
_ALLOWED_FIELD_SETS = frozenset(
    frozenset(_REQUIRED_FIELDS | optional)
    for optional in (frozenset(), {"cashTime"}, {"drawNumber"}, _OPTIONAL_FIELDS)
)


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_strict_json(body: bytes) -> Any:
    try:
        text = body.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, RecursionError, MemoryError) as exc:
        raise ValueError("gdlottery history JSON could not be decoded safely") from exc
    except ValueError:
        raise


def _iso_date(value: Any, field: str, identity: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{identity} {field} must be a string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{identity} {field} is not an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{identity} {field} is not a canonical ISO date")
    return value


def _validate_record(identity: str, supplied: Any) -> dict[str, Any]:
    match = _KEY.fullmatch(identity)
    if match is None:
        raise ValueError(f"invalid gdlottery history identity: {identity!r}")
    if type(supplied) is not dict:
        raise ValueError(f"{identity} record must be an object")
    record = supplied
    if frozenset(record) not in _ALLOWED_FIELD_SETS:
        missing = sorted(_REQUIRED_FIELDS - set(record))
        extra = sorted(set(record) - (_REQUIRED_FIELDS | _OPTIONAL_FIELDS))
        raise ValueError(f"{identity} record fields differ: missing={missing}, extra={extra}")
    for field in ("gameId", "drawId", "kjhm"):
        if type(record[field]) is not str or not record[field]:
            raise ValueError(f"{identity} {field} must be a non-empty string")
    if type(record["id"]) is not int or record["id"] <= 0:
        raise ValueError(f"{identity} id must be a positive integer")
    if "drawNumber" in record and (
        type(record["drawNumber"]) is not int or record["drawNumber"] <= 0
    ):
        raise ValueError(f"{identity} drawNumber must be a positive integer")
    if record["gameId"] != match.group("game_id") or record["drawId"] != match.group("draw_id"):
        raise ValueError(f"{identity} key and record identity disagree")
    _iso_date(record["createTime"], "createTime", identity)
    if "cashTime" in record:
        _iso_date(record["cashTime"], "cashTime", identity)
    return record


def _dlt_numbers(identity: str, value: str) -> tuple[list[int], list[int]]:
    match = _DLT_NUMBERS.fullmatch(value)
    if match is None:
        raise ValueError(f"{identity} kjhm is not canonical DLT 5+2")
    front = [int(match.group(f"f{index}")) for index in range(1, 6)]
    back = [int(match.group(f"b{index}")) for index in range(1, 3)]
    if front != sorted(front) or len(set(front)) != 5 or not all(1 <= value <= 35 for value in front):
        raise ValueError(f"{identity} has invalid DLT front numbers")
    if back != sorted(back) or len(set(back)) != 2 or not all(1 <= value <= 12 for value in back):
        raise ValueError(f"{identity} has invalid DLT back numbers")
    return front, back


def parse(body: bytes, game: str) -> list[dict[str, Any]]:
    """Return the latest 20 DLT facts in deterministic ascending issue order."""
    if game != "dlt":
        raise ValueError("gdlottery history parser supports only dlt")
    root = _load_strict_json(body)
    if type(root) is not dict:
        raise ValueError("gdlottery history root must be an object")
    if not root or len(root) > _MAX_TOP_LEVEL_RECORDS:
        raise ValueError("gdlottery history root count is outside 1..20000")

    candidates: list[dict[str, Any]] = []
    normalized_issues: set[str] = set()
    for identity, supplied in root.items():
        identity_match = _KEY.fullmatch(identity)
        if identity_match is None:
            raise ValueError(f"invalid gdlottery history identity: {identity!r}")
        if identity_match.group("game_id") != _DLT_GAME_ID:
            continue
        record = _validate_record(identity, supplied)
        front, back = _dlt_numbers(identity, record["kjhm"])
        issue_id = "20" + record["drawId"]
        if issue_id in normalized_issues:
            raise ValueError(f"duplicate normalized DLT issue: {issue_id}")
        normalized_issues.add(issue_id)
        candidates.append({
            "raw_issue_id": record["drawId"],
            "issue_id": issue_id,
            "draw_date_local": record["createTime"],
            "front_numbers": front,
            "back_numbers": back,
        })

    if len(candidates) < _LATEST_DLT_RECORDS:
        raise ValueError("gdlottery history contains fewer than 20 DLT issues")
    latest = sorted(candidates, key=lambda item: int(item["issue_id"]), reverse=True)[:_LATEST_DLT_RECORDS]
    return sorted(latest, key=lambda item: int(item["issue_id"]))
