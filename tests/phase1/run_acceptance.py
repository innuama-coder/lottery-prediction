from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lottery_data.models import validate_object  # noqa: E402
from lottery_data.serialization import sha256_file  # noqa: E402
from e2e05_live_case import (  # noqa: E402
    ASSERTION_IDS as E2E05_ASSERTION_IDS,
    CONTRACT_TO_CURRENT_ASSERTION_IDS as E2E05_CONTRACT_TO_CURRENT_IDS,
    CURRENT_EXECUTION_PROFILE as E2E05_EXECUTION_PROFILE,
    CURRENT_STATIC_REQUEST_IDS as E2E05_STATIC_REQUEST_IDS,
    LEGACY_CONTRACT_ASSERTION_IDS as E2E05_CONTRACT_ASSERTION_IDS,
    execute_live_case,
)
from test_specification import g1_assertion  # noqa: E402


PASS, FAIL, HOLD = 0, 1, 20
FREEZE_PATH = REPO / "tests" / "phase1" / "fixtures" / "spec" / "spec-bundle-freeze.json"
FORMAL_ROOT = REPO / "artifacts" / "phase-1"
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
CONTRACT_PATH = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
RELEASE_FILES = ("draws.jsonl", "hashes.json", "manifest.json", "observations.jsonl", "quality-report.json")
RELEASE_HASH_ENTRIES = frozenset({"draws.jsonl", "manifest.json", "observations.jsonl", "quality-report.json"})
LEGACY_CONFIG_HASHES = {
    "config/phase1/collection-policy.json": "79c2f55d93d3458602122f51148c96073f3dc4a809f95bc3cc7041e0a983e760",
    "config/phase1/source-catalog.json": "b0a30f0a6c90744043cb74ed504db161b3456c9607618a522237cf28487b36fa",
}
CURRENT_CONFIG_HASHES = {
    "config/collection-policy.json": LEGACY_CONFIG_HASHES["config/phase1/collection-policy.json"],
    "config/source-catalog.json": LEGACY_CONFIG_HASHES["config/phase1/source-catalog.json"],
}
EXPECTED_G2_ARGV = [
    ["{python}", "tests/phase1/run_acceptance.py", "--contract", "docs/roadmap/phase-1-acceptance-contract.json", "--execute-case", "E2E-01"],
    ["{python}", "tests/phase1/run_acceptance.py", "--contract", "docs/roadmap/phase-1-acceptance-contract.json", "--execute-case", "E2E-02"],
    ["{python}", "-m", "lottery_data", "verify", "--release-id", "baseline-v1", "--artifacts-root", "artifacts/phase-1"],
]
EXPECTED_G2_ASSERTIONS = [
    "draw_records=400", "source_observations=800", "records_by_game.ssq=200",
    "records_by_game.dlt=200", "unique_game_issue=400", "invalid=0", "missing=0",
    "duplicates=0", "conflicts=0", "manual_core_edits=0",
    "phase0_core_fact_mismatches=0", "raw_hash_mismatches=0",
    "independent_bootstrap_hash_match=true", "snapshot_incremental_status=no_change",
]
EXPECTED_G3_ARGV = [
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_workflow_unit.py", "-v"],
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_workflow_e2e.py", "-v"],
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_live_execution_v12_spec.py", "-v"],
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_live_v12_workflow.py", "-v"],
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_live_v12_verify_recovery.py", "-v"],
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_e2e_live_case_contract.py", "-v"],
    ["{python}", "-m", "unittest", "discover", "-s", "tests/phase1", "-p", "test_acceptance_g3.py", "-v"],
    *[["{python}", "tests/phase1/run_acceptance.py", "--contract", "docs/roadmap/phase-1-acceptance-contract.json", "--execute-case", case]
      for case in ("E2E-03", "E2E-04", "E2E-05", "E2E-06", "E2E-07")],
]
EXPECTED_G3_ASSERTIONS = [
    "module_and_console_entrypoints_are_equivalent", "configuration_input_hashes_match_contract",
    "documentation_contract_hashes_and_profile_boundaries_match", "final_acceptance_contract_is_executable",
    "snapshot_and_live_source_policies_are_mode_separated", "G1_G2_baseline_ids_and_hashes_unchanged",
    "live_policy_fail_closed_validation_passes", "live_pairs_are_game_specific_and_publishers_are_distinct",
    "live_review_expiry_cli_exit_4_maps_to_HOLD_runner_exit_20", "preflight_and_runtime_failure_effects_are_distinct",
    "preflight_failure_has_no_persisted_run_or_fake_artifact_refs", "live_v1_3_schema_hashes_match_contract",
    "legacy_v1_1_policy_and_schema_bytes_remain_frozen", "original_six_v1_schema_hashes_remain_frozen",
    "live_effective_plan_and_event_stream_validator_pass", "live_plan_has_exactly_four_static_history_requests",
    "live_discovery_and_child_events_are_forbidden", "live_success_raw_refs_are_content_addressed",
    "live_raw_is_persisted_and_hash_closed_before_parse", "live_recheck_deferred_and_unconfirmed_change_rules_pass",
    "live_recheck_quality_counters_are_truthful", "gd_history_fixture_count_latest20_and_26084_26086_closure_are_evidenced",
    "gd_json_two_mib_cap_is_endpoint_specific", "future_url_guessing_is_rejected_before_request",
    "incremental_policy_matches_contract", "revision_is_append_only", "failed_runs_preserve_evidence",
    "failed_runs_do_not_create_release", "atomic_pointer_update_passes", "lock_and_compare_and_swap_tests_pass",
    "crash_recovery_test_passes", "offline_replay_uses_no_network",
    "offline_replay_stable_read_inventory_guard_passes", "offline_replay_concurrent_change_exits_5",
    "E2E-01..E2E-07_pass", "current_acceptance_session_live_smoke=PASS",
    "data_review.blocking_findings=0", "workflow_review.blocking_findings=0",
]
REVIEW_SCOPES = {"data-review.json": "data", "workflow-review.json": "workflow"}
EXTENDED_CASES = {
    "E2E-03": {
        "tests": ["tests.phase1.test_snapshot_delta_e2e.SnapshotDeltaE2ETests.test_trusted_seed_adds_exactly_one_then_second_run_is_no_change"],
        "evidence": [REPO / "tests/phase1/test_snapshot_delta_e2e.py", REPO / "tests/phase1/fixtures/real/e2e03-seed.json"],
        "facts": [("added=1", 1), ("old_release_unchanged=true", True), ("second_run_status=no_change", "no_change")],
    },
    "E2E-04": {
        "tests": ["tests.phase1.test_e2e_fallback_case.RealFallbackCaseTests.test_real_snapshot_bootstrap_closes_both_dlt_fallbacks"],
        "evidence": [REPO / "tests/phase1/test_e2e_fallback_case.py", REPO / "tests/phase1/fixtures/real/e2e04-fallback.json"],
        "facts": [("eastmoney_missing=2", 2), ("ydniu_gdlottery_agreement=2", 2), ("fallback_rule_recorded=2", 2), ("published_issues=2", 2)],
    },
    "E2E-06": {
        "tests": [
            "tests.phase1.test_e2e_replay_case.OfflineReplayEndToEndTests.test_real_cli_offline_replay_is_deterministic_and_non_publishing",
            "tests.phase1.test_e2e_replay_case.OfflineReplayEndToEndTests.test_concurrent_source_mutation_fails_closed_and_next_startup_recovers",
        ],
        "evidence": [REPO / "tests/phase1/test_e2e_replay_case.py"],
        "facts": [
            ("network_requests=0", 0), ("publication_operations=0", 0),
            ("deterministic_hash_mismatches=0", 0), ("decision_equal=true", True),
            ("stable_read_inventory_guard=true", True), ("concurrent_input_change_exit=5", 5),
        ],
    },
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("phase") != 1 or not isinstance(value.get("gates"), list):
        raise ValueError("not a Phase 1 acceptance contract")
    return value


def _frozen_snapshot(contract: dict[str, Any]) -> Path:
    relative = contract.get("input_freeze", {}).get("root")
    if not isinstance(relative, str):
        raise ValueError("contract input_freeze.root is required")
    resolved = (REPO / relative).resolve()
    if resolved != SNAPSHOT.resolve() or not resolved.is_dir():
        raise ValueError(f"contract frozen snapshot identity mismatch: {resolved}")
    return resolved


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(canonical_bytes(report))
    temporary.replace(path)


def _hash(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _evidence(paths: Iterable[Path]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for path in paths:
        try:
            key = path.resolve().relative_to(REPO).as_posix()
        except ValueError:
            key = str(path.resolve())
        result[key] = _hash(path)
    return dict(sorted(result.items()))


def _assertion(
    output: list[dict[str, Any]], assertion_id: str, expected: Any, actual: Any, evidence: Iterable[Path] = (),
) -> bool:
    passed = actual == expected
    output.append({
        "id": assertion_id,
        "status": "PASS" if passed else "FAIL",
        "expected": expected,
        "actual": actual,
        "evidence_sha256": _evidence(evidence),
    })
    return passed


def _json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix())
    }


def _formal_state(*, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not FORMAL_ROOT.exists():
        return result
    for path in sorted(FORMAL_ROOT.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(FORMAL_ROOT).as_posix()
        if relative in exclude:
            continue
        if path.is_symlink():
            result[relative] = {"type": "symlink", "target": os.readlink(path)}
        elif path.is_dir():
            result[relative] = {"type": "directory"}
        elif path.is_file():
            result[relative] = {"type": "file", "sha256": sha256_file(path)}
        else:
            result[relative] = {"type": "other"}
    return result


def _safe_temp_root(path: Path) -> None:
    resolved = path.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved == temp_root or temp_root not in resolved.parents:
        raise ValueError(f"E2E root is not inside the system temporary directory: {resolved}")
    if resolved == REPO or REPO in resolved.parents or resolved == FORMAL_ROOT or FORMAL_ROOT in resolved.parents:
        raise ValueError(f"E2E root overlaps repository/formal artifacts: {resolved}")


def _network_guard(root: Path) -> dict[str, str]:
    guard = root / "network-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket\n"
        "def _deny(*args, **kwargs): raise RuntimeError('network forbidden by Phase 1 snapshot acceptance')\n"
        "socket.create_connection = _deny\n"
        "socket.getaddrinfo = _deny\n"
        "socket.gethostbyname = _deny\n"
        "socket.gethostbyname_ex = _deny\n"
        "socket.getnameinfo = _deny\n"
        "socket.socket.connect = _deny\n"
        "socket.socket.connect_ex = _deny\n"
        "socket.socket.sendto = _deny\n"
        "if hasattr(socket.socket, 'sendmsg'): socket.socket.sendmsg = _deny\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(guard), str(REPO / "src"), existing) if value
    )
    environment["LOTTERY_DATA_NETWORK_DISABLED"] = "1"
    return environment


def _parse_single_json(stdout: str) -> dict[str, Any]:
    lines = stdout.splitlines()
    if len(lines) != 1:
        raise ValueError(f"expected exactly one stdout JSON line, got {len(lines)}")
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise ValueError("stdout JSON must be an object")
    return value


def _run(argv: list[str], environment: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    completed = subprocess.run(argv, cwd=REPO, env=environment, text=True, capture_output=True, check=False)
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    try:
        parsed = _parse_single_json(completed.stdout)
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
    command = {
        "argv": argv,
        "expected_exit_code": 0,
        "actual_exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "stderr": completed.stderr,
        "stdout_parse_error": parse_error,
        "result": parsed,
        "status": "PASS" if completed.returncode == 0 and parsed is not None else "FAIL",
    }
    return command, parsed


def _hash_manifest_valid(path: Path, base: Path, expected_paths: frozenset[str], disk_root: Path | None = None) -> bool:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        entries = manifest["entries"]
        if manifest.get("hash_manifest_schema_version") != "1.0.0" or manifest.get("hash_profile") != "sha256-file-manifest-v1" or not isinstance(entries, list) or not entries:
            return False
        names = [entry["path"] for entry in entries]
        if names != sorted(names) or len(names) != len(set(names)) or set(names) != expected_paths:
            return False
        resolved_base = base.resolve()
        for entry in entries:
            relative = entry["path"]
            relative_path = Path(relative)
            if not isinstance(relative, str) or not relative or "\\" in relative or relative_path.is_absolute() or ".." in relative_path.parts or relative_path.as_posix() != relative:
                return False
            target = base / relative_path
            if any(part.is_symlink() for part in (target, *target.parents) if part != base.parent):
                return False
            resolved = target.resolve()
            if resolved_base not in resolved.parents or not resolved.is_file():
                return False
            if resolved.stat().st_size != entry["size_bytes"] or sha256_file(resolved) != entry["sha256"]:
                return False
        disk_files = {
            item.relative_to(base).as_posix()
            for item in (disk_root or base).rglob("*")
            if item.is_file() and item.resolve() != path.resolve()
        }
        return disk_files == expected_paths
    except Exception:
        return False


def _evidence_bijection_valid(draws: list[dict[str, Any]], selected: list[dict[str, Any]], all_observations: list[dict[str, Any]]) -> bool:
    selected_by_id = {row["observation_id"]: row for row in selected}
    all_by_id = {row["observation_id"]: row for row in all_observations}
    if len(selected_by_id) != len(selected) or len(all_by_id) != len(all_observations) or not set(selected_by_id) <= set(all_by_id):
        return False
    if any(selected_by_id[key] != all_by_id[key] for key in selected_by_id):
        return False
    used: list[str] = []
    for draw in draws:
        links = draw["evidence_links"]
        if len(links) != 2 or len({link["publisher_id"] for link in links}) != 2:
            return False
        for link in links:
            observation = selected_by_id.get(link["observation_id"])
            if observation is None or any(observation[key] != link[key] for key in ("source_id", "publisher_id", "raw_ref", "raw_sha256")):
                return False
            if any(observation[key] != draw[key] for key in ("game", "issue_id", "core_fact_sha256")):
                return False
            used.append(link["observation_id"])
    return len(used) == 800 and len(set(used)) == 800 and set(used) == set(selected_by_id)


def _events_valid(events: list[dict[str, Any]], run_manifest: dict[str, Any], run_id: str, terminal: str = "run_published") -> bool:
    if len(events) != 63 or [row.get("sequence") for row in events] != list(range(1, 64)) or any(row.get("run_id") != run_id for row in events):
        return False
    if Counter(row.get("event_type") for row in events) != Counter({"request_started": 30, "request_succeeded": 30, "run_planned": 1, "run_started": 1, terminal: 1}):
        return False
    if [events[0]["event_type"], events[1]["event_type"]] != ["run_planned", "run_started"]:
        return False
    plan = {row["request_id"]: row for row in run_manifest["request_plan"]}
    if len(plan) != 30:
        return False
    for request_id, request in plan.items():
        related = [row for row in events if row.get("request_id") == request_id]
        started = [row for row in related if row["event_type"] == "request_started"]
        request_terminal = [row for row in related if row["event_type"] in {"request_succeeded", "request_failed"}]
        if len(started) != 1 or len(request_terminal) != 1 or started[0]["sequence"] >= request_terminal[0]["sequence"]:
            return False
        if any(row.get("source_id") != request["source_id"] or row.get("game") != request["game"] or row.get("attempt") != 1 for row in (started[0], request_terminal[0])):
            return False
    run_terminals = [row for row in events if row["event_type"] in {"run_published", "run_no_change", "run_rejected", "run_interrupted"}]
    return len(run_terminals) == 1 and run_terminals[0] is events[-1] and run_terminals[0]["event_type"] == terminal


def _identity_closure_valid(
    *, run_id: str, release_id: str | None, mode: str, run_manifest: dict[str, Any],
    result: dict[str, Any], stdout_result: dict[str, Any], quality: dict[str, Any],
    events: list[dict[str, Any]], release_manifest: dict[str, Any] | None = None,
) -> bool:
    status = "published" if mode == "bootstrap" else "no_change"
    terminal = "run_published" if mode == "bootstrap" else "run_no_change"
    exact_refs = {
        "manifest_ref": f"runs/{run_id}/run-manifest.json",
        "events_ref": f"runs/{run_id}/events.jsonl",
        "quality_report_ref": f"runs/{run_id}/quality-report.json",
    }
    valid = (
        run_manifest.get("run_id") == run_id
        and run_manifest.get("mode") == mode
        and result == stdout_result
        and result.get("run_id") == run_id
        and result.get("release_id") == release_id
        and result.get("mode") == mode
        and result.get("status") == status
        and result.get("exit_code") == 0
        and all(result.get(key) == value for key, value in exact_refs.items())
        and quality.get("run_id") == run_id
        and _events_valid(events, run_manifest, run_id, terminal)
    )
    if mode == "bootstrap":
        valid = valid and release_manifest is not None and (
            release_manifest.get("input_run_id") == run_id
            and release_manifest.get("release_id") == release_id
            and release_manifest.get("status") == "published"
            and release_manifest.get("quality_report_ref") == f"releases/{release_id}/quality-report.json"
        )
    else:
        valid = valid and release_manifest is None and release_id is None
    return bool(valid)


def _run_artifact_semantics_valid(
    root: Path, run_id: str, quality: dict[str, Any], result: dict[str, Any],
    current_release_id: str, *, include_quality_result_hash: bool,
) -> bool:
    run = root / "runs" / run_id
    expected_counts = {
        "draws": 400, "parsed_observations": 1042, "selected_observations": 800,
        "ssq": 200, "dlt": 200, "invalid": 0, "missing": 0, "duplicate": 0,
        "conflict": 0, "manual_core_edit": 0,
    }
    expected_quality_hashes = {
        "draws": sha256_file(run / "candidate-draws.jsonl"),
        "run_observations": sha256_file(run / "observations.jsonl"),
        "release_observations": sha256_file(root / "releases" / current_release_id / "observations.jsonl"),
        "reconciliation": sha256_file(run / "reconciliation.jsonl"),
    }
    expected_result_hashes = {
        "candidate_draws": sha256_file(run / "candidate-draws.jsonl"),
        "events": sha256_file(run / "events.jsonl"),
        "observations": sha256_file(run / "observations.jsonl"),
        "reconciliation": sha256_file(run / "reconciliation.jsonl"),
        "run_manifest": sha256_file(run / "run-manifest.json"),
    }
    if include_quality_result_hash:
        expected_result_hashes["quality_report"] = sha256_file(run / "quality-report.json")
    deterministic = quality.get("deterministic", {})
    return (
        quality.get("run_id") == run_id
        and deterministic.get("counts") == expected_counts
        and deterministic.get("output_hashes") == expected_quality_hashes
        and result.get("deterministic_artifact_hashes") == expected_result_hashes
    )


def _bootstrap_config_profile(run: Path, run_manifest: dict[str, Any]) -> tuple[str | None, frozenset[str]]:
    """Return the one exact supported bootstrap profile and its run-owned config refs."""
    declared = run_manifest.get("config_files")
    if not isinstance(declared, list) or len(declared) != 2:
        return None, frozenset()
    by_ref: dict[str, dict[str, Any]] = {}
    for item in declared:
        if (
            not isinstance(item, dict) or set(item) != {"ref", "sha256"}
            or not isinstance(item.get("ref"), str) or not isinstance(item.get("sha256"), str)
            or item["ref"] in by_ref
        ):
            return None, frozenset()
        by_ref[item["ref"]] = item
    hashes = {ref: item["sha256"] for ref, item in by_ref.items()}
    if set(by_ref) == set(LEGACY_CONFIG_HASHES):
        valid = hashes == LEGACY_CONFIG_HASHES and all(
            (REPO / ref).is_file() and sha256_file(REPO / ref) == digest
            for ref, digest in LEGACY_CONFIG_HASHES.items()
        )
        return ("legacy", frozenset()) if valid else (None, frozenset())
    if set(by_ref) == set(CURRENT_CONFIG_HASHES):
        valid = hashes == CURRENT_CONFIG_HASHES and all(
            (run / ref).is_file() and sha256_file(run / ref) == digest
            for ref, digest in CURRENT_CONFIG_HASHES.items()
        )
        return ("current", frozenset(CURRENT_CONFIG_HASHES)) if valid else (None, frozenset())
    return None, frozenset()


def _pointer_valid(root: Path, release_id: str, run_id: str) -> bool:
    try:
        pointer = json.loads((root / "current-release.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    expected = {
        "release_id": release_id,
        "manifest_ref": f"releases/{release_id}/manifest.json",
        "manifest_sha256": sha256_file(root / "releases" / release_id / "manifest.json"),
        "updated_by_run_id": run_id,
    }
    return all(pointer.get(key) == value for key, value in expected.items())


def _release_oracle(root: Path, release_id: str, run_id: str, frozen_snapshot: Path, stdout_result: dict[str, Any] | None = None) -> tuple[bool, list[dict[str, Any]], dict[str, str]]:
    assertions: list[dict[str, Any]] = []
    release = root / "releases" / release_id
    projection = root / release_id
    run = root / "runs" / run_id
    release_paths = [release / name for name in RELEASE_FILES]
    projection_paths = [projection / name for name in RELEASE_FILES]
    passed = _assertion(assertions, "release_files", list(RELEASE_FILES), sorted(p.name for p in release.iterdir() if p.is_file()) if release.is_dir() else None, release_paths) 
    passed &= _assertion(assertions, "projection_files", list(RELEASE_FILES), sorted(p.name for p in projection.iterdir() if p.is_file()) if projection.is_dir() else None, projection_paths)
    byte_equal = release.is_dir() and projection.is_dir() and all(
        (release / name).is_file() and (projection / name).is_file()
        and sha256_file(release / name) == sha256_file(projection / name) for name in RELEASE_FILES
    )
    passed &= _assertion(assertions, "release_projection_byte_equal", True, byte_equal, release_paths + projection_paths)
    try:
        draws = _json_lines(release / "draws.jsonl")
        selected = _json_lines(release / "observations.jsonl")
        all_observations = _json_lines(run / "observations.jsonl")
        reconciliation = _json_lines(run / "reconciliation.jsonl")
        events = _json_lines(run / "events.jsonl")
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        run_manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        result = json.loads((run / "run-result.json").read_text(encoding="utf-8"))
        run_quality_bytes = (run / "quality-report.json").read_bytes()
        release_quality_bytes = (release / "quality-report.json").read_bytes()
        projection_quality_bytes = (projection / "quality-report.json").read_bytes()
        quality = json.loads(release_quality_bytes)
    except Exception as exc:
        _assertion(assertions, "read_artifacts", "readable JSON/JSONL", f"{type(exc).__name__}: {exc}", release_paths)
        return False, assertions, {}
    counts = {"draws": len(draws), "selected": len(selected), "parsed": len(all_observations), **Counter(row["game"] for row in draws)}
    passed &= _assertion(assertions, "record_counts", {"draws": 400, "selected": 800, "parsed": 1042, "ssq": 200, "dlt": 200}, counts, [release / "draws.jsonl", release / "observations.jsonl", run / "observations.jsonl"])
    passed &= _assertion(assertions, "unique_game_issue", 400, len({(row["game"], row["issue_id"]) for row in draws}), [release / "draws.jsonl"])
    schema_ok = True
    schema_error = None
    try:
        [validate_object("DrawRecord", row) for row in draws]
        [validate_object("SourceObservation", row) for row in all_observations]
        [validate_object("SourceObservation", row) for row in selected]
        [validate_object("RunEvent", row) for row in events]
        validate_object("DatasetRelease", manifest)
        validate_object("RunManifest", run_manifest)
        validate_object("RunResult", result)
    except Exception as exc:
        schema_ok, schema_error = False, f"{type(exc).__name__}: {exc}"
    passed &= _assertion(assertions, "schemas_and_derived_ids", True, schema_ok, [release / "manifest.json", run / "run-manifest.json", run / "run-result.json"])
    if schema_error:
        assertions[-1]["detail"] = schema_error
    quality_copies_ok = run_quality_bytes == release_quality_bytes == projection_quality_bytes == canonical_bytes(quality)
    passed &= _assertion(assertions, "quality_three_copy_canonical_identity", True, quality_copies_ok, [
        run / "quality-report.json", release / "quality-report.json", projection / "quality-report.json",
    ])
    identity_ok = _identity_closure_valid(
        run_id=run_id, release_id=release_id, mode="bootstrap", run_manifest=run_manifest,
        result=result, stdout_result=result if stdout_result is None else stdout_result, quality=quality, events=events,
        release_manifest=manifest,
    )
    passed &= _assertion(assertions, "run_release_identity_closure", True, identity_ok, [
        run / "run-manifest.json", run / "run-result.json", run / "quality-report.json",
        run / "events.jsonl", release / "manifest.json", root / "current-release.json",
    ])
    evidence_ok = _evidence_bijection_valid(draws, selected, all_observations)
    passed &= _assertion(assertions, "evidence_links_close", True, evidence_ok, [release / "draws.jsonl", release / "observations.jsonl"])
    snapshot = frozen_snapshot.resolve()
    passed &= _assertion(assertions, "frozen_snapshot_identity", str(snapshot), str(Path(run_manifest["bootstrap_snapshot"]["snapshot_root"]).resolve()), [run / "run-manifest.json"])
    capture_path = snapshot / "capture-manifest.jsonl"
    canonical_path = snapshot / "consensus" / "canonical-records.jsonl"
    artifact_hashes_path = snapshot / "artifact-hashes.json"
    capture_rows = _json_lines(capture_path)
    capture = {row["raw_ref"]: row for row in capture_rows}
    capture_by_request = {row["request_id"]: row for row in capture_rows}
    frozen_raw_refs = frozenset(capture)
    canonical = {(row["game"], row["issue_id"]): row for row in _json_lines(canonical_path)}
    plan_rows = run_manifest.get("request_plan", [])
    plan_by_request = {
        row["request_id"]: row for row in plan_rows
        if isinstance(row, dict) and isinstance(row.get("request_id"), str)
    }
    plan_input_refs = [row.get("input_ref") for row in plan_rows if isinstance(row, dict)]
    succeeded_rows = [row for row in events if row.get("event_type") == "request_succeeded"]
    succeeded_by_request = {
        row["request_id"]: row for row in succeeded_rows
        if isinstance(row.get("request_id"), str)
    }
    actual_run_raw_refs = frozenset(
        path.relative_to(run).as_posix() for path in run.joinpath("raw").rglob("*") if path.is_file()
    )
    frozen_raw_contract_ok = (
        len(capture_rows) == len(capture) == len(capture_by_request) == 30
        and all(
            ref.startswith("raw/") and (snapshot / ref).is_file()
            and sha256_file(snapshot / ref) == capture[ref].get("raw_sha256")
            for ref in frozen_raw_refs
        )
        and isinstance(plan_rows, list) and len(plan_rows) == len(plan_by_request) == 30
        and len(plan_input_refs) == 30 and all(isinstance(ref, str) for ref in plan_input_refs)
        and len(set(plan_input_refs)) == 30
        and set(plan_input_refs) == frozen_raw_refs
        and set(plan_by_request) == set(capture_by_request)
        and all(plan_by_request[request_id].get("input_ref") == row["raw_ref"] for request_id, row in capture_by_request.items())
        and len(succeeded_rows) == len(succeeded_by_request) == 30
        and set(succeeded_by_request) == set(plan_by_request)
        and all(
            succeeded_by_request[request_id].get("artifact_ref") == request["input_ref"]
            and capture_by_request[request_id]["raw_ref"] == request["input_ref"]
            and (run / request["input_ref"]).is_file()
            and sha256_file(run / request["input_ref"]) == capture_by_request[request_id]["raw_sha256"]
            for request_id, request in plan_by_request.items()
        )
        and actual_run_raw_refs == frozen_raw_refs
    )
    raw_ok = frozen_raw_contract_ok and all(
        row["raw_ref"] in capture
        and capture[row["raw_ref"]]["raw_sha256"] == row["raw_sha256"]
        and (snapshot / row["raw_ref"]).is_file()
        and sha256_file(snapshot / row["raw_ref"]) == row["raw_sha256"]
        and (run / row["raw_ref"]).is_file()
        and sha256_file(run / row["raw_ref"]) == row["raw_sha256"]
        for row in all_observations
    )
    passed &= _assertion(assertions, "raw_hashes_close", True, raw_ok, [run / "observations.jsonl", capture_path, artifact_hashes_path])
    phase0_ok = all(
        (draw["game"], draw["issue_id"]) in canonical
        and canonical[(draw["game"], draw["issue_id"])]["core_fact_sha256"] == draw["core_fact_sha256"]
        and set(canonical[(draw["game"], draw["issue_id"])]["source_ids"]) == {link["source_id"] for link in draw["evidence_links"]}
        and set(canonical[(draw["game"], draw["issue_id"])]["evidence_refs"]) == {link["raw_ref"] for link in draw["evidence_links"]}
        for draw in draws
    )
    try:
        phase0_hashes = json.loads(artifact_hashes_path.read_text(encoding="utf-8"))
        phase0_ok = phase0_ok and all((snapshot / relative).is_file() and sha256_file(snapshot / relative) == digest for relative, digest in phase0_hashes.items())
    except Exception:
        phase0_ok = False
    passed &= _assertion(assertions, "phase0_core_and_artifact_hashes", True, phase0_ok, [canonical_path, artifact_hashes_path, release / "draws.jsonl"])
    sort_ok = (
        all_observations == sorted(all_observations, key=lambda row: (row["game"], row["issue_id"], row["publisher_id"], row["source_id"], row["observation_id"]))
        and selected == sorted(selected, key=lambda row: (row["game"], row["issue_id"], row["publisher_id"], row["source_id"], row["observation_id"]))
        and draws == sorted(draws, key=lambda row: (row["game"], row["issue_id"], row["revision_id"]))
        and reconciliation == sorted(reconciliation, key=lambda row: (row["game"], row["issue_id"]))
    )
    passed &= _assertion(assertions, "canonical_sorting", True, sort_ok, [release / "draws.jsonl", release / "observations.jsonl", run / "observations.jsonl", run / "reconciliation.jsonl"])
    event_ok = _events_valid(events, run_manifest, run_id)
    passed &= _assertion(assertions, "request_and_event_counts", True, event_ok, [run / "events.jsonl"])
    config_profile, run_config_refs = _bootstrap_config_profile(run, run_manifest)
    config_contract_ok = config_profile is not None
    config_entries = frozenset(f"runs/{run_id}/{ref}" for ref in run_config_refs)
    raw_entries = frozenset(f"runs/{run_id}/{ref}" for ref in frozen_raw_refs)
    run_entries = frozenset({
        f"runs/{run_id}/candidate-draws.jsonl", f"runs/{run_id}/events.jsonl", f"runs/{run_id}/observations.jsonl",
        f"runs/{run_id}/quality-report.json", f"runs/{run_id}/reconciliation.jsonl", f"runs/{run_id}/run-manifest.json",
        f"runs/{run_id}/run-result.json",
    }) | raw_entries | config_entries
    hashes_ok = (
        config_contract_ok
        and frozen_raw_contract_ok and len(raw_entries) == 30 and len(run_entries) == 37 + len(config_entries)
        and _hash_manifest_valid(release / "hashes.json", release, RELEASE_HASH_ENTRIES)
        and _hash_manifest_valid(run / "hashes.json", root, run_entries, run)
    )
    passed &= _assertion(assertions, "hash_manifests", True, hashes_ok, [release / "hashes.json", run / "hashes.json"])
    quality_ok = (
        _run_artifact_semantics_valid(
            root, run_id, quality, result, release_id,
            include_quality_result_hash=config_profile == "current",
        )
        and quality["deterministic"]["output_hashes"]["release_observations"] == sha256_file(release / "observations.jsonl")
        and quality["deterministic"]["output_hashes"]["draws"] == sha256_file(release / "draws.jsonl")
        and manifest.get("input_manifest_sha256") == sha256_file(run / "run-manifest.json")
        and manifest.get("records_sha256") == sha256_file(release / "draws.jsonl")
        and manifest.get("observations_sha256") == sha256_file(release / "observations.jsonl")
    )
    passed &= _assertion(assertions, "quality_hash_semantics", True, quality_ok, [release / "quality-report.json", release / "manifest.json", run / "run-result.json"])
    pointer_ok = _pointer_valid(root, release_id, run_id)
    passed &= _assertion(assertions, "current_pointer", True, pointer_ok, [root / "current-release.json", release / "manifest.json"])
    normalized = {"draws": sha256_file(release / "draws.jsonl"), "observations": sha256_file(release / "observations.jsonl")}
    return bool(passed), assertions, normalized


def _case_report(case_id: str, contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    if case_id == "E2E-07":
        return _fault_case_report(contract, contract_path)
    if case_id in EXTENDED_CASES:
        return _extended_case_report(case_id, contract, contract_path)
    formal_before = _formal_state()
    frozen_snapshot = _frozen_snapshot(contract)
    commands: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"autoresearch-lotte-{case_id.lower()}-") as temporary:
        base = Path(temporary)
        _safe_temp_root(base)
        environment = _network_guard(base)
        if case_id == "E2E-01":
            root = base / "artifacts-a"
            argv = [sys.executable, "-m", "lottery_data", "run", "--mode", "bootstrap", "--source-mode", "snapshot", "--phase0-snapshot", str(SNAPSHOT), "--run-id", "e2e01-bootstrap", "--release-id", "baseline-v1", "--artifacts-root", str(root)]
            command, result = _run(argv, environment)
            commands.append(command)
            if command["actual_exit_code"] != 0 or result is None:
                _assertion(assertions, "formal_artifacts_unchanged", formal_before, _formal_state(), [FORMAL_ROOT / "current-release.json"])
                return FAIL, _base_report(contract, contract_path, case=case_id, status="FAIL", commands=commands, assertions=assertions)
            oracle_ok, oracle_assertions, _ = _release_oracle(root, "baseline-v1", "e2e01-bootstrap", frozen_snapshot, result)
            assertions.extend(oracle_assertions)
            _assertion(assertions, "cli_run_result", {"exit_code": 0, "status": "published"}, {"exit_code": result.get("exit_code"), "status": result.get("status")}, [root / "runs" / "e2e01-bootstrap" / "run-result.json"])
            passed = oracle_ok and all(item["status"] == "PASS" for item in assertions)
        elif case_id == "E2E-02":
            roots = (base / "artifacts-a", base / "artifacts-b")
            normalized: list[dict[str, str]] = []
            passed = True
            for label, root in zip(("a", "b"), roots):
                run_id = f"e2e02-bootstrap-{label}"
                argv = [sys.executable, "-m", "lottery_data", "run", "--mode", "bootstrap", "--source-mode", "snapshot", "--phase0-snapshot", str(SNAPSHOT), "--run-id", run_id, "--release-id", "baseline-v1", "--artifacts-root", str(root)]
                command, result = _run(argv, environment)
                commands.append(command)
                if command["actual_exit_code"] != 0 or result is None:
                    _assertion(assertions, "formal_artifacts_unchanged", formal_before, _formal_state(), [FORMAL_ROOT / "current-release.json"])
                    return FAIL, _base_report(contract, contract_path, case=case_id, status="FAIL", commands=commands, assertions=assertions)
                ok, detail, hashes = _release_oracle(root, "baseline-v1", run_id, frozen_snapshot, result)
                assertions.extend({**item, "id": f"{label}.{item['id']}"} for item in detail)
                normalized.append(hashes)
                passed &= ok
            passed &= _assertion(assertions, "independent_bootstrap_hash_match", normalized[0], normalized[1], [roots[0] / "releases" / "baseline-v1" / "draws.jsonl", roots[1] / "releases" / "baseline-v1" / "draws.jsonl", roots[0] / "releases" / "baseline-v1" / "observations.jsonl", roots[1] / "releases" / "baseline-v1" / "observations.jsonl"])
            pointer_before = (roots[0] / "current-release.json").read_bytes()
            release_before = _tree_hashes(roots[0] / "releases" / "baseline-v1")
            projection_before = _tree_hashes(roots[0] / "baseline-v1")
            releases_before = sorted(path.name for path in (roots[0] / "releases").iterdir() if path.is_dir())
            argv = [sys.executable, "-m", "lottery_data", "run", "--mode", "incremental", "--source-mode", "snapshot", "--snapshot-root", str(SNAPSHOT), "--run-id", "e2e02-incremental", "--artifacts-root", str(roots[0]), "--games", "ssq,dlt"]
            command, result = _run(argv, environment)
            commands.append(command)
            if command["actual_exit_code"] != 0 or result is None:
                _assertion(assertions, "formal_artifacts_unchanged", formal_before, _formal_state(), [FORMAL_ROOT / "current-release.json"])
                return FAIL, _base_report(contract, contract_path, case=case_id, status="FAIL", commands=commands, assertions=assertions)
            incremental_run = roots[0] / "runs" / "e2e02-incremental"
            try:
                incremental_manifest = json.loads((incremental_run / "run-manifest.json").read_text(encoding="utf-8"))
                incremental_result = json.loads((incremental_run / "run-result.json").read_text(encoding="utf-8"))
                incremental_quality = json.loads((incremental_run / "quality-report.json").read_text(encoding="utf-8"))
                incremental_events = _json_lines(incremental_run / "events.jsonl")
                incremental_identity = _identity_closure_valid(
                    run_id="e2e02-incremental", release_id=None, mode="incremental",
                    run_manifest=incremental_manifest, result=incremental_result, stdout_result=result,
                    quality=incremental_quality, events=incremental_events,
                )
            except Exception:
                incremental_identity = False
            passed &= _assertion(assertions, "incremental_identity_closure", True, incremental_identity, [
                incremental_run / "run-manifest.json", incremental_run / "run-result.json",
                incremental_run / "quality-report.json", incremental_run / "events.jsonl",
            ])
            incremental_semantics = _run_artifact_semantics_valid(
                roots[0], "e2e02-incremental", incremental_quality, incremental_result, "baseline-v1",
                include_quality_result_hash=True,
            ) and _pointer_valid(roots[0], "baseline-v1", "e2e02-bootstrap-a")
            passed &= _assertion(assertions, "incremental_hash_and_pointer_semantics", True, incremental_semantics, [
                incremental_run / "quality-report.json", incremental_run / "run-result.json",
                roots[0] / "current-release.json", roots[0] / "releases" / "baseline-v1" / "manifest.json",
            ])
            zero = {key: result.get("change_stats", {}).get(key) for key in ("added", "revised", "duplicate", "conflict")}
            passed &= _assertion(assertions, "snapshot_incremental_status", {"exit_code": 0, "status": "no_change", "release_id": None}, {"exit_code": result.get("exit_code"), "status": result.get("status"), "release_id": result.get("release_id")}, [roots[0] / "runs" / "e2e02-incremental" / "run-result.json"])
            passed &= _assertion(assertions, "incremental_zero_changes", {"added": 0, "revised": 0, "duplicate": 0, "conflict": 0}, zero, [roots[0] / "runs" / "e2e02-incremental" / "run-result.json"])
            unchanged = pointer_before == (roots[0] / "current-release.json").read_bytes() and release_before == _tree_hashes(roots[0] / "releases" / "baseline-v1") and projection_before == _tree_hashes(roots[0] / "baseline-v1") and releases_before == sorted(path.name for path in (roots[0] / "releases").iterdir() if path.is_dir())
            passed &= _assertion(assertions, "no_change_preserves_release_and_pointer", True, unchanged, [roots[0] / "current-release.json", roots[0] / "releases" / "baseline-v1" / "hashes.json", roots[0] / "baseline-v1" / "hashes.json"])
        else:
            raise KeyError(f"case not implemented for G2: {case_id}")
    passed &= _assertion(assertions, "formal_artifacts_unchanged", formal_before, _formal_state(), [FORMAL_ROOT / "current-release.json"])
    status = "PASS" if passed else "FAIL"
    return (PASS if passed else FAIL), _base_report(contract, contract_path, case=case_id, status=status, commands=commands, assertions=assertions)


def _extended_case_report(case_id: str, contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    """Promote independently accepted real-path E2E tests into contract-shaped case evidence."""
    profile = EXTENDED_CASES[case_id]
    case = next((item for item in contract.get("e2e_cases", []) if item.get("id") == case_id), None)
    expected_ids = [item[0] for item in profile["facts"]]
    if case is None or case.get("assertions") != expected_ids or len(set(case.get("assertions", []))) != len(expected_ids):
        return FAIL, _base_report(
            contract, contract_path, case=case_id, status="FAIL",
            reason="case assertion identities/order differ from the executable profile",
            commands=[], assertions=[], oracle_assertions=[],
        )
    formal_before = _formal_state()
    commands: list[dict[str, Any]] = []
    all_passed = True
    for test_id in profile["tests"]:
        argv = [sys.executable, "-B", "-m", "unittest", test_id, "-v"]
        completed = subprocess.run(argv, cwd=REPO, text=True, capture_output=True, check=False)
        passed = completed.returncode == 0
        all_passed &= passed
        commands.append({
            "argv": argv, "expected_exit_code": 0, "actual_exit_code": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
            "status": "PASS" if passed else "FAIL",
        })
    stable_paths = [
        *profile["evidence"], SNAPSHOT / "artifact-hashes.json", SNAPSHOT / "capture-manifest.jsonl",
        FORMAL_ROOT / "current-release.json", FORMAL_ROOT / "releases/baseline-v1/manifest.json",
    ]
    evidence = _evidence(stable_paths)
    for index, command in enumerate(commands, 1):
        evidence[f"command:{case_id}:{index}:stdout_sha256"] = command["stdout_sha256"]
        evidence[f"command:{case_id}:{index}:stderr_sha256"] = command["stderr_sha256"]
    assertions = [{
        "id": assertion_id, "status": "PASS" if all_passed else "FAIL",
        "expected": expected, "actual": expected if all_passed else None,
        "evidence_sha256": dict(sorted(evidence.items())),
    } for assertion_id, expected in profile["facts"]]
    oracle_assertions: list[dict[str, Any]] = []
    unchanged = formal_before == _formal_state()
    _assertion(oracle_assertions, "formal_artifacts_unchanged", True, unchanged, stable_paths[-2:])
    passed = all_passed and unchanged and all(item["status"] == "PASS" for item in assertions)
    return (PASS if passed else FAIL), _base_report(
        contract, contract_path, case=case_id, status="PASS" if passed else "FAIL",
        commands=commands, assertions=assertions, oracle_assertions=oracle_assertions,
    )


def _fault_case_report(contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    from e2e07_case import execute_fault_matrix

    case_id = "E2E-07"
    expected_ids = [
        "every_fault_has_expected_exit=true", "every_started_request_has_one_terminal=true",
        "release_created=false", "current_pointer_unchanged=true", "interrupted_run_recovered=true",
    ]
    case = next((item for item in contract.get("e2e_cases", []) if item.get("id") == case_id), None)
    if case is None or case.get("assertions") != expected_ids or len(set(case.get("assertions", []))) != len(expected_ids):
        return FAIL, _base_report(contract, contract_path, case=case_id, status="FAIL", reason="E2E-07 assertion profile mismatch", commands=[], assertions=[], oracle_assertions=[])
    formal_before = _formal_state()
    outcomes = execute_fault_matrix()
    by_fault = {item["fault"]: item for item in outcomes}
    expected_faults = {
        "source_conflict", "truncated_html", "wrong_encoding", "network_failure",
        "invalid_configuration", "raw_hash_mismatch", "publish_lock", "compare_and_swap",
        "forced_process_termination",
    }
    exact_inventory = set(by_fault) == expected_faults and len(outcomes) == len(expected_faults)
    exit_map_ok = exact_inventory and all(item["actual_exit"] == item["expected_exit"] for item in outcomes)
    closure_ok = exact_inventory and all(item["request_terminal_closed"] for item in outcomes)
    no_release = exact_inventory and not any(item["release_created"] for item in outcomes)
    pointer_unchanged = exact_inventory and all(item["pointer_before"] == item["pointer_after"] for item in outcomes)
    crash = by_fault.get("forced_process_termination", {})
    recovered = crash.get("recovered") is True and crash.get("recovery_idempotent") is True and crash.get("terminal_count") == 1
    facts = [exit_map_ok, closure_ok, no_release, pointer_unchanged, recovered]
    commands = [{
        "argv": ["structured-fault-executor", item["fault"]],
        "expected_exit_code": item["expected_exit"], "actual_exit_code": item["actual_exit"],
        "stdout_sha256": item["stdout_sha256"], "stderr_sha256": item["stderr_sha256"],
        "status": "PASS" if item["actual_exit"] == item["expected_exit"] else "FAIL",
    } for item in outcomes]
    stable_paths = [
        REPO / "tests/phase1/e2e07_case.py", REPO / "tests/phase1/test_e2e_fault_case.py",
        SNAPSHOT / "artifact-hashes.json", SNAPSHOT / "capture-manifest.jsonl",
        REPO / "config/phase1/live-source-policy.json", FORMAL_ROOT / "current-release.json",
    ]
    evidence = _evidence(stable_paths)
    for item in outcomes:
        evidence[f"command:E2E-07:{item['fault']}:stdout_sha256"] = item["stdout_sha256"]
        evidence[f"command:E2E-07:{item['fault']}:stderr_sha256"] = item["stderr_sha256"]
    assertions = [{
        "id": assertion_id, "status": "PASS" if actual else "FAIL",
        "expected": True, "actual": actual, "evidence_sha256": dict(sorted(evidence.items())),
    } for assertion_id, actual in zip(expected_ids, facts)]
    oracle_assertions: list[dict[str, Any]] = []
    unchanged = formal_before == _formal_state()
    _assertion(oracle_assertions, "formal_artifacts_unchanged", True, unchanged, [FORMAL_ROOT / "current-release.json"])
    passed = all(facts) and unchanged
    return (PASS if passed else FAIL), _base_report(
        contract, contract_path, case=case_id, status="PASS" if passed else "FAIL",
        commands=commands, assertions=assertions, oracle_assertions=oracle_assertions,
    )


def _live_helper_evidence(helper: dict[str, Any]) -> dict[str, Any]:
    formal = helper.get("watchdog_parent_formal_state") or helper.get("formal_state") or {}
    before = formal.get("before") if isinstance(formal, dict) else None
    after = formal.get("after") if isinstance(formal, dict) else None
    formal_equal = formal.get("equal", before == after) if isinstance(formal, dict) else False
    worker = helper.get("worker_process") if isinstance(helper.get("worker_process"), dict) else {}
    budget = helper.get("watchdog_budget") if isinstance(helper.get("watchdog_budget"), dict) else {}
    primary = helper.get("primary_result") if isinstance(helper.get("primary_result"), dict) else {}
    errors = helper.get("primary_errors") if isinstance(helper.get("primary_errors"), list) else []
    raw_checks = helper.get("raw_hash_evidence") if isinstance(helper.get("raw_hash_evidence"), list) else []
    parse_checks = helper.get("parse_entry_evidence") if isinstance(helper.get("parse_entry_evidence"), list) else []
    profile = helper.get("execution_profile") if isinstance(helper.get("execution_profile"), dict) else {}
    profile_keys = {
        "profile", "live_policy_schema_version", "run_schema_version", "event_schema_versions",
        "static_request_count", "effective_request_count", "request_started_count", "request_ids", "request_kinds",
        "request_discovered_event_count", "child_authorization_present",
    }
    worker_keys = (
        "timed_out", "terminated", "killed", "reaped", "returncode",
        "workspace_cleanup_verified", "stdout_length", "stderr_length",
    )
    budget_keys = (
        "execution_profile", "static_request_count", "max_dynamic_children", "maximum_effective_requests",
        "distinct_host_count", "same_host_wait_count", "request_timeout_seconds",
        "throttle_interval_seconds", "request_budget_seconds", "throttle_budget_seconds", "retry_budget_seconds",
        "orchestration_margin_seconds", "worker_deadline_seconds", "cleanup_grace_seconds",
        "total_deadline_seconds", "safety_ceiling_seconds",
    )
    return {
        "case_id": helper.get("case_id"), "decision": helper.get("decision"),
        "underlying_exit_code": helper.get("underlying_exit_code"),
        "acceptance_runner_exit_code": helper.get("acceptance_runner_exit_code"),
        "observed_policy_sha256": helper.get("observed_policy_sha256"),
        "assertions": helper.get("assertions"),
        "execution_profile": {
            key: profile.get(key) for key in (
                "profile", "live_policy_schema_version", "run_schema_version", "event_schema_versions",
                "static_request_count", "effective_request_count", "request_started_count", "request_ids", "request_kinds",
                "request_discovered_event_count", "child_authorization_present",
            ) if key in profile
        } | {"shape_closed": set(profile) == profile_keys},
        "formal_state": {"before": before, "after": after, "equal": formal_equal},
        "worker_lifecycle": {key: worker.get(key) for key in worker_keys if key in worker},
        "deadline": {key: budget.get(key) for key in budget_keys if key in budget},
        "execution": {
            "status": primary.get("status"), "exit_code": primary.get("exit_code"),
            "request_stats": primary.get("request_stats"),
            "error_codes": [item.get("error_code") for item in errors if isinstance(item, dict)],
            "raw_hash_check_count": len(raw_checks),
            "raw_hash_checks_closed": bool(raw_checks) and all(isinstance(item, dict) and item.get("closed") is True for item in raw_checks),
            "parse_entry_check_count": len(parse_checks),
            "parse_entry_checks_closed": bool(parse_checks) and all(isinstance(item, dict) and item.get("closed") is True for item in parse_checks),
        },
    }


def _live_case_report(contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    case_id = "E2E-05"
    case = next((item for item in contract.get("e2e_cases", []) if item.get("id") == case_id), None)
    declared = case.get("assertions") if isinstance(case, dict) else None
    expected_ids = list(E2E05_ASSERTION_IDS)
    contract_ids = list(E2E05_ASSERTION_IDS)
    if (
        declared != contract_ids
        or len(set(declared or [])) != len(contract_ids)
        or len(set(expected_ids)) != len(expected_ids)
    ):
        return FAIL, _base_report(
            contract, contract_path, case=case_id, status="FAIL",
            reason="E2E-05 assertion identities/order differ from the frozen contract profile",
            assertions=[], current_session_live={"explicit_cli": True, "source_mode": "live", "helper_called": False},
        )
    helper = execute_live_case()
    sanitized = _live_helper_evidence(helper)
    helper_sha = hashlib.sha256(canonical_bytes(sanitized)).hexdigest()
    helper_assertions = helper.get("assertions")
    exact_helper_ids = (
        isinstance(helper_assertions, Mapping)
        and len(helper_assertions) == len(expected_ids)
        and set(helper_assertions) == set(expected_ids)
    )
    typed_helper_assertions = exact_helper_ids and all(
        type(helper_assertions[assertion_id]) is bool for assertion_id in expected_ids
    )
    stable_evidence = _evidence([
        contract_path, REPO / "tests/phase1/e2e05_live_case.py",
        REPO / "config/phase1/live-source-policy.json", FORMAL_ROOT / "current-release.json",
    ])
    assertions: list[dict[str, Any]] = []
    for assertion_id in expected_ids:
        actual = helper_assertions.get(assertion_id) if isinstance(helper_assertions, Mapping) else None
        passed = typed_helper_assertions and actual is True
        assertions.append({
            "id": assertion_id, "status": "PASS" if passed else "FAIL",
            "expected": True, "actual": actual,
            "evidence": {
                "helper_assertion_id": assertion_id,
                "contract_assertion_id": contract_ids[expected_ids.index(assertion_id)],
                "helper_evidence_sha256": helper_sha,
                "stable_file_sha256": stable_evidence,
            },
        })
    formal = sanitized["formal_state"]
    formal_keys = {"current-release.json", "releases/baseline-v1", "baseline-v1", "runs/p1-baseline-v1"}
    formal_closed = (
        isinstance(formal.get("before"), dict) and set(formal["before"]) == formal_keys
        and isinstance(formal.get("after"), dict) and set(formal["after"]) == formal_keys
        and formal.get("equal") is True and formal["before"] == formal["after"]
    )
    profile = sanitized["execution_profile"]
    current_profile_closed = (
        profile.get("shape_closed") is True
        and profile.get("profile") == E2E05_EXECUTION_PROFILE
        and profile.get("live_policy_schema_version") == "1.3.0"
        and profile.get("run_schema_version") == "1.3.0"
        and profile.get("event_schema_versions") == ["1.3.0"]
        and profile.get("static_request_count") == 4
        and profile.get("effective_request_count") == 4
        and type(profile.get("request_started_count")) is int
        and 0 <= profile["request_started_count"] <= 8
        and profile.get("request_ids") == list(E2E05_STATIC_REQUEST_IDS)
        and profile.get("request_kinds") == ["history"] * 4
        and profile.get("request_discovered_event_count") == 0
        and profile.get("child_authorization_present") is False
    )
    execution = sanitized["execution"]
    successful_request_stats = {
        "planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0,
    }
    success_evidence_closed = (
        execution.get("status") in {"published", "no_change"}
        and execution.get("exit_code") == 0
        and execution.get("request_stats") == successful_request_stats
        and profile.get("static_request_count") == successful_request_stats["planned"]
        and profile.get("effective_request_count") == successful_request_stats["planned"]
        and 4 <= profile.get("request_started_count", -1) <= 8
        and execution.get("raw_hash_check_count") == 4
        and execution.get("raw_hash_checks_closed") is True
        and execution.get("parse_entry_check_count") == 4
        and execution.get("parse_entry_checks_closed") is True
    )
    decision = helper.get("decision")
    underlying = helper.get("underlying_exit_code")
    helper_acceptance = helper.get("acceptance_runner_exit_code")
    helper_identity = helper.get("case_id") == case_id
    all_assertions_pass = typed_helper_assertions and all(item["status"] == "PASS" for item in assertions)
    if helper_identity and decision == "PASS" and underlying == 0 and helper_acceptance == 0 and all_assertions_pass and formal_closed and current_profile_closed and success_evidence_closed:
        code, status = PASS, "PASS"
    elif helper_identity and decision == "HOLD" and underlying in {3, 4} and helper_acceptance == HOLD and typed_helper_assertions and formal_closed and current_profile_closed:
        code, status = HOLD, "HOLD"
    else:
        code, status = FAIL, "FAIL"
    report = _base_report(
        contract, contract_path, case=case_id, status=status,
        underlying_exit_code=underlying, acceptance_runner_exit_code=code,
        current_session_live={
            "explicit_cli": True, "source_mode": "live", "helper_called": True,
            "helper_case_id": helper.get("case_id"),
            "observed_policy_sha256": helper.get("observed_policy_sha256"),
        },
        assertions=assertions, helper_evidence_sha256=helper_sha, helper_evidence=sanitized,
        formal_state=formal, worker_lifecycle=sanitized["worker_lifecycle"],
        watchdog_deadline=sanitized["deadline"],
    )
    return code, report


def _dispatch_case(case_id: str, contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    known = {case["id"] for case in contract.get("e2e_cases", [])}
    if case_id not in known:
        raise KeyError(f"unknown acceptance case: {case_id}")
    if case_id == "E2E-05":
        return _live_case_report(contract, contract_path)
    if case_id in {"E2E-01", "E2E-02", "E2E-03", "E2E-04", "E2E-06", "E2E-07"}:
        return _case_report(case_id, contract, contract_path)
    return FAIL, _base_report(
        contract, contract_path, case=case_id, status="FAIL",
        reason="case is known but not implemented by this runner",
    )


def _base_report(contract: dict[str, Any], contract_path: Path, **values: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase1_gate_acceptance",
        "contract_ref": contract_path.relative_to(REPO).as_posix(),
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "contract_version": contract.get("contract_version"),
        **values,
    }


def _normalize_unittest_elapsed(text: str) -> str:
    """Remove only unittest's nondeterministic elapsed token from summary lines."""
    import re

    return re.sub(
        r"^(Ran \d+ tests? in )\d+(?:\.\d+)?s$",
        r"\g<1><elapsed>s",
        text,
        flags=re.MULTILINE,
    )


def run_g1(contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    gate = next((item for item in contract["gates"] if item.get("id") == "G1"), None)
    if gate is None:
        raise KeyError("contract does not define G1")
    commands = []
    failed = False
    for verification in gate.get("verification", []):
        argv = [sys.executable if token == "{python}" else token for token in verification["argv"]]
        completed = subprocess.run(argv, cwd=REPO, text=True, capture_output=True, check=False)
        expected = verification["expected_exit_code"]
        is_unittest = any(argv[index:index + 2] == ["-m", "unittest"] for index in range(len(argv) - 1))
        stdout = _normalize_unittest_elapsed(completed.stdout) if is_unittest else completed.stdout
        stderr = _normalize_unittest_elapsed(completed.stderr) if is_unittest else completed.stderr
        commands.append({"argv": argv, "expected_exit_code": expected, "actual_exit_code": completed.returncode, "stdout": stdout, "stderr": stderr, "status": "PASS" if completed.returncode == expected else "FAIL"})
        failed |= completed.returncode != expected
    assertions = []
    for assertion in gate.get("assertions", []):
        try:
            passed, detail = g1_assertion(assertion)
        except KeyError as exc:
            passed, detail = False, str(exc)
        assertions.append({"id": assertion, "status": "PASS" if passed else "FAIL", "detail": detail})
        failed |= not passed
    status = "FAIL" if failed else "PASS"
    return (FAIL if failed else PASS), {**_base_report(contract, contract_path), "spec_bundle_freeze_ref": FREEZE_PATH.relative_to(REPO).as_posix(), "spec_bundle_freeze_sha256": _hash(FREEZE_PATH), "gate": "G1", "status": status, "commands": commands, "assertions": assertions}


def run_g2(contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    gate = next((item for item in contract["gates"] if item.get("id") == "G2"), None)
    if gate is None or len(gate.get("verification", [])) != 3:
        raise KeyError("contract does not define the three frozen G2 verification commands")
    if [item.get("argv") for item in gate["verification"]] != EXPECTED_G2_ARGV or any(item.get("expected_exit_code") != 0 for item in gate["verification"]):
        raise ValueError("G2 verification argv/exit whitelist differs from the frozen command identities")
    declared_assertions = gate.get("assertions")
    if declared_assertions != EXPECTED_G2_ASSERTIONS or len(set(declared_assertions or [])) != len(EXPECTED_G2_ASSERTIONS):
        return FAIL, _base_report(
            contract, contract_path, gate="G2", status="FAIL",
            reason="G2 assertion identities/order differ from the frozen contract profile",
            commands=[], assertions=[], oracle_assertions=[],
        )
    try:
        depth = int(os.environ.get("LOTTERY_ACCEPTANCE_DEPTH", "0"))
    except ValueError as exc:
        raise ValueError("invalid LOTTERY_ACCEPTANCE_DEPTH") from exc
    if depth != 0:
        raise RuntimeError("recursive G2 acceptance invocation is forbidden")
    frozen_commands = [
        [sys.executable if token == "{python}" else token for token in verification["argv"]]
        for verification in gate["verification"]
    ]
    g1_code, g1_report = run_g1(contract, contract_path)
    commands: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    oracle_assertions: list[dict[str, Any]] = []
    if g1_code != PASS:
        return FAIL, _base_report(contract, contract_path, gate="G2", status="FAIL", reason="current-session G1 failed", dependency=g1_report, commands=commands, assertions=assertions, oracle_assertions=oracle_assertions)
    formal_release = FORMAL_ROOT / "releases" / "baseline-v1"
    formal_projection = FORMAL_ROOT / "baseline-v1"
    if not formal_release.is_dir() or not formal_projection.is_dir():
        return HOLD, _base_report(contract, contract_path, gate="G2", status="HOLD", reason="formal baseline-v1 has not been signed", dependency=g1_report, commands=commands, assertions=assertions, oracle_assertions=oracle_assertions)
    g2_report_label = _formal_report_map(contract)["G2"][0]
    formal_before = _formal_state(exclude=frozenset({g2_report_label}))
    environment = os.environ.copy()
    environment["LOTTERY_ACCEPTANCE_DEPTH"] = "1"
    children: dict[str, dict[str, Any]] = {}
    for case_id, argv in zip(("E2E-01", "E2E-02"), frozen_commands[:2]):
        command, child = _run(argv, environment)
        commands.append(command)
        if command["actual_exit_code"] != PASS or child is None or child.get("status") != "PASS":
            return FAIL, _base_report(contract, contract_path, gate="G2", status="FAIL", reason=f"{case_id} failed", dependency=g1_report, commands=commands, assertions=assertions, oracle_assertions=oracle_assertions)
        children[case_id] = child
    verify_argv = frozen_commands[2]
    verify_command, verified_result = _run(verify_argv, environment)
    commands.append(verify_command)
    if verify_command["actual_exit_code"] != PASS or verified_result is None:
        return FAIL, _base_report(contract, contract_path, gate="G2", status="FAIL", reason="formal verify failed", dependency=g1_report, commands=commands, assertions=assertions, oracle_assertions=oracle_assertions)
    manifest = json.loads((formal_release / "manifest.json").read_text(encoding="utf-8"))
    oracle_ok, oracle_assertions, _ = _release_oracle(FORMAL_ROOT, "baseline-v1", manifest["input_run_id"], _frozen_snapshot(contract))
    unchanged = formal_before == _formal_state(exclude=frozenset({g2_report_label}))
    oracle_ok &= _assertion(oracle_assertions, "gate_did_not_mutate_formal_baseline", True, unchanged, [FORMAL_ROOT / "current-release.json", formal_release / "hashes.json", formal_projection / "hashes.json"])

    run = FORMAL_ROOT / "runs" / manifest["input_run_id"]
    draws = _json_lines(formal_release / "draws.jsonl")
    observations = _json_lines(formal_release / "observations.jsonl")
    quality = json.loads((formal_release / "quality-report.json").read_text(encoding="utf-8"))
    counts = quality.get("deterministic", {}).get("counts", {})
    canonical = {(row["game"], row["issue_id"]): row for row in _json_lines(_frozen_snapshot(contract) / "consensus" / "canonical-records.jsonl")}
    phase0_mismatches = sum(
        canonical.get((row["game"], row["issue_id"]), {}).get("core_fact_sha256") != row.get("core_fact_sha256")
        for row in draws
    )
    capture = {row["raw_ref"]: row for row in _json_lines(_frozen_snapshot(contract) / "capture-manifest.jsonl")}
    run_observations = _json_lines(run / "observations.jsonl")
    raw_mismatches = sum(
        row.get("raw_ref") not in capture
        or capture.get(row.get("raw_ref"), {}).get("raw_sha256") != row.get("raw_sha256")
        or not (run / str(row.get("raw_ref"))).is_file()
        or sha256_file(run / str(row.get("raw_ref"))) != row.get("raw_sha256")
        for row in run_observations
    )

    def child_fact(assertion_id: str, expected: Any) -> Any:
        matches = [item for item in children["E2E-02"].get("assertions", []) if item.get("id") == assertion_id]
        if len(matches) != 1:
            return None
        item = matches[0]
        fact_ok = item.get("status") == "PASS" and item.get("expected") == expected and item.get("actual") == expected
        return item.get("actual") if fact_ok else None

    independent_rows = [item for item in children["E2E-02"].get("assertions", []) if item.get("id") == "independent_bootstrap_hash_match"]
    independent_match = False
    if len(independent_rows) == 1:
        independent = independent_rows[0]
        expected_normalized, actual_normalized = independent.get("expected"), independent.get("actual")
        independent_match = (
            independent.get("status") == "PASS" and expected_normalized == actual_normalized
            and isinstance(expected_normalized, dict) and set(expected_normalized) == {"draws", "observations"}
            and all(isinstance(value, str) and len(value) == 64 for value in expected_normalized.values())
        )
    incremental_expected = {"exit_code": 0, "status": "no_change", "release_id": None}
    incremental_actual = child_fact("snapshot_incremental_status", incremental_expected)
    incremental_status = incremental_actual.get("status") if isinstance(incremental_actual, dict) else None

    facts = [
        (400, len(draws), [formal_release / "draws.jsonl"]),
        (800, len(observations), [formal_release / "observations.jsonl"]),
        (200, Counter(row["game"] for row in draws)["ssq"], [formal_release / "draws.jsonl"]),
        (200, Counter(row["game"] for row in draws)["dlt"], [formal_release / "draws.jsonl"]),
        (400, len({(row["game"], row["issue_id"]) for row in draws}), [formal_release / "draws.jsonl"]),
        (0, counts.get("invalid"), [formal_release / "quality-report.json"]),
        (0, counts.get("missing"), [formal_release / "quality-report.json"]),
        (0, counts.get("duplicate"), [formal_release / "quality-report.json"]),
        (0, counts.get("conflict"), [formal_release / "quality-report.json"]),
        (0, counts.get("manual_core_edit"), [formal_release / "quality-report.json"]),
        (0, phase0_mismatches, [formal_release / "draws.jsonl", _frozen_snapshot(contract) / "consensus" / "canonical-records.jsonl"]),
        (0, raw_mismatches, [run / "observations.jsonl", _frozen_snapshot(contract) / "capture-manifest.jsonl", run / "hashes.json"]),
    ]
    for assertion_id, (expected, actual, evidence) in zip(EXPECTED_G2_ASSERTIONS[:12], facts):
        _assertion(assertions, assertion_id, expected, actual, evidence)
    derived_evidence = _evidence([
        formal_release / "draws.jsonl", formal_release / "observations.jsonl",
        FORMAL_ROOT / "current-release.json", _frozen_snapshot(contract) / "artifact-hashes.json",
    ])
    derived_evidence["command:E2E-02:stdout_sha256"] = commands[1].get("stdout_sha256")
    assertions.append({"id": EXPECTED_G2_ASSERTIONS[12], "status": "PASS" if independent_match else "FAIL", "expected": True, "actual": independent_match, "evidence_sha256": dict(sorted(derived_evidence.items()))})
    assertions.append({"id": EXPECTED_G2_ASSERTIONS[13], "status": "PASS" if incremental_status == "no_change" else "FAIL", "expected": "no_change", "actual": incremental_status, "evidence_sha256": dict(sorted(derived_evidence.items()))})

    status = "PASS" if (
        g1_code == PASS and len(commands) == 3 and all(item.get("status") == "PASS" for item in commands)
        and [item["id"] for item in assertions] == EXPECTED_G2_ASSERTIONS
        and len(oracle_assertions) == 18 and oracle_ok
        and all(item["status"] == "PASS" for item in assertions + oracle_assertions)
    ) else "FAIL"
    return (PASS if status == "PASS" else FAIL), _base_report(contract, contract_path, gate="G2", status=status, dependency=g1_report, commands=commands, assertions=assertions, oracle_assertions=oracle_assertions)


def _contract_identity(contract: dict[str, Any], contract_path: Path) -> tuple[str, str]:
    return str(contract.get("contract_version")), sha256_file(contract_path)


def _dependency_report(path: Path, gate: str, contract: dict[str, Any], contract_path: Path) -> tuple[str, dict[str, Any] | None]:
    if not path.is_file():
        return "missing", None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "invalid", None
    version, digest = _contract_identity(contract, contract_path)
    if (
        report.get("artifact_type") != "phase1_gate_acceptance" or report.get("gate") != gate
        or report.get("status") != "PASS" or report.get("contract_version") != version
        or report.get("contract_sha256") != digest
    ):
        return "stale", report
    if gate == "G1":
        valid = (
            report.get("spec_bundle_freeze_ref") == FREEZE_PATH.relative_to(REPO).as_posix()
            and report.get("spec_bundle_freeze_sha256") == sha256_file(FREEZE_PATH)
            and [row.get("id") for row in report.get("assertions", [])]
            == next(item for item in contract["gates"] if item["id"] == "G1").get("assertions")
            and all(row.get("status") == "PASS" for row in report.get("assertions", []))
        )
    else:
        valid = (
            [row.get("id") for row in report.get("assertions", [])] == EXPECTED_G2_ASSERTIONS
            and all(row.get("status") == "PASS" for row in report.get("assertions", []))
            and len(report.get("oracle_assertions", [])) == 18
            and all(row.get("status") == "PASS" for row in report.get("oracle_assertions", []))
            and isinstance(report.get("dependency"), dict)
            and report["dependency"].get("gate") == "G1" and report["dependency"].get("status") == "PASS"
            and report["dependency"].get("contract_version") == version
            and report["dependency"].get("contract_sha256") == digest
        )
    return ("valid" if valid else "stale"), report


def _review_report(path: Path, scope: str, contract: dict[str, Any], contract_path: Path) -> tuple[str, dict[str, Any] | None]:
    if not path.is_file():
        return "missing", None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "invalid", None
    expected_keys = {
        "artifact_type", "review_scope", "contract_version", "contract_sha256",
        "reviewer", "reviewed_artifact_hashes", "blocking_findings",
    }
    version, digest = _contract_identity(contract, contract_path)
    hashes = report.get("reviewed_artifact_hashes")
    valid = (
        set(report) == expected_keys and report.get("artifact_type") == "phase1_independent_review"
        and report.get("review_scope") == scope and report.get("contract_version") == version
        and report.get("contract_sha256") == digest
        and isinstance(report.get("reviewer"), str) and bool(report["reviewer"].strip())
        and isinstance(hashes, dict) and bool(hashes)
        and isinstance(report.get("blocking_findings"), list) and not report["blocking_findings"]
    )
    if valid:
        for relative, expected in hashes.items():
            candidate = Path(relative) if isinstance(relative, str) else Path("")
            target = (REPO / candidate).resolve()
            if (
                not isinstance(relative, str) or candidate.is_absolute() or ".." in candidate.parts
                or REPO.resolve() not in target.parents or not target.is_file()
                or not isinstance(expected, str) or sha256_file(target) != expected
            ):
                valid = False
                break
    return ("valid" if valid else "invalid"), report


def _configuration_hashes_close(contract: dict[str, Any]) -> bool:
    files = contract.get("configuration_inputs", {}).get("files")
    return isinstance(files, list) and bool(files) and all(
        isinstance(item, dict) and isinstance(item.get("path"), str)
        and isinstance(item.get("expected_sha256"), str)
        and (REPO / item["path"]).is_file()
        and sha256_file(REPO / item["path"]) == item["expected_sha256"]
        for item in files
    )


def _documentation_contract_closes(contract: dict[str, Any]) -> bool:
    documentation = contract.get("documentation_contract")
    if not isinstance(documentation, dict):
        return False
    expected_precedence = [
        "docs/roadmap/phase-1-acceptance-contract.json",
        "docs/data/lottery-live-execution-spec-v1.3.md",
        "docs/data/lottery-data-spec-v1.md",
    ]
    expected_matrix = {
        "base_snapshot_v1": {"manifest_event_version": "1.0.0", "max_attempts_per_request": 1, "status": "current frozen baseline"},
        "legacy_live_v1_1": {"manifest_event_version": "1.1", "max_attempts_per_request": 1, "status": "historical read-only compatibility"},
        "historical_live_v1_2": {"manifest_event_version": "1.2", "max_attempts_per_request": 1, "status": "historical read-only compatibility"},
        "current_live_v1_3": {"manifest_event_version": "1.3", "max_attempts_per_request": 2, "status": "current incremental live"},
    }
    files = documentation.get("files")
    if (
        documentation.get("current_entrypoint") != "docs/data/lottery-data-spec-index.md"
        or documentation.get("precedence") != expected_precedence
        or documentation.get("profile_matrix") != expected_matrix
        or not isinstance(files, list) or len(files) != 5
    ):
        return False
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("expected_sha256"), str):
            return False
        path = item["path"]
        target = (REPO / path).resolve()
        try:
            target.relative_to(REPO.resolve())
        except ValueError:
            return False
        if not target.is_file() or target.is_symlink() or sha256_file(target) != item["expected_sha256"]:
            return False
        paths.append(path)
    if len(paths) != len(set(paths)):
        return False
    try:
        freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
        frozen_ok, _detail = g1_assertion("spec_bundle_hash_is_frozen")
    except Exception:
        return False
    return bool(
        frozen_ok
        and freeze.get("expected_bundle_sha256") == documentation.get("base_spec_bundle_expected_sha256")
    )


def _validated_acceptance_report_ref(ref: Any) -> tuple[str, Path]:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise ValueError("acceptance report ref must be a non-empty POSIX relative path")
    pure = PurePosixPath(ref)
    if pure.is_absolute() or ".." in pure.parts or pure.parts[:3] != ("artifacts", "phase-1", "acceptance"):
        raise ValueError(f"acceptance report ref escapes the formal acceptance root: {ref!r}")
    if len(pure.parts) != 4 or pure.suffix != ".json" or pure.name in {".json", "", ".", ".."}:
        raise ValueError(f"acceptance report ref must name one JSON file: {ref!r}")
    if FORMAL_ROOT.is_symlink():
        raise ValueError("formal root must not be a symlink")
    formal_root = FORMAL_ROOT.resolve()
    acceptance_path = FORMAL_ROOT / "acceptance"
    if acceptance_path.is_symlink():
        raise ValueError("formal acceptance root must not be a symlink")
    acceptance_root = acceptance_path.resolve()
    if acceptance_root.parent != formal_root:
        raise ValueError("formal acceptance root escapes the formal root")
    label = PurePosixPath(*pure.parts[2:]).as_posix()
    target = FORMAL_ROOT / Path(*pure.parts[2:])
    if target.is_symlink():
        raise ValueError(f"acceptance report target must not be a symlink: {ref!r}")
    resolved = target.resolve()
    if resolved.parent != acceptance_root:
        raise ValueError(f"acceptance report target escapes its root: {ref!r}")
    return label, resolved


def _formal_report_map(contract: dict[str, Any]) -> dict[str, tuple[str, Path]]:
    reports: dict[str, tuple[str, Path]] = {}
    for gate_id in ("G1", "G2", "G3"):
        gate = next((item for item in contract.get("gates", []) if item.get("id") == gate_id), None)
        if not isinstance(gate, dict):
            raise ValueError(f"contract does not define {gate_id}")
        candidates = []
        for value in gate.get("required_evidence", []):
            try:
                candidates.append(_validated_acceptance_report_ref(value))
            except ValueError:
                continue
        if len(candidates) != 1:
            raise ValueError(f"{gate_id} must declare exactly one acceptance report")
        reports[gate_id] = candidates[0]
    final = contract.get("final_acceptance")
    if not isinstance(final, dict):
        raise ValueError("contract does not define final_acceptance")
    required_ref = final.get("required_report")
    reports["ALL"] = _validated_acceptance_report_ref(required_ref)
    labels = [value[0] for value in reports.values()]
    paths = [value[1] for value in reports.values()]
    if len(labels) != len(set(labels)) or len(paths) != len(set(paths)):
        raise ValueError("formal acceptance report paths collide")
    runner_argv = final.get("runner_argv")
    if (
        not isinstance(runner_argv, list) or runner_argv[-2:] != ["--output", required_ref]
        or "--gate" not in runner_argv or runner_argv[runner_argv.index("--gate") + 1] != "ALL"
    ):
        raise ValueError("final runner_argv output/gate does not match required_report/ALL")
    return reports


def _current_derived_outputs() -> frozenset[str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    reports = _formal_report_map(contract)
    return frozenset({reports["G3"][0], reports["ALL"][0]})


G3_DERIVED_OUTPUTS = _current_derived_outputs()
G3_TEST_REQUIREMENTS: dict[str, tuple[int, tuple[str, ...]]] = {
    "module_and_console_entrypoints_are_equivalent": (0, ("test_workflow_unit.WorkflowUnitEntryContractTests.test_explicit_unit_suite_identity_count_and_uniqueness",)),
    "live_policy_fail_closed_validation_passes": (3, ("test_live_v12_workflow.LiveV12WorkflowTests.test_network_rejection_closes_started_request_and_keeps_four_planned",)),
    "live_review_expiry_cli_exit_4_maps_to_HOLD_runner_exit_20": (5, ("test_e2e_live_case_contract.E2ELiveCaseContractTests.test_changed_policy_is_preflight_hold20_with_underlying4",)),
    "preflight_and_runtime_failure_effects_are_distinct": (5, ("test_e2e_live_case_contract.E2ELiveCaseContractTests.test_network_unavailable_is_hold20_and_never_assumed_pass",)),
    "live_effective_plan_and_event_stream_validator_pass": (2, ("test_live_execution_v12_spec.LiveExecutionV12SpecTests.test_success_requires_exact_ordered_four_request_closure_and_one_terminal",)),
    "live_success_raw_refs_are_content_addressed": (3, ("test_live_v12_workflow.LiveV12WorkflowTests.test_preflight_and_success_use_exact_v12_static_plan_and_persist_raw_before_parse",)),
    "live_raw_is_persisted_and_hash_closed_before_parse": (3, ("test_live_v12_workflow.LiveV12WorkflowTests.test_preflight_and_success_use_exact_v12_static_plan_and_persist_raw_before_parse",)),
    "live_recheck_deferred_and_unconfirmed_change_rules_pass": (0, (
        "test_incremental_engine.IncrementalEngineTests.test_live_dlt_recheck_defers_nineteen_unchanged_single_side_old_issues",
        "test_incremental_engine.IncrementalEngineTests.test_live_dlt_single_side_old_change_is_unconfirmed_and_blocks",
    )),
    "live_recheck_quality_counters_are_truthful": (0, ("test_incremental_engine.IncrementalEngineTests.test_live_ssq_two_history_sources_complete_twenty_rechecks",)),
    "revision_is_append_only": (0, ("test_incremental_engine.IncrementalEngineTests.test_revised_supersedes_old_revision_without_mutating_old_release",)),
    "failed_runs_preserve_evidence": (3, ("test_live_v12_workflow.LiveV12WorkflowTests.test_parser_rejection_preserves_content_addressed_raw_and_does_not_publish",)),
    "failed_runs_do_not_create_release": (3, ("test_live_v12_workflow.LiveV12WorkflowTests.test_quality_rejection_occurs_after_all_four_static_requests",)),
    "lock_and_compare_and_swap_tests_pass": (0, ("test_transaction_replay_components.JournalRecoveryTests.test_recovery_and_publish_share_one_os_lock",)),
    "crash_recovery_test_passes": (0, ("test_transaction_replay_components.JournalRecoveryTests.test_recovery_itself_is_crash_idempotent_at_every_durable_step",)),
    "final_acceptance_contract_is_executable": (6, ("test_acceptance_g3.AcceptanceG3Tests.test_20_final_acceptance_contract_drives_all_output_and_derived_inventory",)),
}
G3_E2E_REQUIREMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "atomic_pointer_update_passes": ("E2E-03", ("old_release_unchanged=true",)),
    "offline_replay_uses_no_network": ("E2E-06", ("network_requests=0", "publication_operations=0")),
    "offline_replay_stable_read_inventory_guard_passes": ("E2E-06", ("stable_read_inventory_guard=true",)),
    "offline_replay_concurrent_change_exits_5": ("E2E-06", ("concurrent_input_change_exit=5",)),
    "current_acceptance_session_live_smoke=PASS": ("E2E-05", ("status_in=published|no_change",)),
}


def _tree_identity(path: Path) -> dict[str, Any]:
    files = _tree_hashes(path)
    return {"type": "tree", "sha256": hashlib.sha256(canonical_bytes(files)).hexdigest(), "files": files}


def _g3_input_inventory(contract: dict[str, Any]) -> dict[str, Any]:
    reports = _formal_report_map(contract)
    targets = {
        "current-release.json": FORMAL_ROOT / "current-release.json",
        "releases/baseline-v1": FORMAL_ROOT / "releases" / "baseline-v1",
        "baseline-v1": FORMAL_ROOT / "baseline-v1",
        "runs/p1-baseline-v1": FORMAL_ROOT / "runs" / "p1-baseline-v1",
        reports["G1"][0]: reports["G1"][1],
        reports["G2"][0]: reports["G2"][1],
        "reviews/data-review.json": FORMAL_ROOT / "reviews" / "data-review.json",
        "reviews/workflow-review.json": FORMAL_ROOT / "reviews" / "workflow-review.json",
    }
    inventory = {}
    for label, path in targets.items():
        if path.is_file() and not path.is_symlink():
            inventory[label] = {"type": "file", "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        elif path.is_dir() and not path.is_symlink():
            inventory[label] = _tree_identity(path)
        else:
            inventory[label] = {"type": "missing"}
    return inventory


def _inventory_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _parse_unittest_verbose(stderr: str) -> dict[str, Any]:
    import re
    rows = []
    pattern = re.compile(r"^\S+ \(([^)]+)\) \.\.\. (.+)$")
    for line in stderr.splitlines():
        match = pattern.match(line.strip())
        if match:
            rows.append({"id": match.group(1), "status": match.group(2)})
    count_match = re.search(r"^Ran (\d+) tests? in ", stderr, re.MULTILINE)
    terminal_ok = bool(re.search(r"^OK$", stderr, re.MULTILINE))
    ids = [row["id"] for row in rows]
    forbidden = any(row["status"] != "ok" for row in rows)
    valid = (
        bool(rows) and count_match is not None and int(count_match.group(1)) == len(rows)
        and len(ids) == len(set(ids)) and terminal_ok and not forbidden
    )
    return {"valid": valid, "ran": int(count_match.group(1)) if count_match else None, "terminal": "OK" if terminal_ok else None, "tests": rows}


def _validate_child(case_id: str, child: Any, contract: dict[str, Any], contract_path: Path, *, allow_hold: bool = False) -> bool:
    if not isinstance(child, dict):
        return False
    version, digest = _contract_identity(contract, contract_path)
    declared = next(item for item in contract["e2e_cases"] if item["id"] == case_id)["assertions"]
    rows = child.get("assertions")
    return (
        child.get("artifact_type") == "phase1_gate_acceptance"
        and child.get("contract_version") == version and child.get("contract_sha256") == digest
        and (child.get("case") == case_id or child.get("case_id") == case_id)
        and child.get("status") in ({"PASS", "HOLD"} if allow_hold else {"PASS"})
        and isinstance(rows, list) and [row.get("id") for row in rows] == declared
        and all(
            isinstance(row, dict) and row.get("expected") is not None and row.get("actual") is not None
            and (row.get("status") == "PASS" if child.get("status") == "PASS" else row.get("status") in {"PASS", "FAIL"})
            for row in rows
        )
    )


def _g3_machine_command(argv: list[str], expected: int, environment: dict[str, str], index: int, contract: dict[str, Any], contract_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    completed = subprocess.run(argv, cwd=REPO, env=environment, text=True, capture_output=True, check=False)
    evidence = {
        "argv": argv, "expected_exit_code": expected, "actual_exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }
    child = None
    if index < 7:
        normalized = _parse_unittest_verbose(completed.stderr)
        evidence["kind"] = "unittest"
        evidence["unittest"] = normalized
        evidence["status"] = "PASS" if completed.returncode == expected and normalized["valid"] else "FAIL"
    else:
        case_id = argv[-1]
        try:
            child = _parse_single_json(completed.stdout)
        except Exception:
            child = None
        hold = case_id == "E2E-05" and completed.returncode == HOLD and _validate_child(case_id, child, contract, contract_path, allow_hold=True) and child.get("status") == "HOLD"
        passed = completed.returncode == expected and _validate_child(case_id, child, contract, contract_path)
        evidence.update({"kind": "e2e", "case_id": case_id, "child": child, "status": "HOLD" if hold else "PASS" if passed else "FAIL"})
    return evidence, child


def _child_assertions_closed(child: dict[str, Any] | None, required: tuple[str, ...]) -> bool:
    if not isinstance(child, dict) or child.get("status") != "PASS":
        return False
    index = {row.get("id"): row for row in child.get("assertions", []) if isinstance(row, dict)}
    return all(
        item in index and index[item].get("status") == "PASS"
        and index[item].get("expected") is not None and index[item].get("actual") is not None
        for item in required
    )


def _evaluate_g3_assertions(
    contract: dict[str, Any], contract_path: Path, inventory: dict[str, Any],
    commands: list[dict[str, Any]], children: dict[str, dict[str, Any] | None],
    dependencies: dict[str, dict[str, Any] | None], review_states: list[str],
) -> list[dict[str, Any]]:
    from lottery_data.parsers.gdlottery_history import parse as parse_gd_history
    from lottery_data.steps.live_policy import build_live_request_plan, load_live_policy

    policy_path = REPO / "config/phase1/live-source-policy.json"
    try:
        policy = load_live_policy(policy_path)
        plan = build_live_request_plan(policy, ("ssq", "dlt"))
    except Exception:
        policy, plan = {}, []
    config_closed = _configuration_hashes_close(contract)
    sources = {row.get("source_id"): row for row in policy.get("sources", []) if isinstance(row, dict)}
    gd = sources.get("gdlottery", {})
    baseline_identity_closed = False
    try:
        pointer = json.loads((FORMAL_ROOT / "current-release.json").read_text(encoding="utf-8"))
        release = FORMAL_ROOT / "releases" / "baseline-v1"
        projection = FORMAL_ROOT / "baseline-v1"
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        baseline_identity_closed = (
            pointer.get("release_id") == "baseline-v1"
            and pointer.get("updated_by_run_id") == "p1-baseline-v1"
            and pointer.get("manifest_ref") == "releases/baseline-v1/manifest.json"
            and pointer.get("manifest_sha256") == sha256_file(release / "manifest.json")
            and manifest.get("release_id") == "baseline-v1"
            and manifest.get("input_run_id") == "p1-baseline-v1"
            and _tree_hashes(release) == _tree_hashes(projection)
            and inventory["runs/p1-baseline-v1"].get("type") == "tree"
        )
    except Exception:
        pass
    static: dict[str, bool] = {
        "configuration_input_hashes_match_contract": config_closed,
        "documentation_contract_hashes_and_profile_boundaries_match": _documentation_contract_closes(contract),
        "snapshot_and_live_source_policies_are_mode_separated": policy.get("live_policy_schema_version") == "1.3.0" and policy.get("baseline_separation", {}).get("baseline_release_id") == "baseline-v1",
        "G1_G2_baseline_ids_and_hashes_unchanged": baseline_identity_closed,
        "live_pairs_are_game_specific_and_publishers_are_distinct": policy.get("game_source_pairs", {}).get("ssq", {}).get("source_ids") == ["ydniu", "swlc"] and policy.get("game_source_pairs", {}).get("dlt", {}).get("source_ids") == ["ydniu", "gdlottery"] and len({sources.get(x, {}).get("publisher_id") for x in ("ydniu", "swlc", "gdlottery")}) == 3,
        "preflight_failure_has_no_persisted_run_or_fake_artifact_refs": _child_assertions_closed(children.get("E2E-05"), ("preflight_policy_failure_creates_no_request_run_or_release=true",)),
        "live_v1_3_schema_hashes_match_contract": config_closed and (REPO / "schemas/phase1/run-manifest-v1.3.schema.json").is_file() and (REPO / "schemas/phase1/run-event-v1.3.schema.json").is_file(),
        "legacy_v1_1_policy_and_schema_bytes_remain_frozen": sha256_file(REPO / "tests/phase1/fixtures/live-policy/live-source-policy-v1.1.1.json") == "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1",
        "original_six_v1_schema_hashes_remain_frozen": isinstance(dependencies.get("g1"), dict) and dependencies["g1"].get("spec_bundle_freeze_sha256") == sha256_file(FREEZE_PATH),
        "live_plan_has_exactly_four_static_history_requests": len(plan) == 4 and [row.get("sequence") for row in plan] == [1, 2, 3, 4] and all(row.get("request_kind") == "history" for row in plan),
        "live_discovery_and_child_events_are_forbidden": len(plan) == 4 and all(not ({"child_authorization", "parent_request_id", "discovery_request_id"} & set(row)) for row in plan),
        "gd_history_fixture_count_latest20_and_26084_26086_closure_are_evidenced": False,
        "gd_json_two_mib_cap_is_endpoint_specific": gd.get("max_response_bytes") == 2_097_152 and policy.get("network_policy", {}).get("max_response_bytes") == 1_048_576,
        "future_url_guessing_is_rejected_before_request": gd.get("endpoints") == {"dlt": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json"} and "?" not in str(gd.get("endpoints", {}).get("dlt", "")),
        "incremental_policy_matches_contract": policy.get("live_policy_schema_version") == contract.get("live_recheck_contract", {}).get("policy_schema_version"),
    }
    try:
        facts = parse_gd_history((REPO / "tests/phase1/fixtures/real/gd-game-number-history-20260803.json").read_bytes(), "dlt")
        static["gd_history_fixture_count_latest20_and_26084_26086_closure_are_evidenced"] = len(facts) == 20 and {"2026084", "2026085", "2026086"} <= {row["issue_id"] for row in facts}
    except Exception:
        pass

    output = []
    for assertion_id in EXPECTED_G3_ASSERTIONS:
        evidence: dict[str, Any]
        if assertion_id in static:
            actual = static[assertion_id]
            evidence = {"kind": "static", "input_inventory_sha256": _inventory_digest(inventory), "contract_sha256": sha256_file(contract_path)}
        elif assertion_id in G3_TEST_REQUIREMENTS:
            command_index, required = G3_TEST_REQUIREMENTS[assertion_id]
            command = commands[command_index] if command_index < len(commands) else {}
            normalized = command.get("unittest", {})
            by_id = {row.get("id"): row.get("status") for row in normalized.get("tests", []) if isinstance(row, dict)}
            actual = command.get("status") == "PASS" and all(by_id.get(test_id) == "ok" for test_id in required)
            evidence = {"kind": "unittest", "command_index": command_index, "required_test_ids": list(required), "stdout_sha256": command.get("stdout_sha256"), "stderr_sha256": command.get("stderr_sha256")}
        elif assertion_id in G3_E2E_REQUIREMENTS:
            case_id, required = G3_E2E_REQUIREMENTS[assertion_id]
            actual = _child_assertions_closed(children.get(case_id), required)
            evidence = {"kind": "e2e", "case_id": case_id, "required_assertion_ids": list(required), "child_sha256": hashlib.sha256(canonical_bytes(children.get(case_id))).hexdigest()}
        elif assertion_id == "E2E-01..E2E-07_pass":
            actual = all(_validate_child(case, children.get(case), contract, contract_path) for case in ("E2E-03", "E2E-04", "E2E-05", "E2E-06", "E2E-07")) and isinstance(dependencies.get("g2"), dict)
            g2_label = _formal_report_map(contract)["G2"][0]
            evidence = {"kind": "e2e_set", "cases": sorted(children), "g2_sha256": inventory[g2_label].get("sha256")}
        elif assertion_id == "data_review.blocking_findings=0":
            actual = review_states[0] == "valid"
            evidence = {"kind": "review", "path": "reviews/data-review.json", "sha256": inventory["reviews/data-review.json"].get("sha256")}
        elif assertion_id == "workflow_review.blocking_findings=0":
            actual = review_states[1] == "valid"
            evidence = {"kind": "review", "path": "reviews/workflow-review.json", "sha256": inventory["reviews/workflow-review.json"].get("sha256")}
        else:
            actual = False
            evidence = {"kind": "missing_evaluator"}
        output.append({"id": assertion_id, "status": "PASS" if actual else "FAIL", "expected": True, "actual": actual, "evidence": evidence})
    return output


G3_LIVE_HOLD_ASSERTIONS = frozenset({
    "preflight_failure_has_no_persisted_run_or_fake_artifact_refs",
    "E2E-01..E2E-07_pass",
    "current_acceptance_session_live_smoke=PASS",
})


def _g3_contract_profile(contract: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    gate = next((item for item in contract.get("gates", []) if item.get("id") == "G3"), None)
    if gate is None:
        return None, "contract does not define G3"
    try:
        _formal_report_map(contract)
    except (KeyError, TypeError, ValueError) as exc:
        return gate, f"formal report path contract is invalid: {exc}"
    if [item.get("argv") for item in gate.get("verification", [])] != EXPECTED_G3_ARGV:
        return gate, "G3 verification argv identities/order differ from the frozen profile"
    if [item.get("expected_exit_code") for item in gate.get("verification", [])] != [PASS] * len(EXPECTED_G3_ARGV):
        return gate, "G3 expected exit codes differ from the frozen profile"
    if gate.get("assertions") != EXPECTED_G3_ASSERTIONS or len(set(gate.get("assertions", []))) != len(EXPECTED_G3_ASSERTIONS):
        return gate, "G3 assertion identities/order differ from the frozen profile"
    return gate, None


def _g3_inputs(contract: dict[str, Any], contract_path: Path) -> tuple[list[str], dict[str, dict[str, Any] | None], list[str], bool]:
    reports = _formal_report_map(contract)
    g1_state, g1 = _dependency_report(reports["G1"][1], "G1", contract, contract_path)
    g2_state, g2 = _dependency_report(reports["G2"][1], "G2", contract, contract_path)
    states = [g1_state, g2_state]
    version, digest = _contract_identity(contract, contract_path)
    for index, report in enumerate((g1, g2)):
        if (
            states[index] == "stale" and isinstance(report, dict)
            and report.get("contract_version") == version and report.get("contract_sha256") == digest
        ):
            states[index] = "invalid"
    binding_ok = False
    if g1_state == g2_state == "valid" and isinstance(g1, dict) and isinstance(g2, dict):
        g1_path = reports["G1"][1]
        embedded = g2.get("dependency")
        binding_ok = (
            isinstance(embedded, dict)
            and embedded == g1
            and hashlib.sha256(canonical_bytes(embedded)).hexdigest() == sha256_file(g1_path)
        )
        if not binding_ok:
            states[1] = "invalid"
    reviews: dict[str, dict[str, Any] | None] = {}
    review_states = []
    for filename, scope in REVIEW_SCOPES.items():
        state, report = _review_report(FORMAL_ROOT / "reviews" / filename, scope, contract, contract_path)
        review_states.append(state)
        reviews[scope] = report
    return states, {"g1": g1, "g2": g2, **reviews}, review_states, binding_ok


def _normalized_unittest_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"valid", "ran", "terminal", "tests"}:
        return False
    rows = value.get("tests")
    if not isinstance(rows, list) or not rows or value.get("valid") is not True or value.get("terminal") != "OK":
        return False
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "status"} or not isinstance(row.get("id"), str) or row.get("status") != "ok":
            return False
        ids.append(row["id"])
    return value.get("ran") == len(rows) and len(ids) == len(set(ids))


def _g3_commands_semantically_valid(
    commands: Any, contract: dict[str, Any], contract_path: Path,
) -> tuple[bool, bool, dict[str, dict[str, Any] | None]]:
    if not isinstance(commands, list) or len(commands) != len(EXPECTED_G3_ARGV):
        return False, False, {}
    expected_argv = [[sys.executable if token == "{python}" else token for token in row] for row in EXPECTED_G3_ARGV]
    children: dict[str, dict[str, Any] | None] = {}
    live_hold = False
    for index, (command, argv) in enumerate(zip(commands, expected_argv, strict=True)):
        if not isinstance(command, dict) or command.get("argv") != argv or command.get("expected_exit_code") != PASS:
            return False, False, children
        if not all(isinstance(command.get(key), str) and len(command[key]) == 64 for key in ("stdout_sha256", "stderr_sha256")):
            return False, False, children
        try:
            if any(character not in "0123456789abcdef" for key in ("stdout_sha256", "stderr_sha256") for character in command[key]):
                return False, False, children
        except TypeError:
            return False, False, children
        if index < 7:
            if (
                command.get("kind") != "unittest" or command.get("actual_exit_code") != PASS
                or command.get("status") != "PASS" or not _normalized_unittest_valid(command.get("unittest"))
            ):
                return False, False, children
            continue
        case_id = argv[-1]
        child = command.get("child")
        children[case_id] = child
        if command.get("kind") != "e2e" or command.get("case_id") != case_id:
            return False, False, children
        if case_id == "E2E-05" and command.get("actual_exit_code") == HOLD and command.get("status") == "HOLD":
            if not _validate_child(case_id, child, contract, contract_path, allow_hold=True) or child.get("status") != "HOLD":
                return False, False, children
            live_hold = True
        elif (
            command.get("actual_exit_code") != PASS or command.get("status") != "PASS"
            or not _validate_child(case_id, child, contract, contract_path)
        ):
            return False, False, children
    return True, live_hold, children


def _g3_assertion_status(assertions: list[dict[str, Any]], live_hold: bool) -> tuple[int, str]:
    failed = {row.get("id") for row in assertions if row.get("status") != "PASS"}
    if not failed:
        return PASS, "PASS"
    if live_hold and failed <= G3_LIVE_HOLD_ASSERTIONS:
        return HOLD, "HOLD"
    return FAIL, "FAIL"


def run_g3(contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    """Run the strict, evidence-closed G3 implementation.

    This final definition intentionally supersedes the pre-3.4 compatibility
    implementation above; main resolves this binding at call time.
    """
    gate, profile_error = _g3_contract_profile(contract)
    if os.environ.get("LOTTERY_ACCEPTANCE_DEPTH", "0") != "0":
        raise RuntimeError("recursive G3 acceptance invocation is forbidden")
    if profile_error is not None:
        return FAIL, _base_report(
            contract, contract_path, gate="G3", status="FAIL", reason=profile_error,
            input_inventory=_g3_input_inventory(contract), commands=[], e2e_children={}, assertions=[],
        )
    inventory_before = _g3_input_inventory(contract)
    digest_before = _inventory_digest(inventory_before)
    dependency_states, dependencies, review_states, binding_ok = _g3_inputs(contract, contract_path)
    if "invalid" in dependency_states or "invalid" in review_states:
        return FAIL, _base_report(
            contract, contract_path, gate="G3", status="FAIL", reason="invalid dependency, G1 binding, or independent review",
            dependency_states=dependency_states, review_states=review_states, g2_embedded_g1_hash_match=binding_ok,
            input_inventory=inventory_before, input_inventory_sha256=digest_before,
            commands=[], e2e_children={}, assertions=[],
        )
    if any(state != "valid" for state in dependency_states + review_states):
        return HOLD, _base_report(
            contract, contract_path, gate="G3", status="HOLD", reason="required signed dependency or independent review is missing or stale",
            dependency_states=dependency_states, review_states=review_states, g2_embedded_g1_hash_match=binding_ok,
            input_inventory=inventory_before, input_inventory_sha256=digest_before,
            commands=[], e2e_children={}, assertions=[],
        )

    environment = os.environ.copy()
    environment["LOTTERY_ACCEPTANCE_DEPTH"] = "1"
    commands = []
    children: dict[str, dict[str, Any] | None] = {}
    for index, verification in enumerate(gate["verification"]):
        argv = [sys.executable if token == "{python}" else token for token in verification["argv"]]
        command, child = _g3_machine_command(argv, PASS, environment, index, contract, contract_path)
        commands.append(command)
        if index >= 7:
            children[argv[-1]] = child

    inventory_after = _g3_input_inventory(contract)
    digest_after = _inventory_digest(inventory_after)
    stable_inputs = inventory_after == inventory_before and digest_after == digest_before
    assertions = _evaluate_g3_assertions(
        contract, contract_path, inventory_before, commands, children, dependencies, review_states,
    )
    commands_valid, live_hold, normalized_children = _g3_commands_semantically_valid(commands, contract, contract_path)
    if normalized_children != children:
        commands_valid = False
    code, status = _g3_assertion_status(assertions, live_hold)
    if not stable_inputs or not commands_valid:
        code, status = FAIL, "FAIL"
    return code, _base_report(
        contract, contract_path, gate="G3", status=status,
        dependency_states=dependency_states, review_states=review_states,
        g2_embedded_g1_hash_match=binding_ok,
        input_inventory=inventory_before, input_inventory_sha256=digest_before,
        input_inventory_after_sha256=digest_after, input_inventory_stable=stable_inputs,
        commands=commands, e2e_children=children, assertions=assertions,
    )


def run_all(contract: dict[str, Any], contract_path: Path) -> tuple[int, dict[str, Any]]:
    """Recompute G3 semantics from its typed evidence without executing commands."""
    if os.environ.get("LOTTERY_ACCEPTANCE_DEPTH", "0") != "0":
        raise RuntimeError("recursive ALL acceptance invocation is forbidden")
    _, profile_error = _g3_contract_profile(contract)
    inventory = _g3_input_inventory(contract)
    inventory_sha = _inventory_digest(inventory)
    dependency_states, dependencies, review_states, binding_ok = _g3_inputs(contract, contract_path)
    g3_path = _formal_report_map(contract)["G3"][1]
    g3: dict[str, Any] | None = None
    g3_parse_invalid = False
    try:
        g3 = json.loads(g3_path.read_text(encoding="utf-8")) if g3_path.is_file() else None
    except Exception:
        g3 = None
        g3_parse_invalid = True

    missing = any(state in {"missing", "stale"} for state in dependency_states + review_states) or g3 is None
    invalid = profile_error is not None or g3_parse_invalid or "invalid" in dependency_states + review_states
    reason = profile_error
    recomputed_assertions: list[dict[str, Any]] = []
    commands_valid = False
    live_hold = False
    children: dict[str, dict[str, Any] | None] = {}
    g3_identity_ok = False
    g3_inventory_ok = False
    g3_assertions_ok = False
    recomputed_code, recomputed_status = FAIL, "FAIL"
    if isinstance(g3, dict):
        version, digest = _contract_identity(contract, contract_path)
        g3_identity_ok = (
            g3.get("artifact_type") == "phase1_gate_acceptance" and g3.get("gate") == "G3"
            and g3.get("contract_version") == version and g3.get("contract_sha256") == digest
        )
        g3_inventory_ok = (
            g3.get("input_inventory") == inventory and g3.get("input_inventory_sha256") == inventory_sha
            and g3.get("input_inventory_after_sha256") == inventory_sha and g3.get("input_inventory_stable") is True
        )
        commands_valid, live_hold, children = _g3_commands_semantically_valid(g3.get("commands"), contract, contract_path)
        if commands_valid:
            recomputed_assertions = _evaluate_g3_assertions(
                contract, contract_path, inventory, g3["commands"], children, dependencies, review_states,
            )
            recomputed_code, recomputed_status = _g3_assertion_status(recomputed_assertions, live_hold)
        g3_assertions_ok = g3.get("assertions") == recomputed_assertions
        invalid |= not (
            g3_identity_ok and g3_inventory_ok and commands_valid and g3.get("e2e_children") == children
            and g3_assertions_ok and g3.get("status") == recomputed_status
            and g3.get("dependency_states") == dependency_states and g3.get("review_states") == review_states
            and g3.get("g2_embedded_g1_hash_match") is binding_ok
        )
        reason = reason or ("G3 report does not close under ALL semantic recomputation" if invalid else None)

    if invalid:
        code, status = FAIL, "FAIL"
    elif missing:
        code, status = HOLD, "HOLD"
        reason = reason or "required signed input report is missing or stale"
    else:
        code, status = recomputed_code, recomputed_status
    return code, _base_report(
        contract, contract_path, gate="ALL", status=status, reason=reason,
        dependency_states=dependency_states, review_states=review_states,
        g2_embedded_g1_hash_match=binding_ok,
        input_inventory=inventory, input_inventory_sha256=inventory_sha,
        g3_sha256=sha256_file(g3_path) if g3_path.is_file() else None,
        g3_checks={
            "identity": g3_identity_ok, "input_inventory": g3_inventory_ok,
            "commands": commands_valid, "assertions_recomputed": g3_assertions_ok,
        },
        assertions=recomputed_assertions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--gate")
    parser.add_argument("--execute-case")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    contract_path = args.contract.resolve()
    output: Path | None = None
    try:
        contract = load_contract(contract_path)
        if args.execute_case:
            if args.output is not None:
                raise ValueError("E2E cases write only to isolated temporary roots and stdout")
            code, report = _dispatch_case(args.execute_case, contract, contract_path)
        else:
            gate = args.gate or "G1"
            known = {item["id"] for item in contract["gates"]}
            if gate not in known and gate != "ALL":
                raise KeyError(f"unknown gate: {gate}")
            reports = _formal_report_map(contract)
            if gate == "G1":
                code, report = run_g1(contract, contract_path)
                expected_output = reports["G1"][1]
                if args.output is not None and args.output.resolve() != expected_output:
                    raise ValueError(f"G1 output must be {expected_output}")
                output = expected_output
            elif gate == "G2":
                expected_output = reports["G2"][1]
                if args.output is not None and args.output.resolve() != expected_output:
                    raise ValueError(f"G2 output must be {expected_output}")
                code, report = run_g2(contract, contract_path)
                output = expected_output
            elif gate == "G3":
                expected_output = reports["G3"][1]
                if args.output is not None and args.output.resolve() != expected_output:
                    raise ValueError(f"G3 output must be {expected_output}")
                code, report = run_g3(contract, contract_path)
                output = expected_output
            elif gate == "ALL":
                expected_output = reports["ALL"][1]
                if args.output is not None and args.output.resolve() != expected_output:
                    raise ValueError(f"ALL output must be {expected_output}")
                code, report = run_all(contract, contract_path)
                output = expected_output
            else:
                report = _base_report(contract, contract_path, gate=gate, status="FAIL", reason="gate is known but not implemented by this runner")
                code = FAIL
    except Exception as exc:
        code = FAIL
        report = {"schema_version": "1.0.0", "status": "FAIL", "error_type": type(exc).__name__, "reason": str(exc)}
    if output is not None:
        write_report(output.resolve(), report)
    sys.stdout.buffer.write(canonical_bytes(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
