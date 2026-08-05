"""Select the frozen two-publisher evidence set using canonical only as oracle."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lottery_data.models import ContractViolation

from .snapshot import load_json, load_jsonl, source_index


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_POLICY = _REPO_ROOT / "config" / "phase1" / "collection-policy.json"


def _policy_index(policy: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for rule in policy.get("approved_issue_fallbacks", []):
        key = (str(rule["game"]), str(rule["issue_id"]))
        if key in result:
            raise ContractViolation("bootstrap-transform", f"duplicate fallback policy: {key}")
        result[key] = dict(rule)
    return result


def reconcile_bootstrap(
    snapshot_root: Path,
    observations: Sequence[Mapping[str, Any]],
    source_catalog: Mapping[str, Any],
    collection_policy: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    policy = dict(collection_policy) if collection_policy is not None else load_json(_DEFAULT_POLICY)
    normal_pair = list(policy.get("normal_source_pair", []))
    if normal_pair != ["ydniu", "eastmoney"]:
        raise ContractViolation("bootstrap-transform", "normal source pair is not frozen ydniu+eastmoney")
    fallbacks = _policy_index(policy)
    catalog = source_index(source_catalog)
    canonical_rows = load_jsonl(snapshot_root / "consensus" / "canonical-records.jsonl")
    if len(canonical_rows) != 400:
        raise ContractViolation("bootstrap-transform", f"canonical oracle must contain 400 rows, found {len(canonical_rows)}")

    by_exact: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    by_issue: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for supplied in observations:
        observation = dict(supplied)
        exact = (
            observation["game"], observation["issue_id"], observation["source_id"], observation["raw_ref"],
        )
        if exact in by_exact:
            raise ContractViolation("bootstrap-transform", f"observation mapping is not unique: {exact}")
        by_exact[exact] = observation
        by_issue.setdefault((observation["game"], observation["issue_id"]), []).append(observation)

    selected: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    normal_count = 0
    fallback_count = 0
    seen_issues: set[tuple[str, str]] = set()
    for canonical in canonical_rows:
        game = canonical.get("game")
        issue_id = canonical.get("issue_id")
        key = (game, issue_id)
        if key in seen_issues:
            raise ContractViolation("bootstrap-transform", f"duplicate canonical issue: {key}")
        seen_issues.add(key)
        fallback = fallbacks.get(key)
        expected_pair = list(fallback["source_pair"]) if fallback else normal_pair
        source_ids = canonical.get("source_ids")
        evidence_refs = canonical.get("evidence_refs")
        if source_ids != expected_pair or not isinstance(evidence_refs, list) or len(evidence_refs) != 2:
            raise ContractViolation(
                "bootstrap-transform", f"canonical evidence selection violates Phase 1 policy: {game}/{issue_id}",
            )
        if len({catalog[source_id]["publisher_id"] for source_id in expected_pair}) != 2:
            raise ContractViolation("bootstrap-transform", f"selected publishers are not distinct: {game}/{issue_id}")

        chosen: list[dict[str, Any]] = []
        for source_id, raw_ref in zip(source_ids, evidence_refs, strict=True):
            observation = by_exact.get((game, issue_id, source_id, raw_ref))
            if observation is None:
                raise ContractViolation(
                    "bootstrap-transform", f"missing selected observation: {game}/{issue_id}/{source_id}/{raw_ref}",
                )
            if observation["core_fact_sha256"] != canonical.get("core_fact_sha256"):
                raise ContractViolation(
                    "bootstrap-transform", f"canonical/observation core fact conflict: {game}/{issue_id}/{source_id}",
                )
            for phase0_name, phase1_name in (
                ("draw_date", "draw_date_local"),
                ("front_numbers", "front_numbers"),
                ("back_numbers", "back_numbers"),
            ):
                if canonical.get(phase0_name) != observation.get(phase1_name):
                    raise ContractViolation(
                        "bootstrap-transform", f"canonical/observation field conflict: {game}/{issue_id}/{phase1_name}",
                    )
            chosen.append(observation)

        observed = by_issue.get(key, [])
        dissenting = [
            item for item in observed if item["core_fact_sha256"] != canonical["core_fact_sha256"]
        ]
        if dissenting:
            raise ContractViolation("bootstrap-transform", f"dissenting publication blocks issue: {game}/{issue_id}")
        agreeing_ids = sorted({item["observation_id"] for item in observed})
        selected_ids = [item["observation_id"] for item in chosen]
        missing_source_ids: list[str] = []
        reason_codes = ["TWO_PUBLISHER_CORE_FACT_AGREEMENT"]
        fallback_rule_id: str | None = None
        if fallback:
            fallback_count += 1
            fallback_rule_id = fallback["rule_id"]
            missing_source = fallback["missing_source_id"]
            if any(item["source_id"] == missing_source for item in observed):
                raise ContractViolation(
                    "bootstrap-transform", f"approved fallback source is unexpectedly present: {game}/{issue_id}",
                )
            missing_source_ids = [missing_source]
            reason_codes.append("APPROVED_ISSUE_FALLBACK")
        else:
            normal_count += 1

        reconciliation.append({
            "reconciliation_schema_version": "1.0.0",
            "game": game,
            "issue_id": issue_id,
            "decision": "verified",
            "core_fact_sha256": canonical["core_fact_sha256"],
            "selected_observation_ids": selected_ids,
            "agreeing_observation_ids": agreeing_ids,
            "missing_source_ids": missing_source_ids,
            "dissenting_observation_ids": [],
            "fallback_rule_id": fallback_rule_id,
            "reason_codes": reason_codes,
        })
        selected.extend(chosen)

    if normal_count != 398 or fallback_count != 2:
        raise ContractViolation(
            "bootstrap-transform", f"source-pair counts must be normal=398/fallback=2, got {normal_count}/{fallback_count}",
        )
    if len(selected) != 800 or len({item["observation_id"] for item in selected}) != 800:
        raise ContractViolation("bootstrap-transform", "selected release observations must be exactly 800 unique rows")
    game_counts = {game: sum(1 for item in canonical_rows if item["game"] == game) for game in ("ssq", "dlt")}
    if game_counts != {"ssq": 200, "dlt": 200}:
        raise ContractViolation("bootstrap-transform", f"canonical game counts are not frozen 200/200: {game_counts}")

    reconciliation.sort(key=lambda item: (item["game"], item["issue_id"]))
    selected.sort(
        key=lambda item: (
            item["game"], item["issue_id"], item["publisher_id"], item["source_id"], item["observation_id"],
        ),
    )
    return reconciliation, selected
