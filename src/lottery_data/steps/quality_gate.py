"""Deterministic quality report for the bootstrap transform."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import re
from typing import Any

from lottery_data.models import ContractViolation


_REQUIRED_OUTPUT_HASHES = frozenset({
    "draws",
    "run_observations",
    "release_observations",
    "reconciliation",
})
_SHA256_PATTERN = re.compile(r"[0-9A-Fa-f]{64}")


def build_bootstrap_quality_report(
    *,
    run_id: str,
    draws: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    reconciliation: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Any],
    input_hashes: Mapping[str, str],
    output_hashes: Mapping[str, str],
    generated_at_utc: str,
) -> dict[str, Any]:
    parsed_observations = audit.get("parsed_observations")
    if (
        isinstance(parsed_observations, bool)
        or not isinstance(parsed_observations, int)
        or parsed_observations < 0
    ):
        raise ContractViolation(
            "bootstrap-transform", "audit.parsed_observations must be a non-negative integer",
        )
    missing_output_hashes = sorted(_REQUIRED_OUTPUT_HASHES - output_hashes.keys())
    invalid_output_hashes = sorted(
        key
        for key, value in output_hashes.items()
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
    )
    if missing_output_hashes or invalid_output_hashes or "observations" in output_hashes:
        raise ContractViolation(
            "bootstrap-transform",
            "invalid output hashes: "
            f"missing={missing_output_hashes}, invalid={invalid_output_hashes}, "
            f"ambiguous_observations_key={'observations' in output_hashes}",
        )

    draw_games = Counter(item["game"] for item in draws)
    decisions = Counter(item["decision"] for item in reconciliation)
    counts = {
        "draws": len(draws),
        "parsed_observations": parsed_observations,
        "selected_observations": len(observations),
        "ssq": draw_games["ssq"],
        "dlt": draw_games["dlt"],
        "invalid": 0,
        "missing": 0,
        "duplicate": 0,
        "conflict": 0,
        "manual_core_edit": 0,
    }
    checks = [
        {
            "check_id": "capture_request_count",
            "status": "PASS" if audit.get("request_count") == 30 else "FAIL",
            "expected": 30,
            "actual": audit.get("request_count"),
            "evidence_refs": ["capture-manifest.jsonl"],
        },
        {
            "check_id": "draw_count",
            "status": "PASS" if len(draws) == 400 else "FAIL",
            "expected": 400,
            "actual": len(draws),
            "evidence_refs": ["consensus/canonical-records.jsonl"],
        },
        {
            "check_id": "fallback_count",
            "status": "PASS" if audit.get("fallback_count") == 2 else "FAIL",
            "expected": 2,
            "actual": audit.get("fallback_count"),
            "evidence_refs": ["config/phase1/collection-policy.json"],
        },
        {
            "check_id": "normal_pair_count",
            "status": "PASS" if audit.get("normal_pair_count") == 398 else "FAIL",
            "expected": 398,
            "actual": audit.get("normal_pair_count"),
            "evidence_refs": ["config/phase1/collection-policy.json"],
        },
        {
            "check_id": "reparsed_source_counts",
            "status": "PASS" if audit.get("reparsed_counts") == audit.get("expected_reparsed_counts") else "FAIL",
            "expected": audit.get("expected_reparsed_counts"),
            "actual": audit.get("reparsed_counts"),
            "evidence_refs": ["capture-manifest.jsonl", "raw/"],
        },
        {
            "check_id": "selected_observation_count",
            "status": "PASS" if len(observations) == 800 else "FAIL",
            "expected": 800,
            "actual": len(observations),
            "evidence_refs": ["consensus/canonical-records.jsonl"],
        },
        {
            "check_id": "verified_reconciliation_count",
            "status": "PASS" if decisions == Counter({"verified": 400}) else "FAIL",
            "expected": {"verified": 400},
            "actual": dict(sorted(decisions.items())),
            "evidence_refs": ["consensus/canonical-records.jsonl"],
        },
    ]
    checks.sort(key=lambda item: item["check_id"])
    blocking = [item["check_id"] for item in checks if item["status"] != "PASS"]
    if blocking:
        raise ContractViolation("bootstrap-transform", f"bootstrap quality checks failed: {blocking}")
    return {
        "quality_schema_version": "1.0.0",
        "run_id": run_id,
        "decision": "PASS",
        "deterministic": {
            "counts": counts,
            "checks": checks,
            "input_hashes": dict(sorted(input_hashes.items())),
            "output_hashes": dict(sorted(output_hashes.items())),
            "blocking_reason_codes": [],
        },
        "generated_at_utc": generated_at_utc,
    }
