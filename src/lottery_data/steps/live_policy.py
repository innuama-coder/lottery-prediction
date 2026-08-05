"""Fail-closed loading and request planning for the frozen Phase 1 live policy."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


LIVE_POLICY_SHA256 = "23b7fc1bd1d5d7518b345ee92dd8fd7a3172305b7478fe82175d7af38aa80a1b"
LIVE_POLICY_V13_SHA256 = "9f3f7c91e73632511a588122cc6e2f7e25a1190aecef4e455c6099a28d7d53a0"

_EXPECTED_RECHECK_POLICY = {
    "recheck_policy_schema_version": "1.1.0",
    "window_per_game_latest_issues": 20,
    "new_issue_target_rule": "Any newly observed issue is a target; a missing required source pair is unresolved and rejects publication.",
    "existing_complete_pair_rule": "Inside the latest-20 per-game window, a complete required pair follows normal unchanged, revised, or conflict handling; any extra dissent blocks publication.",
    "deferred_rule": "For an existing target with an incomplete required pair, if every available observation equals the current draw core, retain the old draw and evidence, record RECHECK_DEFERRED_MISSING_PARTNER, do not count unresolved, do not block, and do not claim the recheck complete.",
    "unconfirmed_change_rule": "For an existing target with an incomplete required pair, any available observation whose core differs from the current draw is RECHECK_UNCONFIRMED_CHANGE, unresolved, and rejects publication.",
    "quality_counters": ["recheck_attempted", "recheck_complete", "recheck_deferred"],
    "source_expansion_approved": False,
}


class LivePolicyError(ValueError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        stage: str = "preflight",
        exit_code: int = 4,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.exit_code = exit_code
        self.retryable = retryable


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", f"{label} keys differ from frozen policy")


def _https_url(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", f"{label} must be a URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", f"unsafe {label}") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or port is not None:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", f"unsafe {label}")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", f"unsafe {label}")
    return value


def load_live_policy(path: Path, *, today: date | None = None) -> dict[str, Any]:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise LivePolicyError("expired_or_changed_policy", "live policy is unavailable") from exc
    digest = hashlib.sha256(body).hexdigest()
    version_by_digest = {
        LIVE_POLICY_SHA256: "1.2.0",
        LIVE_POLICY_V13_SHA256: "1.3.0",
    }
    expected_version = version_by_digest.get(digest)
    if expected_version is None:
        raise LivePolicyError("expired_or_changed_policy", "live policy SHA-256 differs from the frozen contract")
    try:
        policy = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LivePolicyError("expired_or_changed_policy", "live policy is not valid UTF-8 JSON") from exc
    if not isinstance(policy, dict):
        raise LivePolicyError("expired_or_changed_policy", "live policy must be an object")
    if policy.get("live_policy_schema_version") != expected_version:
        raise LivePolicyError("expired_or_changed_policy", "live policy version differs from its frozen digest")
    _validate_policy(policy, today=today or date.today())
    return policy


def _validate_policy(policy: Mapping[str, Any], *, today: date) -> None:
    _exact_keys(policy, {
        "live_policy_schema_version", "reviewed_at", "valid_until", "review_expiry_rule", "scope",
        "production_collection_approved", "redistribution_approved", "baseline_separation", "network_policy",
        "failure_classification", "game_source_pairs", "recheck_policy", "sources",
    }, "live policy")
    profile_version = policy["live_policy_schema_version"]
    if profile_version not in {"1.2.0", "1.3.0"} or policy["production_collection_approved"] is not False or policy["redistribution_approved"] is not False:
        raise LivePolicyError("expired_or_changed_policy", "live policy is not internal-only and fail-closed")
    try:
        valid_until = date.fromisoformat(str(policy["valid_until"]))
    except ValueError as exc:
        raise LivePolicyError("expired_or_changed_policy", "invalid live review expiry") from exc
    if today > valid_until:
        raise LivePolicyError("expired_or_changed_policy", "live source review has expired")
    recheck = policy["recheck_policy"]
    if not isinstance(recheck, dict):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "recheck policy must be an object")
    _exact_keys(recheck, set(_EXPECTED_RECHECK_POLICY), "recheck policy")
    if recheck != _EXPECTED_RECHECK_POLICY:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "recheck policy changed")
    network = policy["network_policy"]
    if not isinstance(network, dict):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "network policy must be an object")
    expected_network = {
        "method_allowlist": ["GET"], "https_only": True, "authentication_allowed": False,
        "cookies_allowed": False, "cross_process_same_host_min_interval_seconds": 2.0,
        "request_timeout_seconds": 30, "max_attempts_per_request": 1, "max_response_bytes": 1048576,
        "redirect_policy": "same_origin_only", "max_redirects": 3, "persist_raw_before_parse": True,
        "cache_may_satisfy_current_run": False,
        "request_plan_rule": "Only the exact static endpoints declared in this file may enter the current live request plan. A calendar, expected issue number, timestamp cache-buster, discovered child, or guessed future URL is never a request source.",
    }
    if profile_version == "1.3.0":
        expected_network.update({
            "max_attempts_per_request": 2,
            "retry_backoff_seconds": 2,
            "retryable_error_categories": ["dns_timeout_tls_or_required_source_unavailable"],
        })
    _exact_keys(network, set(expected_network), "network policy")
    for key, expected in expected_network.items():
        if network.get(key) != expected:
            raise LivePolicyError("endpoint_or_configuration_policy_violation", f"network policy changed: {key}")
    if profile_version == "1.3.0":
        classification = policy["failure_classification"]
        preflight = classification.get("preflight") if isinstance(classification, dict) else None
        runtime = classification.get("runtime") if isinstance(classification, dict) else None
        if not isinstance(preflight, dict) or not isinstance(runtime, dict):
            raise LivePolicyError("endpoint_or_configuration_policy_violation", "retry failure classification is malformed")
        retryable_category = "dns_timeout_tls_or_required_source_unavailable"
        categories = {
            "expired_or_changed_policy": preflight.get("expired_or_changed_policy"),
            "endpoint_or_configuration_policy_violation": preflight.get("endpoint_or_configuration_policy_violation"),
            "redirect_policy_violation": runtime.get("redirect_policy_violation"),
            "authentication_cookie_or_challenge_required": runtime.get("authentication_cookie_or_challenge_required"),
            retryable_category: runtime.get(retryable_category),
            "http_non_success_or_response_too_large": runtime.get("http_non_success_or_response_too_large"),
            "content_type_encoding_or_parse_failure": runtime.get("content_type_encoding_or_parse_failure"),
            "publisher_core_fact_conflict": runtime.get("publisher_core_fact_conflict"),
        }
        for category, rule in categories.items():
            if not isinstance(rule, dict) or rule.get("retryable") is not (category == retryable_category):
                raise LivePolicyError(
                    "endpoint_or_configuration_policy_violation",
                    f"retry classification changed: {category}",
                )
    pairs = policy["game_source_pairs"]
    if not isinstance(pairs, dict) or set(pairs) != {"ssq", "dlt"}:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "game source pairs changed")
    expected_pairs = {"ssq": ["ydniu", "swlc"], "dlt": ["ydniu", "gdlottery"]}
    pair_keys = {"source_ids", "required_observations_per_issue", "distinct_publisher_ids_required", "core_fact_sha256_agreement_required", "on_missing_or_conflict"}
    for game, source_ids in expected_pairs.items():
        pair = pairs[game]
        if not isinstance(pair, dict):
            raise LivePolicyError("endpoint_or_configuration_policy_violation", f"invalid pair: {game}")
        _exact_keys(pair, pair_keys, f"pair {game}")
        if pair["source_ids"] != source_ids or pair["required_observations_per_issue"] != 2 or pair["distinct_publisher_ids_required"] is not True or pair["core_fact_sha256_agreement_required"] is not True or pair["on_missing_or_conflict"] != "reject_run":
            raise LivePolicyError("endpoint_or_configuration_policy_violation", f"pair policy changed: {game}")
    sources = policy["sources"]
    if not isinstance(sources, list) or [item.get("source_id") for item in sources if isinstance(item, dict)] != ["ydniu", "swlc", "gdlottery"]:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live sources changed")
    publishers = [item.get("publisher_id") for item in sources]
    if len(set(publishers)) != 3:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "live publishers are not distinct")
    by_id = {item["source_id"]: item for item in sources}
    expected_endpoints = {
        "ydniu": {"ssq": "https://www.ydniu.com/open/ssq-500/1.html", "dlt": "https://www.ydniu.com/open/dlt-500/1.html"},
        "swlc": {"ssq": "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30"},
        "gdlottery": {"dlt": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json"},
    }
    for source_id, endpoints in expected_endpoints.items():
        if by_id[source_id].get("endpoints") != endpoints:
            raise LivePolicyError("endpoint_or_configuration_policy_violation", f"endpoints changed: {source_id}")
        for game, endpoint in endpoints.items():
            _https_url(endpoint, f"{source_id}/{game}")
    gd = by_id["gdlottery"]
    if (
        gd.get("parser_id") != "phase1-gdlottery-history-parser"
        or gd.get("parser_version") != "1.0.0"
        or gd.get("endpoint_kind") != "exact_history_json"
        or gd.get("expected_content_type") != "application/json"
        or gd.get("expected_encoding") != "utf-8"
        or gd.get("max_response_bytes") != 2097152
        or gd.get("observed_response_bytes") != 1828447
        or gd.get("observed_response_sha256") != "dae5c9e0f33cfc09e8b245e9f093bfeaf115ed9383c673dd78ef08f34c98b5ac"
    ):
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "gdlottery JSON history profile changed")


def source_index(policy: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(source["source_id"]): dict(source) for source in policy["sources"]}


def build_live_request_plan(policy: Mapping[str, Any], games: Iterable[str]) -> list[dict[str, Any]]:
    wanted = set(games)
    if not wanted or not wanted <= {"ssq", "dlt"}:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "unsupported live game selection")
    sources = source_index(policy)
    profile_version = policy.get("live_policy_schema_version")
    if profile_version not in {"1.1.1", "1.2.0", "1.3.0"}:
        raise LivePolicyError("endpoint_or_configuration_policy_violation", "unknown live policy profile")
    requests: list[dict[str, Any]] = []
    frozen_sequence = 0
    for game in ("ssq", "dlt"):
        for source_id in policy["game_source_pairs"][game]["source_ids"]:
            frozen_sequence += 1
            if game not in wanted:
                continue
            source = sources[source_id]
            legacy_discovery = profile_version == "1.1.1" and source_id == "gdlottery"
            url = source["discovery_endpoint"] if legacy_discovery else source["endpoints"][game]
            kind = "discovery" if legacy_discovery else "history"
            request = {
                "request_id": f"live-{source_id}-{game}-{kind}", "sequence": frozen_sequence,
                "source_id": source_id, "publisher_id": source["publisher_id"], "game": game,
                "method": "GET", "url": url, "request_kind": kind,
                "parser_id": source["parser_id"], "parser_version": source["parser_version"],
            }
            if profile_version in {"1.2.0", "1.3.0"}:
                request["response_profile"] = {
                    "expected_media_type": "application/json" if source_id == "gdlottery" else "text/html",
                    "max_response_bytes": int(source.get("max_response_bytes", policy["network_policy"]["max_response_bytes"])),
                }
            requests.append(request)
    return requests
