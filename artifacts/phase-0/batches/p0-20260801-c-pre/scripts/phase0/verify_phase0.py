"""Offline, fail-closed verifier for Phase 0 machine evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phase0lib import (
    ValidationError,
    canonical_sha256,
    find_nulls,
    lint_strict_schema,
    load_json,
    load_jsonl,
    schemas_manifest_sha256,
    sha256_bytes,
    sha256_file,
    validate_json_file,
    validate_jsonl_file,
    validate_schema_instance,
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
AUXILIARY_SCHEMAS = {
    "p0-04-evidence-migration.schema.json",
    "p0-05-work-plan.schema.json",
    "p0-06-runtime-plan.schema.json",
    "p0-06-scheduler-install-audit.schema.json",
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
    "scripts/phase0/p0_06_runner.py",
    "scripts/phase0/install_p0_06_scheduled_task.ps1",
    "scripts/phase0/p0_04_http.py",
    "scripts/phase0/p0_04_pipeline.py",
    "scripts/phase0/p0_04_parser.py",
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
    expected = set(SCHEMA_BY_KEY.values()) | AUXILIARY_SCHEMAS
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
    expected_paths = {f"artifacts/phase-0/schemas/{name}" for name in (set(SCHEMA_BY_KEY.values()) | AUXILIARY_SCHEMAS)}
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


def frozen_issue_keys(scope: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for game_entry in scope["corroboration_sample"]["games"]:
        game = game_entry["game"]
        for stratum in game_entry["strata"]:
            for issue_id in stratum["candidate_issue_ids"]:
                key = (game, issue_id)
                fail(key in keys, f"{game}/{issue_id}: duplicate frozen candidate issue")
                keys.add(key)
    fail(len(keys) != 700, f"frozen candidate universe must contain exactly 700 game/issues, got {len(keys)}")
    return keys


def verify_source_catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    games = {entry["game"]: entry for entry in catalog["games"]}
    fail(set(games) != {"dlt", "ssq"} or len(games) != len(catalog["games"]), "source catalog must contain dlt and ssq exactly once")
    source_ids: set[str] = set()
    computed_readiness: dict[str, dict[str, Any]] = {}
    for game, entry in games.items():
        sources = [("authoritative_primary", entry["authoritative_primary"])] + [
            ("official_corroborator", source) for source in entry["official_corroborators"]
        ]
        for slot, source in sources:
            label = f"{game}/{source['source_id']}"
            fail(source["source_id"] in source_ids, f"duplicate source_id: {source['source_id']}")
            source_ids.add(source["source_id"])
            fail(source["role"] != slot, f"{label}: source role contradicts catalog slot")
            fail(slot == "authoritative_primary" and source["authority_tier"] != "A", f"{label}: authoritative primary must be tier A")
            observed = source["observed_access"]
            status, outcome, approved = observed["http_status"], observed["outcome"], source["approved_use"]
            fail(status in {403, 567} and outcome != "blocked", f"{label}: HTTP {status} must remain an observed blocked state")
            fail(outcome == "accessible" and not 200 <= status < 400, f"{label}: accessible outcome contradicts HTTP status")
            fail(outcome == "accessible" and not observed["response_content_type"], f"{label}: accessible observation lacks response_content_type")
            fail(outcome == "blocked" and approved != "blocked", f"{label}: blocked access must use approved_use=blocked")
            fail(approved == "blocked" and outcome != "blocked", f"{label}: approved_use=blocked contradicts observed outcome")
            fail(observed["redirect_chain"] and observed["redirect_chain"][-1] != observed["final_url"], f"{label}: redirect chain does not end at final_url")
            fail(not observed["redirect_chain"] and observed["final_url"] != source["url"], f"{label}: final_url drift lacks a redirect chain")
            if approved == "scheduled_low_rate_fetch":
                fail(outcome != "accessible", f"{label}: scheduled fetch requires accessible observation")
                fail(observed["access_controls_bypassed"], f"{label}: scheduled fetch cannot bypass access controls")
                fail(any(observed[name] for name in ("requires_login", "captcha_observed", "javascript_required", "token_required")), f"{label}: scheduled public fetch cannot require login, captcha, JavaScript, or token")
                compliance = source["compliance_evidence"]
                fail(source["rate_policy"]["max_requests_per_minute"] > 2, f"{label}: scheduled fetch exceeds two requests per minute")
                fail(compliance["terms_status"] == "restricted", f"{label}: scheduled fetch is forbidden by restricted terms")
                fail(compliance["robots_status"] in {"disallowed", "unreachable"}, f"{label}: scheduled fetch is forbidden by robots status")
                fail(not observed["evidence_refs"] or not compliance["terms_refs"] or not compliance["robots_refs"] or not compliance["copyright_refs"], f"{label}: scheduled fetch lacks official access/compliance evidence references")
            fail(approved == "hold_pending" and outcome == "blocked", f"{label}: blocked observation must not masquerade as hold_pending")
            fail(source["review_due"] < catalog["assessed_at_utc"][:10], f"{label}: review_due predates assessment")
        scheduled = sorted(source["source_id"] for _, source in sources if source["approved_use"] == "scheduled_low_rate_fetch")
        held = sorted(source["source_id"] for _, source in sources if source["approved_use"] in {"pending", "hold_pending"})
        blocked = sorted(source["source_id"] for _, source in sources if source["approved_use"] == "blocked")
        conclusion = "ready" if scheduled else "hold" if held else "blocked"
        computed_readiness[game] = {
            "game": game,
            "acquisition_ready": bool(scheduled),
            "scheduled_source_ids": scheduled,
            "hold_source_ids": held,
            "blocked_source_ids": blocked,
            "policy_conclusion": conclusion,
        }
    declared = {entry["game"]: entry for entry in catalog["operational_readiness"]}
    fail(set(declared) != {"dlt", "ssq"} or len(declared) != len(catalog["operational_readiness"]), "operational readiness must contain dlt and ssq exactly once")
    for game, expected in computed_readiness.items():
        fail(declared[game] != expected, f"{game}: declared operational readiness contradicts source policies")
    return computed_readiness


def _schema_types(schema: dict[str, Any], root: dict[str, Any]) -> set[str]:
    if "$ref" in schema:
        current: Any = root
        for token in schema["$ref"][2:].split("/"):
            current = current[token.replace("~1", "/").replace("~0", "~")]
        return _schema_types(current, root)
    if "type" in schema:
        return {schema["type"]} if isinstance(schema["type"], str) else set(schema["type"])
    if "enum" in schema:
        return {"null" if value is None else "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "string" for value in schema["enum"]}
    types: set[str] = set()
    for child in schema.get("anyOf", []):
        types |= _schema_types(child, root)
    return types


def verify_field_contract(field_contract: dict[str, Any], normalized_schema: dict[str, Any]) -> None:
    fields = {entry["name"]: entry for entry in field_contract["fields"]}
    fail(len(fields) != len(field_contract["fields"]), "field contract contains duplicate field names")
    expected = set(normalized_schema["required"]) - {"schema_version", "artifact_type"}
    fail(set(fields) != expected, f"field contract and normalized schema fields disagree: missing={sorted(expected-set(fields))}, extra={sorted(set(fields)-expected)}")
    for name, entry in fields.items():
        types = _schema_types(normalized_schema["properties"][name], normalized_schema)
        fail(entry["json_type"] not in types, f"field {name}: json_type contradicts normalized schema")
        fail(entry["nullable"] != ("null" in types), f"field {name}: nullable contradicts normalized schema")
    for name in ("draw_date_local", "draw_at"):
        policy = (fields[name]["source_semantics"] + " " + fields[name]["normalization"]).lower()
        fail("official" not in policy, f"field {name}: official source semantics are missing")
        for forbidden_source in ("schedule", "url", "http", "retrieval", "publication"):
            fail(forbidden_source not in policy, f"field {name}: non-substitution policy omits {forbidden_source}")
    fail(not fields["draw_date_local"]["evidence_required"], "draw_date_local must require evidence")
    fail(not fields["draw_at"]["nullable"], "draw_at must remain nullable when actual time is not evidenced")
    fail(not fields["available_at"]["nullable"] or not fields["corroboration_tier"]["nullable"], "non-verified availability and corroboration must be nullable")


def verify_rule_bundles(scope: dict[str, Any], artifact: dict[str, Any]) -> None:
    evidence = {item["evidence_id"]: item for item in artifact["evidence"]}
    fail(len(evidence) != len(artifact["evidence"]), "duplicate rule evidence_id")
    fail(len({item["url"] for item in artifact["evidence"]}) != len(evidence), "rule evidence URLs must be unique stable entries")

    def check_refs(refs: list[str], game: str, label: str) -> None:
        for evidence_id in refs:
            fail(evidence_id not in evidence, f"{label}: unknown evidence reference {evidence_id}")
            fail(evidence[evidence_id]["game"] != game, f"{label}: cross-game evidence reference {evidence_id}")
            fail(evidence[evidence_id]["status"] != "verified", f"{label}: pending evidence cannot support a frozen rule")

    registries: dict[str, dict[str, dict[str, Any]]] = {}
    for axis in ("number_space_versions", "draw_process_versions", "prize_rule_versions"):
        entries = artifact["version_registry"][axis]
        index = {entry["version_id"]: entry for entry in entries}
        fail(len(index) != len(entries), f"duplicate version_id in {axis}")
        for version_id, entry in index.items():
            check_refs(entry["evidence_refs"], entry["game"], f"version {version_id}")
        registries[axis] = index
    expected_spaces = {
        "dlt": (5, 1, 35, 2, 1, 12),
        "ssq": (6, 1, 33, 1, 1, 16),
    }
    for entry in artifact["version_registry"]["number_space_versions"]:
        actual = tuple(entry[name] for name in ("front_count", "front_min", "front_max", "back_count", "back_min", "back_max"))
        fail(actual != expected_spaces[entry["game"]], f"{entry['version_id']}: number-space definition contradicts game rules")

    promotions = {item["promotion_id"]: item for item in artifact["promotion_registry"]}
    fail(len(promotions) != len(artifact["promotion_registry"]), "duplicate promotion_id")
    state_promotions: dict[str, dict[str, Any]] = {}
    for promotion_id, promotion in promotions.items():
        check_refs(promotion["evidence_refs"], promotion["game"], f"promotion {promotion_id}")
        end = promotion["effective_end_issue"]
        fail(end is not None and int(promotion["effective_start_issue"]) > int(end), f"promotion {promotion_id}: reversed effective range")
        machine = promotion["state_machine"]
        fail(promotion["kind"] == "fixed_issue_range" and machine is not None, f"promotion {promotion_id}: fixed range must not define a state machine")
        fail(promotion["kind"] == "state_machine" and machine is None, f"promotion {promotion_id}: state_machine definition is required")
        if machine is not None:
            states = set(machine["states"])
            fail(machine["initial_state"] not in states, f"promotion {promotion_id}: initial state is undeclared")
            for transition in machine["transitions"]:
                fail(transition["from_state"] not in states or transition["to_state"] not in states, f"promotion {promotion_id}: transition uses undeclared state")
            state_promotions[promotion_id] = promotion
    if "SSQ_2026_FUYUN_SPECIAL" in state_promotions:
        fuyun = state_promotions["SSQ_2026_FUYUN_SPECIAL"]
        expected_transitions = {
            ("inactive", "active", "greater_than_or_equal", 1500000000),
            ("active", "inactive", "less_than", 300000000),
        }
        actual_transitions = {(item["from_state"], item["to_state"], item["operator"], item["threshold_yuan"]) for item in fuyun["state_machine"]["transitions"]}
        fail(fuyun["game"] != "ssq" or fuyun["effective_start_issue"] != "2026014" or fuyun["state_machine"]["initial_state"] != "inactive", "SSQ Fuyun promotion seed contract is invalid")
        fail(actual_transitions != expected_transitions, "SSQ Fuyun activation/exit thresholds are invalid")

    bundles = {item["bundle_id"]: item for item in artifact["bundles"]}
    fail(len(bundles) != len(artifact["bundles"]), "duplicate bundle_id")
    by_game: dict[str, list[tuple[int, int, str]]] = {"dlt": [], "ssq": []}
    for bundle_id, bundle in bundles.items():
        game = bundle["game"]
        start, end = int(bundle["effective_start_issue"]), int(bundle["effective_end_issue"])
        fail(start > end, f"bundle {bundle_id}: reversed effective range")
        by_game[game].append((start, end, bundle_id))
        check_refs(bundle["evidence_refs"], game, f"bundle {bundle_id}")
        for field, axis in (("number_space_version", "number_space_versions"), ("draw_process_version", "draw_process_versions"), ("prize_rule_version", "prize_rule_versions")):
            version_id = bundle[field]
            fail(version_id not in registries[axis], f"bundle {bundle_id}: unknown {field} {version_id}")
            fail(registries[axis][version_id]["game"] != game, f"bundle {bundle_id}: cross-game {field}")
        for promotion_id in bundle["active_promotion_ids"]:
            fail(promotion_id not in promotions, f"bundle {bundle_id}: unknown promotion {promotion_id}")
            fail(promotions[promotion_id]["game"] != game, f"bundle {bundle_id}: cross-game promotion {promotion_id}")
    for game, intervals in by_game.items():
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            fail(current[0] <= previous[1], f"{game}: bundle ranges overlap: {previous[2]} and {current[2]}")

    expected_keys = frozen_issue_keys(scope)
    mappings: dict[tuple[str, str], str] = {}
    for mapping in artifact["issue_mappings"]:
        key = (mapping["game"], mapping["issue_id"])
        fail(key in mappings, f"duplicate issue mapping: {key[0]}/{key[1]}")
        mappings[key] = mapping["bundle_id"]
        fail(mapping["bundle_id"] not in bundles, f"{key[0]}/{key[1]}: unknown bundle mapping")
        bundle = bundles[mapping["bundle_id"]]
        fail(bundle["game"] != key[0], f"{key[0]}/{key[1]}: mapping and bundle games disagree")
        fail(not int(bundle["effective_start_issue"]) <= int(key[1]) <= int(bundle["effective_end_issue"]), f"{key[0]}/{key[1]}: mapping falls outside bundle range")
    fail(set(mappings) != expected_keys, f"issue mapping coverage must equal frozen 700-game/issue universe: missing={len(expected_keys-set(mappings))}, extra={len(set(mappings)-expected_keys)}")
    fail(set(bundles) != set(mappings.values()), "every bundle must be referenced by at least one frozen issue mapping")

    ledger: dict[tuple[str, str], dict[str, Any]] = {}
    for row in artifact["activity_ledger"]:
        key = (row["promotion_id"], row["issue_id"])
        fail(key in ledger, f"duplicate activity ledger row: {key[0]}/{key[1]}")
        ledger[key] = row
        fail(row["promotion_id"] not in state_promotions, f"activity ledger references non-state promotion {row['promotion_id']}")
        check_refs(row["evidence_refs"], state_promotions[row["promotion_id"]]["game"], f"activity {key[0]}/{key[1]}")
        exact, lower, upper = row["post_draw_pool_yuan"], row["post_draw_pool_lower_bound_yuan"], row["post_draw_pool_upper_bound_yuan"]
        fail(exact is None and lower is None and upper is None, f"activity {key[0]}/{key[1]}: pool evidence is absent")
        fail(exact is not None and lower is not None and exact < lower, f"activity {key[0]}/{key[1]}: exact pool is below lower bound")
        fail(exact is not None and upper is not None and exact > upper, f"activity {key[0]}/{key[1]}: exact pool is above upper bound")
        fail(lower is not None and upper is not None and lower > upper, f"activity {key[0]}/{key[1]}: pool bounds are reversed")

    for promotion_id, promotion in state_promotions.items():
        game = promotion["game"]
        start = int(promotion["effective_start_issue"])
        end = int(promotion["effective_end_issue"]) if promotion["effective_end_issue"] else max(int(issue) for mapped_game, issue in expected_keys if mapped_game == game)
        expected_issues = [issue for mapped_game, issue in sorted(expected_keys) if mapped_game == game and start <= int(issue) <= end]
        rows = [ledger[(promotion_id, issue)] for issue in expected_issues if (promotion_id, issue) in ledger]
        fail(len(rows) != len(expected_issues), f"promotion {promotion_id}: activity ledger does not cover frozen effective issues")
        if promotion_id == "SSQ_2026_FUYUN_SPECIAL":
            fail(not rows[0]["active_for_issue"] or rows[0]["transition_reason"] != "seeded_active_by_official_issue_evidence", "SSQ Fuyun issue 2026014 must carry the audited active seed")
            issue_020 = ledger[(promotion_id, "2026020")]
            fail(issue_020["post_draw_pool_lower_bound_yuan"] is None or issue_020["post_draw_pool_lower_bound_yuan"] < 300000000, "SSQ Fuyun issue 2026020 lacks the audited official pool lower bound")
        machine = promotion["state_machine"]
        activation = next((item for item in machine["transitions"] if item["operator"] == "greater_than_or_equal"), None)
        exit_transition = next((item for item in machine["transitions"] if item["operator"] == "less_than"), None)
        fail(activation is None or exit_transition is None, f"promotion {promotion_id}: activation and exit transitions are both required")
        for index, row in enumerate(rows):
            if index:
                fail(row["active_for_issue"] != rows[index - 1]["active_for_next_issue"], f"promotion {promotion_id}/{row['issue_id']}: state does not recurse from prior issue")
            exact, lower, upper = row["post_draw_pool_yuan"], row["post_draw_pool_lower_bound_yuan"], row["post_draw_pool_upper_bound_yuan"]
            if row["active_for_issue"]:
                definitely_exit = (exact is not None and exact < exit_transition["threshold_yuan"]) or (upper is not None and upper < exit_transition["threshold_yuan"])
                definitely_stay = (exact is not None and exact >= exit_transition["threshold_yuan"]) or (lower is not None and lower >= exit_transition["threshold_yuan"])
                fail(not definitely_exit and not definitely_stay, f"promotion {promotion_id}/{row['issue_id']}: pool evidence cannot determine next active state")
                expected_next = not definitely_exit
            else:
                definitely_enter = (exact is not None and exact >= activation["threshold_yuan"]) or (lower is not None and lower >= activation["threshold_yuan"])
                definitely_stay = (exact is not None and exact < activation["threshold_yuan"]) or (upper is not None and upper < activation["threshold_yuan"])
                fail(not definitely_enter and not definitely_stay, f"promotion {promotion_id}/{row['issue_id']}: pool evidence cannot determine next inactive state")
                expected_next = definitely_enter
            fail(row["active_for_next_issue"] != expected_next, f"promotion {promotion_id}/{row['issue_id']}: active_for_next_issue contradicts state machine")

    for key, bundle_id in mappings.items():
        game, issue_id = key
        expected_promotions: set[str] = set()
        for promotion_id, promotion in promotions.items():
            if promotion["game"] != game or int(issue_id) < int(promotion["effective_start_issue"]):
                continue
            end = promotion["effective_end_issue"]
            if end is not None and int(issue_id) > int(end):
                continue
            if promotion["kind"] == "fixed_issue_range" or ledger[(promotion_id, issue_id)]["active_for_issue"]:
                expected_promotions.add(promotion_id)
        fail(set(bundles[bundle_id]["active_promotion_ids"]) != expected_promotions, f"{game}/{issue_id}: bundle promotions contradict promotion registry/activity ledger")


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


def verify_p0_02(contract_path: Path, artifacts: Path, schema_dir: Path) -> None:
    scope = validate_artifact("scope_freeze", artifacts / "scope-freeze.json", schema_dir)
    verify_scope(scope, contract_path)
    catalog = validate_artifact("source_catalog", artifacts / "source-catalog.json", schema_dir)
    field_contract = validate_artifact("field_contract", artifacts / "field-contract.json", schema_dir)
    normalized_schema = load_json(schema_dir / SCHEMA_BY_KEY["normalized_records"])
    verify_source_catalog(catalog)
    verify_field_contract(field_contract, normalized_schema)


def verify_p0_03(contract_path: Path, artifacts: Path, schema_dir: Path) -> None:
    scope = validate_artifact("scope_freeze", artifacts / "scope-freeze.json", schema_dir)
    verify_scope(scope, contract_path)
    rule_bundles = validate_artifact("rule_bundles", artifacts / "rule-bundles.json", schema_dir)
    verify_rule_bundles(scope, rule_bundles)


def verify_p0_05_semantics(
    scope: dict[str, Any],
    rules: dict[str, Any],
    evidence: list[dict[str, Any]],
    catalog: dict[str, Any],
    work_plan: dict[str, Any],
    coverage: dict[str, Any],
    reconciliation: list[dict[str, Any]],
) -> None:
    from p0_05_history import EXPECTED, FIELDS, build_coverage, build_work_plan

    fail(work_plan != build_work_plan(scope, rules, evidence), "P0-05 work plan does not replay from frozen inputs")
    fail(coverage != build_coverage(scope, rules, evidence), "P0-05 coverage report does not replay from frozen inputs")
    fail(bool(reconciliation), "P0-05 reconciliation must be empty while authoritative primaries are blocked")
    fail(work_plan["changed_field_order"] != FIELDS, "P0-05 changed-field order drift")
    games = {entry["game"]: entry for entry in work_plan["games"]}
    fail(set(games) != {"dlt", "ssq"}, "P0-05 work plan must contain both games exactly once")
    expected_counts = {"dlt": {"sample": 30, "transition": 9, "work_union": 52}, "ssq": {"sample": 30, "transition": 6, "work_union": 45}}
    for game, entry in games.items():
        fail(entry["counts"] != expected_counts[game], f"{game}: P0-05 selection counts drift")
        fail(entry["sample_issue_ids_sha256"] != EXPECTED[game]["sample"], f"{game}: frozen sample hash drift")
        fail(entry["work_issue_ids_sha256"] != EXPECTED[game]["union"], f"{game}: work-union hash drift")
    fail(games["dlt"]["existing_reusable_evidence_issue_ids"] != ["2026050"], "DLT reusable evidence must be exactly issue 2026050")
    fail(games["ssq"]["existing_reusable_evidence_issue_ids"] != [], "SSQ must not claim reusable evidence")
    fail(len(games["dlt"]["planned_new_issue_ids"]) != 51 or len(games["ssq"]["planned_new_issue_ids"]) != 45, "P0-05 planned-new counts drift")
    fail(work_plan["network_runner_authorized"] or work_plan["budget_audit"]["certified_authorized_new_requests"] != 0, "P0-05 must not release network requests")
    catalog_games = {entry["game"]: entry for entry in catalog["games"]}
    fail(any(catalog_games[game]["authoritative_primary"]["approved_use"] != "blocked" for game in ("dlt", "ssq")), "empty reconciliation requires blocked authoritative primaries")
    coverage_games = {entry["game"]: entry for entry in coverage["games"]}
    for game, entry in coverage_games.items():
        fail(len(entry["target_expected_issues"]) != 350 or len(entry["minimum_expected_issues"]) != 200, f"{game}: coverage universe counts drift")
        fail(entry["coverage_tier"] != "none" or entry["holiday"] != [], f"{game}: P0-05 must report no coverage tier and no unaudited holiday claims")


def verify_p0_05(artifacts: Path, schema_dir: Path) -> None:
    scope = validate_artifact("scope_freeze", artifacts / "scope-freeze.json", schema_dir)
    rules = validate_artifact("rule_bundles", artifacts / "rule-bundles.json", schema_dir)
    evidence = validate_artifact("evidence_manifest", artifacts / "evidence-manifest.jsonl", schema_dir)
    catalog = validate_artifact("source_catalog", artifacts / "source-catalog.json", schema_dir)
    work_plan = validate_json_file(artifacts / "p0-05-work-plan.json", schema_dir / "p0-05-work-plan.schema.json")
    coverage = validate_artifact("coverage_report", artifacts / "coverage-report.json", schema_dir)
    reconciliation = validate_artifact("reconciliation", artifacts / "reconciliation.jsonl", schema_dir)
    verify_p0_05_semantics(scope, rules, evidence, catalog, work_plan, coverage, reconciliation)


def _resolve_p0_06_evidence(
    repo_root: Path,
    entry: dict[str, Any],
    evidence_manifest: dict[str, dict[str, Any]],
    *,
    require: str,
) -> dict[str, Any]:
    request_id = entry["request_id"]
    prefix = "artifacts/phase-0/evidence-manifest.jsonl#"
    reference = entry.get("evidence_ref")
    fail(not isinstance(reference, str) or not reference.startswith(prefix), f"{request_id}: evidence_ref must use the canonical manifest fragment prefix")
    evidence_id = reference[len(prefix):]
    fail(not evidence_id or "#" in evidence_id or reference != prefix + evidence_id, f"{request_id}: evidence_ref has an invalid evidence_id fragment")
    fail(evidence_id not in evidence_manifest, f"{request_id}: referenced evidence does not exist")
    evidence = evidence_manifest[evidence_id]
    fail(evidence.get("game") != entry["game"] or evidence.get("issue_id") != entry["scheduled_issue_id"], f"{request_id}: evidence game/issue does not match soak request")
    stored_payload_path = evidence.get("stored_payload_path")
    fail(entry.get("raw_payload_ref") != stored_payload_path, f"{request_id}: raw_payload_ref does not match evidence stored_payload_path")
    fail(not isinstance(stored_payload_path, str), f"{request_id}: evidence stored_payload_path is invalid")
    raw_path = repo_path(repo_root, stored_payload_path)
    fail(not raw_path.is_file(), f"{request_id}: referenced raw payload does not exist")
    fail(evidence.get("stored_payload_sha256") != sha256_file(raw_path), f"{request_id}: raw payload SHA-256 does not match evidence")

    normalized_ref = evidence.get("normalized_record_ref")
    normalized_hash = evidence.get("normalized_record_sha256")
    fail(not isinstance(normalized_ref, str), f"{request_id}: evidence normalized_record_ref is invalid")
    normalized_path = repo_path(repo_root, normalized_ref)

    def verify_normalized() -> None:
        fail(not normalized_path.is_file(), f"{request_id}: normalized record does not exist")
        fail(normalized_hash != canonical_sha256(load_json(normalized_path)), f"{request_id}: normalized record canonical SHA-256 mismatch")

    if require == "unverified":
        fail(evidence.get("status") != "unverified", f"{request_id}: unverified soak result requires unverified evidence status")
        fail(normalized_hash == "0" * 64, f"{request_id}: unverified soak result requires a real normalized record hash")
        verify_normalized()
    elif require == "raw_only":
        fail(evidence.get("status") != "invalid" or evidence.get("field_parsing_succeeded") is not True or normalized_hash != "0" * 64, f"{request_id}: raw-only rule-mapping classification contradicts evidence manifest")
        fail(normalized_path.exists(), f"{request_id}: raw-only rule-mapping capture must not have a normalized artifact")
    else:
        fail(evidence.get("status") != "invalid", f"{request_id}: invalid soak result requires invalid evidence status")
        if normalized_hash == "0" * 64:
            fail(normalized_path.exists(), f"{request_id}: zero normalized hash contradicts existing normalized artifact")
        else:
            verify_normalized()
    return evidence


def verify_p0_06_semantics(plan: dict[str, Any], entries: list[dict[str, Any]], observation: dict[str, Any], catalog: dict[str, Any], repo_root: Path) -> None:
    from p0_06_runner import build_runtime_plan

    fail(plan != build_runtime_plan(observation, catalog), "P0-06 runtime plan does not replay from frozen observation/source policy")
    fail(plan["status"] != "prepared_not_started", "P0-06 current artifact must remain prepared_not_started")
    authorized = plan["network_authorization"]["authorized_request_ids"]
    mechanically_authorized = [item["request_id"] for item in plan["requests"] if item["game"] == "dlt" and item["execution_policy"] == "network_attempted"]
    fail(authorized != mechanically_authorized or len(authorized) != 6, "P0-06 authorization must exactly equal six scheduled DLT requests")
    fail(len(plan["requests"]) != 24 or len(plan["scheduler"]["triggers"]) != 24, "P0-06 must preserve all 24 exact request triggers")
    requests = {item["request_id"]: item for item in plan["requests"]}
    fail(len(requests) != 24, "P0-06 request IDs must be unique")
    seen: set[str] = set()
    evidence_manifest: dict[str, dict[str, Any]] = {}
    for evidence in load_jsonl(repo_root / "artifacts/phase-0/evidence-manifest.jsonl"):
        evidence_id = evidence.get("evidence_id")
        fail(not isinstance(evidence_id, str) or evidence_id in evidence_manifest, "P0-06 evidence manifest contains invalid/duplicate evidence_id")
        evidence_manifest[evidence_id] = evidence
    for entry in entries:
        request_id = entry["request_id"]
        fail(request_id in seen or request_id not in requests, f"P0-06 unknown/duplicate soak request: {request_id}")
        seen.add(request_id); request = requests[request_id]
        for log_field, plan_field in (("game", "game"), ("planned_at_utc", "planned_at_utc"), ("source_slot", "source_slot"), ("source_id", "source_id"), ("scheduled_issue_id", "scheduled_issue_id")):
            fail(entry[log_field] != request[plan_field], f"{request_id}: soak field drift: {log_field}")
        fail(entry["request_schedule_sha256"] != plan["request_schedule_sha256"], f"{request_id}: schedule hash drift")
        fail(entry["started_at_utc"] is None or entry["completed_at_utc"] is None, f"{request_id}: executed row lacks actual timestamps")
        fail(entry["started_at_utc"] < entry["planned_at_utc"] or entry["completed_at_utc"] < entry["started_at_utc"], f"{request_id}: actual timestamps precede plan/start")
        blocked = entry["execution_disposition"] in {"policy_blocked", "compliance_hold"}
        if blocked:
            fail(entry["attempts"] != 0 or entry["network_used"] or entry["evidence_ref"] is not None or entry["raw_payload_ref"] is not None, f"{request_id}: blocked/hold row must be zero-network with no evidence")
            clock_failure = entry["classification_reason"] == "fresh_clock_check_failed"
            cutoff_block = entry["classification_reason"] == "acceptance_cutoff_passed_no_collection"
            fail(clock_failure and (entry["clock_check_at_utc"] is None or entry["clock_offset_seconds"] is None), f"{request_id}: clock failure lacks audit fields")
            fail(clock_failure and request["execution_policy"] != "network_attempted", f"{request_id}: only scheduled acquisition may fail its clock gate")
            fail(cutoff_block and (request["execution_policy"] != "network_attempted" or entry["execution_disposition"] != "policy_blocked" or entry["result"] != "invalid" or entry["started_at_utc"] <= plan["acceptance_cutoff_utc"]), f"{request_id}: cutoff block contradicts frozen request/cutoff")
            fail(not clock_failure and not cutoff_block and entry["execution_disposition"] != request["execution_policy"], f"{request_id}: blocked/hold disposition contradicts request policy")
            fail(not clock_failure and (entry["clock_check_at_utc"] is not None or entry["clock_offset_seconds"] is not None), f"{request_id}: blocked/hold clock fields are contradictory")
        else:
            fail(request_id not in authorized or request["execution_policy"] != "network_attempted" or entry["attempts"] < 1 or not entry["network_used"], f"{request_id}: network attempt contradicts frozen authorization/attempt count")
            fail(entry["clock_check_at_utc"] is None or entry["clock_offset_seconds"] is None or abs(entry["clock_offset_seconds"]) > 5, f"{request_id}: network attempt lacks fresh passing clock")
            fail(entry["started_at_utc"] is None or entry["completed_at_utc"] is None, f"{request_id}: network attempt lacks real execution timestamps")
            if entry["result"] == "unverified":
                fail(entry["evidence_ref"] is None or entry["raw_payload_ref"] is None, f"{request_id}: successful unverified capture lacks evidence/raw reference")
                _resolve_p0_06_evidence(repo_root, entry, evidence_manifest, require="unverified")
            else:
                fail((entry["evidence_ref"] is None) != (entry["raw_payload_ref"] is None), f"{request_id}: invalid attempt evidence/raw refs must be both present or both absent")
                if entry["evidence_ref"] is not None:
                    required = "raw_only" if entry["classification_reason"] == "captured_raw_core_fields_parsed_rule_mapping_unavailable" else "invalid"
                    _resolve_p0_06_evidence(repo_root, entry, evidence_manifest, require=required)
                else:
                    fail(not entry["classification_reason"].startswith("network_or_capture_failure:"), f"{request_id}: evidence-free invalid attempt requires explicit network_or_capture_failure classification")
        fail(entry["result"] == "verified", f"{request_id}: P0-06 cannot claim verified")


P0_06_INSTALL_CHECKS = {
    "ActionCountOne",
    "ExecuteExact",
    "ArgumentsExact",
    "WorkingDirectoryExact",
    "TriggerCount24",
    "TriggerTimesExact",
    "StartWhenAvailable",
    "ExecutionTimeLimit15Minutes",
    "MultipleInstancesIgnoreNew",
    "PrincipalCurrentSid",
    "PrincipalInteractive",
    "PrincipalLimited",
}


def _parse_audit_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"P0-06 install audit has invalid {label}: {value}") from exc
    fail(parsed.tzinfo is None, f"P0-06 install audit {label} lacks a UTC offset")
    return parsed


def verify_p0_06_install_audit_semantics(
    audit: dict[str, Any],
    plan: dict[str, Any],
    artifacts: Path,
) -> None:
    plan_path = artifacts / "p0-06-runtime-plan.json"
    evidence_path = artifacts / "evidence-manifest.jsonl"
    soak_path = artifacts / "soak-run-log.jsonl"
    fail(audit["runtime_plan_sha256"] != sha256_file(plan_path), "P0-06 install audit runtime plan SHA-256 mismatch")
    fail(audit["installed"] is not True or audit["matches_frozen_plan"] is not True, "P0-06 install audit does not record a matching installed task")
    checks = audit["checks"]
    fail(set(checks) != P0_06_INSTALL_CHECKS, "P0-06 install audit check inventory mismatch")
    fail(any(value is not True for value in checks.values()), "P0-06 install audit contains a failed task-definition check")

    plan_triggers = plan["scheduler"]["triggers"]
    fail(plan["scheduler"]["trigger_count"] != 24 or len(plan_triggers) != 24, "P0-06 frozen plan does not contain 24 scheduler triggers")
    fail(audit["trigger_count"] != plan["scheduler"]["trigger_count"], "P0-06 install audit trigger count differs from the frozen plan")
    recorded = _parse_audit_time(audit["recorded_at_utc"], "recorded_at_utc").astimezone(timezone.utc)
    trigger_times = [(_parse_audit_time(item["local_at"], "plan trigger local_at"), item["local_at"]) for item in plan_triggers]
    future_triggers = [(instant, text) for instant, text in trigger_times if instant.astimezone(timezone.utc) > recorded]
    fail(not future_triggers, "P0-06 install audit was recorded after the final frozen trigger")
    expected_next = min(future_triggers, key=lambda item: item[0].astimezone(timezone.utc))[1]
    fail(audit["next_run_local"] != expected_next, "P0-06 install audit next run is not the earliest remaining frozen trigger")

    fail(audit["last_run_state"] != "never_run", "P0-06 install audit must be captured before the task has ever run")
    fail(audit["last_task_result"] != 267011, "P0-06 install audit last task result is inconsistent with Windows SCHED_S_TASK_HAS_NOT_RUN")
    fail(audit["missed_runs"] != 0, "P0-06 install audit records missed scheduled runs")
    fail(not soak_path.is_file() or audit["soak_log_bytes"] != soak_path.stat().st_size or audit["soak_log_bytes"] != 0, "P0-06 install audit does not prove an empty soak log at capture time")
    fail(not evidence_path.is_file() or audit["evidence_manifest_sha256"] != sha256_file(evidence_path), "P0-06 install audit evidence manifest SHA-256 mismatch")
    declared_mtime = _parse_audit_time(audit["evidence_manifest_last_write_utc"], "evidence_manifest_last_write_utc").astimezone(timezone.utc)
    actual_mtime = datetime.fromtimestamp(evidence_path.stat().st_mtime, timezone.utc)
    fail(abs((declared_mtime - actual_mtime).total_seconds()) > 0.001, "P0-06 install audit evidence manifest last-write time mismatch")
    fail(declared_mtime > recorded, "P0-06 install audit evidence manifest was written after the audit timestamp")

    command = audit["verify_command"]
    fail("install_p0_06_scheduled_task.ps1" not in command or "-Action Verify" not in command or "-AuditPath artifacts/phase-0/p0-06-scheduler-install-audit.json" not in command, "P0-06 install audit verify command is not the explicit canonical snapshot command")
    fail(audit["exit_code"] != 0, "P0-06 install audit verify command did not succeed")
    fail(audit["os_state_claim_scope"] != "point_in_time_snapshot_not_continuous_os_proof", "P0-06 install audit overclaims continuous operating-system state")


def verify_p0_06(repo_root: Path, artifacts: Path, schema_dir: Path) -> None:
    observation = validate_artifact("observation_plan", artifacts / "observation-plan.json", schema_dir)
    catalog = validate_artifact("source_catalog", artifacts / "source-catalog.json", schema_dir)
    plan = validate_json_file(artifacts / "p0-06-runtime-plan.json", schema_dir / "p0-06-runtime-plan.schema.json")
    sidecar = (artifacts / "p0-06-runtime-plan.json.sha256").read_text(encoding="ascii").strip()
    fail(re.fullmatch(r"[0-9a-f]{64}", sidecar) is None or sidecar != sha256_file(artifacts / "p0-06-runtime-plan.json"), "P0-06 runtime plan hash sidecar mismatch")
    install_audit = validate_json_file(artifacts / "p0-06-scheduler-install-audit.json", schema_dir / "p0-06-scheduler-install-audit.schema.json")
    verify_p0_06_install_audit_semantics(install_audit, plan, artifacts)
    soak = validate_artifact("soak_log", artifacts / "soak-run-log.jsonl", schema_dir)
    verify_p0_06_semantics(plan, soak, observation, catalog, repo_root)


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
        if entry["normalized_record_sha256"] == "0" * 64:
            fail(entry["status"] != "invalid", f"{label}: zero normalized hash requires invalid status")
            fail(normalized_path.exists(), f"{label}: zero normalized hash contradicts existing normalized artifact")
        else:
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
    scope = load_json(artifacts / "scope-freeze.json")
    verify_source_catalog(validated["source_catalog"])
    verify_field_contract(validated["field_contract"], load_json(schema_dir / SCHEMA_BY_KEY["normalized_records"]))
    verify_rule_bundles(scope, validated["rule_bundles"])
    work_plan = validate_json_file(artifacts / "p0-05-work-plan.json", schema_dir / "p0-05-work-plan.schema.json")
    verify_p0_05_semantics(scope, validated["rule_bundles"], validated["evidence_manifest"], validated["source_catalog"], work_plan, validated["coverage_report"], validated["reconciliation"])
    runtime_plan = validate_json_file(artifacts / "p0-06-runtime-plan.json", schema_dir / "p0-06-runtime-plan.schema.json")
    runtime_sidecar = (artifacts / "p0-06-runtime-plan.json.sha256").read_text(encoding="ascii").strip()
    fail(re.fullmatch(r"[0-9a-f]{64}", runtime_sidecar) is None or runtime_sidecar != sha256_file(artifacts / "p0-06-runtime-plan.json"), "P0-06 runtime plan hash sidecar mismatch")
    install_audit = validate_json_file(artifacts / "p0-06-scheduler-install-audit.json", schema_dir / "p0-06-scheduler-install-audit.schema.json")
    verify_p0_06_install_audit_semantics(install_audit, runtime_plan, artifacts)
    verify_p0_06_semantics(runtime_plan, validated["soak_log"], load_json(artifacts / "observation-plan.json"), validated["source_catalog"], repo_root)
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
    parser.add_argument("--stage", choices=("p0-01", "p0-02", "p0-03", "p0-05", "p0-06", "full"), default="full")
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
        elif args.stage == "p0-02":
            verify_p0_02(contract_path, artifacts, schema_dir)
        elif args.stage == "p0-03":
            verify_p0_03(contract_path, artifacts, schema_dir)
        elif args.stage == "p0-05":
            verify_p0_05(artifacts, schema_dir)
        elif args.stage == "p0-06":
            verify_p0_06(repo_root, artifacts, schema_dir)
        else:
            verify_full(contract, repo_root, contract_path, artifacts, schema_dir)
        print(json.dumps({"status": "PASS", "stage": args.stage, "contract_version": contract["version"], "network_used": False}, separators=(",", ":")))
        return 0
    except (ValidationError, KeyError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "FAIL", "stage": args.stage, "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
