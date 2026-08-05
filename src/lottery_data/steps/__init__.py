"""Pure Phase 0 snapshot to Phase 1 bootstrap transformation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lottery_data.models import ContractViolation
from .normalize import build_draw_records
from .live import (
    HostThrottle,
    build_gd_announcement_request,
    fetch_to_raw,
    validate_gd_announcement_result,
    validate_live_request,
)
from .live_policy import LIVE_POLICY_SHA256, LivePolicyError, build_live_request_plan, load_live_policy
from .incremental import DeltaOutsideG2Scope, compare_no_change
from .parse import parse_snapshot_raw
from .quality_gate import build_bootstrap_quality_report
from .reconcile import reconcile_bootstrap
from .replay import (
    ReplayContractError,
    ReplayMutationError,
    ReplayPlan,
    ReplaySession,
    prepare_replay,
    replay_session,
)
from .snapshot import (
    audit_snapshot,
    build_bootstrap_request_plan,
    load_json,
    load_source_catalog,
    materialize_snapshot_request,
    source_index,
)
from .transform import EXPECTED_REPARSED_COUNTS, TransformResult, transform_observations
from .validate import validate_observations
from .verify import RawHashMismatchError, VerifyContractError, verify_release


def transform_bootstrap_snapshot(
    *,
    snapshot_root: Path,
    source_catalog_path: Path,
    run_raw_root: Path | None = None,
    games: Sequence[str] = ("ssq", "dlt"),
    run_id: str = "bootstrap-transform",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    catalog = load_source_catalog(source_catalog_path)
    policy_path = source_catalog_path.with_name("collection-policy.json")
    policy = load_json(policy_path)
    snapshot_audit = audit_snapshot(snapshot_root)
    request_plan = build_bootstrap_request_plan(snapshot_root, games, catalog)
    sources = source_index(catalog)

    parsed: list[dict[str, Any]] = []
    for request in request_plan:
        materialized = materialize_snapshot_request(request, snapshot_root, run_raw_root)
        enriched = dict(request)
        enriched["provenance"] = materialized
        source = sources[request["source_id"]]
        observations = parse_snapshot_raw(
            enriched,
            materialized["raw_path"],
            publisher_id=source["publisher_id"],
        )
        expected_parser_id = source["parser_id"]
        expected_parser_version = source["parser_version"]
        for observation in observations:
            if observation["parser_id"] != expected_parser_id or observation["parser_version"] != expected_parser_version:
                raise ContractViolation(
                    "bootstrap-transform", f"parser identity/catalog mismatch: {request['source_id']}",
                )
        parsed.extend(observations)

    input_hashes = {
        "canonical": snapshot_audit["canonical_sha256"],
        "capture_manifest": snapshot_audit["capture_manifest_sha256"],
        "request_events": snapshot_audit["request_events_sha256"],
    }
    completed_at = generated_at_utc or snapshot_audit["collection_summary"]["completed_at_utc"]
    transformed = transform_observations(
        parsed,
        snapshot_root,
        catalog,
        policy,
        run_id,
        input_hashes,
        completed_at,
    )
    audit = dict(transformed.audit)
    audit.update({
        "canonical_sha256": snapshot_audit["canonical_sha256"],
        "capture_manifest_sha256": snapshot_audit["capture_manifest_sha256"],
        "request_events_sha256": snapshot_audit["request_events_sha256"],
    })
    return {
        "request_plan": request_plan,
        "observations_all": list(transformed.observations_all),
        "observations_selected": list(transformed.observations_selected),
        "reconciliation": list(transformed.reconciliation),
        "draws": list(transformed.draws),
        "quality_report": transformed.quality_report,
        "audit": audit,
    }


__all__ = [
    "EXPECTED_REPARSED_COUNTS",
    "DeltaOutsideG2Scope",
    "RawHashMismatchError",
    "TransformResult",
    "VerifyContractError",
    "build_bootstrap_quality_report",
    "build_bootstrap_request_plan",
    "build_draw_records",
    "HostThrottle",
    "LIVE_POLICY_SHA256",
    "LivePolicyError",
    "build_gd_announcement_request",
    "build_live_request_plan",
    "fetch_to_raw",
    "validate_gd_announcement_result",
    "validate_live_request",
    "load_live_policy",
    "compare_no_change",
    "materialize_snapshot_request",
    "parse_snapshot_raw",
    "reconcile_bootstrap",
    "ReplayContractError",
    "ReplayMutationError",
    "ReplayPlan",
    "ReplaySession",
    "prepare_replay",
    "replay_session",
    "transform_bootstrap_snapshot",
    "transform_observations",
    "validate_observations",
    "verify_release",
]
