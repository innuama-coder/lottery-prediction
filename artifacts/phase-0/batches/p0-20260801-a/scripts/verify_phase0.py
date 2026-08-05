"""Offline, fail-closed verifier for Phase 0 machine evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from phase0lib import (
    ValidationError,
    canonical_sha256,
    find_nulls,
    lint_strict_schema,
    load_json,
    schemas_manifest_sha256,
    sha256_bytes,
    sha256_file,
    validate_json_file,
    validate_jsonl_file,
)


SCHEMA_BY_KEY = {
    "scope_freeze": "scope-freeze.schema.json",
    "source_catalog": "source-catalog.schema.json",
    "field_contract": "field-contract.schema.json",
    "rule_bundles": "rule-bundles.schema.json",
    "observation_plan": "observation-plan.schema.json",
    "reviewer_assignment": "reviewer-assignment.schema.json",
    "environment_lock": "environment-lock.schema.json",
    "verification_command": "verification-command.schema.json",
    "evidence_manifest": "evidence-manifest.schema.json",
    "normalized_records": "normalized-records.schema.json",
    "reconciliation": "reconciliation.schema.json",
    "coverage_report": "coverage-report.schema.json",
    "revision_report": "revision-report.schema.json",
    "soak_log": "soak-log.schema.json",
    "replay_report": "replay-report.schema.json",
    "reviewer_attestation": "reviewer-attestation.schema.json",
    "stage1_handoff_fixture": "stage1-handoff-fixture.schema.json",
}

JSONL_KEYS = {"evidence_manifest", "reconciliation", "soak_log"}
P0_01_KEYS = {"scope_freeze", "observation_plan", "reviewer_assignment", "verification_command"}
ALL_GATES = {
    "G-SCOPE", "G-AUTHORITY", "G-COMPLIANCE", "G-SCHEMA", "G-PROVENANCE",
    "G-RULES", "G-TIME", "G-PARSE", "G-CORRECTNESS", "G-COVERAGE",
    "G-REVISION", "G-RECOVERY", "G-REPRODUCIBILITY", "G-HANDOFF",
}
BOOTSTRAP_COMMAND = (
    "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/verify_phase0.ps1 "
    "--contract docs/roadmap/phase-0-acceptance-contract.json --artifacts artifacts/phase-0 --stage p0-01"
)
FULL_REPLAY_COMMAND = (
    "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/verify_phase0.ps1 "
    "--contract docs/roadmap/phase-0-acceptance-contract.json --artifacts artifacts/phase-0 --stage full"
)
VERIFIER_FILES = {
    "scripts/phase0/verify_phase0.ps1",
    "scripts/phase0/verify_phase0.py",
    "scripts/phase0/phase0lib.py",
    "scripts/phase0/hash_artifact.py",
}


def fail(condition: bool, message: str) -> None:
    if condition:
        raise ValidationError(message)


def repo_path(repo_root: Path, relative: str) -> Path:
    candidate = (repo_root / relative).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValidationError(f"path escapes repository: {relative}") from exc
    return candidate


def validate_schema_inventory(contract: dict[str, Any], schema_dir: Path) -> None:
    declared = contract["schema_policy"]["machine_artifacts_requiring_schema"]
    fail(set(declared) != set(SCHEMA_BY_KEY), "contract machine schema keys do not match verifier inventory")
    actual = {path.name for path in schema_dir.glob("*.schema.json")}
    expected = set(SCHEMA_BY_KEY.values())
    fail(actual != expected, f"schema inventory mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    for filename in sorted(expected):
        schema = load_json(schema_dir / filename)
        lint_strict_schema(schema, source=str(schema_dir / filename))


def validate_artifact(key: str, artifact_path: Path, schema_dir: Path) -> Any:
    schema_path = schema_dir / SCHEMA_BY_KEY[key]
    if key in JSONL_KEYS:
        return validate_jsonl_file(artifact_path, schema_path)
    return validate_json_file(artifact_path, schema_path)


def verify_hash(value: Any, expected: str, label: str) -> None:
    actual = canonical_sha256(value)
    fail(actual != expected, f"{label} canonical SHA-256 mismatch: expected {expected}, got {actual}")


def verify_scope(scope: dict[str, Any], contract_path: Path) -> None:
    nulls = list(find_nulls(scope))
    fail(bool(nulls), f"scope-freeze contains nulls: {nulls[:5]}")
    fail(scope["source_observation_started"] is not False, "source observation must not have started at freeze")
    fail(Path(scope["contract"]["path"]).as_posix() != "docs/roadmap/phase-0-acceptance-contract.json", "unexpected contract path")
    fail(sha256_file(contract_path) != scope["contract"]["sha256"], "frozen contract hash mismatch")
    games = {entry["game"]: entry for entry in scope["games"]}
    fail(set(games) != {"dlt", "ssq"}, "scope must contain dlt and ssq exactly once")
    for game, item in games.items():
        target = item["target_interval"]
        minimum = item["minimum_viable_interval"]
        fail(int(target["start_issue"]) > int(target["end_issue"]), f"{game}: reversed target interval")
        fail(int(minimum["start_issue"]) > int(minimum["end_issue"]), f"{game}: reversed minimum interval")
        fail(int(minimum["start_issue"]) < int(target["start_issue"]) or int(minimum["end_issue"]) > int(target["end_issue"]), f"{game}: minimum interval is not within target")

    sample = scope["corroboration_sample"]
    seed = sample["seed"]
    fail(sha256_bytes(seed.encode("utf-8")) != sample["seed_sha256"], "sample seed hash mismatch")
    sample_games = {entry["game"]: entry for entry in sample["games"]}
    fail(set(sample_games) != {"dlt", "ssq"}, "sample design must contain dlt and ssq exactly once")
    for game, game_sample in sample_games.items():
        combined: list[str] = []
        stratum_ids: set[str] = set()
        for stratum in game_sample["strata"]:
            fail(stratum["stratum_id"] in stratum_ids, f"{game}: duplicate sample stratum")
            stratum_ids.add(stratum["stratum_id"])
            candidates = stratum["candidate_issue_ids"]
            selected = stratum["selected_issue_ids"]
            verify_hash(candidates, stratum["candidate_universe_sha256"], f"{game}/{stratum['stratum_id']} candidate universe")
            verify_hash(selected, stratum["selected_issue_ids_sha256"], f"{game}/{stratum['stratum_id']} selection")
            fail(stratum["sample_size"] > len(candidates), f"{game}: sample larger than universe")
            year = candidates[0][:4]
            fail(any(issue[:4] != year for issue in candidates), f"{game}: mixed years in stratum")
            ranked = sorted(candidates, key=lambda issue: (sha256_bytes(f"{seed}|{game}|{year}|{issue}".encode("utf-8")), issue))
            expected = sorted(ranked[: stratum["sample_size"]])
            fail(selected != expected, f"{game}/{stratum['stratum_id']}: deterministic sample does not replay")
            combined.extend(selected)
        fail(game_sample["final_selected_issue_ids"] != combined, f"{game}: final selected IDs are not the ordered concatenation of strata")
        verify_hash(combined, game_sample["final_selected_issue_ids_sha256"], f"{game} final selection")


def verify_observation(scope: dict[str, Any], observation: dict[str, Any]) -> None:
    nulls = list(find_nulls(observation))
    fail(bool(nulls), f"observation-plan contains nulls: {nulls[:5]}")
    fail(observation["freeze_id"] != scope["freeze_id"], "observation freeze_id mismatch")
    fail(observation["frozen_at_utc"] != scope["frozen_at_utc"], "observation frozen_at mismatch")
    scope_games = {entry["game"]: entry for entry in scope["games"]}
    plan_games = {entry["game"]: entry for entry in observation["games"]}
    fail(set(plan_games) != {"dlt", "ssq"}, "observation must contain dlt and ssq exactly once")
    fail(any(item["acceptance_cutoff_utc"] != observation["acceptance_cutoff_utc"] for item in scope_games.values()), "acceptance cutoffs disagree")
    requests = observation["request_schedule"]
    verify_hash(requests, observation["request_schedule_sha256"], "request schedule")
    fail(len({item["request_id"] for item in requests}) != len(requests), "duplicate request_id")
    fail([item["sequence"] for item in requests] != list(range(1, len(requests) + 1)), "request sequence must be contiguous")
    slot_priority = {"authoritative_primary": 0, "official_corroborator_if_available": 1}
    expected_order = sorted(
        requests,
        key=lambda item: (item["scheduled_at_utc"], slot_priority[item["source_slot"]], item["game"], item["request_id"]),
    )
    fail(requests != expected_order, "request schedule must be globally sorted by UTC, source-slot priority, game, and request_id")
    fail(any(item["scheduled_at_utc"] > observation["acceptance_cutoff_utc"] for item in requests), "request scheduled after cutoff")
    fail(observation["budgets"]["total_request_limit"] < len(requests), "request schedule exceeds total request budget")
    retry_need = len(requests) * (observation["retry_policy"]["maximum_attempts_per_request"] - 1)
    fail(observation["budgets"]["total_retry_limit"] < retry_need, "retry budget cannot cover frozen worst-case attempts")
    for game, plan in plan_games.items():
        game_requests = [item for item in requests if item["game"] == game]
        fail(len(game_requests) != plan["planned_request_count"], f"{game}: planned request count mismatch")
        times = [item["scheduled_at_utc"] for item in game_requests]
        fail(min(times) != plan["first_request_at_utc"] or max(times) != plan["last_request_at_utc"], f"{game}: first/last request mismatch")
        primary = [item for item in game_requests if item["source_slot"] == "authoritative_primary"]
        fail(len(primary) != plan["planned_draw_count"], f"{game}: planned draw count does not match primary requests")
        fail(plan["minimum_complete_draw_cycles"] < 2, f"{game}: fewer than two cycles")


def verify_reviewer(scope: dict[str, Any], assignment: dict[str, Any]) -> None:
    nulls = list(find_nulls(assignment))
    fail(bool(nulls), f"reviewer-assignment contains nulls: {nulls[:5]}")
    fail(assignment["freeze_id"] != scope["freeze_id"], "reviewer freeze_id mismatch")
    fail(assignment["frozen_at_utc"] != scope["frozen_at_utc"], "reviewer frozen_at mismatch")
    reviewer_ids = {entry["reviewer_id"] for entry in assignment["reviewers"]}
    fail(len(reviewer_ids) != len(assignment["reviewers"]), "duplicate reviewer IDs")
    roles = assignment["role_separation"]
    executor_ids, parser_ids, declared_reviewer_ids = map(set, (roles["executor_ids"], roles["parser_author_ids"], roles["reviewer_ids"]))
    fail(bool(executor_ids & parser_ids or executor_ids & declared_reviewer_ids or parser_ids & declared_reviewer_ids), "role sets are not pairwise disjoint")
    fail(declared_reviewer_ids != reviewer_ids, "reviewer IDs disagree with role separation")
    fail(set(assignment["independence_declaration"]["declared_by_reviewer_ids"]) != reviewer_ids, "independence declarants disagree")


def verify_command(repo_root: Path, contract_path: Path, artifacts: Path, schema_dir: Path, command: dict[str, Any]) -> None:
    fail(command["bootstrap_gate_command"] != BOOTSTRAP_COMMAND, "P0-01 bootstrap command is not canonical")
    fail(command["bootstrap_expected_exit_code"] != 0, "P0-01 bootstrap expected exit code must be zero")
    fail(command["command"] != FULL_REPLAY_COMMAND, "verification command must be the canonical full offline replay")
    fail(command["full_replay_command"] != FULL_REPLAY_COMMAND, "full_replay_command is not canonical")
    fail(command["command"] != command["full_replay_command"], "command and full_replay_command must be identical")
    fail(command["working_directory"] != ".", "verification working directory must be repository root")
    interpreter = Path(command["interpreter_path"])
    fail(not interpreter.is_absolute() or not interpreter.is_file(), "frozen interpreter absolute path is unavailable")
    fail(sha256_file(interpreter) != command["interpreter_sha256"], "frozen interpreter executable hash mismatch")
    version_result = subprocess.run(
        [str(interpreter), "-c", "import platform; print(platform.python_version())"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    fail(version_result.returncode != 0, "cannot query frozen interpreter version")
    fail(version_result.stdout.strip() != command["interpreter_version"], "frozen interpreter version mismatch")
    running_interpreter = Path(sys.executable).resolve()
    fail(not running_interpreter.is_file(), "running interpreter path is unavailable")
    fail(sha256_file(running_interpreter) != command["interpreter_sha256"], "running interpreter executable hash differs from frozen interpreter")
    fail(platform.python_version() != command["interpreter_version"], "running interpreter version differs from frozen interpreter")
    fail(not repo_path(repo_root, command["launcher_path"]).is_file(), "verification launcher is missing")
    fail(command["contract_sha256"] != sha256_file(contract_path), "verification contract hash mismatch")
    fail(command["schemas_manifest_sha256"] != schemas_manifest_sha256(schema_dir), "verification schemas manifest hash mismatch")
    schema_records = {item["path"]: item["sha256"] for item in command["schema_hashes"]}
    expected_paths = {f"artifacts/phase-0/schemas/{name}" for name in SCHEMA_BY_KEY.values()}
    fail(set(schema_records) != expected_paths, "verification schema_hashes inventory mismatch")
    for relative, expected in schema_records.items():
        fail(sha256_file(repo_path(repo_root, relative)) != expected, f"frozen schema hash mismatch: {relative}")
    verifier_records = {item["path"]: item["sha256"] for item in command["verifier_file_hashes"]}
    fail(set(verifier_records) != VERIFIER_FILES, "verification tool hash inventory mismatch")
    for relative, expected in verifier_records.items():
        fail(sha256_file(repo_path(repo_root, relative)) != expected, f"frozen verification tool hash mismatch: {relative}")
    planned = {
        "scope_freeze": artifacts / "scope-freeze.json",
        "observation_plan": artifacts / "observation-plan.json",
        "reviewer_assignment": artifacts / "reviewer-assignment.json",
    }
    for key, path in planned.items():
        fail(command["frozen_input_hashes"][key] != sha256_file(path), f"frozen artifact hash mismatch: {key}")
    sidecar = repo_path(repo_root, command["self_hash_sidecar"])
    fail(not sidecar.is_file(), "verification command hash sidecar is missing")
    text = sidecar.read_text(encoding="ascii").strip()
    fail(re.fullmatch(r"[0-9a-f]{64}", text) is None, "verification command hash sidecar is malformed")
    fail(text != sha256_file(artifacts / "verification-command.json"), "verification command sidecar hash mismatch")


def verify_p0_01(repo_root: Path, contract_path: Path, artifacts: Path, schema_dir: Path) -> None:
    paths = {
        "scope_freeze": artifacts / "scope-freeze.json",
        "observation_plan": artifacts / "observation-plan.json",
        "reviewer_assignment": artifacts / "reviewer-assignment.json",
        "verification_command": artifacts / "verification-command.json",
    }
    for key, path in paths.items():
        fail(not path.is_file(), f"missing P0-01 artifact: {path}")
    values = {key: validate_artifact(key, path, schema_dir) for key, path in paths.items()}
    versions = {value["contract_version"] for value in values.values()}
    fail(versions != {"1.3"}, "P0-01 contract versions disagree")
    verify_scope(values["scope_freeze"], contract_path)
    verify_observation(values["scope_freeze"], values["observation_plan"])
    verify_reviewer(values["scope_freeze"], values["reviewer_assignment"])
    verify_command(repo_root, contract_path, artifacts, schema_dir, values["verification_command"])


def verify_normalized_record(record: dict[str, Any], label: str) -> None:
    front = record["front_numbers"]
    back = record["back_numbers"]
    fail(front != sorted(front) or back != sorted(back), f"{label}: lottery numbers must be strictly ascending")
    if record["game"] == "dlt":
        fail(len(front) != 5 or len(back) != 2, f"{label}: DLT must contain 5 front and 2 back numbers")
        fail(any(not 1 <= int(value) <= 35 for value in front), f"{label}: DLT front number out of range")
        fail(any(not 1 <= int(value) <= 12 for value in back), f"{label}: DLT back number out of range")
    else:
        fail(len(front) != 6 or len(back) != 1, f"{label}: SSQ must contain 6 front and 1 back numbers")
        fail(any(not 1 <= int(value) <= 33 for value in front), f"{label}: SSQ front number out of range")
        fail(any(not 1 <= int(value) <= 16 for value in back), f"{label}: SSQ back number out of range")


def verify_provenance(repo_root: Path, environment_lock_path: Path, records: list[dict[str, Any]]) -> None:
    environment_hash = sha256_file(environment_lock_path)
    evidence_ids: set[str] = set()
    for entry in records:
        label = f"evidence {entry['evidence_id']}"
        fail(entry["evidence_id"] in evidence_ids, f"duplicate {label}")
        evidence_ids.add(entry["evidence_id"])
        raw_path = repo_path(repo_root, entry["stored_payload_path"])
        fail(not raw_path.is_file(), f"{label}: raw payload is missing")
        fail(sha256_file(raw_path) != entry["stored_payload_sha256"], f"{label}: raw payload hash mismatch")
        fail(entry["environment_lock_sha256"] != environment_hash, f"{label}: environment lock hash mismatch")
        normalized_path = repo_path(repo_root, entry["normalized_record_ref"])
        fail(not normalized_path.is_file(), f"{label}: normalized record is missing")
        normalized = load_json(normalized_path)
        fail(canonical_sha256(normalized) != entry["normalized_record_sha256"], f"{label}: normalized canonical hash mismatch")
        verify_normalized_record(normalized, str(normalized_path))


def expected_project_decision(outcomes: list[str]) -> str:
    if outcomes.count("PASS_FULL") == 2:
        return "GO"
    if any(outcome in {"PASS_FULL", "PASS_LIMITED"} for outcome in outcomes):
        return "LIMITED_GO"
    if "HOLD" in outcomes:
        return "HOLD"
    if outcomes.count("STOP") == 2:
        return "STOP"
    raise ValidationError("per-game outcomes do not produce a unique project decision")


def verify_full(contract: dict[str, Any], repo_root: Path, contract_path: Path, artifacts: Path, schema_dir: Path) -> None:
    verify_p0_01(repo_root, contract_path, artifacts, schema_dir)
    planned = contract["planned_artifacts"]
    validated: dict[str, Any] = {}
    for key in SCHEMA_BY_KEY:
        if key in P0_01_KEYS:
            continue
        relative = planned[key]
        path = repo_path(repo_root, relative)
        if key == "normalized_records":
            fail(not path.is_dir(), f"missing normalized records directory: {path}")
            records = sorted(path.rglob("*.json")) + sorted(path.rglob("*.jsonl"))
            fail(not records, "normalized records directory is empty")
            for record_path in records:
                if record_path.suffix == ".jsonl":
                    values = validate_jsonl_file(record_path, schema_dir / SCHEMA_BY_KEY[key])
                    for index, value in enumerate(values, 1):
                        verify_normalized_record(value, f"{record_path}:{index}")
                else:
                    value = validate_json_file(record_path, schema_dir / SCHEMA_BY_KEY[key])
                    verify_normalized_record(value, str(record_path))
        else:
            fail(not path.is_file(), f"missing artifact: {path}")
            validated[key] = validate_artifact(key, path, schema_dir)
    raw_path = repo_path(repo_root, planned["raw_snapshots"])
    fail(not raw_path.is_dir() or not any(path.is_file() for path in raw_path.rglob("*")), "raw snapshot directory is missing or empty")
    report_path = repo_path(repo_root, planned["acceptance_report"])
    fail(not report_path.is_file() or report_path.stat().st_size == 0, "acceptance report is missing or empty")
    verify_provenance(
        repo_root,
        repo_path(repo_root, planned["environment_lock"]),
        validated["evidence_manifest"],
    )
    replay = validated["replay_report"]
    gate_ids = [entry["gate_id"] for entry in replay["gate_results"]]
    fail(set(gate_ids) != ALL_GATES or len(gate_ids) != len(set(gate_ids)), "replay report must evaluate each hard gate exactly once")
    handoff = validated["stage1_handoff_fixture"]
    active, excluded = set(handoff["active_games"]), set(handoff["excluded_games"])
    fail(active & excluded or active | excluded != {"dlt", "ssq"}, "handoff games must form an exact partition")
    results = {entry["game"]: entry for entry in handoff["game_results"]}
    fail(set(results) != {"dlt", "ssq"}, "handoff game results must contain both games exactly once")
    for game, result in results.items():
        passing = result["per_game_outcome"] in {"PASS_FULL", "PASS_LIMITED"}
        fail((game in active) != passing, f"{game}: active/excluded classification contradicts outcome")
        expected_coverage = {"PASS_FULL": "target", "PASS_LIMITED": "minimum_viable"}.get(result["per_game_outcome"], "none")
        fail(result["coverage_tier"] != expected_coverage, f"{game}: coverage tier contradicts outcome")
    outcomes = [results[game]["per_game_outcome"] for game in ("dlt", "ssq")]
    fail(handoff["project_decision"] != expected_project_decision(outcomes), "handoff project decision contradicts ordered decision logic")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--stage", choices=("p0-01", "full"), default="full")
    args = parser.parse_args(argv)
    try:
        repo_root = Path.cwd().resolve()
        contract_path = args.contract.resolve()
        artifacts = args.artifacts.resolve()
        contract = load_json(contract_path)
        fail(contract.get("version") != "1.3", "unsupported Phase 0 contract version")
        fail(contract.get("contract_status") not in {"ready_to_start_p0_01", "p0_01_frozen", "in_progress", "completed"}, "invalid contract status")
        schema_dir = artifacts / "schemas"
        validate_schema_inventory(contract, schema_dir)
        if args.stage == "p0-01":
            verify_p0_01(repo_root, contract_path, artifacts, schema_dir)
        else:
            verify_full(contract, repo_root, contract_path, artifacts, schema_dir)
        print(json.dumps({"status": "PASS", "stage": args.stage, "contract_version": contract["version"], "network_used": False}, separators=(",", ":")))
        return 0
    except (ValidationError, KeyError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "FAIL", "stage": args.stage, "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
