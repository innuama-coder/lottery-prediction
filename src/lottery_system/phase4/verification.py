from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping

from .identity import content_id, validate_stable_id, verify_content_id


SOURCE_PAIRS = {
    "ssq": ("swlc", "ydniu"),
    "dlt": ("gdlottery", "ydniu"),
}
RULE_IDS = {
    "ssq": "ssq-ns-33c6-16c1-v1",
    "dlt": "dlt-ns-35c5-12c2-v1",
}


class SourceVerificationError(ValueError):
    exit_code = 20
    terminal = "HOLD_SOURCE_CONFLICT"


def _validate_numbers(game: str, numbers: Mapping[str, Any]) -> dict[str, list[int]]:
    if set(numbers) != {"front", "back"}:
        raise SourceVerificationError("draw numbers must contain exactly front and back")
    expected = (6, 1, 33, 16) if game == "ssq" else (5, 2, 35, 12)
    front_count, back_count, front_max, back_max = expected
    front, back = numbers["front"], numbers["back"]
    if not isinstance(front, list) or not isinstance(back, list) or any(type(item) is not int for item in [*front, *back]):
        raise SourceVerificationError("draw numbers must be integer arrays")
    if len(front) != front_count or len(back) != back_count:
        raise SourceVerificationError("draw number counts do not match the game rule")
    if front != sorted(set(front)) or back != sorted(set(back)):
        raise SourceVerificationError("draw numbers must be unique and strictly increasing")
    if not all(1 <= item <= front_max for item in front) or not all(1 <= item <= back_max for item in back):
        raise SourceVerificationError("draw number is outside the game rule")
    return {"front": list(front), "back": list(back)}


def normalize_issue(game: str, supplied: object) -> str:
    if not isinstance(supplied, str):
        raise SourceVerificationError("issue identity must be a string")
    value = supplied.strip()
    if game == "dlt" and re.fullmatch(r"\d{5}", value):
        value = "20" + value
    if not re.fullmatch(r"20\d{5}", value):
        raise SourceVerificationError(f"invalid {game} issue identity")
    return value


def normalized_fact(
    *,
    source_id: str,
    game: str,
    observation_id: str,
    issue_id: object,
    draw_business_date: object,
    front_numbers: list[int],
    back_numbers: list[int],
) -> dict[str, Any]:
    if game not in SOURCE_PAIRS or source_id not in SOURCE_PAIRS[game]:
        raise SourceVerificationError("source/game pair is not registered")
    validate_stable_id(observation_id, "observation identity")
    issue = normalize_issue(game, issue_id)
    if not isinstance(draw_business_date, str):
        raise SourceVerificationError("draw business date must be a string")
    try:
        parsed_date = date.fromisoformat(draw_business_date)
    except ValueError as exc:
        raise SourceVerificationError("draw business date is not canonical ISO date") from exc
    if parsed_date.isoformat() != draw_business_date:
        raise SourceVerificationError("draw business date is not canonical ISO date")
    numbers = _validate_numbers(game, {"front": front_numbers, "back": back_numbers})
    body = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_parsed_source_fact",
        "source_id": source_id,
        "game": game,
        "issue_id": issue,
        "draw_business_date": draw_business_date,
        "numbers": numbers,
        "observation_id": observation_id,
    }
    body["parsed_fact_id"] = content_id("parsed-fact", body)
    return body


def deduplicate_facts(facts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for supplied in facts:
        fact = dict(supplied)
        required = {
            "schema_version", "artifact_type", "parsed_fact_id", "source_id", "game",
            "issue_id", "draw_business_date", "numbers", "observation_id",
        }
        if set(fact) != required or fact["schema_version"] != "1.0.0" or fact["artifact_type"] != "phase4_parsed_source_fact":
            raise SourceVerificationError("parsed source fact shape mismatch")
        verify_content_id(fact["parsed_fact_id"], "parsed-fact", fact, excluded_fields=("parsed_fact_id",))
        canonical = normalized_fact(
            source_id=fact["source_id"], game=fact["game"], observation_id=fact["observation_id"],
            issue_id=fact["issue_id"], draw_business_date=fact["draw_business_date"],
            front_numbers=fact["numbers"]["front"], back_numbers=fact["numbers"]["back"],
        )
        if canonical != fact:
            raise SourceVerificationError("parsed source fact is not canonical")
        key = (fact["source_id"], fact["game"], fact["issue_id"])
        previous = by_key.get(key)
        if previous is not None and previous != fact:
            raise SourceVerificationError("conflicting duplicate source issue")
        by_key[key] = fact
    return [by_key[key] for key in sorted(by_key)]


def verify_result_revision(
    primary: Mapping[str, Any],
    corroborating: Mapping[str, Any],
    *,
    verified_at_utc: str,
    supersedes_revision_id: str | None = None,
) -> dict[str, Any]:
    first, second = deduplicate_facts([primary, corroborating])
    if first["game"] != second["game"]:
        raise SourceVerificationError("source facts are for different games")
    game = first["game"]
    expected_primary, expected_corroborating = SOURCE_PAIRS[game]
    by_source = {first["source_id"]: first, second["source_id"]: second}
    if set(by_source) != {expected_primary, expected_corroborating}:
        raise SourceVerificationError("verified result requires the registered independent source pair")
    primary_fact, corroborating_fact = by_source[expected_primary], by_source[expected_corroborating]
    core_fields = ("game", "issue_id", "draw_business_date", "numbers")
    if any(primary_fact[field] != corroborating_fact[field] for field in core_fields):
        raise SourceVerificationError("primary and corroborating source facts conflict")
    if supersedes_revision_id is not None:
        validate_stable_id(supersedes_revision_id, "superseded result revision identity")
    body = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_result_revision",
        "game": game,
        "issue_id": primary_fact["issue_id"],
        "draw_business_date": primary_fact["draw_business_date"],
        "numbers": primary_fact["numbers"],
        "primary_observation_id": primary_fact["observation_id"],
        "corroborating_observation_id": corroborating_fact["observation_id"],
        "verified_at_utc": verified_at_utc,
        "supersedes_revision_id": supersedes_revision_id,
    }
    body["result_revision_id"] = content_id("result-revision", body)
    return body


def verify_revision_successor(previous: Mapping[str, Any], successor: Mapping[str, Any]) -> None:
    for row in (previous, successor):
        verify_content_id(row["result_revision_id"], "result-revision", row, excluded_fields=("result_revision_id",))
    if successor["supersedes_revision_id"] != previous["result_revision_id"]:
        raise SourceVerificationError("result revision does not supersede the direct predecessor")
    if any(successor[field] != previous[field] for field in ("game", "issue_id")):
        raise SourceVerificationError("result revision changes its game or issue")
    if successor["numbers"] == previous["numbers"] and successor["draw_business_date"] == previous["draw_business_date"]:
        raise SourceVerificationError("result revision contains no corrected core fact")
