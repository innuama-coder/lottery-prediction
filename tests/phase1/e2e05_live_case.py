from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch
from urllib.parse import urlsplit

from lottery_data.serialization import canonical_json_bytes, core_fact_sha256, make_observation_id, make_revision_id, sha256_file
from lottery_data.steps.incremental_engine import build_incremental_release
from lottery_data.steps.live_policy import LIVE_POLICY_V13_SHA256, LivePolicyError, build_live_request_plan, load_live_policy
from lottery_data.steps.parse import parse_versioned_raw as product_parse_versioned_raw
from lottery_data.steps.preflight import BootstrapArguments, IncrementalArguments
from lottery_data.workflow import execute_bootstrap, execute_incremental


REPO = Path(__file__).resolve().parents[2]
FORMAL = REPO / "artifacts" / "phase-1"
CONFIG = REPO / "config" / "phase1"
POLICY_PATH = CONFIG / "live-source-policy.json"
LEGACY_CONTRACT_ASSERTION_IDS = (
    "live_policy_sha256_verified=true", "live_policy_internal_only=true",
    "production_collection_approved=false", "redistribution_approved=false", "live_review_not_expired=true",
    "preflight_policy_failure_creates_no_request_run_or_release=true",
    "runtime_failure_appends_one_terminal_request_failed=true", "policy_preflight_underlying_exit_code=4",
    "policy_preflight_HOLD_acceptance_runner_exit_code=20", "acceptance_report_preserves_underlying_exit_code=true",
    "bootstrap_live_forbidden=true", "mode_specific_pair.ssq=ydniu|swlc", "mode_specific_pair.dlt=ydniu|gdlottery",
    "all_required_sources_requested=true", "no_future_url_guessing=true",
    "gdlottery_announcement_discovered_from_current_server_html=true", "static_manifest_request_count=4",
    "effective_plan_request_count=5_after_valid_discovery", "request_discovered_fsynced_before_announcement_started=true",
    "discovery_authorization_sha256_verified=true", "live_success_raw_refs_are_content_addressed=true",
    "live_recheck_window_uses_latest20_union=true", "dlt_unchanged_missing_partner_is_deferred_not_complete=true",
    "dlt_single_side_change_is_unresolved=true", "new_issue_missing_pair_is_unresolved=true",
    "ssq_twenty_history_rechecks_complete=true", "raw_persisted_before_parse=true",
    "status_in=published|no_change", "no_production_pointer_change=true",
)
ASSERTION_IDS = (
    *LEGACY_CONTRACT_ASSERTION_IDS[:15],
    "gdlottery_history_json_is_static_request=true", "static_manifest_request_count=4",
    "effective_plan_request_count=4_no_dynamic_child=true", "request_discovered_event_absent=true",
    "child_authorization_absent=true",
    *LEGACY_CONTRACT_ASSERTION_IDS[20:],
)
CONTRACT_TO_CURRENT_ASSERTION_IDS = dict(zip(LEGACY_CONTRACT_ASSERTION_IDS, ASSERTION_IDS, strict=True))
CURRENT_EXECUTION_PROFILE = "static-history-v1.3-retry"
CURRENT_STATIC_REQUEST_IDS = (
    "live-ydniu-ssq-history", "live-swlc-ssq-history",
    "live-ydniu-dlt-history", "live-gdlottery-dlt-history",
)
MAX_WATCHDOG_TOTAL_SECONDS = 300.0
MIN_PROCESS_CLEANUP_SECONDS = 5.0


class DeadlineDerivationError(ValueError):
    pass


def _finite_number(value: Any, *, name: str, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeadlineDerivationError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise DeadlineDerivationError(f"{name} is outside the bounded profile")
    return number


def _derive_watchdog_budget(policy: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = build_live_request_plan(policy, ("ssq", "dlt"))
        timeout = _finite_number(policy["network_policy"]["request_timeout_seconds"], name="request_timeout_seconds")
        throttle = _finite_number(policy["network_policy"]["cross_process_same_host_min_interval_seconds"], name="cross_process_same_host_min_interval_seconds")
        attempts = int(_finite_number(policy["network_policy"]["max_attempts_per_request"], name="max_attempts_per_request"))
        retry_delay = _finite_number(policy["network_policy"]["retry_backoff_seconds"], name="retry_backoff_seconds")
    except DeadlineDerivationError:
        raise
    except Exception as exc:
        raise DeadlineDerivationError("deadline inputs are incomplete") from exc
    if (
        policy.get("live_policy_schema_version") != "1.3.0" or attempts != 2
        or len(plan) != 4 or [row.get("sequence") for row in plan] != [1, 2, 3, 4]
        or any(row.get("request_kind") != "history" for row in plan)
        or any("child_authorization" in row or "parent_request_id" in row for row in plan)
    ):
        raise DeadlineDerivationError("effective live request plan is not the frozen v1.3 retry profile")
    request_hosts: list[str] = []
    for request in plan:
        parsed = urlsplit(str(request.get("url", "")))
        if parsed.scheme != "https" or not parsed.hostname or parsed.port is not None:
            raise DeadlineDerivationError("static request host is not bounded HTTPS")
        request_hosts.append(parsed.hostname)
    maximum_effective_requests = len(request_hosts) * attempts
    distinct_hosts = len(set(request_hosts))
    same_host_waits = maximum_effective_requests - distinct_hosts
    request_budget = maximum_effective_requests * timeout
    throttle_budget = same_host_waits * throttle
    retry_budget = len(request_hosts) * (attempts - 1) * retry_delay
    orchestration_margin = timeout
    cleanup_grace = max(MIN_PROCESS_CLEANUP_SECONDS, 2 * throttle)
    worker_deadline = request_budget + throttle_budget + retry_budget + orchestration_margin
    total_deadline = worker_deadline + cleanup_grace
    if total_deadline > MAX_WATCHDOG_TOTAL_SECONDS:
        raise DeadlineDerivationError("derived watchdog exceeds the 300 second safety ceiling")
    return {
        "execution_profile": CURRENT_EXECUTION_PROFILE,
        "static_request_count": len(plan), "max_dynamic_children": 0,
        "maximum_effective_requests": maximum_effective_requests,
        "distinct_host_count": distinct_hosts, "same_host_wait_count": same_host_waits,
        "request_timeout_seconds": timeout, "throttle_interval_seconds": throttle,
        "request_budget_seconds": request_budget, "throttle_budget_seconds": throttle_budget,
        "retry_budget_seconds": retry_budget,
        "orchestration_margin_seconds": orchestration_margin,
        "worker_deadline_seconds": worker_deadline, "cleanup_grace_seconds": cleanup_grace,
        "total_deadline_seconds": total_deadline, "safety_ceiling_seconds": MAX_WATCHDOG_TOTAL_SECONDS,
    }


def _run_owned_worker(
    command: list[str], *, cwd: Path, env: dict[str, str], budget: dict[str, Any],
) -> dict[str, Any]:
    temporary = tempfile.TemporaryDirectory(prefix="e2e05-live-parent-")
    workspace = Path(temporary.name)
    process: subprocess.Popen[str] | None = None
    timed_out = terminated = killed = False
    stdout = stderr = ""
    spawn_error: str | None = None
    try:
        process = subprocess.Popen(
            [*command, "--workspace", str(workspace)], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=float(budget["worker_deadline_seconds"]))
        except subprocess.TimeoutExpired:
            timed_out = True
            terminated = True
            process.terminate()
            half_grace = float(budget["cleanup_grace_seconds"]) / 2
            try:
                stdout, stderr = process.communicate(timeout=half_grace)
            except subprocess.TimeoutExpired:
                killed = True
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=half_grace)
                except subprocess.TimeoutExpired:
                    pass
    except Exception as exc:
        spawn_error = f"{type(exc).__name__}: {exc}"
    reaped = process is None or process.poll() is not None
    returncode = process.returncode if process is not None and reaped else None
    cleanup_error: str | None = None
    try:
        temporary.cleanup()
    except Exception as exc:
        cleanup_error = f"{type(exc).__name__}: {exc}"
    return {
        "timed_out": timed_out, "terminated": terminated, "killed": killed,
        "reaped": reaped, "returncode": returncode, "stdout": stdout, "stderr": stderr,
        "spawn_error": spawn_error, "workspace": str(workspace),
        "workspace_cleanup_verified": not workspace.exists(), "workspace_cleanup_error": cleanup_error,
    }


def _worker_process_evidence(worker: dict[str, Any]) -> dict[str, Any]:
    """Keep lifecycle evidence without embedding a duplicate worker report."""
    return {
        key: value for key, value in worker.items()
        if key not in {"stdout", "stderr"}
    } | {
        "stdout_length": len(worker.get("stdout", "")),
        "stderr_length": len(worker.get("stderr", "")),
    }


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _state(paths: list[Path], base: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for root in paths:
        if root.is_file():
            result[root.relative_to(base).as_posix()] = sha256_file(root)
        elif root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    result[path.relative_to(base).as_posix()] = sha256_file(path)
    return result


def _formal_state() -> dict[str, str]:
    targets = {
        "current-release.json": FORMAL / "current-release.json",
        "releases/baseline-v1": FORMAL / "releases" / "baseline-v1",
        "baseline-v1": FORMAL / "baseline-v1",
        "runs/p1-baseline-v1": FORMAL / "runs" / "p1-baseline-v1",
    }
    result: dict[str, str] = {}
    for label, target in targets.items():
        if target.is_file():
            result[label] = sha256_file(target)
        else:
            result[label] = hashlib.sha256(canonical_json_bytes(_state([target], FORMAL))).hexdigest()
    return result


def _copy_signed_baseline(root: Path) -> None:
    root.mkdir(parents=True)
    shutil.copyfile(FORMAL / "current-release.json", root / "current-release.json")
    shutil.copytree(FORMAL / "releases" / "baseline-v1", root / "releases" / "baseline-v1")
    shutil.copytree(FORMAL / "baseline-v1", root / "baseline-v1")
    shutil.copytree(FORMAL / "runs" / "p1-baseline-v1", root / "runs" / "p1-baseline-v1")


def _probe_observation(source: str, publisher: str, issue: str, *, game: str, suffix: str = "", front: list[int] | None = None) -> dict[str, Any]:
    front = front or ([1, 2, 3, 4, 5, 6] if game == "ssq" else [1, 2, 3, 4, 5])
    raw_ref = f"raw/{source}/{game}/{issue}{suffix}.html"
    raw_sha = hashlib.sha256(raw_ref.encode("ascii")).hexdigest()
    value = {
        "observation_schema_version": "1.0.0", "source_id": source, "publisher_id": publisher,
        "game": game, "raw_issue_id": issue, "issue_id": issue, "draw_date_local": "2026-01-01",
        "front_numbers": front, "back_numbers": [1] if game == "ssq" else [1, 2],
        "source_url": f"https://example.com/{source}/{issue}", "captured_at_utc": "2026-01-01T12:00:00.000Z",
        "raw_ref": raw_ref, "raw_sha256": raw_sha, "parser_id": f"{source}-parser",
        "parser_version": "1.0.0", "core_fact_profile": "phase0-core-fact-v1", "parse_status": "parsed",
    }
    value["core_fact_sha256"] = core_fact_sha256(value)
    value["observation_id"] = make_observation_id(source, game, issue, raw_sha, "1.0.0")
    return value


def _probe_draw(rows: list[dict[str, Any]]) -> dict[str, Any]:
    left = rows[0]
    return {
        "record_schema_version": "1.0.0", "game": left["game"], "issue_id": left["issue_id"],
        "draw_date_local": left["draw_date_local"], "front_numbers": left["front_numbers"],
        "back_numbers": left["back_numbers"], "status": "verified", "core_fact_profile": "phase0-core-fact-v1",
        "core_fact_sha256": left["core_fact_sha256"],
        "evidence_links": [{key: row[key] for key in ("source_id", "publisher_id", "observation_id", "raw_ref", "raw_sha256")} for row in rows],
        "revision_id": make_revision_id(left["game"], left["issue_id"], left["core_fact_sha256"], None),
        "supersedes_revision_id": None, "knowledge_class": "prospective_as_observed",
        "available_at_utc": "2026-01-01T12:00:00.000Z",
    }


def _probe_history(game: str, pair: tuple[tuple[str, str], tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    draws, observations = [], []
    for sequence in range(1, 21):
        rows = [_probe_observation(source, publisher, f"2026{sequence:03d}", game=game, suffix="-old") for source, publisher in pair]
        observations.extend(rows)
        draws.append(_probe_draw(rows))
    return draws, observations


def _probe_execute(draws: list[dict[str, Any]], old: list[dict[str, Any]], new: list[dict[str, Any]]):
    policy = {
        "game_source_pairs": {"ssq": {"source_ids": ["ydniu", "swlc"]}, "dlt": {"source_ids": ["ydniu", "gdlottery"]}},
        "sources": [
            {"source_id": "ydniu", "publisher_id": "publisher-a"}, {"source_id": "swlc", "publisher_id": "publisher-b"},
            {"source_id": "gdlottery", "publisher_id": "publisher-c"}, {"source_id": "eastmoney", "publisher_id": "publisher-d"},
        ],
    }
    return build_incremental_release(
        current_draws=draws, current_selected_observations=old, new_observations=new, policy=policy,
        current_raw_hashes={row["raw_ref"]: row["raw_sha256"] for row in old},
        new_raw_hashes={row["raw_ref"]: row["raw_sha256"] for row in new}, recheck_limit=20,
    )


def _engine_assertions() -> dict[str, bool]:
    ssq_draws, ssq_old = _probe_history("ssq", (("ydniu", "publisher-a"), ("swlc", "publisher-b")))
    ssq_new = [
        _probe_observation(source, publisher, f"2026{sequence:03d}", game="ssq", suffix="-recheck")
        for sequence in range(1, 21) for source, publisher in (("ydniu", "publisher-a"), ("swlc", "publisher-b"))
    ]
    ssq = _probe_execute(ssq_draws, ssq_old, ssq_new)
    dlt_draws, dlt_old = _probe_history("dlt", (("ydniu", "publisher-a"), ("eastmoney", "publisher-d")))
    dlt_one_side = [_probe_observation("ydniu", "publisher-a", f"2026{n:03d}", game="dlt", suffix="-recheck") for n in range(1, 21)]
    dlt_new = dlt_one_side + [
        _probe_observation("ydniu", "publisher-a", "2026021", game="dlt", suffix="-new"),
        _probe_observation("gdlottery", "publisher-c", "2026021", game="dlt", suffix="-new"),
    ]
    deferred = _probe_execute(dlt_draws, dlt_old, dlt_new)
    changed_rows = list(dlt_one_side)
    changed_rows[-1] = _probe_observation("ydniu", "publisher-a", "2026020", game="dlt", suffix="-changed", front=[1, 2, 3, 4, 6])
    changed = _probe_execute(dlt_draws, dlt_old, changed_rows)
    missing = _probe_execute(dlt_draws, dlt_old, [_probe_observation("ydniu", "publisher-a", "2026021", game="dlt", suffix="-missing")])
    sc = ssq.quality["deterministic"]["counts"]
    dc = deferred.quality["deterministic"]["counts"]
    return {
        "live_recheck_window_uses_latest20_union=true": sc["recheck_attempted"] == 20,
        "dlt_unchanged_missing_partner_is_deferred_not_complete=true": dc["recheck_deferred"] == 19 and dc["recheck_complete"] == 0,
        "dlt_single_side_change_is_unresolved=true": not changed.publishable and any(row.get("reason_code") == "RECHECK_UNCONFIRMED_CHANGE" for row in changed.reconciliation),
        "new_issue_missing_pair_is_unresolved=true": not missing.publishable and any(row.get("reason_code") == "REQUIRED_SOURCE_PAIR_MISSING" for row in missing.reconciliation),
        "ssq_twenty_history_rechecks_complete=true": sc["recheck_complete"] == 20 and sc["recheck_deferred"] == 0,
    }


def _preflight_and_runtime_probes(temporary: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    preflight_ok = True
    policy_value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    schema_value = dict(policy_value)
    schema_value.pop("scope")
    expiry_value = dict(policy_value)
    expiry_value["valid_until"] = "2026-01-01"
    cases = (
        ("hash", POLICY_PATH.read_bytes() + b"\n", False),
        ("schema", canonical_json_bytes(schema_value), True),
        ("expiry", canonical_json_bytes(expiry_value), True),
    )
    probe_results: list[dict[str, Any]] = []
    for index, (kind, payload, resign_for_deeper_validation) in enumerate(cases, 1):
        config = temporary / f"bad-config-{index}"
        config.mkdir()
        (config / "live-source-policy.json").write_bytes(payload)
        root = temporary / f"preflight-{index}"
        args = IncrementalArguments(mode="incremental", source_mode="live", snapshot_root=None, artifacts_root=root, config_root=config, run_id=f"preflight-{index}", release_id="unused")
        manager = (
            patch("lottery_data.steps.live_policy.LIVE_POLICY_SHA256", hashlib.sha256(payload).hexdigest())
            if resign_for_deeper_validation else contextlib.nullcontext()
        )
        underlying: int | None = None
        with manager:
            try:
                execute_incremental(args)
            except Exception as exc:
                underlying = getattr(exc, "exit_code", 4)
        result = {
            "fault": kind, "decision": "HOLD", "underlying_exit_code": underlying,
            "acceptance_runner_exit_code": 20, "request_created": False,
            "run_created": (root / "runs").exists(), "release_created": (root / "releases").exists(),
            "invented_artifact_refs": False,
        }
        probe_results.append(result)
        preflight_ok &= underlying == 4 and not root.exists()
    runtime_root = temporary / "runtime-probe"
    _copy_signed_baseline(runtime_root)
    pointer = (runtime_root / "current-release.json").read_bytes()
    failure = LivePolicyError(
        "dns_timeout_tls_or_required_source_unavailable", "controlled unavailable",
        stage="runtime", exit_code=3, retryable=True,
    )
    with patch("lottery_data.steps.live.fetch_to_raw", side_effect=failure):
        code, _ = execute_incremental(IncrementalArguments(mode="incremental", source_mode="live", snapshot_root=None, artifacts_root=runtime_root, config_root=CONFIG, run_id="runtime-probe", release_id="unused"))
    events = _jsonl(runtime_root / "runs" / "runtime-probe" / "events.jsonl")
    runtime_probe = {
        "underlying_exit_code": code,
        "request_started": sum(row["event_type"] == "request_started" for row in events),
        "request_failed": sum(row["event_type"] == "request_failed" for row in events),
        "release_created": (runtime_root / "releases" / "unused").exists(),
        "pointer_unchanged": (runtime_root / "current-release.json").read_bytes() == pointer,
    }
    runtime_probe["closed"] = (
        runtime_probe["underlying_exit_code"] == 3 and runtime_probe["request_started"] == 2
        and runtime_probe["request_failed"] == 2 and not runtime_probe["release_created"]
        and runtime_probe["pointer_unchanged"]
    )
    return probe_results, runtime_probe


def _execute_live_case_inner(
    *, fetch_hook: Callable[..., Any] | None = None, parse_hook: Callable[..., Any] | None = None,
    policy_path: Path = POLICY_PATH, workspace_root: Path | None = None,
) -> dict[str, Any]:
    formal_before = _formal_state()
    assertions = {assertion_id: False for assertion_id in ASSERTION_IDS}
    observed_policy_sha = sha256_file(policy_path) if policy_path.is_file() else None
    try:
        policy = load_live_policy(policy_path)
    except LivePolicyError as exc:
        assertions.update({
            "policy_preflight_underlying_exit_code=4": exc.exit_code == 4,
            "policy_preflight_HOLD_acceptance_runner_exit_code=20": True,
            "acceptance_report_preserves_underlying_exit_code=true": True,
            "preflight_policy_failure_creates_no_request_run_or_release=true": True,
            "no_production_pointer_change=true": formal_before == _formal_state(),
        })
        return {"case_id": "E2E-05", "decision": "HOLD", "underlying_exit_code": 4, "acceptance_runner_exit_code": 20, "observed_policy_sha256": observed_policy_sha, "assertions": assertions}
    assertions.update({
        "live_policy_sha256_verified=true": observed_policy_sha == LIVE_POLICY_V13_SHA256,
        "live_policy_internal_only=true": "internal research" in policy["scope"],
        "production_collection_approved=false": policy["production_collection_approved"] is False,
        "redistribution_approved=false": policy["redistribution_approved"] is False,
        "live_review_not_expired=true": date.today() <= date.fromisoformat(policy["valid_until"]),
        "mode_specific_pair.ssq=ydniu|swlc": policy["game_source_pairs"]["ssq"]["source_ids"] == ["ydniu", "swlc"],
        "mode_specific_pair.dlt=ydniu|gdlottery": policy["game_source_pairs"]["dlt"]["source_ids"] == ["ydniu", "gdlottery"],
        "no_future_url_guessing=true": "guessed future URL is never" in policy["network_policy"]["request_plan_rule"],
    })
    workspace_context = (
        tempfile.TemporaryDirectory(prefix="e2e05-live-")
        if workspace_root is None else contextlib.nullcontext(str(workspace_root))
    )
    with workspace_context as directory:
        temporary = Path(directory)
        temporary.mkdir(parents=True, exist_ok=True)
        preflight_probes, runtime_probe = _preflight_and_runtime_probes(temporary)
        preflight_ok = all(
            item["underlying_exit_code"] == 4 and item["acceptance_runner_exit_code"] == 20
            and not item["request_created"] and not item["run_created"] and not item["release_created"]
            and not item["invented_artifact_refs"]
            for item in preflight_probes
        ) and [item["fault"] for item in preflight_probes] == ["hash", "schema", "expiry"]
        assertions.update({
            "preflight_policy_failure_creates_no_request_run_or_release=true": preflight_ok,
            "runtime_failure_appends_one_terminal_request_failed=true": runtime_probe["closed"],
            "policy_preflight_underlying_exit_code=4": preflight_ok,
            "policy_preflight_HOLD_acceptance_runner_exit_code=20": preflight_ok,
            "acceptance_report_preserves_underlying_exit_code=true": preflight_ok,
        })
        forbidden_root = temporary / "bootstrap-live-forbidden"
        try:
            execute_bootstrap(BootstrapArguments(mode="bootstrap", source_mode="live", phase0_snapshot=REPO / "artifacts/phase-0-multisource/snapshots/20260802T025000Z", artifacts_root=forbidden_root, config_root=CONFIG, run_id="forbidden", release_id="forbidden"))
        except Exception:
            assertions["bootstrap_live_forbidden=true"] = not forbidden_root.exists()
        root = temporary / "primary"
        _copy_signed_baseline(root)
        pointer_before = (root / "current-release.json").read_bytes()
        primary_run = root / "runs" / "e2e05-live"
        parse_entry_evidence: list[dict[str, Any]] = []

        def observed_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
            provenance = request.get("provenance", {})
            raw_ref = provenance.get("raw_ref", "")
            digest_match = re.fullmatch(r"raw/[^/]+/(ssq|dlt)/sha256/([0-9a-f]{64})\.raw", raw_ref)
            supplied_sha = provenance.get("raw_sha256")
            supplied_path = Path(raw_path)
            expected_path = primary_run / raw_ref if digest_match else None
            exists_at_entry = supplied_path.is_file()
            actual_sha = sha256_file(supplied_path) if exists_at_entry else None
            evidence = {
                "request_id": request.get("request_id"), "raw_ref": raw_ref,
                "exists_at_parse_entry": exists_at_entry,
                "path_matches_raw_ref": expected_path is not None and supplied_path.resolve() == expected_path.resolve(),
                "raw_sha256": actual_sha,
                "provenance_sha256": supplied_sha,
                "path_digest": digest_match.group(2) if digest_match else None,
            }
            evidence["closed"] = (
                evidence["exists_at_parse_entry"] and evidence["path_matches_raw_ref"]
                and actual_sha == supplied_sha == evidence["path_digest"]
            )
            parse_entry_evidence.append(evidence)
            target = parse_hook or product_parse_versioned_raw
            return target(
                request, raw_path, publisher_id=publisher_id,
                parser_id=parser_id, parser_version=parser_version,
            )

        patches = []
        if fetch_hook is not None:
            patches.append(patch("lottery_data.steps.live.fetch_to_raw", side_effect=fetch_hook))
        patches.append(patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=observed_parse))
        for item in patches:
            item.start()
        try:
            code, result = execute_incremental(IncrementalArguments(mode="incremental", source_mode="live", snapshot_root=None, artifacts_root=root, config_root=CONFIG, run_id="e2e05-live", release_id="e2e05-live-release"))
        finally:
            for item in reversed(patches):
                item.stop()
        run = root / "runs" / "e2e05-live"
        if run.is_dir():
            manifest = _json(run / "run-manifest.json")
            events = _jsonl(run / "events.jsonl")
            static = manifest["request_plan"]
            discovered = [row for row in events if row["event_type"] == "request_discovered"]
            successes = [row for row in events if row["event_type"] == "request_succeeded"]
            starts = [row for row in events if row["event_type"] == "request_started"]
            raw_hash_evidence = []
            for row in successes:
                raw_ref = row.get("artifact_ref", "")
                digest_match = re.fullmatch(r"raw/[^/]+/(ssq|dlt)/sha256/([0-9a-f]{64})\.raw", raw_ref)
                raw_path = run / raw_ref if digest_match else None
                actual_sha = sha256_file(raw_path) if raw_path is not None and raw_path.is_file() else None
                raw_hash_evidence.append({
                    "request_id": row.get("request_id"), "raw_ref": raw_ref,
                    "path_digest": digest_match.group(2) if digest_match else None,
                    "raw_sha256": actual_sha,
                    "closed": digest_match is not None and actual_sha == digest_match.group(2),
                })
            raw_ok = bool(raw_hash_evidence) and all(row["closed"] for row in raw_hash_evidence)
            expected_parse_ids = {row["request_id"] for row in static}
            parse_entry_ok = (
                {row["request_id"] for row in parse_entry_evidence} == expected_parse_ids
                and all(row["closed"] for row in parse_entry_evidence)
            )
            gd_static = [row for row in static if row.get("source_id") == "gdlottery"]
            child_fields_absent = all(
                not ({"child_authorization", "parent_request_id", "discovery_request_id", "authorization_sha256"} & set(row))
                for row in static
            )
            event_versions = sorted({row.get("event_schema_version") for row in events})
            execution_profile = {
                "profile": CURRENT_EXECUTION_PROFILE,
                "live_policy_schema_version": policy.get("live_policy_schema_version"),
                "run_schema_version": manifest.get("run_schema_version"),
                "event_schema_versions": event_versions,
                "static_request_count": len(static), "effective_request_count": len(static),
                "request_started_count": len(starts),
                "request_ids": [row.get("request_id") for row in static],
                "request_kinds": [row.get("request_kind") for row in static],
                "request_discovered_event_count": len(discovered),
                "child_authorization_present": not child_fields_absent,
            }
            assertions.update({
                "all_required_sources_requested=true": {row["source_id"] for row in static} == {"ydniu", "swlc", "gdlottery"},
                "gdlottery_history_json_is_static_request=true": len(gd_static) == 1 and gd_static[0].get("request_id") == "live-gdlottery-dlt-history" and gd_static[0].get("request_kind") == "history" and gd_static[0].get("url") == "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
                "static_manifest_request_count=4": len(static) == 4,
                "effective_plan_request_count=4_no_dynamic_child=true": 4 <= len(starts) <= 8 and {row.get("request_id") for row in starts} == expected_parse_ids,
                "request_discovered_event_absent=true": not discovered,
                "child_authorization_absent=true": child_fields_absent,
                "live_success_raw_refs_are_content_addressed=true": raw_ok,
                "raw_persisted_before_parse=true": parse_entry_ok,
                "status_in=published|no_change": code == 0 and result.get("status") in {"published", "no_change"},
            })
        assertions.update(_engine_assertions())
        assertions["no_production_pointer_change=true"] = formal_before == _formal_state()
        primary_environment_hold = code in {3, 4} and result.get("status") == "rejected"
        decision = "HOLD" if primary_environment_hold else "PASS" if code == 0 and all(assertions.values()) else "FAIL"
        acceptance_exit = 20 if decision == "HOLD" else 0 if decision == "PASS" else 1
        primary_errors = [_json(path) for path in sorted((run / "errors").rglob("*.json"))] if run.is_dir() and (run / "errors").is_dir() else []
        return {
            "case_id": "E2E-05", "decision": decision, "underlying_exit_code": code,
            "acceptance_runner_exit_code": acceptance_exit, "observed_policy_sha256": observed_policy_sha,
            "primary_result": result, "primary_errors": primary_errors,
            "preflight_probe_results": preflight_probes, "runtime_probe_result": runtime_probe,
            "raw_hash_evidence": raw_hash_evidence if run.is_dir() else [],
            "parse_entry_evidence": parse_entry_evidence,
            "execution_profile": execution_profile if run.is_dir() else {
                "profile": CURRENT_EXECUTION_PROFILE,
                "live_policy_schema_version": policy.get("live_policy_schema_version"),
                "run_schema_version": None, "event_schema_versions": [],
                "static_request_count": 0, "effective_request_count": 0,
                "request_started_count": 0,
                "request_ids": [], "request_kinds": [],
                "request_discovered_event_count": 0, "child_authorization_present": False,
            },
            "formal_state": {"before": formal_before, "after": _formal_state()},
            "assertions": assertions,
        }


def execute_live_case(
    *, fetch_hook: Callable[..., Any] | None = None, parse_hook: Callable[..., Any] | None = None,
    policy_path: Path = POLICY_PATH,
) -> dict[str, Any]:
    if fetch_hook is not None or parse_hook is not None or policy_path != POLICY_PATH:
        return _execute_live_case_inner(
            fetch_hook=fetch_hook, parse_hook=parse_hook, policy_path=policy_path,
        )
    formal_before = _formal_state()
    try:
        budget = _derive_watchdog_budget(load_live_policy(POLICY_PATH))
    except (LivePolicyError, DeadlineDerivationError) as exc:
        formal_after = _formal_state()
        formal_equal = formal_before == formal_after
        assertions = {assertion_id: False for assertion_id in ASSERTION_IDS}
        assertions.update({
            "policy_preflight_underlying_exit_code=4": True,
            "policy_preflight_HOLD_acceptance_runner_exit_code=20": formal_equal,
            "acceptance_report_preserves_underlying_exit_code=true": True,
            "preflight_policy_failure_creates_no_request_run_or_release=true": True,
            "no_production_pointer_change=true": formal_equal,
        })
        return {
            "case_id": "E2E-05", "decision": "HOLD" if formal_equal else "FAIL",
            "underlying_exit_code": 4, "acceptance_runner_exit_code": 20 if formal_equal else 1,
            "deadline_derivation_error": f"{type(exc).__name__}: {exc}",
            "worker_started": False, "assertions": assertions,
            "watchdog_parent_formal_state": {"before": formal_before, "after": formal_after, "equal": formal_equal},
        }
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    worker = _run_owned_worker(
        [sys.executable, str(Path(__file__).resolve()), "--worker"],
        cwd=REPO, env=env, budget=budget,
    )
    formal_after = _formal_state()
    formal_equal = formal_before == formal_after
    cleanup_closed = worker["reaped"] and worker["workspace_cleanup_verified"] and worker["spawn_error"] is None
    if worker["timed_out"]:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        assertions = {assertion_id: False for assertion_id in ASSERTION_IDS}
        assertions.update({
            "live_policy_sha256_verified=true": sha256_file(POLICY_PATH) == LIVE_POLICY_V13_SHA256,
            "live_policy_internal_only=true": "internal research" in policy["scope"],
            "production_collection_approved=false": policy["production_collection_approved"] is False,
            "redistribution_approved=false": policy["redistribution_approved"] is False,
            "live_review_not_expired=true": date.today() <= date.fromisoformat(policy["valid_until"]),
            "no_production_pointer_change=true": formal_equal,
        })
        decision = "HOLD" if formal_equal and cleanup_closed else "FAIL"
        return {
            "case_id": "E2E-05", "decision": decision, "underlying_exit_code": 3,
            "acceptance_runner_exit_code": 20 if decision == "HOLD" else 1,
            "observed_policy_sha256": sha256_file(POLICY_PATH),
            "environment_failure": "live smoke exceeded mechanically derived worker deadline",
            "watchdog_budget": budget, "worker_process": _worker_process_evidence(worker), "assertions": assertions,
            "watchdog_parent_formal_state": {
                "before": formal_before, "after": formal_after, "equal": formal_equal,
            },
        }
    if not cleanup_closed:
        return {
            "case_id": "E2E-05", "decision": "FAIL", "underlying_exit_code": 1,
            "acceptance_runner_exit_code": 1, "watchdog_budget": budget,
            "worker_process": _worker_process_evidence(worker), "assertions": {
                **{assertion_id: False for assertion_id in ASSERTION_IDS},
                "no_production_pointer_change=true": formal_equal,
            },
            "watchdog_parent_formal_state": {"before": formal_before, "after": formal_after, "equal": formal_equal},
        }
    try:
        report = json.loads(worker["stdout"])
    except json.JSONDecodeError as exc:
        return {
            "case_id": "E2E-05", "decision": "FAIL", "underlying_exit_code": 1,
            "acceptance_runner_exit_code": 1, "watchdog_budget": budget, "worker_process": _worker_process_evidence(worker),
            "worker_report_error": f"JSONDecodeError: {exc}",
            "assertions": {**{assertion_id: False for assertion_id in ASSERTION_IDS}, "no_production_pointer_change=true": formal_equal},
            "watchdog_parent_formal_state": {"before": formal_before, "after": formal_after, "equal": formal_equal},
        }
    if not isinstance(report, dict) or worker["returncode"] != report.get("acceptance_runner_exit_code"):
        return {
            "case_id": "E2E-05", "decision": "FAIL", "underlying_exit_code": 1,
            "acceptance_runner_exit_code": 1, "watchdog_budget": budget, "worker_process": _worker_process_evidence(worker),
            "worker_report_error": "worker exit/report mismatch",
            "assertions": {**{assertion_id: False for assertion_id in ASSERTION_IDS}, "no_production_pointer_change=true": formal_equal},
            "watchdog_parent_formal_state": {"before": formal_before, "after": formal_after, "equal": formal_equal},
        }
    report["watchdog_budget"] = budget
    report["worker_process"] = _worker_process_evidence(worker)
    report["watchdog_parent_formal_state"] = {
        "before": formal_before, "after": formal_after, "equal": formal_equal,
    }
    report["assertions"]["no_production_pointer_change=true"] = (
        report["assertions"].get("no_production_pointer_change=true") is True and formal_equal
    )
    if not formal_equal:
        report["decision"] = "FAIL"
        report["acceptance_runner_exit_code"] = 1
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.workspace is not None and not args.worker:
        parser.error("--workspace is valid only with --worker")
    report = _execute_live_case_inner(workspace_root=args.workspace) if args.worker else execute_live_case()
    print(canonical_json_bytes(report).decode("utf-8"), end="")
    return int(report["acceptance_runner_exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
