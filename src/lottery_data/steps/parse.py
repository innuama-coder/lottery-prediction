"""Turn saved raw pages into Phase 1 SourceObservation objects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lottery_data.models import ContractViolation
from lottery_data.parsers import get_parser, get_versioned_parser
from lottery_data.serialization import core_fact_sha256, make_observation_id


def _facts_to_observations(
    facts: Sequence[Mapping[str, Any]], request: Mapping[str, Any], *,
    publisher_id: str, parser_id: str, parser_version: str,
) -> list[dict[str, Any]]:
    source_id = str(request["source_id"])
    game = str(request["game"])
    provenance = request.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ContractViolation("bootstrap-transform", f"missing materialized provenance: {request.get('request_id')}")
    observations: list[dict[str, Any]] = []
    for fact in facts:
        observation: dict[str, Any] = {
            "observation_schema_version": "1.0.0",
            "observation_id": "",
            "source_id": source_id,
            "publisher_id": publisher_id,
            "game": game,
            "raw_issue_id": fact["raw_issue_id"],
            "issue_id": fact["issue_id"],
            "draw_date_local": fact["draw_date_local"],
            "front_numbers": fact["front_numbers"],
            "back_numbers": fact["back_numbers"],
            "source_url": provenance["url"],
            "captured_at_utc": provenance["captured_at_utc"],
            "raw_ref": provenance["raw_ref"],
            "raw_sha256": provenance["raw_sha256"],
            "parser_id": parser_id,
            "parser_version": parser_version,
            "core_fact_profile": "phase0-core-fact-v1",
            "core_fact_sha256": "",
            "parse_status": "parsed",
        }
        observation["core_fact_sha256"] = core_fact_sha256(observation)
        observation["observation_id"] = make_observation_id(
            source_id,
            game,
            observation["issue_id"],
            observation["raw_sha256"],
            parser_version,
        )
        observations.append(observation)
    return observations


def parse_snapshot_raw(
    request: Mapping[str, Any], raw_path: Path, *, publisher_id: str,
) -> list[dict[str, Any]]:
    source_id = str(request["source_id"])
    game = str(request["game"])
    try:
        facts = get_parser(source_id)(raw_path.read_bytes(), game)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContractViolation(
            "bootstrap-transform", f"parser failed for {request.get('request_id')}: {exc}",
        ) from exc
    return _facts_to_observations(
        facts, request, publisher_id=publisher_id,
        parser_id=f"phase1-{source_id}-parser", parser_version="1.0.0",
    )


def parse_versioned_raw(
    request: Mapping[str, Any], raw_path: Path, *, publisher_id: str,
    parser_id: str, parser_version: str,
) -> list[dict[str, Any]]:
    """Parse already-authorized raw using the exact parser identity supplied by preflight."""
    if any(not isinstance(value, str) or not value for value in (publisher_id, parser_id, parser_version)):
        raise ContractViolation("incremental-transform", "versioned parser identity is incomplete")
    try:
        facts = get_versioned_parser(parser_id, parser_version)(raw_path.read_bytes(), str(request["game"]))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContractViolation(
            "incremental-transform", f"parser failed for {request.get('request_id')}: {exc}",
        ) from exc
    return _facts_to_observations(
        facts, request, publisher_id=publisher_id, parser_id=parser_id, parser_version=parser_version,
    )


def deduplicate_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for supplied in observations:
        observation = dict(supplied)
        key = (observation["source_id"], observation["game"], observation["issue_id"])
        previous = by_key.get(key)
        if previous is not None and previous["core_fact_sha256"] != observation["core_fact_sha256"]:
            raise ContractViolation("bootstrap-transform", f"conflicting duplicate observation: {key}")
        by_key.setdefault(key, observation)
    return sorted(
        by_key.values(),
        key=lambda item: (item["source_id"], item["game"], item["draw_date_local"], item["issue_id"]),
    )
