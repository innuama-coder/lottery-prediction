from __future__ import annotations

import copy
import hashlib
import json
import re
import string
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from lottery_data.steps.live_policy import LIVE_POLICY_V13_SHA256, LivePolicyError, load_live_policy


REPO = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO / "config" / "phase1" / "live-source-policy.json"
LEGACY_POLICY_PATH = REPO / "tests" / "phase1" / "fixtures" / "live-policy" / "live-source-policy-v1.1.1.json"
LEGACY_POLICY_SHA256 = "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1"
CONTRACT_PATH = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
SNAPSHOT_HASHES = {
    "config/phase1/source-catalog.json": "b0a30f0a6c90744043cb74ed504db161b3456c9607618a522237cf28487b36fa",
    "config/phase1/collection-policy.json": "79c2f55d93d3458602122f51148c96073f3dc4a809f95bc3cc7041e0a983e760",
}
EXPECTED_HOSTS = {
    "ydniu": "www.ydniu.com",
    "swlc": "www.swlc.net.cn",
    "gdlottery": "www.gdlottery.cn",
}
FORMATTER = string.Formatter()
TOP_LEVEL_KEYS = {
    "live_policy_schema_version",
    "reviewed_at",
    "valid_until",
    "review_expiry_rule",
    "scope",
    "production_collection_approved",
    "redistribution_approved",
    "baseline_separation",
    "network_policy",
    "failure_classification",
    "game_source_pairs",
    "recheck_policy",
    "sources",
}
RECHECK_POLICY = {
    "recheck_policy_schema_version": "1.1.0",
    "window_per_game_latest_issues": 20,
    "new_issue_target_rule": "Any newly observed issue is a target; a missing required source pair is unresolved and rejects publication.",
    "existing_complete_pair_rule": "Inside the latest-20 per-game window, a complete required pair follows normal unchanged, revised, or conflict handling; any extra dissent blocks publication.",
    "deferred_rule": "For an existing target with an incomplete required pair, if every available observation equals the current draw core, retain the old draw and evidence, record RECHECK_DEFERRED_MISSING_PARTNER, do not count unresolved, do not block, and do not claim the recheck complete.",
    "unconfirmed_change_rule": "For an existing target with an incomplete required pair, any available observation whose core differs from the current draw is RECHECK_UNCONFIRMED_CHANGE, unresolved, and rejects publication.",
    "quality_counters": ["recheck_attempted", "recheck_complete", "recheck_deferred"],
    "source_expansion_approved": False,
}
LEGACY_RECHECK_POLICY = {**RECHECK_POLICY, "recheck_policy_schema_version": "1.0.0",
                         "gd_discovery_observed_selector_link_count": 1}
NETWORK_POLICY = {
    "method_allowlist": ["GET"],
    "https_only": True,
    "authentication_allowed": False,
    "cookies_allowed": False,
    "cross_process_same_host_min_interval_seconds": 2.0,
    "request_timeout_seconds": 30,
    "max_attempts_per_request": 2,
    "retry_backoff_seconds": 2,
    "retryable_error_categories": ["dns_timeout_tls_or_required_source_unavailable"],
    "max_response_bytes": 1_048_576,
    "redirect_policy": "same_origin_only",
    "max_redirects": 3,
    "persist_raw_before_parse": True,
    "cache_may_satisfy_current_run": False,
    "request_plan_rule": "Only the exact static endpoints declared in this file may enter the current live request plan. A calendar, expected issue number, timestamp cache-buster, discovered child, or guessed future URL is never a request source.",
}
LEGACY_NETWORK_POLICY = {key: value for key, value in NETWORK_POLICY.items() if key not in {
    "retry_backoff_seconds", "retryable_error_categories",
}} | {"max_attempts_per_request": 1,
    "request_plan_rule": "Only the exact endpoints or an observed, validated discovery link declared in this file may enter a live request plan. A calendar, expected issue number, or guessed future URL is never a request source.",
}
PAIR_KEYS = {
    "source_ids",
    "required_observations_per_issue",
    "distinct_publisher_ids_required",
    "core_fact_sha256_agreement_required",
    "on_missing_or_conflict",
}
YDNIU_KEYS = {
    "source_id", "publisher_id", "games", "parser_id", "parser_version", "endpoint_kind",
    "endpoints", "expected_content_type", "expected_encoding", "observed_page_capacity",
    "reviewed_at", "valid_until", "review_evidence_urls",
}
SWLC_KEYS = YDNIU_KEYS | {"query_rule"}
GDLOTTERY_KEYS = {
    "source_id", "publisher_id", "games", "parser_id", "parser_version", "endpoint_kind",
    "endpoints", "expected_content_type", "expected_encoding", "max_response_bytes",
    "observed_response_bytes", "observed_response_sha256", "reviewed_at", "valid_until",
    "review_evidence_urls", "known_limits",
}
LEGACY_GDLOTTERY_KEYS = {
    "source_id", "publisher_id", "games", "parser_id", "parser_version", "endpoint_kind",
    "discovery_endpoint", "discovery_selector", "announcement_origin", "announcement_path_pattern",
    "announcement_rule", "pdf_rule", "expected_content_type", "expected_encoding", "reviewed_at",
    "valid_until", "review_evidence_urls", "known_limits",
}


class ContractError(ValueError):
    """The live-source policy is absent, ambiguous, or unsafe."""


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"required policy file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"policy is not readable UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError("policy root must be an object")
    return value


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise ContractError(f"frozen config is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise ContractError(
            f"{label} keys differ; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def template_fields(template: str) -> tuple[str, ...]:
    try:
        parsed = tuple(field for _, field, spec, conversion in FORMATTER.parse(template) if field is not None)
    except ValueError as exc:
        raise ContractError("malformed URL template") from exc
    if any(not field or spec or conversion for _, field, spec, conversion in FORMATTER.parse(template) if field is not None):
        raise ContractError("URL placeholders must be simple names without formatting or conversion")
    if len(parsed) != len(set(parsed)):
        raise ContractError("URL placeholders must be unique")
    return parsed


def validate_url_template(
    template: str,
    *,
    expected_host: str,
    allowed_placeholders: set[str],
    placeholder_samples: dict[str, str],
    allowed_query: dict[str, set[str]],
) -> None:
    if not isinstance(template, str) or not template:
        raise ContractError("URL template must be a non-empty string")
    fields = set(template_fields(template))
    if fields != allowed_placeholders or set(placeholder_samples) != allowed_placeholders:
        raise ContractError("URL placeholder set differs from the frozen contract")
    try:
        rendered = template.format_map(placeholder_samples)
    except (KeyError, ValueError) as exc:
        raise ContractError("URL template cannot be rendered with contract samples") from exc
    split = urlsplit(rendered)
    if split.scheme != "https":
        raise ContractError("only HTTPS source URLs are allowed")
    if split.username is not None or split.password is not None:
        raise ContractError("URL credentials are forbidden")
    try:
        port = split.port
    except ValueError as exc:
        raise ContractError("invalid URL port") from exc
    if port is not None:
        raise ContractError("explicit URL ports are forbidden")
    if split.hostname != expected_host or split.netloc != expected_host:
        raise ContractError("source host must exactly equal its allowlisted host")
    if split.fragment:
        raise ContractError("URL fragments are forbidden")
    if not split.path.startswith("/") or "\\" in split.path or "//" in split.path:
        raise ContractError("URL path is not canonical")
    try:
        pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ContractError("malformed query string") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise ContractError("duplicate query keys are forbidden")
    actual_query = {key: value for key, value in pairs}
    if set(actual_query) != set(allowed_query):
        raise ContractError("query keys differ from the allowlist")
    for key, value in actual_query.items():
        if value not in allowed_query[key]:
            raise ContractError(f"query value is not allowlisted: {key}")
    if re.search(r"(?:^|[/?&=])(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?)", rendered, re.I):
        raise ContractError("loopback URL material is forbidden")


def validate_top_and_network(policy: dict[str, Any], *, legacy: bool = False) -> None:
    require_exact_keys(policy, TOP_LEVEL_KEYS, "policy")
    if policy["live_policy_schema_version"] != ("1.1.1" if legacy else "1.3.0"):
        raise ContractError("live policy schema version is not frozen")
    if policy["reviewed_at"] != "2026-08-03" or policy["valid_until"] != "2026-08-16":
        raise ContractError("policy review window differs from the frozen decision")
    expiry = policy["review_expiry_rule"]
    if not isinstance(expiry, str) or "HOLD" not in expiry or re.search(r"exit(?:s)? 4", expiry) is None:
        raise ContractError("expired policy must fail closed as HOLD / exit 4")
    scope = policy["scope"]
    if not isinstance(scope, str) or not all(token in scope.lower() for token in ("phase 1", "internal", "live")):
        raise ContractError("scope must explicitly limit the policy to Phase 1 internal live collection")
    for flag in ("production_collection_approved", "redistribution_approved"):
        if policy[flag] is not False:
            raise ContractError(f"{flag} must be the JSON boolean false")
    network_policy = LEGACY_NETWORK_POLICY if legacy else NETWORK_POLICY
    recheck_policy = LEGACY_RECHECK_POLICY if legacy else RECHECK_POLICY
    require_exact_keys(policy["network_policy"], set(network_policy), "network_policy")
    if policy["network_policy"] != network_policy:
        raise ContractError("network policy differs from the frozen fail-closed limits")
    require_exact_keys(policy["recheck_policy"], set(recheck_policy), "recheck_policy")
    if policy["recheck_policy"] != recheck_policy:
        raise ContractError("live recheck policy differs from the frozen decision")


def validate_baseline_and_failures(policy: dict[str, Any], *, legacy: bool = False) -> None:
    baseline = policy["baseline_separation"]
    require_exact_keys(
        baseline,
        {"rule", "immutable_baseline_config", "baseline_source_pair", "baseline_release_id", "baseline_gate_ids"},
        "baseline_separation",
    )
    if baseline["immutable_baseline_config"] != list(SNAPSHOT_HASHES):
        raise ContractError("immutable baseline config paths differ")
    if baseline["baseline_source_pair"] != ["ydniu", "eastmoney"]:
        raise ContractError("snapshot baseline source pair was reinterpreted")
    if baseline["baseline_release_id"] != "baseline-v1" or baseline["baseline_gate_ids"] != ["G1", "G2"]:
        raise ContractError("snapshot baseline identity differs")
    rule = baseline["rule"]
    if not isinstance(rule, str) or not all(token in rule for token in ("source-mode=live", "mode=incremental", "source-mode=snapshot")):
        raise ContractError("baseline/live separation rule is incomplete")

    validate_failure_oracle(policy, legacy=legacy)


def validate_failure_oracle(policy: dict[str, Any], *, legacy: bool = False) -> dict[str, dict[str, Any]]:
    failures = policy["failure_classification"]
    require_exact_keys(failures, {"preflight", "runtime", "acceptance_mapping"}, "failure_classification")
    preflight = failures["preflight"]
    preflight_expected = {
        "expired_or_changed_policy": {
            "decision": "HOLD", "cli_exit_code": 4, "effect": "creates_no_request_run_or_release",
        },
        "endpoint_or_configuration_policy_violation": {
            "decision": "HOLD", "cli_exit_code": 4, "effect": "creates_no_request_run_or_release",
        },
    }
    if not legacy:
        preflight_expected = {key: {**value, "retryable": False} for key, value in preflight_expected.items()}
    require_exact_keys(preflight, {"stage_rule"} | set(preflight_expected), "failure_classification.preflight")
    if preflight["stage_rule"] != "These checks finish before a request plan or run is created.":
        raise ContractError("preflight stage boundary differs")
    if {key: preflight[key] for key in preflight_expected} != preflight_expected:
        raise ContractError("preflight failures must be CLI 4 with no request, run, or release")

    runtime = failures["runtime"]
    runtime_expected = {
        "redirect_policy_violation": {"decision": "HOLD", "cli_exit_code": 4},
        "authentication_cookie_or_challenge_required": {"decision": "HOLD", "cli_exit_code": 3},
        "dns_timeout_tls_or_required_source_unavailable": {"decision": "HOLD", "cli_exit_code": 3},
        "http_non_success_or_response_too_large": {"decision": "HOLD", "cli_exit_code": 3},
        "content_type_encoding_or_parse_failure": {"decision": "FAIL", "cli_exit_code": 2},
        "publisher_core_fact_conflict": {"decision": "FAIL", "cli_exit_code": 2},
    }
    if legacy:
        runtime_expected["missing_stale_or_mismatched_discovered_issue"] = {"decision": "FAIL", "cli_exit_code": 2}
    else:
        runtime_expected = {
            key: {**value, "retryable": key == "dns_timeout_tls_or_required_source_unavailable"}
            for key, value in runtime_expected.items()
        }
    require_exact_keys(runtime, {"stage_rule"} | set(runtime_expected), "failure_classification.runtime")
    expected_runtime_effect = (
        "These failures occur only after a run and request_started event exist. Append and flush exactly one "
        "terminal request_failed event, reject the run, create no release, and leave current-release.json unchanged."
        if legacy else
        "These failures occur only after a run and request_started attempt exist. Append and flush exactly one "
        "request_failed terminal for that attempt. A retryable attempt-1 failure schedules exactly one attempt-2 "
        "after the frozen delay; a non-retryable or exhausted failure rejects the run, creates no release, and "
        "leaves current-release.json unchanged."
    )
    if runtime["stage_rule"] != expected_runtime_effect:
        raise ContractError("runtime event, rejection, release, or pointer effect differs")
    if {key: runtime[key] for key in runtime_expected} != runtime_expected:
        raise ContractError("runtime failure classification differs from the frozen matrix")

    oracle: dict[str, dict[str, Any]] = {}
    for category, outcome in preflight_expected.items():
        oracle[category] = {"stage": "preflight", "effect": outcome["effect"], **outcome}
    for category, outcome in runtime_expected.items():
        if category in oracle:
            raise ContractError(f"failure category has ambiguous stages: {category}")
        oracle[category] = {"stage": "runtime", "effect": expected_runtime_effect, **outcome}
    if len(oracle) != len(preflight_expected) + len(runtime_expected):
        raise ContractError("each failure category must have exactly one stage/effect oracle")
    return oracle


def validate_acceptance_exit_mapping(policy: dict[str, Any], contract: dict[str, Any]) -> None:
    mapping = policy["failure_classification"]["acceptance_mapping"]
    require_exact_keys(
        mapping, {"rule", "hold_runner_exit_code", "expired_policy_underlying_exit_code"},
        "failure_classification.acceptance_mapping",
    )
    if mapping["hold_runner_exit_code"] != 20 or mapping["expired_policy_underlying_exit_code"] != 4:
        raise ContractError("policy must preserve CLI 4 under acceptance HOLD 20")
    if not all(token in mapping["rule"] for token in ("underlying exit code", "HOLD", "underlying_exit_code", "exits 20")):
        raise ContractError("policy CLI-to-acceptance mapping rule is incomplete")

    try:
        contract_mapping = contract["configuration_inputs"]["acceptance_exit_mapping"]
    except (KeyError, TypeError) as exc:
        raise ContractError("acceptance contract mapping is missing") from exc
    expected_contract_mapping = {
        "product_cli_preflight_policy_exit_code": 4,
        "acceptance_decision": "HOLD",
        "acceptance_runner_exit_code": 20,
        "required_report_fields": ["decision", "underlying_exit_code", "acceptance_runner_exit_code"],
        "rule": (
            "Never report product CLI exit 4 as the acceptance runner exit. Preserve it as underlying_exit_code "
            "while the E2E-05 or G3 runner returns 20 for HOLD."
        ),
    }
    require_exact_keys(contract_mapping, set(expected_contract_mapping), "configuration_inputs.acceptance_exit_mapping")
    if contract_mapping != expected_contract_mapping:
        raise ContractError("acceptance contract must map CLI 4 to HOLD runner exit 20")
    runner_codes = contract.get("runtime_contract", {}).get("acceptance_runner_exit_codes")
    if runner_codes != {"0": "PASS", "1": "FAIL", "20": "HOLD"}:
        raise ContractError("acceptance runner exit-code vocabulary differs")


def source_map(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sources = policy["sources"]
    if not isinstance(sources, list) or len(sources) != 3 or any(not isinstance(item, dict) for item in sources):
        raise ContractError("sources must be exactly three objects")
    result = {item.get("source_id"): item for item in sources}
    if set(result) != set(EXPECTED_HOSTS) or None in result or len(result) != len(sources):
        raise ContractError("source ids must be unique and exactly frozen")
    return result


def validate_review_metadata(source: dict[str, Any], expected_urls: list[str]) -> None:
    if source["reviewed_at"] != "2026-08-02" or source["valid_until"] != "2026-08-16":
        raise ContractError("source review window differs")
    if source["review_evidence_urls"] != expected_urls:
        raise ContractError("source review evidence differs")
    expected_host = EXPECTED_HOSTS[source["source_id"]]
    for url in expected_urls:
        split = urlsplit(url)
        if split.scheme != "https" or split.hostname != expected_host or split.netloc != expected_host:
            raise ContractError("review evidence must stay on the exact HTTPS source host")


def validate_pairs_and_sources(policy: dict[str, Any], *, legacy: bool = False) -> None:
    pairs = policy["game_source_pairs"]
    require_exact_keys(pairs, {"ssq", "dlt"}, "game_source_pairs")
    expected_pairs = {"ssq": ["ydniu", "swlc"], "dlt": ["ydniu", "gdlottery"]}
    for game, expected_ids in expected_pairs.items():
        pair = pairs[game]
        require_exact_keys(pair, PAIR_KEYS, f"game_source_pairs.{game}")
        expected_pair = {
            "source_ids": expected_ids,
            "required_observations_per_issue": 2,
            "distinct_publisher_ids_required": True,
            "core_fact_sha256_agreement_required": True,
            "on_missing_or_conflict": "reject_run",
        }
        if pair != expected_pair:
            raise ContractError(f"{game} source pair differs from the frozen contract")

    sources = source_map(policy)
    ydniu, swlc, gdlottery = sources["ydniu"], sources["swlc"], sources["gdlottery"]
    require_exact_keys(ydniu, YDNIU_KEYS, "sources.ydniu")
    require_exact_keys(swlc, SWLC_KEYS, "sources.swlc")
    require_exact_keys(gdlottery, LEGACY_GDLOTTERY_KEYS if legacy else GDLOTTERY_KEYS, "sources.gdlottery")
    common_expected = {
        "ydniu": (["ssq", "dlt"], "ydniu-publisher", "phase1-ydniu-parser", "1.0.0"),
        "swlc": (["ssq"], "swlc-publisher", "phase1-swlc-live-parser", "1.0.0"),
        "gdlottery": (["dlt"], "gdlottery-publisher",
                      "phase1-gdlottery-live-parser" if legacy else "phase1-gdlottery-history-parser",
                      "2.0.0" if legacy else "1.0.0"),
    }
    for source_id, (games, publisher, parser, version) in common_expected.items():
        source = sources[source_id]
        if (source["games"], source["publisher_id"], source["parser_id"], source["parser_version"]) != (games, publisher, parser, version):
            raise ContractError(f"{source_id} identity differs")
        expected_media = "application/json" if source_id == "gdlottery" and not legacy else "text/html"
        if source["expected_content_type"] != expected_media or source["expected_encoding"] != "utf-8":
            raise ContractError(f"{source_id} response contract differs")
    for game, expected_ids in expected_pairs.items():
        publishers = [sources[source_id]["publisher_id"] for source_id in expected_ids]
        if len(set(publishers)) != 2:
            raise ContractError(f"{game} pair does not use distinct publishers")

    if ydniu["endpoint_kind"] != "exact_history_page" or ydniu["observed_page_capacity"] != 30:
        raise ContractError("ydniu endpoint semantics differ")
    expected_ydniu = {
        "ssq": "https://www.ydniu.com/open/ssq-500/1.html",
        "dlt": "https://www.ydniu.com/open/dlt-500/1.html",
    }
    if ydniu["endpoints"] != expected_ydniu:
        raise ContractError("ydniu exact endpoints differ")
    for url in expected_ydniu.values():
        validate_url_template(url, expected_host="www.ydniu.com", allowed_placeholders=set(), placeholder_samples={}, allowed_query={})

    expected_swlc = "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30"
    if swlc["endpoint_kind"] != "exact_history_query" or swlc["endpoints"] != {"ssq": expected_swlc} or swlc["observed_page_capacity"] != 30:
        raise ContractError("swlc exact endpoint semantics differ")
    if not all(token in swlc["query_rule"] for token in ("exact", "immutable", "must not change", "add parameters")):
        raise ContractError("swlc query must be immutable")
    validate_url_template(
        expected_swlc,
        expected_host="www.swlc.net.cn",
        allowed_placeholders=set(),
        placeholder_samples={},
        allowed_query={"view": {"previous"}, "limit": {"30"}},
    )

    if not legacy:
        expected_gd = "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json"
        if gdlottery["endpoint_kind"] != "exact_history_json" or gdlottery["endpoints"] != {"dlt": expected_gd}:
            raise ContractError("gdlottery current endpoint must be the exact JSON history URL")
        validate_url_template(expected_gd, expected_host="www.gdlottery.cn", allowed_placeholders=set(), placeholder_samples={}, allowed_query={})
        if gdlottery["max_response_bytes"] != 2_097_152 or gdlottery["observed_response_bytes"] > gdlottery["max_response_bytes"]:
            raise ContractError("gdlottery JSON response cap differs")
        if set(gdlottery) & {"discovery_endpoint", "discovery_selector", "announcement_origin", "announcement_path_pattern", "announcement_rule"}:
            raise ContractError("current gdlottery source must not retain discovery/child authorization")
        validate_review_metadata(ydniu, [
            "https://www.ydniu.com/open/ssq-500/1.html", "https://www.ydniu.com/open/dlt-500/1.html",
            "https://www.ydniu.com/robots.txt", "https://www.ydniu.com/about.aspx",
        ])
        validate_review_metadata(swlc, [
            expected_swlc, "https://www.swlc.net.cn/robots.txt", "https://www.swlc.net.cn/",
        ])
        validate_review_metadata(gdlottery, [expected_gd, "https://www.gdlottery.cn/robots.txt", "https://www.gdlottery.cn/"])
        return

    if gdlottery["endpoint_kind"] != "server_rendered_discovery_then_announcement":
        raise ContractError("legacy gdlottery must use discovery, not a future URL template")
    if gdlottery["discovery_endpoint"] != "https://www.gdlottery.cn/html/dlt/index.html":
        raise ContractError("gdlottery discovery endpoint differs")
    validate_url_template(
        gdlottery["discovery_endpoint"], expected_host="www.gdlottery.cn",
        allowed_placeholders=set(), placeholder_samples={}, allowed_query={},
    )
    if gdlottery["discovery_selector"] != ".btn a[href^='/f_html/kjgg/P085_'][href$='.html']":
        raise ContractError("gdlottery server-rendered discovery selector differs")
    if gdlottery["announcement_origin"] != "https://www.gdlottery.cn":
        raise ContractError("gdlottery announcement origin differs")
    if gdlottery["announcement_path_pattern"] != r"^/f_html/kjgg/P085_[0-9]{5}\.html$":
        raise ContractError("gdlottery announcement allowlist differs")
    announcement_rule = gdlottery["announcement_rule"]
    if not all(token in announcement_rule for token in ("only the href observed", "same origin", "path pattern", "Never construct")):
        raise ContractError("gdlottery future-URL construction is not forbidden")

    validate_review_metadata(ydniu, [
        "https://www.ydniu.com/open/ssq-500/1.html", "https://www.ydniu.com/open/dlt-500/1.html",
        "https://www.ydniu.com/robots.txt", "https://www.ydniu.com/about.aspx",
    ])
    validate_review_metadata(swlc, [
        expected_swlc, "https://www.swlc.net.cn/robots.txt", "https://www.swlc.net.cn/",
    ])
    validate_review_metadata(gdlottery, [
        "https://www.gdlottery.cn/html/dlt/index.html",
        "https://www.gdlottery.cn/f_html/kjgg/P085_26086.html",
        "https://www.gdlottery.cn/f_html/kjgg_pdf_bak/P085_26086.pdf",
        "https://www.gdlottery.cn/robots.txt", "https://www.gdlottery.cn/",
    ])


class LivePolicyTopLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_json(POLICY_PATH)
        cls.legacy_policy = load_json(LEGACY_POLICY_PATH)
        cls.contract = load_json(CONTRACT_PATH)

    def test_top_level_review_scope_and_network_contract(self) -> None:
        validate_top_and_network(self.policy)

    def test_pre_3_3_policy_shape_is_rejected_and_current_bytes_are_frozen(self) -> None:
        old_shape = copy.deepcopy(self.policy)
        old_shape["live_policy_schema_version"] = "1.0.0"
        old_shape.pop("recheck_policy")
        with self.assertRaises(ContractError):
            validate_top_and_network(old_shape)

        live_inputs = [
            item
            for item in self.contract["configuration_inputs"]["files"]
            if item["path"] == "config/phase1/live-source-policy.json"
        ]
        self.assertEqual(len(live_inputs), 1)
        self.assertEqual(sha256_file(POLICY_PATH), LIVE_POLICY_V13_SHA256)
        self.assertEqual(sha256_file(POLICY_PATH), live_inputs[0]["expected_sha256"])
        self.assertEqual(sha256_file(LEGACY_POLICY_PATH), LEGACY_POLICY_SHA256)

    def test_3_3_0_policy_is_hold20_and_3_3_1_policy_loads(self) -> None:
        current = LEGACY_POLICY_PATH.read_bytes()
        loaded = load_live_policy(POLICY_PATH, today=date(2026, 8, 3))
        self.assertEqual(loaded["live_policy_schema_version"], "1.3.0")
        self.assertEqual(
            self.contract["live_recheck_contract"]["policy_schema_version"],
            loaded["live_policy_schema_version"],
        )
        old = current.replace(b'"live_policy_schema_version": "1.1.1"', b'"live_policy_schema_version": "1.1.0"', 1)
        old = old.replace(
            ".btn a[href^='/f_html/kjgg/P085_'][href$='.html']".encode("utf-8"),
            ".btn a[title='查看中奖详情']".encode("utf-8"),
            1,
        )
        self.assertEqual(hashlib.sha256(old).hexdigest(), "d4b3af0624ca2b1e518b91fa4f32af8c96a888275bdbc79ca9c324b79abf424e")
        with tempfile.TemporaryDirectory() as directory:
            old_path = Path(directory) / "live-source-policy-3.3.0.json"
            old_path.write_bytes(old)
            with self.assertRaises(LivePolicyError) as raised:
                load_live_policy(old_path, today=date(2026, 8, 3))
        self.assertEqual((raised.exception.category, raised.exception.exit_code), ("expired_or_changed_policy", 4))
        validate_top_and_network(self.legacy_policy, legacy=True)
        validate_baseline_and_failures(self.legacy_policy, legacy=True)
        validate_pairs_and_sources(self.legacy_policy, legacy=True)
        mapping = self.contract["configuration_inputs"]["acceptance_exit_mapping"]
        self.assertEqual((mapping["acceptance_decision"], mapping["acceptance_runner_exit_code"]), ("HOLD", 20))

    def test_snapshot_configs_retain_the_pre_live_policy_bytes(self) -> None:
        actual = {relative: sha256_file(REPO / relative) for relative in SNAPSHOT_HASHES}
        self.assertEqual(actual, SNAPSHOT_HASHES)

    def test_baseline_failure_pair_source_and_evidence_contracts(self) -> None:
        validate_baseline_and_failures(self.policy)
        validate_pairs_and_sources(self.policy)

    def test_each_failure_has_one_stage_effect_and_cli4_maps_to_hold20(self) -> None:
        oracle = validate_failure_oracle(self.policy)
        self.assertEqual(len(oracle), 8)
        self.assertEqual({item["stage"] for item in oracle.values()}, {"preflight", "runtime"})
        validate_acceptance_exit_mapping(self.policy, self.contract)

    def test_top_level_and_network_negative_mutations_fail_closed(self) -> None:
        mutations = (
            ("extra top-level key", lambda value: value.__setitem__("future_policy", {})),
            ("production true", lambda value: value.__setitem__("production_collection_approved", True)),
            ("redistribution true", lambda value: value.__setitem__("redistribution_approved", True)),
            ("POST allowed", lambda value: value["network_policy"].__setitem__("method_allowlist", ["GET", "POST"])),
            ("authentication allowed", lambda value: value["network_policy"].__setitem__("authentication_allowed", True)),
            ("cookies allowed", lambda value: value["network_policy"].__setitem__("cookies_allowed", True)),
            ("process-local throttle", lambda value: value["network_policy"].__setitem__("cross_process_same_host_min_interval_seconds", 1.99)),
            ("timeout above cap", lambda value: value["network_policy"].__setitem__("request_timeout_seconds", 31)),
            ("retry budget widened", lambda value: value["network_policy"].__setitem__("max_attempts_per_request", 3)),
            ("response cap above 1 MiB", lambda value: value["network_policy"].__setitem__("max_response_bytes", 1_048_577)),
            ("cross-host redirect", lambda value: value["network_policy"].__setitem__("redirect_policy", "https_only")),
            ("recheck window widened", lambda value: value["recheck_policy"].__setitem__("window_per_game_latest_issues", 21)),
            ("deferred claimed complete", lambda value: value["recheck_policy"].__setitem__("quality_counters", ["recheck_attempted", "recheck_complete"])),
            ("unreviewed source expansion", lambda value: value["recheck_policy"].__setitem__("source_expansion_approved", True)),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                with self.assertRaises(ContractError):
                    validate_top_and_network(candidate)

    def test_baseline_pair_source_and_url_negative_mutations_fail_closed(self) -> None:
        mutations = (
            ("snapshot config removed", lambda value: value["baseline_separation"]["immutable_baseline_config"].pop()),
            ("snapshot pair rewritten", lambda value: value["baseline_separation"].__setitem__("baseline_source_pair", ["ydniu", "swlc"])),
            ("failure softened", lambda value: value["failure_classification"]["runtime"]["publisher_core_fact_conflict"].__setitem__("decision", "HOLD")),
            ("ssq source reordered", lambda value: value["game_source_pairs"]["ssq"].__setitem__("source_ids", ["swlc", "ydniu"])),
            ("single observation", lambda value: value["game_source_pairs"]["dlt"].__setitem__("required_observations_per_issue", 1)),
            ("publisher independence disabled", lambda value: value["game_source_pairs"]["ssq"].__setitem__("distinct_publisher_ids_required", False)),
            ("same publisher", lambda value: next(item for item in value["sources"] if item["source_id"] == "swlc").__setitem__("publisher_id", "ydniu-publisher")),
            ("ydniu HTTP", lambda value: next(item for item in value["sources"] if item["source_id"] == "ydniu")["endpoints"].__setitem__("ssq", "http://www.ydniu.com/open/ssq-500/1.html")),
            ("ydniu attacker host", lambda value: next(item for item in value["sources"] if item["source_id"] == "ydniu")["endpoints"].__setitem__("dlt", "https://www.ydniu.com.evil.example/open/dlt-500/1.html")),
            ("swlc query changed", lambda value: next(item for item in value["sources"] if item["source_id"] == "swlc")["endpoints"].__setitem__("ssq", "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=100")),
            ("swlc query added", lambda value: next(item for item in value["sources"] if item["source_id"] == "swlc")["endpoints"].__setitem__("ssq", "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30&next=https://127.0.0.1")),
            ("review evidence removed", lambda value: next(item for item in value["sources"] if item["source_id"] == "swlc")["review_evidence_urls"].pop()),
            ("GD query added", lambda value: next(item for item in value["sources"] if item["source_id"] == "gdlottery")["endpoints"].__setitem__("dlt", "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json?t=1")),
            ("GD media type changed", lambda value: next(item for item in value["sources"] if item["source_id"] == "gdlottery").__setitem__("expected_content_type", "text/html")),
            ("GD cap reduced to default", lambda value: next(item for item in value["sources"] if item["source_id"] == "gdlottery").__setitem__("max_response_bytes", 1_048_576)),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                with self.assertRaises(ContractError):
                    validate_baseline_and_failures(candidate)
                    validate_pairs_and_sources(candidate)

    def test_failure_stage_effect_and_acceptance_mapping_negative_mutations_fail_closed(self) -> None:
        policy_mutations = (
            ("preflight creates run", lambda value: value["failure_classification"]["preflight"]["expired_or_changed_policy"].__setitem__("effect", "create_rejected_run")),
            ("preflight uses runtime exit", lambda value: value["failure_classification"]["preflight"]["endpoint_or_configuration_policy_violation"].__setitem__("cli_exit_code", 3)),
            ("runtime loses terminal event", lambda value: value["failure_classification"]["runtime"].__setitem__("stage_rule", "reject the run")),
            ("category in two stages", lambda value: value["failure_classification"]["runtime"].__setitem__("expired_or_changed_policy", {"decision": "HOLD", "cli_exit_code": 4})),
            ("policy maps HOLD to 4", lambda value: value["failure_classification"]["acceptance_mapping"].__setitem__("hold_runner_exit_code", 4)),
            ("policy drops underlying 4", lambda value: value["failure_classification"]["acceptance_mapping"].__setitem__("expired_policy_underlying_exit_code", 20)),
        )
        for label, mutate in policy_mutations:
            with self.subTest(layer="policy", case=label):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                with self.assertRaises(ContractError):
                    validate_failure_oracle(candidate)
                    validate_acceptance_exit_mapping(candidate, self.contract)

        contract_mutations = (
            ("contract reports CLI 4 as runner exit", lambda value: value["configuration_inputs"]["acceptance_exit_mapping"].__setitem__("acceptance_runner_exit_code", 4)),
            ("contract loses underlying field", lambda value: value["configuration_inputs"]["acceptance_exit_mapping"]["required_report_fields"].remove("underlying_exit_code")),
            ("contract HOLD vocabulary becomes 4", lambda value: value["runtime_contract"]["acceptance_runner_exit_codes"].__setitem__("4", "HOLD")),
        )
        for label, mutate in contract_mutations:
            with self.subTest(layer="contract", case=label):
                candidate = copy.deepcopy(self.contract)
                mutate(candidate)
                with self.assertRaises(ContractError):
                    validate_acceptance_exit_mapping(self.policy, candidate)


class UrlSafetyHelperTests(unittest.TestCase):
    def assert_rejected(self, template: str, **overrides: Any) -> None:
        arguments: dict[str, Any] = {
            "expected_host": "www.ydniu.com",
            "allowed_placeholders": {"page"},
            "placeholder_samples": {"page": "1"},
            "allowed_query": {},
        }
        arguments.update(overrides)
        with self.assertRaises(ContractError):
            validate_url_template(template, **arguments)

    def test_valid_path_placeholder_and_fixed_query_templates(self) -> None:
        validate_url_template(
            "https://www.ydniu.com/open/ssq-500/{page}.html",
            expected_host="www.ydniu.com",
            allowed_placeholders={"page"},
            placeholder_samples={"page": "1"},
            allowed_query={},
        )
        validate_url_template(
            "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30",
            expected_host="www.swlc.net.cn",
            allowed_placeholders=set(),
            placeholder_samples={},
            allowed_query={"view": {"previous"}, "limit": {"30"}},
        )

    def test_rejects_ssrf_host_scheme_credentials_port_and_fragment(self) -> None:
        for bad in (
            "http://www.ydniu.com/open/ssq-500/{page}.html",
            "https://www.ydniu.com.evil.example/open/ssq-500/{page}.html",
            "https://user:password@www.ydniu.com/open/ssq-500/{page}.html",
            "https://www.ydniu.com:443/open/ssq-500/{page}.html",
            "https://127.0.0.1/open/ssq-500/{page}.html",
            "https://www.ydniu.com/open/ssq-500/{page}.html#fragment",
        ):
            with self.subTest(url=bad):
                self.assert_rejected(bad)

    def test_rejects_placeholder_and_query_confusion(self) -> None:
        self.assert_rejected("https://www.ydniu.com/open/ssq-500/{issue}.html")
        self.assert_rejected("https://www.ydniu.com/open/ssq-500/{page!r}.html")
        self.assert_rejected("https://www.ydniu.com/open/ssq-500/{page:02d}.html")
        self.assert_rejected("https://www.ydniu.com/open/ssq-500/{page}.html?next=https://127.0.0.1")
        self.assert_rejected(
            "https://www.ydniu.com/open/ssq-500/{page}.html?view=previous&view=previous",
            allowed_query={"view": {"previous"}},
        )
        self.assert_rejected(
            "https://www.ydniu.com/open/ssq-500/{page}.html?limit=500",
            allowed_query={"limit": {"30"}},
        )

    def test_fixed_url_contract_rejects_dynamic_gdlottery_issue(self) -> None:
        self.assert_rejected(
            "https://www.gdlottery.cn/f_html/kjgg/P085_{issue}.html",
            expected_host="www.gdlottery.cn",
            allowed_placeholders=set(),
            placeholder_samples={},
        )


if __name__ == "__main__":
    unittest.main()
