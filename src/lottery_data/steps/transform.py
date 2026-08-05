"""Pure deterministic observation-to-release transformation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lottery_data.models import ContractViolation
from lottery_data.serialization import canonical_jsonl_bytes, sha256_bytes

from .normalize import build_draw_records
from .parse import deduplicate_observations
from .quality_gate import build_bootstrap_quality_report
from .reconcile import reconcile_bootstrap
from .snapshot import load_jsonl
from .validate import validate_observations


EXPECTED_REPARSED_COUNTS = {
    "ydniu:ssq": 270,
    "ydniu:dlt": 270,
    "eastmoney:ssq": 250,
    "eastmoney:dlt": 250,
    "gdlottery:dlt": 2,
}


@dataclass(frozen=True)
class TransformResult:
    observations_all: tuple[dict[str, Any], ...]
    observations_selected: tuple[dict[str, Any], ...]
    reconciliation: tuple[dict[str, Any], ...]
    draws: tuple[dict[str, Any], ...]
    quality_report: dict[str, Any]
    audit: dict[str, Any]
    output_hashes: dict[str, str]


def transform_observations(
    observations_all: Sequence[Mapping[str, Any]],
    snapshot_root: Path,
    source_catalog: Mapping[str, Any],
    collection_policy: Mapping[str, Any],
    run_id: str,
    input_hashes: Mapping[str, str],
    generated_at_utc: str,
) -> TransformResult:
    """Build the frozen 1042/800/400 bootstrap view from supplied observations."""
    observations = deduplicate_observations(observations_all)
    validate_observations(observations)
    reparsed_counts = dict(sorted(Counter(
        f"{item['source_id']}:{item['game']}" for item in observations
    ).items()))
    if reparsed_counts != dict(sorted(EXPECTED_REPARSED_COUNTS.items())):
        raise ContractViolation(
            "bootstrap-transform", f"reparsed counts differ from frozen raw: {reparsed_counts}",
        )

    reconciliation, selected = reconcile_bootstrap(
        snapshot_root, observations, source_catalog, collection_policy,
    )
    draws = build_draw_records(reconciliation, selected)
    fallback_count = sum(item["fallback_rule_id"] is not None for item in reconciliation)
    audit = {
        "request_count": len(load_jsonl(snapshot_root / "capture-manifest.jsonl")),
        "parsed_observations": len(observations),
        "reparsed_counts": reparsed_counts,
        "expected_reparsed_counts": dict(sorted(EXPECTED_REPARSED_COUNTS.items())),
        "normal_pair_count": len(reconciliation) - fallback_count,
        "fallback_count": fallback_count,
    }
    output_hashes = {
        "draws": sha256_bytes(canonical_jsonl_bytes(
            draws, sort_keys=("game", "issue_id", "revision_id"),
        )),
        "run_observations": sha256_bytes(canonical_jsonl_bytes(
            observations,
            sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"),
        )),
        "release_observations": sha256_bytes(canonical_jsonl_bytes(
            selected,
            sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"),
        )),
        "reconciliation": sha256_bytes(canonical_jsonl_bytes(
            reconciliation, sort_keys=("game", "issue_id"),
        )),
    }
    quality_report = build_bootstrap_quality_report(
        run_id=run_id,
        draws=draws,
        observations=selected,
        reconciliation=reconciliation,
        audit=audit,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        generated_at_utc=generated_at_utc,
    )
    return TransformResult(
        observations_all=tuple(observations),
        observations_selected=tuple(selected),
        reconciliation=tuple(reconciliation),
        draws=tuple(draws),
        quality_report=quality_report,
        audit=audit,
        output_hashes=output_hashes,
    )
