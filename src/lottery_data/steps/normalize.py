"""Build verified DrawRecord objects from selected observation evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lottery_data.models import ContractViolation, validate_object
from lottery_data.serialization import make_revision_id


def build_draw_records(
    reconciliation: Sequence[Mapping[str, Any]],
    selected_observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    observations = {item["observation_id"]: dict(item) for item in selected_observations}
    if len(observations) != len(selected_observations):
        raise ContractViolation("bootstrap-transform", "selected observation ids are not unique")
    draws: list[dict[str, Any]] = []
    for decision in reconciliation:
        if decision.get("decision") != "verified":
            raise ContractViolation("bootstrap-transform", "non-verified reconciliation cannot produce DrawRecord")
        selected_ids = decision.get("selected_observation_ids")
        if not isinstance(selected_ids, list) or len(selected_ids) != 2:
            raise ContractViolation("bootstrap-transform", "verified reconciliation requires two selected observations")
        try:
            evidence = [observations[observation_id] for observation_id in selected_ids]
        except KeyError as exc:
            raise ContractViolation("bootstrap-transform", f"selected observation is absent: {exc.args[0]}") from exc
        first, second = evidence
        for field in ("game", "issue_id", "draw_date_local", "front_numbers", "back_numbers", "core_fact_sha256"):
            if first[field] != second[field]:
                raise ContractViolation("bootstrap-transform", f"selected evidence disagrees on {field}")
        supersedes_revision_id = None
        record: dict[str, Any] = {
            "record_schema_version": "1.0.0",
            "game": first["game"],
            "issue_id": first["issue_id"],
            "draw_date_local": first["draw_date_local"],
            "front_numbers": list(first["front_numbers"]),
            "back_numbers": list(first["back_numbers"]),
            "status": "verified",
            "core_fact_profile": "phase0-core-fact-v1",
            "core_fact_sha256": first["core_fact_sha256"],
            "evidence_links": [
                {
                    "source_id": item["source_id"],
                    "publisher_id": item["publisher_id"],
                    "observation_id": item["observation_id"],
                    "raw_ref": item["raw_ref"],
                    "raw_sha256": item["raw_sha256"],
                }
                for item in evidence
            ],
            "revision_id": make_revision_id(
                first["game"], first["issue_id"], first["core_fact_sha256"], supersedes_revision_id,
            ),
            "supersedes_revision_id": supersedes_revision_id,
            "knowledge_class": "retrospective_current_view",
            "available_at_utc": None,
        }
        validate_object("DrawRecord", record)
        draws.append(record)
    draws.sort(key=lambda item: (item["game"], item["issue_id"], item["revision_id"]))
    if len(draws) != 400 or len({(item["game"], item["issue_id"]) for item in draws}) != 400:
        raise ContractViolation("bootstrap-transform", "DrawRecord output must be exactly 400 unique game/issues")
    return draws
