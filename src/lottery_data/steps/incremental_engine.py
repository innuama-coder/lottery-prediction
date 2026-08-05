"""Generic, count-independent incremental release decision core."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lottery_data.models import ContractViolation, validate_object
from lottery_data.serialization import canonical_jsonl_bytes, make_revision_id, sha256_bytes

from .lineage import build_raw_lineage_copy_plan


_OBS_SORT = ("game", "issue_id", "publisher_id", "source_id", "observation_id")
_DRAW_SORT = ("game", "issue_id", "revision_id")
_RECONCILIATION_SORT = ("game", "issue_id")


@dataclass(frozen=True)
class IncrementalDecision:
    draws: tuple[dict[str, Any], ...]
    release_observations: tuple[dict[str, Any], ...]
    run_observations: tuple[dict[str, Any], ...]
    reconciliation: tuple[dict[str, Any], ...]
    changes: Mapping[str, int]
    quality: Mapping[str, Any]
    raw_lineage_copy_plan: tuple[dict[str, Any], ...]
    publishable: bool


def _unique(rows: Sequence[Mapping[str, Any]], fields: tuple[str, ...], label: str) -> list[dict[str, Any]]:
    output = [dict(row) for row in rows]
    keys = [tuple(row.get(field) for field in fields) for row in output]
    if len(keys) != len(set(keys)):
        raise ContractViolation("incremental-engine", f"duplicate {label} identity")
    return output


def _source_publishers(
    policy: Mapping[str, Any], source_identities: Mapping[str, str] | None,
) -> dict[str, str]:
    result = dict(source_identities or {})
    for source in policy.get("sources", []):
        source_id = source.get("source_id")
        publisher_id = source.get("publisher_id")
        if source_id in result and result[source_id] != publisher_id:
            raise ContractViolation("incremental-engine", f"source publisher identity conflict: {source_id}")
        if isinstance(source_id, str) and isinstance(publisher_id, str):
            result[source_id] = publisher_id
    return result


def _validate_pair(
    supplied: Any,
    *,
    game: str,
    issue_id: str | None,
    publishers: Mapping[str, str],
    canonicalize: bool,
) -> list[str]:
    label = f"{game}/{issue_id}" if issue_id is not None else game
    if isinstance(supplied, (str, bytes)):
        raise ContractViolation("incremental-engine", f"{label} pair must contain two source ids")
    try:
        pair = list(supplied)
    except TypeError as exc:
        raise ContractViolation("incremental-engine", f"missing source pair for {label}") from exc
    if len(pair) != 2 or any(not isinstance(source_id, str) for source_id in pair) or len(set(pair)) != 2:
        raise ContractViolation("incremental-engine", f"{label} pair must contain two sources")
    if any(source_id not in publishers for source_id in pair):
        raise ContractViolation("incremental-engine", f"{label} pair has unknown source")
    if len({publishers[source_id] for source_id in pair}) != 2:
        raise ContractViolation("incremental-engine", f"{label} pair publishers are not distinct")
    return sorted(pair) if canonicalize else pair


def _pair(policy: Mapping[str, Any], game: str, publishers: Mapping[str, str]) -> list[str]:
    try:
        value = policy["game_source_pairs"][game]
        supplied = value["source_ids"]
    except (KeyError, TypeError) as exc:
        raise ContractViolation("incremental-engine", f"missing live pair for {game}") from exc
    return _validate_pair(supplied, game=game, issue_id=None, publishers=publishers, canonicalize=False)


def _evidence_link(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: observation[key]
        for key in ("source_id", "publisher_id", "observation_id", "raw_ref", "raw_sha256")
    }


def _draw_from_observation(
    observation: Mapping[str, Any], chosen: Sequence[Mapping[str, Any]], supersedes: str | None,
) -> dict[str, Any]:
    revision_id = make_revision_id(
        observation["game"], observation["issue_id"], observation["core_fact_sha256"], supersedes,
    )
    return {
        "record_schema_version": "1.0.0",
        "game": observation["game"],
        "issue_id": observation["issue_id"],
        "draw_date_local": observation["draw_date_local"],
        "front_numbers": list(observation["front_numbers"]),
        "back_numbers": list(observation["back_numbers"]),
        "status": "verified",
        "core_fact_profile": "phase0-core-fact-v1",
        "core_fact_sha256": observation["core_fact_sha256"],
        "evidence_links": [_evidence_link(item) for item in chosen],
        "revision_id": revision_id,
        "supersedes_revision_id": supersedes,
        "knowledge_class": "prospective_as_observed",
        "available_at_utc": max(item["captured_at_utc"] for item in chosen),
    }


def _gap_keys(keys: set[tuple[str, str]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    grouped: dict[tuple[str, str], set[int]] = defaultdict(set)
    for game, issue_id in keys:
        if len(issue_id) == 7 and issue_id.isdigit():
            grouped[(game, issue_id[:4])].add(int(issue_id[4:]))
    for (game, year), sequences in grouped.items():
        if len(sequences) < 2:
            continue
        for sequence in range(min(sequences), max(sequences) + 1):
            if sequence not in sequences:
                result.add((game, f"{year}{sequence:03d}"))
    return result


def build_incremental_release(
    *,
    current_draws: Sequence[Mapping[str, Any]],
    current_selected_observations: Sequence[Mapping[str, Any]],
    new_observations: Sequence[Mapping[str, Any]],
    new_reconciliation: Sequence[Mapping[str, Any]] = (),
    policy: Mapping[str, Any],
    source_identities: Mapping[str, str] | None = None,
    current_raw_hashes: Mapping[str, str],
    new_raw_hashes: Mapping[str, str],
    recheck_limit: int = 20,
    pair_resolver: Callable[[str, str], Sequence[str]] | None = None,
) -> IncrementalDecision:
    """Build a complete candidate release without mutating any input.

    Existing issues are eligible only inside the newest ``recheck_limit`` per
    game.  Every genuinely new observed issue is eligible.  Calendar-derived
    issues are never invented; only bounded same-year gaps are reported as
    unresolved.
    """
    if isinstance(recheck_limit, bool) or not isinstance(recheck_limit, int) or recheck_limit <= 0:
        raise ContractViolation("incremental-engine", "recheck_limit must be a positive integer")
    current = _unique(current_draws, ("game", "issue_id"), "current draw")
    old_selected = _unique(current_selected_observations, ("observation_id",), "current observation")
    observed = _unique(new_observations, ("observation_id",), "new observation")
    for row in (*current,):
        validate_object("DrawRecord", row)
    for row in (*old_selected, *observed):
        validate_object("SourceObservation", row)

    current_by_key = {(row["game"], row["issue_id"]): row for row in current}
    old_observation_by_id = {row["observation_id"]: row for row in old_selected}
    old_selected_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for draw in current:
        for link in draw["evidence_links"]:
            observation = old_observation_by_id.get(link["observation_id"])
            if observation is None or _evidence_link(observation) != link:
                raise ContractViolation("incremental-engine", f"current evidence link does not close: {draw['game']}/{draw['issue_id']}")
            old_selected_by_key[(draw["game"], draw["issue_id"])].append(observation)
    if any(len(rows) != 2 for rows in old_selected_by_key.values()) or len(old_observation_by_id) != sum(
        len(rows) for rows in old_selected_by_key.values()
    ):
        raise ContractViolation("incremental-engine", "current selected observations are not exactly covered")

    publishers = _source_publishers(policy, source_identities)
    pairs = (
        {game: _pair(policy, game, publishers) for game in sorted({row["game"] for row in (*current, *observed)})}
        if pair_resolver is None else {}
    )
    observed_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in observed:
        if observation["publisher_id"] != publishers.get(observation["source_id"]):
            raise ContractViolation("incremental-engine", f"observation publisher identity mismatch: {observation['observation_id']}")
        observed_by_key[(observation["game"], observation["issue_id"])].append(observation)

    recent_existing: set[tuple[str, str]] = set()
    all_observed_keys = set(current_by_key) | set(observed_by_key)
    for game in sorted({key[0] for key in all_observed_keys}):
        window = sorted((key for key in all_observed_keys if key[0] == game), key=lambda key: key[1])[-recheck_limit:]
        recent_existing.update(key for key in window if key in current_by_key)
    candidate_keys = set(observed_by_key) - set(current_by_key) | (set(observed_by_key) & recent_existing)

    result_by_key = dict(current_by_key)
    selected_by_key = {key: list(value) for key, value in old_selected_by_key.items()}
    reconciliation: list[dict[str, Any]] = []
    stats = {"added": 0, "revised": 0, "unchanged": 0, "conflict": 0, "unresolved": 0}
    recheck_stats = {"recheck_attempted": 0, "recheck_complete": 0, "recheck_deferred": 0}
    blocking: set[str] = set()

    supplied_reconciliation = {
        (row.get("game"), row.get("issue_id")): dict(row) for row in new_reconciliation
    }
    if len(supplied_reconciliation) != len(new_reconciliation):
        raise ContractViolation("incremental-engine", "duplicate supplied reconciliation issue")

    for key in sorted(candidate_keys):
        game, issue_id = key
        rows = observed_by_key[key]
        cores = {row["core_fact_sha256"] for row in rows}
        if pair_resolver is None:
            pair = pairs[game]
        else:
            try:
                supplied_pair = pair_resolver(game, issue_id)
            except ContractViolation:
                raise
            except Exception as exc:
                raise ContractViolation("incremental-engine", f"pair resolver failed for {game}/{issue_id}") from exc
            pair = _validate_pair(
                supplied_pair, game=game, issue_id=issue_id, publishers=publishers, canonicalize=True,
            )
        chosen: list[dict[str, Any]] = []
        for source_id in pair:
            matching = sorted(
                (row for row in rows if row["source_id"] == source_id),
                key=lambda row: (row["captured_at_utc"], row["observation_id"]),
            )
            if matching:
                chosen.append(matching[-1])
        old = current_by_key.get(key)
        pair_complete = len(chosen) == 2
        reason_code: str | None = None
        if old is not None:
            recheck_stats["recheck_attempted"] += 1
            if pair_complete:
                recheck_stats["recheck_complete"] += 1

        if old is not None and not pair_complete:
            if all(row["core_fact_sha256"] == old["core_fact_sha256"] for row in rows):
                decision = "deferred"
                reason_code = "RECHECK_DEFERRED_MISSING_PARTNER"
                recheck_stats["recheck_deferred"] += 1
            else:
                decision = "unresolved"
                reason_code = "RECHECK_UNCONFIRMED_CHANGE"
                stats["unresolved"] += 1
                blocking.add(reason_code)
        elif len(cores) != 1:
            decision = "conflict"
            stats["conflict"] += 1
            blocking.add("PUBLISHER_CORE_FACT_CONFLICT")
        elif not pair_complete:
            decision = "unresolved"
            reason_code = "REQUIRED_SOURCE_PAIR_MISSING"
            stats["unresolved"] += 1
            blocking.add("REQUIRED_LIVE_PAIR_MISSING")
        else:
            decision = "verified"
            core = next(iter(cores))
            if old is None:
                result_by_key[key] = _draw_from_observation(chosen[0], chosen, None)
                selected_by_key[key] = chosen
                stats["added"] += 1
            elif old["core_fact_sha256"] == core:
                # Retain byte-for-byte old draw and its evidence selection.
                stats["unchanged"] += 1
            else:
                result_by_key[key] = _draw_from_observation(chosen[0], chosen, old["revision_id"])
                selected_by_key[key] = chosen
                stats["revised"] += 1
        supplied = supplied_reconciliation.get(key)
        if supplied is not None and supplied.get("decision") not in {None, decision}:
            raise ContractViolation("incremental-engine", f"supplied reconciliation disagrees: {game}/{issue_id}")
        reconciliation.append({
            "game": game,
            "issue_id": issue_id,
            "decision": decision,
            "selected_observation_ids": (
                [row["observation_id"] for row in chosen] if decision == "verified"
                else [row["observation_id"] for row in old_selected_by_key[key]] if decision == "deferred"
                else []
            ),
            "agreeing_observation_ids": sorted(row["observation_id"] for row in rows) if len(cores) == 1 else [],
            "dissenting_observation_ids": (
                sorted(row["observation_id"] for row in rows if old is not None and row["core_fact_sha256"] != old["core_fact_sha256"])
                if reason_code == "RECHECK_UNCONFIRMED_CHANGE"
                else sorted(row["observation_id"] for row in rows) if len(cores) != 1 else []
            ),
            "core_fact_sha256": next(iter(cores)) if len(cores) == 1 else None,
            "reason_code": reason_code,
        })

    combined_observed_keys = set(current_by_key) | set(observed_by_key)
    gaps = _gap_keys(combined_observed_keys)
    if gaps:
        stats["unresolved"] += len(gaps)
        blocking.add("BOUNDED_SAME_YEAR_INTERNAL_GAP")
        for game, issue_id in sorted(gaps):
            reconciliation.append({
                "game": game, "issue_id": issue_id, "decision": "unresolved",
                "selected_observation_ids": [], "agreeing_observation_ids": [],
                "dissenting_observation_ids": [], "core_fact_sha256": None,
            })

    draws = sorted(result_by_key.values(), key=lambda row: (row["game"], row["issue_id"], row["revision_id"]))
    release_observations = sorted(
        (row for rows in selected_by_key.values() for row in rows),
        key=lambda row: tuple(row[field] for field in _OBS_SORT),
    )
    if len(release_observations) != 2 * len(draws):
        raise ContractViolation("incremental-engine", "candidate release requires exactly two observations per draw")
    for draw in draws:
        validate_object("DrawRecord", draw)

    raw_plan = build_raw_lineage_copy_plan(
        # Verify the two provenance sides independently before the lineage
        # planner deduplicates any identical raw_ref/observation identity.
        current_release_observations=old_selected,
        run_observations=observed,
        current_raw_hashes=current_raw_hashes,
        new_raw_hashes=new_raw_hashes,
    )
    output_hashes = {
        "draws": sha256_bytes(canonical_jsonl_bytes(draws, sort_keys=_DRAW_SORT)),
        "release_observations": sha256_bytes(canonical_jsonl_bytes(release_observations, sort_keys=_OBS_SORT)),
        "run_observations": sha256_bytes(canonical_jsonl_bytes(observed, sort_keys=_OBS_SORT)),
        "reconciliation": sha256_bytes(canonical_jsonl_bytes(reconciliation, sort_keys=_RECONCILIATION_SORT)),
    }
    publishable = not blocking
    quality = {
        "decision": "PASS" if publishable else "FAIL",
        "deterministic": {
            "counts": {
                "draws": len(draws),
                "release_observations": len(release_observations),
                "run_observations": len(observed),
                **stats,
                **recheck_stats,
            },
            "output_hashes": dict(sorted(output_hashes.items())),
            "blocking_reason_codes": sorted(blocking),
        },
    }
    return IncrementalDecision(
        draws=tuple(draws), release_observations=tuple(release_observations),
        run_observations=tuple(sorted(observed, key=lambda row: tuple(row[field] for field in _OBS_SORT))),
        reconciliation=tuple(sorted(reconciliation, key=lambda row: (row["game"], row["issue_id"]))),
        changes=dict(stats), quality=quality, raw_lineage_copy_plan=tuple(raw_plan), publishable=publishable,
    )
