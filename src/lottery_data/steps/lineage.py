"""Pure raw-evidence lineage validation for incremental releases."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from lottery_data.models import ContractViolation


def build_raw_lineage_copy_plan(
    *,
    current_release_observations: Sequence[Mapping[str, Any]],
    run_observations: Sequence[Mapping[str, Any]],
    current_raw_hashes: Mapping[str, str],
    new_raw_hashes: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Close every selected observation to verified raw bytes.

    ``current_raw_hashes`` and ``new_raw_hashes`` are digests observed by the
    caller from stable reads.  The function deliberately accepts hashes, not
    paths, so planning remains pure and filesystem copying is a later step.
    """
    by_ref: dict[str, str] = {}
    observation_ids: dict[str, list[str]] = defaultdict(list)
    origins: dict[str, set[str]] = defaultdict(set)

    def add(rows: Sequence[Mapping[str, Any]], origin: str, observed: Mapping[str, str]) -> None:
        for supplied in rows:
            observation = dict(supplied)
            raw_ref = observation.get("raw_ref")
            expected = observation.get("raw_sha256")
            observation_id = observation.get("observation_id")
            if not all(isinstance(value, str) and value for value in (raw_ref, expected, observation_id)):
                raise ContractViolation("incremental-lineage", f"{origin} observation has incomplete raw identity")
            # Verification happens before cross-side deduplication.  In
            # particular, an identical observation_id/raw_ref appearing in
            # both inputs must close independently to both stable-read maps.
            if observed.get(raw_ref) != expected:
                raise ContractViolation(
                    "incremental-lineage",
                    f"{origin} raw hash is absent or mismatched: {raw_ref}",
                )
            prior = by_ref.setdefault(raw_ref, expected)
            if prior != expected:
                raise ContractViolation("incremental-lineage", f"raw_ref has conflicting digests: {raw_ref}")
            observation_ids[raw_ref].append(observation_id)
            origins[raw_ref].add(origin)

    add(current_release_observations, "current_release", current_raw_hashes)
    add(run_observations, "current_run", new_raw_hashes)

    plan: list[dict[str, Any]] = []
    for raw_ref in sorted(by_ref):
        origin_set = origins[raw_ref]
        ordered_origins = [origin for origin in ("current_release", "current_run") if origin in origin_set]
        # The plan is deduplicated only after both sides have been verified.
        # A current-release copy remains authoritative when the same bytes are
        # also present in this run.
        origin = "both" if len(ordered_origins) == 2 else ordered_origins[0]
        plan.append({
            "raw_ref": raw_ref,
            "raw_sha256": by_ref[raw_ref],
            "origin": origin,
            "origins": ordered_origins,
            "action": "copy_verified" if "current_release" in origin_set else "retain_verified",
            "observation_ids": sorted(set(observation_ids[raw_ref])),
        })
    return plan
