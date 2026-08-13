from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


TASK_ACCEPTANCE_ROLE = {
    **{f"T{i:02d}": "acceptance_engineer" for i in range(1, 11)},
    "T00": "data_custodian", "T11": "release_controller",
    **{f"T{i:02d}": "acceptance_engineer" for i in range(12, 21)},
    "T21": "independent_reviewer", "T22": "release_controller", "T23": "release_controller",
    "T24": "acceptance_approver",
}

TASK_PASS_TERMINAL = {
    "T00": "T00_AUTHORITY_GENESIS_PROTECTION_FROZEN",
    "T01": "T01_RESULT_BLIND_MACHINE_CONTRACT_FROZEN",
    "T02": "T02_STORAGE_DATA_CHAIN_CLI_KERNEL_PASS",
    "T03": "T03_SOURCE_CALENDAR_PASS",
    "T04": "T04_PROBABILITY_RANKING_PASS",
    "T05": "T05_FORECAST_TIME_LABEL_PASS",
    "T06": "T06_SCORE_WINDOW_CORRECTION_PASS",
    "T07": "T07_RESEARCH_ALPHA_PASS",
    "T08": "T08_SCHEDULER_RECOVERY_PASS",
    "T09": "T09_CLI_STATE_INTEGRATION_PASS",
    "T10": "T10_ORACLE_FEASIBILITY_FROZEN",
    "T11": "T11_PRODUCT_VALIDATION_PASS",
    "T12": "T12_DEVELOPMENT_DESIGN_SELECTED",
    "T13": "T13_POWER_DESIGN_FROZEN",
    "T14": "T14_OFFLINE_REBUILD_PASS",
}

# The two T01 script paths are the only narrow executable-provider resolution
# authorized by the controller. Everything else follows the task cards.
TASK_SCOPES = {
    "T00": ["config/phase4/authority-freeze.json", "config/phase4/genesis.json", "schemas/phase4/authority-freeze.schema.json", "schemas/phase4/genesis.schema.json", "schemas/phase4/protected-inventory.schema.json", "scripts/phase4/freeze_authority.py", "artifacts/phase-4-prep/*/control/**", "artifacts/phase-4-prep/*/work-items/T00*/**"],
    "T01": ["config/phase4/*.json", "schemas/phase4/*.schema.json", "docs/runbooks/phase-4-mvp-runtime.md", "requirements/phase4.in", "scripts/phase4/validate_contract_bundle.py", "scripts/phase4_independent/validate_work_item.py", "artifacts/phase-4-prep/*/work-items/T01*/**"],
    "T02": ["src/lottery_system/phase4/serialization.py", "src/lottery_system/phase4/identity.py", "src/lottery_system/phase4/storage.py", "src/lottery_system/phase4/ledger.py", "src/lottery_system/phase4/checkpoint.py", "src/lottery_system/phase4/data_chain.py", "src/lottery_system/phase4/cli_kernel.py", "src/lottery_system/phase4/__main__.py", "src/lottery_system/phase4/commands/contract.py", "src/lottery_system/phase4/commands/data_core.py", "tests/phase4/test_identity.py", "tests/phase4/test_ledger.py", "tests/phase4/test_data_chain.py", "artifacts/phase-4-prep/*/work-items/T02*/**", "artifacts/phase-4-runtime/*/**"],
    "T03": ["src/lottery_system/phase4/official_adapter.py", "src/lottery_system/phase4/verification.py", "src/lottery_system/phase4/calendar.py", "src/lottery_system/phase4/commands/data_official.py", "src/lottery_system/phase4/commands/calendar.py", "config/phase4/source-policy.json", "config/phase4/calendar-policy.json", "scripts/phase4/*source*", "tests/phase4/*source*", "tests/phase4/*calendar*", "tests/phase4/fixtures/**", "artifacts/phase-4-prep/*/work-items/T03*/**", "artifacts/phase-4-staging/*/**"],
    "T04": ["src/lottery_system/phase4/rules.py", "src/lottery_system/phase4/probability.py", "src/lottery_system/phase4/ranking.py", "src/lottery_system/phase4/commands/probability_validation.py", "tests/phase4/test_rules_probability_ranking.py", "artifacts/phase-4-prep/*/work-items/T04*/**"],
    "T05": ["src/lottery_system/phase4/forecast.py", "src/lottery_system/phase4/lock.py", "src/lottery_system/phase4/time_gate.py", "src/lottery_system/phase4/label_capability.py", "src/lottery_system/phase4/commands/forecast.py", "src/lottery_system/phase4/commands/result_unlock.py", "src/lottery_system/phase4/commands/probability_validation.py", "tests/phase4/test_forecast_lock.py", "tests/phase4/test_label_capability.py", "tests/phase4/test_forecast_diagnostic.py", "artifacts/phase-4-prep/*/work-items/T05*/**", "artifacts/phase-4-runtime/*/**"],
    "T06": ["src/lottery_system/phase4/metrics.py", "src/lottery_system/phase4/windows.py", "src/lottery_system/phase4/correction.py", "src/lottery_system/phase4/commands/score.py", "tests/phase4/test_metrics.py", "tests/phase4/test_correction.py", "tests/phase4/fixtures/correction/**", "artifacts/phase-4-prep/*/work-items/T06*/**", "artifacts/phase-4-runtime/*/**"],
    "T07": ["src/lottery_system/phase4/research/**", "src/lottery_system/phase4/commands/research.py", "tests/phase4/test_research_controller.py", "tests/phase4/test_cli_state_integration.py", "tests/phase4/fixtures/research/**", "artifacts/phase-4-prep/*/work-items/T07*/**", "artifacts/phase-4-runtime/*/**"],
    "T08": ["src/lottery_system/phase4/scheduler.py", "src/lottery_system/phase4/orchestrator.py", "src/lottery_system/phase4/recovery.py", "src/lottery_system/phase4/alerts.py", "src/lottery_system/phase4/commands/schedule.py", "deploy/systemd-user/**", "tests/phase4/test_scheduler_recovery.py", "tests/phase4/fixtures/schedule/**", "artifacts/phase-4-prep/*/work-items/T08*/**", "artifacts/phase-4-runtime/*/**"],
    "T09": ["src/lottery_system/phase4/commands/**", "src/lottery_system/phase4/state_projection.py", "src/lottery_system/phase4/provider_registry.py", "pyproject.toml", "tests/phase4/test_cli_state_integration.py", "artifacts/phase-4-prep/*/work-items/T09*/**", "artifacts/phase-4-runtime/*/**"],
    "T10": ["scripts/phase4_independent/oracle_*.py", "scripts/phase4_independent/check_qualification_feasibility.py", "scripts/phase4_independent/run_known_answers.py", "tests/phase4_oracle/**", "qualification-design/full-rule-spec-candidate.json", "qualification-design/analytic-feasibility-spec.json", "artifacts/phase-4-prep/*/work-items/T10/**"],
    "T11": ["tests/phase4/**", "scripts/phase4/validate_bottom_up.py", "scripts/phase4/benchmark_prequalification.py", "artifacts/phase-4-prep/*/work-items/T11*/**"],
    "T12": ["artifacts/phase-4-prep/*/qualification-design/preflight-benchmark/**", "artifacts/phase-4-prep/*/qualification-design/development/**", "artifacts/phase-4-prep/*/work-items/T12*/**"],
    "T13": ["artifacts/phase-4-prep/*/qualification-design/power/**", "artifacts/phase-4-prep/*/work-items/T13*/**"],
    "T14": ["requirements/phase4.lock", "pyproject.toml", "artifacts/phase-4-prep/*/wheelhouse/**", "artifacts/phase-4-prep/*/work-items/T14*/**"],
}

RETRY_SCOPED_TASKS = {f"T{i:02d}" for i in (*range(2, 10), *range(11, 15))}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _safe(root: Path, relative: str) -> Path:
    if relative.startswith("/") or "latest" in relative or "*" in relative or any(part == ".." for part in Path(relative).parts):
        raise ValueError(f"unsafe concrete receipt path: {relative}")
    path = (root / relative).resolve()
    path.relative_to(root)
    return path


def _in_scope(task: str, path: str) -> bool:
    work_item = re.search(r"(?:^|/)work-items/([^/]+)/", path)
    if task in RETRY_SCOPED_TASKS and work_item and work_item.group(1).startswith(task):
        if re.fullmatch(rf"{re.escape(task)}(?:-I[0-9]{{2}})?", work_item.group(1)) is None:
            return False
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in TASK_SCOPES.get(task, []))


def _actor_by_id(assignment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in assignment["assignments"]:
        if row["actor_id"] in rows:
            raise ValueError(f"duplicate actor assignment: {row['actor_id']}")
        rows[row["actor_id"]] = row
    return rows


def _actors_for_role(assignment: dict[str, Any], role: str) -> set[str]:
    return {row["actor_id"] for row in assignment["assignments"] if role in row["roles"]}


def _validate_task_records(root: Path, assignment: dict[str, Any]) -> None:
    for row in assignment["assignments"]:
        record = _safe(root, row["task_record_path"])
        if not record.is_file() or _sha(record) != row["task_record_sha256"]:
            raise ValueError(f"actor task-record mismatch: {row['actor_id']}")
        record_payload = _load(record)
        if record_payload.get("actor_id") != row["actor_id"] or record_payload.get("session_id") != row["session_id"] or record_payload.get("actor_type") != row["actor_type"] or record_payload.get("roles") != row["roles"] or record_payload.get("task_ids") != row["task_ids"]:
            raise ValueError(f"actor task-record identity mismatch: {row['actor_id']}")


def _validate_pass_terminal(receipt: dict[str, Any], expected_task: str) -> None:
    terminal = TASK_PASS_TERMINAL.get(expected_task)
    if terminal is None:
        raise ValueError(f"no registered PASS terminal for {expected_task}")
    if receipt.get("status") != "PASS":
        raise ValueError(f"receipt status is not PASS: {receipt.get('status')}")
    if receipt.get("process_exit_code") != 0:
        raise ValueError(f"receipt process_exit_code is not zero: {receipt.get('process_exit_code')}")
    if receipt.get("terminal") != terminal:
        raise ValueError(f"receipt terminal is not registered PASS terminal for {expected_task}: {receipt.get('terminal')}")


def _terminal_mutation_self_tests() -> dict[str, bool]:
    base = {"status": "PASS", "process_exit_code": 0, "terminal": TASK_PASS_TERMINAL["T01"]}

    def rejected(mutation: dict[str, Any]) -> bool:
        candidate = dict(base)
        candidate.update(mutation)
        try:
            _validate_pass_terminal(candidate, "T01")
        except ValueError:
            return True
        return False

    return {
        "status_fail_rejected": rejected({"status": "FAIL"}),
        "status_hold_rejected": rejected({"status": "HOLD"}),
        "nonzero_exit_rejected": rejected({"process_exit_code": 99}),
        "fail_terminal_rejected": rejected({"terminal": "FAIL_MUTATION"}),
        "hold_terminal_rejected": rejected({"terminal": "HOLD_MACHINE_CONTRACT"}),
    }


def _scope_retry_mutation_self_tests() -> dict[str, bool]:
    identity = "p4-prep-controller-issued-i01"
    retry_tasks = sorted(RETRY_SCOPED_TASKS)
    exact_and_retry = all(
        _in_scope(task, f"artifacts/phase-4-prep/{identity}/work-items/{task}/receipt.json")
        and _in_scope(task, f"artifacts/phase-4-prep/{identity}/work-items/{task}-I02/receipt.json")
        for task in retry_tasks
    )
    cross_task = all(
        not _in_scope(task, f"artifacts/phase-4-prep/{identity}/work-items/T{(int(task[1:]) % 14) + 2:02d}-I02/receipt.json")
        for task in retry_tasks
    )
    prefix_confusable = all(
        not _in_scope(task, f"artifacts/phase-4-prep/{identity}/work-items/{confusable}/receipt.json")
        for task in retry_tasks
        for confusable in (f"{task}0", f"{task}-I02x", f"{task}-extra")
    )
    retry_output = {
        "path": f"artifacts/phase-4-prep/{identity}/work-items/T05-I03/command-log.json",
        "producer_actor_id": "implementation",
        "task_id": "T05",
        "session_id": "/implementation",
        "source_commit": "0" * 40,
        "role": "implementation_author",
    }
    current, historical, cumulative = _derive_current_and_cumulative_sets([retry_output], [])
    truthful_retry_derivation = (
        _in_scope("T05", retry_output["path"])
        and current["task"] == {"implementation"}
        and current["product"] == {"implementation"}
        and cumulative["all"] == {"implementation"}
        and not historical["all"]
    )
    preserved_exceptions = (
        _in_scope("T01", f"artifacts/phase-4-prep/{identity}/work-items/T01-T05-SCOPE-REPAIR-I02/receipt.json")
        and _in_scope("T10", f"artifacts/phase-4-prep/{identity}/work-items/T10/attempts/T10-I07/receipt.json")
        and not _in_scope("T10", f"artifacts/phase-4-prep/{identity}/work-items/T10-I07/receipt.json")
    )
    fixture_output = {
        "path": "tests/phase4/fixtures/correction/valid.json",
        "producer_actor_id": "implementation",
        "task_id": "T06",
        "session_id": "/implementation",
        "source_commit": "0" * 40,
        "role": "implementation_author",
    }
    fixture_current, fixture_historical, fixture_cumulative = _derive_current_and_cumulative_sets([fixture_output], [])
    truthful_T06_fixture_derivation = (
        _in_scope("T06", fixture_output["path"])
        and fixture_current["task"] == {"implementation"}
        and fixture_current["product"] == {"implementation"}
        and fixture_cumulative["all"] == {"implementation"}
        and not fixture_historical["all"]
    )
    unrelated_T06_fixtures_rejected = all(not _in_scope("T06", path) for path in (
        "tests/phase4/fixtures/source/valid.json",
        "tests/phase4/fixtures/calendar/valid.json",
        "tests/phase4/fixtures/corrections/valid.json",
        "tests/phase4/fixtures/correction-extra/valid.json",
    ))
    research_fixture_output = {
        "path": "tests/phase4/fixtures/research/parameter-positive.json",
        "producer_actor_id": "implementation",
        "task_id": "T07",
        "session_id": "/implementation",
        "source_commit": "0" * 40,
        "role": "implementation_author",
    }
    research_current, research_historical, research_cumulative = _derive_current_and_cumulative_sets([research_fixture_output], [])
    truthful_T07_fixture_derivation = (
        _in_scope("T07", research_fixture_output["path"])
        and research_current["task"] == {"implementation"}
        and research_current["product"] == {"implementation"}
        and research_cumulative["all"] == {"implementation"}
        and not research_historical["all"]
    )
    unrelated_T07_fixtures_rejected = all(not _in_scope("T07", path) for path in (
        "tests/phase4/fixtures/source/parameter-positive.json",
        "tests/phase4/fixtures/correction/parameter-positive.json",
        "tests/phase4/fixtures/researches/parameter-positive.json",
        "tests/phase4/fixtures/research-extra/parameter-positive.json",
    ))
    T07_cli_compatibility_output = {
        "path": "tests/phase4/test_cli_state_integration.py",
        "producer_actor_id": "implementation",
        "task_id": "T07",
        "session_id": "/implementation",
        "source_commit": "0" * 40,
        "role": "implementation_author",
    }
    T07_cli_current, T07_cli_historical, T07_cli_cumulative = _derive_current_and_cumulative_sets(
        [T07_cli_compatibility_output], []
    )
    truthful_T07_cli_compatibility_derivation = (
        _in_scope("T07", T07_cli_compatibility_output["path"])
        and T07_cli_current["task"] == {"implementation"}
        and T07_cli_current["product"] == {"implementation"}
        and T07_cli_cumulative["all"] == {"implementation"}
        and not T07_cli_historical["all"]
    )
    unrelated_T07_integration_tests_rejected = all(not _in_scope("T07", path) for path in (
        "tests/phase4/test_cli_state_integration_extra.py",
        "tests/phase4/test_data_chain.py",
        "tests/phase4/test_scheduler_recovery.py",
    ))
    T08_schedule_fixture_output = {
        "path": "tests/phase4/fixtures/schedule/dual-game.json",
        "producer_actor_id": "implementation",
        "task_id": "T08",
        "session_id": "/implementation",
        "source_commit": "0" * 40,
        "role": "implementation_author",
    }
    T08_current, T08_historical, T08_cumulative = _derive_current_and_cumulative_sets([T08_schedule_fixture_output], [])
    truthful_T08_schedule_fixture_derivation = (
        _in_scope("T08", T08_schedule_fixture_output["path"])
        and T08_current["task"] == {"implementation"}
        and T08_current["product"] == {"implementation"}
        and T08_cumulative["all"] == {"implementation"}
        and not T08_historical["all"]
    )
    exact_T08_attempt_roots = (
        _in_scope("T08", f"artifacts/phase-4-prep/{identity}/work-items/T08/receipt.json")
        and _in_scope("T08", f"artifacts/phase-4-prep/{identity}/work-items/T08-I02/receipt.json")
    )
    T08_prefix_confusables_rejected = all(not _in_scope("T08", path) for path in (
        f"artifacts/phase-4-prep/{identity}/work-items/T080/receipt.json",
        f"artifacts/phase-4-prep/{identity}/work-items/T08-I02x/receipt.json",
        f"artifacts/phase-4-prep/{identity}/work-items/T08-extra/receipt.json",
        "tests/phase4/fixtures/schedules/dual-game.json",
        "tests/phase4/fixtures/schedule-extra/dual-game.json",
    ))
    T08_cross_task_rejected = all(not _in_scope(task, T08_schedule_fixture_output["path"]) for task in ("T06", "T07", "T09"))
    unrelated_T08_fixtures_rejected = all(not _in_scope("T08", path) for path in (
        "tests/phase4/fixtures/source/dual-game.json",
        "tests/phase4/fixtures/correction/dual-game.json",
        "tests/phase4/fixtures/research/dual-game.json",
        "tests/phase4/fixtures/scheduler/dual-game.json",
    ))
    return {
        "all_retry_tasks_exact_and_I02_roots_accepted": exact_and_retry,
        "cross_task_retry_roots_rejected": cross_task,
        "prefix_confusable_roots_rejected": prefix_confusable,
        "same_attempt_retry_evidence_derived_as_current_product_output": truthful_retry_derivation,
        "T01_and_T10_semantics_preserved": preserved_exceptions,
        "T06_correction_fixture_derived_as_current_product_output": truthful_T06_fixture_derivation,
        "unrelated_T06_fixture_trees_rejected": unrelated_T06_fixtures_rejected,
        "T07_research_fixture_derived_as_current_product_output": truthful_T07_fixture_derivation,
        "unrelated_T07_fixture_trees_rejected": unrelated_T07_fixtures_rejected,
        "T07_exact_CLI_compatibility_test_derived_as_current_product_output": truthful_T07_cli_compatibility_derivation,
        "unrelated_T07_integration_tests_rejected": unrelated_T07_integration_tests_rejected,
        "T08_schedule_fixture_derived_as_current_product_output": truthful_T08_schedule_fixture_derivation,
        "T08_exact_task_and_attempt_roots_accepted": exact_T08_attempt_roots,
        "T08_prefix_confusable_paths_rejected": T08_prefix_confusables_rejected,
        "T08_schedule_fixture_cross_task_rejected": T08_cross_task_rejected,
        "unrelated_T08_fixture_trees_rejected": unrelated_T08_fixtures_rejected,
    }


SET_NAMES = (
    "task", "historical_task", "all", "evidence", "acknowledgement_only", "unclassified", "product",
    "controller", "operator", "oracle", "development", "power", "formal",
    "e2e_readiness", "manifest", "replay", "validator", "reviewer", "delivery_statement",
)

ROLE_CATEGORIES = {
    "implementation_author": {"product"},
    "release_controller": {"controller"},
    "contract_owner": set(),
    "statistical_owner": set(),
    "independent_oracle_author": {"oracle"},
    "independent_power_operator": {"operator", "power"},
    "run_operator": {"operator", "formal"},
    "vps_operator": {"operator", "e2e_readiness"},
    "independent_replay_operator": {"operator", "replay"},
    "acceptance_engineer": {"validator"},
    "independent_validator": {"validator"},
    "independent_reviewer": {"reviewer"},
    "machine_delivery_statement": {"delivery_statement"},
    "acceptance_approver": set(),
}

EVIDENCE_CLASS_CATEGORIES = {
    "oracle": {"oracle"}, "development": {"development"}, "power": {"power"},
    "formal_qualification": {"formal"}, "e2e_readiness": {"e2e_readiness"},
    "manifest": {"manifest"}, "replay": {"replay"}, "validator": {"validator"},
    "review": {"reviewer"}, "machine_delivery_statement": {"delivery_statement"},
    "phase4_evidence_manifest": {"manifest"}, "phase4_replay_closure": {"replay"},
    "phase4_validator_closure": {"validator"}, "phase4_review_closure": {"reviewer"},
    "phase4_machine_delivery_closure": {"delivery_statement"},
    "phase4_independent_work_item_validation": {"validator"},
    "phase4_machine_delivery_statement": {"delivery_statement"},
}

PRODUCT_PATH_PREFIXES = ("src/lottery_system/phase4/",)

MANIFEST_ARTIFACT_TYPES = {
    "phase4_evidence_manifest", "phase4_replay_closure", "phase4_validator_closure",
    "phase4_review_closure", "phase4_machine_delivery_closure",
}

PRESERVATION_ARTIFACT_TYPE = "phase4_immutable_attempt_shared_output_preservation"
INDEPENDENT_VALIDATION_ARTIFACT_TYPE = "phase4_independent_work_item_validation"


def _empty_sets() -> dict[str, set[str]]:
    return {name: set() for name in SET_NAMES}


def _categories(fact: dict[str, Any]) -> set[str]:
    categories = set(ROLE_CATEGORIES.get(fact.get("role"), set()))
    categories.update(EVIDENCE_CLASS_CATEGORIES.get(fact.get("evidence_class"), set()))
    if any(fact.get("path", "").startswith(prefix) for prefix in PRODUCT_PATH_PREFIXES):
        categories.add("product")
    return categories


def _derive_sets(facts: list[dict[str, Any]], *, historical: bool = False) -> dict[str, set[str]]:
    result = _empty_sets()
    for fact in facts:
        actor = fact["producer_actor_id"]
        if fact.get("evidence_class") == "human_responsibility_acknowledgement":
            result["acknowledgement_only"].add(actor)
            continue
        result["all"].add(actor)
        result["evidence"].add(actor)
        result["historical_task" if historical else "task"].add(actor)
        categories = _categories(fact)
        if not categories:
            result["unclassified"].add(actor)
        for category in categories:
            result[category].add(actor)
    return result


def _fact(provenance: dict[str, Any], *, evidence_class: str, source_kind: str, default_path: str, default_task: str | None = None, default_role: str | None = None) -> dict[str, Any]:
    actor = provenance.get("producer_actor_id", provenance.get("actor_id"))
    return {
        "producer_actor_id": actor,
        "task_id": provenance.get("task_id", default_task),
        "session_id": provenance.get("session_id"),
        "source_commit": provenance.get("source_commit"),
        "path": provenance.get("path", default_path),
        "role": provenance.get("role", default_role),
        "evidence_class": provenance.get("evidence_class", evidence_class),
        "source_kind": source_kind,
    }


def _manifest_rows(payload: dict[str, Any], relative: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("files", []):
        provenance = item.get("producer_provenance")
        if not isinstance(provenance, dict) or provenance.get("path") != item.get("path"):
            raise ValueError("manifest file producer provenance/path mismatch")
        rows.append(_fact(provenance, evidence_class=payload["artifact_type"], source_kind="manifest_file", default_path=item["path"]))
    return rows


def _is_provenance_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    artifact_type = payload.get("artifact_type")
    if artifact_type == "phase4_work_item_receipt" or artifact_type in MANIFEST_ARTIFACT_TYPES:
        return True
    if isinstance(payload.get("provenance"), dict) or isinstance(payload.get("producer_provenance"), dict):
        return True
    if any(key.endswith("_provenance") and isinstance(value, dict) for key, value in payload.items()):
        return True
    return bool(
        payload.get("reviewer_actor_id")
        or payload.get("signer_actor_id")
        or (
            artifact_type == "phase4_machine_delivery_statement_acknowledgement"
            and payload.get("machine_delivery_statement_actor_id")
        )
    )


def _is_evidence_container_path(relative: str) -> bool:
    parts = Path(relative).parts
    name = Path(relative).name
    if "work-items" in parts or "manifest" in parts or "signatures" in parts or "acceptance" in parts:
        return True
    return (
        name == "receipt.json"
        or name == "preservation-map.json"
        or name.startswith("independent-validation")
        or name.startswith("actor-assignment-validation")
        or name == "human-delivery_statement-acknowledgement.json"
    )


def _preservation_map_paths(root: Path) -> list[Path]:
    base = root / "artifacts" / "phase-4-prep"
    if not base.is_dir():
        return []
    result: list[Path] = []
    for path in base.glob("*/work-items/*/preserved-shared-outputs/preservation-map.json"):
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            continue
        if re.fullmatch(
            r"artifacts/phase-4-prep/[^/]+/work-items/[^/]+/preserved-shared-outputs/preservation-map\.json",
            relative.as_posix(),
        ):
            result.append(path.resolve())
    return sorted(result)


def _receipt_output(receipt: dict[str, Any], relative: str, sha256: str, size: int) -> bool:
    return any(
        isinstance(row, dict)
        and row.get("path") == relative
        and row.get("sha256") == sha256
        and row.get("bytes") == size
        for row in receipt.get("outputs", [])
    )


def _receipt_input(receipt: dict[str, Any], relative: str, sha256: str) -> bool:
    return any(
        isinstance(row, dict)
        and row.get("path") == relative
        and row.get("sha256") == sha256
        for row in receipt.get("inputs", [])
    )


def _collision_candidate_identity(
    root: Path, candidate: tuple[Path, dict[str, Any], dict[str, Any]]
) -> dict[str, Any]:
    map_path, preservation, _ = candidate
    map_relative = map_path.relative_to(root).as_posix()
    match = re.fullmatch(
        r"artifacts/phase-4-prep/([^/]+)/work-items/([^/]+)/preserved-shared-outputs/preservation-map\.json",
        map_relative,
    )
    if match is None:
        raise ValueError(f"prefix-confusable collision candidate map: {map_relative}")
    prep_id, map_attempt_id = match.groups()
    replacement_attempt_id = preservation.get("replacement_attempt_id", map_attempt_id)
    receipt_relative = f"artifacts/phase-4-prep/{prep_id}/work-items/{replacement_attempt_id}/receipt.json"
    receipt_path = _safe(root, receipt_relative)
    verdicts: list[dict[str, Any]] = []
    repair_root = receipt_path.parent
    if repair_root.is_dir():
        for verdict_path in sorted(repair_root.glob("independent-validation*.json")):
            verdict_relative = verdict_path.relative_to(root).as_posix()
            if re.fullmatch(re.escape(repair_root.relative_to(root).as_posix()) + r"/independent-validation(?:-I[0-9]{2})?\.json", verdict_relative):
                verdicts.append({"path": verdict_relative, "sha256": _sha(verdict_path), "bytes": verdict_path.stat().st_size})
    return {
        "attempt_id": map_attempt_id,
        "preservation_map": {"path": map_relative, "sha256": _sha(map_path), "bytes": map_path.stat().st_size},
        "receipt": {
            "path": receipt_relative,
            "sha256": _sha(receipt_path) if receipt_path.is_file() else None,
            "bytes": receipt_path.stat().st_size if receipt_path.is_file() else None,
        },
        "verdicts": verdicts,
    }


def _select_preservation_candidate(
    root: Path, candidates: list[tuple[Path, dict[str, Any], dict[str, Any]]], relative: str
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"missing accepted preservation transition for historical provenance container: {relative}")
    recoveries = [
        candidate for candidate in candidates
        if isinstance(candidate[1].get("collision_recovery"), dict)
        and _collision_candidate_identity(root, candidate)["receipt"]["sha256"] is not None
        and candidate[1]["collision_recovery"].get("quarantined_candidates")
        == [_collision_candidate_identity(root, other) for other in candidates if other is not candidate]
    ]
    if len(recoveries) != 1:
        raise ValueError(f"ambiguous accepted preservation transition for historical provenance container: {relative}")
    recovery = recoveries[0]
    declared = recovery[1]["collision_recovery"].get("quarantined_candidates")
    actual = [_collision_candidate_identity(root, candidate) for candidate in candidates if candidate is not recovery]
    if declared != actual:
        raise ValueError(f"collision recovery does not exactly quarantine displaced transitions: {relative}")
    return recovery


def _validate_collision_archive_binding(
    root: Path, repair_receipt: dict[str, Any], row: dict[str, Any], repair_root_relative: str,
    *, label: str, require_live_source: bool, require_receipt_input: bool = False,
    archive_prefix: str = "collision-archive/",
) -> None:
    relative = row.get("path", "")
    sha256 = row.get("sha256", "")
    size = row.get("bytes")
    archive_relative = row.get("archive_path", "")
    archive_sha256 = row.get("archive_sha256")
    archive_bytes = row.get("archive_bytes")
    expected_prefix = f"{repair_root_relative}/{archive_prefix}"
    if not isinstance(archive_relative, str) or not archive_relative.startswith(expected_prefix):
        raise ValueError(f"{label} archive is outside exact collision recovery root: {archive_relative}")
    archive_path = _safe(root, archive_relative)
    if (
        not archive_path.is_file()
        or archive_sha256 != sha256
        or archive_bytes != size
        or _sha(archive_path) != sha256
        or archive_path.stat().st_size != size
        or not _receipt_output(repair_receipt, archive_relative, sha256, size)
    ):
        raise ValueError(f"{label} archive path/hash is unbound: {archive_relative}")
    if require_live_source:
        source_path = _safe(root, relative)
        if (
            not source_path.is_file()
            or _sha(source_path) != sha256
            or source_path.stat().st_size != size
            or (require_receipt_input and not _receipt_input(repair_receipt, relative, sha256))
        ):
            raise ValueError(f"{label} live source changed or is unbound: {relative}")


def _validate_collision_recovery(
    root: Path, preservation: dict[str, Any], repair_receipt: dict[str, Any], repair_root_relative: str
) -> None:
    recovery = preservation.get("collision_recovery")
    if not isinstance(recovery, dict):
        return
    required_acceptor = recovery.get("required_acceptance_actor_id")
    if (
        recovery.get("status") != "COMPLETE"
        or recovery.get("recovery_attempt_id") != preservation.get("replacement_attempt_id")
        or not isinstance(required_acceptor, str)
        or re.fullmatch(r"p4-acceptance-engineer-i[0-9]{2}", required_acceptor) is None
    ):
        raise ValueError("historical collision recovery identity mismatch")
    acceptor = repair_receipt.get("acceptance_actor_provenance")
    if not isinstance(acceptor, dict) or acceptor.get("actor_id") != recovery["required_acceptance_actor_id"]:
        raise ValueError("historical collision recovery acceptance actor mismatch")
    current_bindings = recovery.get("current_byte_quarantine")
    observed_bindings = recovery.get("observed_prior_bytes")
    authority_binding = recovery.get("authoritative_assignment")
    requirements = recovery.get("quarantine_requirements")
    if (
        not isinstance(current_bindings, list) or not current_bindings
        or not isinstance(observed_bindings, list)
        or not isinstance(authority_binding, dict)
        or not isinstance(requirements, dict)
    ):
        raise ValueError("historical collision recovery byte inventory missing")
    actual_by_reason: dict[str, set[str]] = {}
    for row in current_bindings:
        for reason in row.get("quarantine_reasons", []) if isinstance(row, dict) else []:
            actual_by_reason.setdefault(reason, set()).add(row.get("path", ""))
    for reason in (
        "overwritten_i01_acceptance_path", "unauthorized_assignment_branch",
        "overwritten_assignment_branch", "phantom_task_record", "rogue_i02_artifact",
        "partial_i04_artifact", "invalid_i10_attempt_artifact",
        "invalid_i10_acceptance_path", "invalid_t07_i02_artifact",
    ):
        declared_paths = requirements.get(reason)
        if not isinstance(declared_paths, list) or set(declared_paths) != actual_by_reason.get(reason, set()):
            raise ValueError(f"historical collision quarantine scope mismatch: {reason}")
    _validate_collision_archive_binding(
        root, repair_receipt, authority_binding, repair_root_relative,
        label="authoritative collision-recovery assignment", require_live_source=True, require_receipt_input=True,
    )
    for row in current_bindings:
        if not isinstance(row, dict):
            raise ValueError("historical collision quarantine row malformed")
        _validate_collision_archive_binding(
            root, repair_receipt, row, repair_root_relative, label="historical collision quarantine", require_live_source=True
        )
    for row in observed_bindings:
        if not isinstance(row, dict):
            raise ValueError("historical observed-byte row malformed")
        _validate_collision_archive_binding(
            root, repair_receipt, row, repair_root_relative, label="historical observed byte", require_live_source=False
        )
    pre_checker = recovery.get("pre_recovery_checker")
    if not isinstance(pre_checker, dict):
        raise ValueError("pre-recovery checker preservation missing")
    _validate_collision_archive_binding(
        root, repair_receipt, pre_checker, repair_root_relative, label="pre-recovery checker",
        require_live_source=False, archive_prefix="preserved-shared-outputs/pre-recovery-checker/",
    )
    reserved_attempts = recovery.get("reserved_collision_identities")
    if not isinstance(reserved_attempts, list) or not reserved_attempts:
        raise ValueError("reserved collision identity quarantine missing")
    for row in reserved_attempts:
        relative = row.get("path", "") if isinstance(row, dict) else ""
        path = _safe(root, relative)
        files = sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else []
        state = row.get("state") if isinstance(row, dict) else None
        if state == "NONEMPTY_QUARANTINED":
            valid = path.is_dir() and row.get("file_count") == len(files) and len(files) > 0
        elif state == "ABSENT_RESERVED":
            valid = not path.exists() and row.get("file_count") == 0
        else:
            valid = False
        if not valid:
            raise ValueError(f"reserved collision identity changed: {relative}")


def _verdict_actor_id(verdict: dict[str, Any]) -> str | None:
    provenance = verdict.get("validator_provenance")
    if not isinstance(provenance, dict):
        provenance = verdict.get("producer_provenance")
    if not isinstance(provenance, dict):
        provenance = verdict.get("provenance")
    if isinstance(provenance, dict):
        return provenance.get("producer_actor_id", provenance.get("actor_id"))
    return None


def _accepted_repair_chain(
    root: Path,
    map_path: Path,
    preservation: dict[str, Any],
    row: dict[str, Any],
    relative: str,
    expected_sha256: str,
    live_path: Path,
    source_payload: dict[str, Any],
    *,
    require_provenance: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    map_relative = map_path.relative_to(root).as_posix()
    map_match = re.fullmatch(
        r"artifacts/phase-4-prep/([^/]+)/work-items/([^/]+)/preserved-shared-outputs/preservation-map\.json",
        map_relative,
    )
    if map_match is None:
        raise ValueError(f"prefix-confusable preservation map path: {map_relative}")
    prep_id, map_attempt_id = map_match.groups()
    if preservation.get("artifact_type") != PRESERVATION_ARTIFACT_TYPE or preservation.get("status") != "COMPLETE":
        raise ValueError(f"invalid historical preservation map: {map_relative}")
    if preservation.get("task_id") != "T01" or preservation.get("file_count") != len(preservation.get("files", [])):
        raise ValueError(f"historical preservation map identity mismatch: {map_relative}")
    if preservation.get("attempt_id") != map_attempt_id:
        raise ValueError(f"historical preservation map attempt/path mismatch: {map_relative}")

    archive_relative = row.get("archive_path", "")
    expected_archive = (
        Path(map_relative).parent / Path(relative)
    ).as_posix()
    if archive_relative != expected_archive:
        raise ValueError(f"prefix-confusable or wrong historical archive path: {archive_relative}")
    archive_path = _safe(root, archive_relative)
    if (
        row.get("original_path") != relative
        or row.get("original_receipt_sha256") != expected_sha256
        or row.get("archive_sha256") != expected_sha256
        or not archive_path.is_file()
        or _sha(archive_path) != expected_sha256
        or row.get("archive_bytes") != archive_path.stat().st_size
        or row.get("original_receipt_bytes") != archive_path.stat().st_size
        or row.get("byte_exact", True) is not True
    ):
        raise ValueError(f"historical preservation archive path/hash mismatch: {relative}")
    archived_payload: dict[str, Any] | None = None
    if require_provenance:
        try:
            archived_payload = _load(archive_path)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"historical preservation archive is not JSON: {relative}") from exc
        if not _is_provenance_payload(archived_payload):
            raise ValueError(f"historical preservation archive is not a provenance container: {relative}")

    replacement_attempt_id = preservation.get("replacement_attempt_id", map_attempt_id)
    if not isinstance(replacement_attempt_id, str) or re.fullmatch(r"T01(?:-[A-Z0-9]+)+-I[0-9]{2}|T01-I[0-9]{2}", replacement_attempt_id) is None:
        raise ValueError(f"invalid replacement attempt identity: {replacement_attempt_id}")
    repair_root_relative = f"artifacts/phase-4-prep/{prep_id}/work-items/{replacement_attempt_id}"
    repair_root = _safe(root, repair_root_relative)
    if repair_root.parent.name != "work-items" or repair_root.name != replacement_attempt_id:
        raise ValueError(f"prefix-confusable repair root: {repair_root_relative}")
    receipt_relative = f"{repair_root_relative}/receipt.json"
    receipt_path = _safe(root, receipt_relative)
    if not receipt_path.is_file():
        raise ValueError(f"historical transition repair receipt missing: {receipt_relative}")
    repair_receipt = _load(receipt_path)
    if (
        repair_receipt.get("artifact_type") != "phase4_work_item_receipt"
        or repair_receipt.get("task_id") != "T01"
        or repair_receipt.get("status") != "PASS"
        or repair_receipt.get("process_exit_code") != 0
        or repair_receipt.get("terminal") != TASK_PASS_TERMINAL["T01"]
    ):
        raise ValueError(f"historical transition repair receipt is not PASS: {receipt_relative}")

    replacement = preservation.get("current_replacement")
    collision_recovery = isinstance(preservation.get("collision_recovery"), dict)
    replacement_relative = replacement.get("path", relative) if collision_recovery and isinstance(replacement, dict) else relative
    replacement_path = _safe(root, replacement_relative)
    replacement_metadata_valid = (
        not collision_recovery
        or (
            isinstance(replacement, dict)
            and replacement.get("sha256") == _sha(replacement_path)
            and replacement.get("bytes") == replacement_path.stat().st_size
        )
    )
    if (
        not replacement_path.is_file()
        or not replacement_metadata_valid
        or not _receipt_output(repair_receipt, replacement_relative, _sha(replacement_path), replacement_path.stat().st_size)
    ):
        raise ValueError(f"historical transition repair does not bind current replacement: {replacement_relative}")
    if replacement_relative != relative and Path(__file__).resolve() != replacement_path.resolve():
        raise ValueError(f"historical transition recovery checker is not the bound replacement: {replacement_relative}")
    _validate_collision_recovery(root, preservation, repair_receipt, repair_root_relative)
    map_sha256 = _sha(map_path)
    if not _receipt_output(repair_receipt, map_relative, map_sha256, map_path.stat().st_size):
        raise ValueError(f"historical transition repair does not bind preservation map: {map_relative}")
    if not _receipt_output(repair_receipt, archive_relative, expected_sha256, archive_path.stat().st_size):
        raise ValueError(f"historical transition repair does not bind archive: {archive_relative}")

    verdict_candidates: list[tuple[Path, dict[str, Any]]] = []
    for verdict_path in sorted(repair_root.glob("independent-validation*.json")):
        verdict_relative = verdict_path.relative_to(root).as_posix()
        if re.fullmatch(re.escape(repair_root_relative) + r"/independent-validation(?:-I[0-9]{2})?\.json", verdict_relative) is None:
            continue
        verdict = _load(verdict_path)
        if (
            verdict.get("artifact_type") == INDEPENDENT_VALIDATION_ARTIFACT_TYPE
            and verdict.get("task_id") == "T01"
            and verdict.get("status") == "PASS"
            and verdict.get("receipt_path") == receipt_relative
            and verdict.get("receipt_sha256") == _sha(receipt_path)
        ):
            verdict_candidates.append((verdict_path, verdict))
    if len(verdict_candidates) != 1:
        raise ValueError(f"historical transition repair requires one bound accepted verdict: {replacement_attempt_id}")
    verdict_path, verdict = verdict_candidates[0]
    acceptor = repair_receipt.get("acceptance_actor_provenance")
    if not isinstance(acceptor, dict) or _verdict_actor_id(verdict) != acceptor.get("actor_id"):
        raise ValueError(f"historical transition verdict actor is unbound: {replacement_attempt_id}")
    source_ended = source_payload.get("ended_at_utc")
    repair_started = repair_receipt.get("started_at_utc")
    repair_ended = repair_receipt.get("ended_at_utc")
    verdict_completed = verdict.get("completed_at_utc")
    if source_ended and repair_started and repair_started <= source_ended:
        raise ValueError(f"historical transition repair is not later than source: {replacement_attempt_id}")
    if repair_ended and verdict_completed and verdict_completed < repair_ended:
        raise ValueError(f"historical transition verdict predates repair completion: {replacement_attempt_id}")

    return archived_payload, [
        {"path": map_relative, "sha256": map_sha256},
        {"path": receipt_relative, "sha256": _sha(receipt_path)},
        {"path": verdict_path.relative_to(root).as_posix(), "sha256": _sha(verdict_path)},
    ]


def _resolve_preserved_input_transition(
    root: Path,
    relative: str,
    expected_sha256: str,
    live_path: Path,
    source_payload: dict[str, Any],
    *,
    require_provenance: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidates: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for map_path in _preservation_map_paths(root):
        preservation = _load(map_path)
        if not isinstance(preservation, dict):
            continue
        for row in preservation.get("files", []):
            if (
                isinstance(row, dict)
                and row.get("original_path") == relative
                and row.get("original_receipt_sha256") == expected_sha256
            ):
                candidates.append((map_path, preservation, row))
    selected = _select_preservation_candidate(root, candidates, relative)
    return _accepted_repair_chain(
        root, *selected, relative, expected_sha256, live_path, source_payload,
        require_provenance=require_provenance,
    )


def _resolve_historical_container_transition(
    root: Path,
    relative: str,
    expected_sha256: str,
    live_path: Path,
    source_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload, links = _resolve_preserved_input_transition(
        root, relative, expected_sha256, live_path, source_payload, require_provenance=True
    )
    if payload is None:
        raise ValueError(f"historical preservation payload missing: {relative}")
    return payload, links


def _linked_historical_rows(root: Path, receipt: dict[str, Any], actors: dict[str, dict[str, Any]], visited: set[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    visited = set() if visited is None else visited
    rows: list[dict[str, Any]] = []
    containers: list[str] = []
    for link in receipt.get("inputs", []):
        relative = link.get("path", "")
        if not relative.endswith(".json") or relative in visited:
            continue
        path = _safe(root, relative)
        if not path.is_file():
            raise ValueError(f"historical linked JSON missing: {relative}")
        transition_links: list[dict[str, str]] = []
        current_sha256 = _sha(path)
        try:
            current_payload = _load(path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if current_sha256 != link.get("sha256") and _is_evidence_container_path(relative):
                payload, transition_links = _resolve_historical_container_transition(
                    root, relative, link.get("sha256", ""), path, receipt
                )
            elif _is_evidence_container_path(relative):
                raise ValueError(f"historical evidence container is not valid JSON: {relative}")
            else:
                continue
        else:
            if current_sha256 == link.get("sha256"):
                payload = current_payload
                if not _is_provenance_payload(payload):
                    continue
            else:
                if not _is_provenance_payload(current_payload) and not _is_evidence_container_path(relative):
                    continue
                payload, transition_links = _resolve_historical_container_transition(
                    root, relative, link.get("sha256", ""), path, receipt
                )
        artifact_type = payload.get("artifact_type") if isinstance(payload, dict) else None
        if artifact_type == "phase4_work_item_receipt":
            visited.add(relative)
            containers.append(relative)
            task_id = payload.get("task_id")
            for actor_id in payload.get("task_producer_set", []):
                actor = actors.get(actor_id)
                if actor is None:
                    raise ValueError(f"historical task producer absent from assignment: {actor_id}")
                matching_roles = [role for role in actor["roles"] if task_id in actor["task_ids"]]
                if not matching_roles:
                    raise ValueError(f"historical task producer task mismatch: {actor_id}")
                for role in matching_roles:
                    rows.append(_fact({"producer_actor_id": actor_id, "session_id": actor["session_id"], "role": role}, evidence_class="work_item_task_producer", source_kind="receipt_task_producer", default_path=relative, default_task=task_id))
            acceptor = payload.get("acceptance_actor_provenance")
            if isinstance(acceptor, dict):
                rows.append(_fact(acceptor, evidence_class="work_item_acceptance", source_kind="receipt_acceptance_actor", default_path=relative, default_task=task_id, default_role=TASK_ACCEPTANCE_ROLE.get(task_id)))
            historical_outputs = payload.get("outputs")
            if not isinstance(historical_outputs, list):
                raise ValueError(f"historical receipt outputs missing: {relative}")
            if payload.get("task_id") == "T00":
                # T00 predates the strict output-provenance row shape; it cannot
                # contain product/evidence writes and remains protected by its
                # independently frozen authority receipt and inventory.
                historical_outputs = [row for row in historical_outputs if isinstance(row, dict) and "producer_actor_id" in row]
            rows.extend(_fact(row, evidence_class="work_item_output", source_kind="receipt_output", default_path=row.get("path", relative), default_task=task_id) for row in historical_outputs)
            nested_rows, nested_containers = _linked_historical_rows(root, payload, actors, visited)
            rows.extend(nested_rows)
            containers.extend(nested_containers)
        elif artifact_type in MANIFEST_ARTIFACT_TYPES:
            visited.add(relative)
            containers.append(relative)
            rows.extend(_manifest_rows(payload, relative))
        elif isinstance(payload, dict):
            provenance_rows: list[tuple[str, dict[str, Any]]] = []
            if isinstance(payload.get("provenance"), dict):
                provenance_rows.append(("provenance", payload["provenance"]))
            if isinstance(payload.get("producer_provenance"), dict):
                provenance_rows.append(("producer_provenance", payload["producer_provenance"]))
            for key, value in payload.items():
                if key.endswith("_provenance") and key not in {"provenance", "producer_provenance"} and isinstance(value, dict):
                    provenance_rows.append((key, value))
            direct = []
            if payload.get("reviewer_actor_id"):
                direct.append({"producer_actor_id": payload["reviewer_actor_id"], "task_id": payload.get("reviewer_task_id", "T22"), "session_id": payload.get("reviewer_session_id"), "role": "independent_reviewer"})
            if payload.get("signer_actor_id"):
                direct.append({"producer_actor_id": payload["signer_actor_id"], "task_id": "T23", "role": "machine_delivery_statement"})
            if artifact_type == "phase4_machine_delivery_statement_acknowledgement" and payload.get("machine_delivery_statement_actor_id"):
                direct.append({"producer_actor_id": payload["machine_delivery_statement_actor_id"], "task_id": "T00-ACKNOWLEDGEMENT", "role": "machine_delivery_statement", "evidence_class": "human_responsibility_acknowledgement"})
            if provenance_rows or direct:
                visited.add(relative)
                containers.append(relative)
                for key, provenance in provenance_rows:
                    rows.append(_fact(provenance, evidence_class=provenance.get("evidence_class", artifact_type or "standalone_evidence"), source_kind=f"standalone_{key}", default_path=relative, default_task=payload.get("task_id")))
                for provenance in direct:
                    rows.append(_fact(provenance, evidence_class=artifact_type or "standalone_evidence", source_kind="standalone_direct_actor", default_path=relative))
                if isinstance(payload.get("inputs"), list):
                    nested_rows, nested_containers = _linked_historical_rows(root, payload, actors, visited)
                    rows.extend(nested_rows)
                    containers.extend(nested_containers)
        if transition_links:
            transition_rows, transition_containers = _linked_historical_rows(
                root, {"inputs": transition_links}, actors, visited
            )
            rows.extend(transition_rows)
            containers.extend(transition_containers)
    required = {"path", "producer_actor_id", "task_id", "role"}
    for row in rows:
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("historical actual-write provenance row incomplete")
        actor = actors.get(row["producer_actor_id"])
        if actor is None or (row.get("session_id") is not None and row["session_id"] != actor["session_id"]) or row["task_id"] not in actor["task_ids"] or row["role"] not in actor["roles"]:
            raise ValueError(f"historical actual-write actor provenance mismatch: {row.get('path')}")
    return rows, containers


def _history_transition_mutation_self_tests() -> dict[str, bool]:
    relative = "artifacts/phase-4-prep/prep/work-items/T02/receipt.json"
    attempt = "T01-HISTORY-TRANSITION-REPAIR-I01"
    map_relative = f"artifacts/phase-4-prep/prep/work-items/{attempt}/preserved-shared-outputs/preservation-map.json"
    archive_relative = f"artifacts/phase-4-prep/prep/work-items/{attempt}/preserved-shared-outputs/{relative}"
    receipt_relative = f"artifacts/phase-4-prep/prep/work-items/{attempt}/receipt.json"
    verdict_relative = f"artifacts/phase-4-prep/prep/work-items/{attempt}/independent-validation-I02.json"
    old_payload = {
        "artifact_type": "phase4_work_item_receipt", "task_id": "T02", "inputs": [], "outputs": [],
        "task_producer_set": [], "ended_at_utc": "2026-08-11T00:00:00Z",
    }
    live_payload = {
        "artifact_type": "phase4_work_item_receipt", "task_id": "T02", "inputs": [], "outputs": [],
        "task_producer_set": [], "ended_at_utc": "2026-08-11T00:30:00Z",
    }

    def write_json(root: Path, path: str, payload: dict[str, Any]) -> Path:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_canonical(payload))
        return target

    def build(root: Path, mutation: str = "") -> tuple[str, Path, dict[str, Any]]:
        live_path = write_json(root, relative, live_payload)
        archive_path = write_json(root, archive_relative, old_payload)
        old_sha256 = _sha(archive_path)
        original_path = relative
        original_sha256 = old_sha256
        mapped_archive_relative = archive_relative
        if mutation == "wrong_path":
            original_path = relative + "-confusable"
        if mutation == "wrong_hash":
            original_sha256 = "f" * 64
        if mutation == "prefix_archive":
            mapped_archive_relative = archive_relative.replace("preserved-shared-outputs/", "preserved-shared-outputs-confusable/", 1)
            write_json(root, mapped_archive_relative, old_payload)
        preservation = {
            "schema_version": "1.0.0",
            "artifact_type": PRESERVATION_ARTIFACT_TYPE,
            "attempt_id": attempt,
            "replacement_attempt_id": attempt,
            "task_id": "T01",
            "status": "COMPLETE",
            "file_count": 1,
            "files": [{
                "original_path": original_path,
                "original_receipt_sha256": original_sha256,
                "original_receipt_bytes": archive_path.stat().st_size,
                "archive_path": mapped_archive_relative,
                "archive_sha256": old_sha256,
                "archive_bytes": archive_path.stat().st_size,
                "byte_exact": True,
            }],
            "provenance": {
                "producer_actor_id": "producer", "task_id": "T01", "session_id": "/producer",
                "source_commit": "0" * 40, "role": "contract_owner", "evidence_class": "history_transition",
            },
        }
        map_path = write_json(root, map_relative, preservation)
        repair_receipt = {
            "schema_version": "1.0.0", "artifact_type": "phase4_work_item_receipt", "task_id": "T01",
            "source_commit": "0" * 40, "actor_assignment_sha256": "a" * 64,
            "task_producer_set": ["producer"],
            "acceptance_actor_provenance": {"actor_id": "acceptor", "session_id": "/acceptor"},
            "inputs": [],
            "outputs": [
                {"path": relative, "sha256": _sha(live_path), "bytes": live_path.stat().st_size},
                {"path": archive_relative, "sha256": old_sha256, "bytes": archive_path.stat().st_size},
                {"path": map_relative, "sha256": _sha(map_path), "bytes": map_path.stat().st_size},
            ],
            "started_at_utc": "2026-08-11T01:00:00Z", "ended_at_utc": "2026-08-11T02:00:00Z",
            "process_exit_code": 0, "status": "PASS", "terminal": TASK_PASS_TERMINAL["T01"],
        }
        receipt_path = write_json(root, receipt_relative, repair_receipt)
        verdict = {
            "schema_version": "1.0.0", "artifact_type": INDEPENDENT_VALIDATION_ARTIFACT_TYPE,
            "task_id": "T01", "status": "PASS", "receipt_path": receipt_relative,
            "receipt_sha256": _sha(receipt_path), "completed_at_utc": "2026-08-11T03:00:00Z",
            "validator_provenance": {"producer_actor_id": "acceptor"},
        }
        if mutation == "unaccepted":
            verdict["status"] = "HOLD"
        if mutation == "unbound":
            verdict["receipt_sha256"] = "b" * 64
        write_json(root, verdict_relative, verdict)
        if mutation == "current_tamper":
            write_json(root, relative, {**live_payload, "tampered": True})
        if mutation == "malformed_current_tamper":
            (root / relative).write_bytes(b"{")
        return old_sha256, live_path, old_payload

    def accepted(mutation: str = "") -> bool:
        with tempfile.TemporaryDirectory(prefix="p4-history-transition-") as temporary:
            root = Path(temporary).resolve()
            expected_sha256, live_path, source_payload = build(root, mutation)
            try:
                _resolve_historical_container_transition(root, relative, expected_sha256, live_path, source_payload)
            except ValueError:
                return False
            return True

    with tempfile.TemporaryDirectory(prefix="p4-history-nonprovenance-") as temporary:
        root = Path(temporary).resolve()
        config_relative = "config/phase4/cli-contract.json"
        write_json(root, config_relative, {"artifact_type": "phase4_cli_contract", "commands": []})
        try:
            rows, containers = _linked_historical_rows(
                root,
                {"inputs": [{"path": config_relative, "sha256": "0" * 64}]},
                {},
            )
            nonprovenance_skipped = rows == [] and containers == []
        except ValueError:
            nonprovenance_skipped = False

    with tempfile.TemporaryDirectory(prefix="p4-history-missing-map-") as temporary:
        root = Path(temporary).resolve()
        live_path = write_json(root, relative, live_payload)
        try:
            _resolve_historical_container_transition(root, relative, _sha(write_json(root, "old.json", old_payload)), live_path, old_payload)
        except ValueError:
            missing_map_rejected = True
        else:
            missing_map_rejected = False

    return {
        "changed_nonprovenance_json_dependency_skipped": nonprovenance_skipped,
        "exact_later_accepted_transition_passes": accepted(),
        "missing_map_rejected": missing_map_rejected,
        "wrong_original_path_rejected": not accepted("wrong_path"),
        "wrong_original_hash_rejected": not accepted("wrong_hash"),
        "unaccepted_repair_rejected": not accepted("unaccepted"),
        "unbound_repair_verdict_rejected": not accepted("unbound"),
        "prefix_confusable_archive_rejected": not accepted("prefix_archive"),
        "current_replacement_tamper_rejected": not accepted("current_tamper"),
        "malformed_current_replacement_tamper_rejected": not accepted("malformed_current_tamper"),
    }


def _collision_recovery_mutation_self_tests() -> dict[str, bool]:
    def write(root: Path, relative: str, content: bytes) -> Path:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    with tempfile.TemporaryDirectory(prefix="p4-history-collision-") as temporary:
        root = Path(temporary).resolve()
        old_attempt = "T01-HISTORY-TRANSITION-REPAIR-I01"
        recovery_attempt = "T01-HISTORY-TRANSITION-REPAIR-I04"
        old_map_relative = f"artifacts/phase-4-prep/prep/work-items/{old_attempt}/preserved-shared-outputs/preservation-map.json"
        recovery_map_relative = f"artifacts/phase-4-prep/prep/work-items/{recovery_attempt}/preserved-shared-outputs/preservation-map.json"
        old_receipt_relative = f"artifacts/phase-4-prep/prep/work-items/{old_attempt}/receipt.json"
        old_verdict_relative = f"artifacts/phase-4-prep/prep/work-items/{old_attempt}/independent-validation-I02.json"
        old_receipt = write(root, old_receipt_relative, _canonical({"artifact_type": "phase4_work_item_receipt"}))
        write(root, old_verdict_relative, _canonical({"artifact_type": INDEPENDENT_VALIDATION_ARTIFACT_TYPE, "status": "PASS"}))
        old_preservation = {"attempt_id": old_attempt, "replacement_attempt_id": old_attempt}
        old_map = write(root, old_map_relative, _canonical(old_preservation))
        recovery_preservation: dict[str, Any] = {
            "attempt_id": recovery_attempt, "replacement_attempt_id": recovery_attempt,
            "collision_recovery": {"quarantined_candidates": []},
        }
        recovery_map = write(root, recovery_map_relative, _canonical(recovery_preservation))
        recovery_receipt_relative = f"artifacts/phase-4-prep/prep/work-items/{recovery_attempt}/receipt.json"
        write(root, recovery_receipt_relative, _canonical({"artifact_type": "phase4_work_item_receipt"}))
        old_candidate = (old_map, old_preservation, {})
        recovery_candidate = (recovery_map, recovery_preservation, {})
        recovery_preservation["collision_recovery"]["quarantined_candidates"] = [_collision_candidate_identity(root, old_candidate)]
        recovery_map.write_bytes(_canonical(recovery_preservation))
        candidates = [old_candidate, (recovery_map, recovery_preservation, {})]
        selected = _select_preservation_candidate(root, candidates, "path")
        overwritten_pass_quarantined = selected[1] is recovery_preservation

        wrong = json.loads(json.dumps(recovery_preservation))
        wrong["collision_recovery"]["quarantined_candidates"][0]["verdicts"][0]["sha256"] = "f" * 64
        try:
            _select_preservation_candidate(root, [old_candidate, (recovery_map, wrong, {})], "path")
        except ValueError:
            wrong_verdict_hash_rejected = True
        else:
            wrong_verdict_hash_rejected = False

        omitted = json.loads(json.dumps(recovery_preservation))
        omitted["collision_recovery"]["quarantined_candidates"] = []
        try:
            _select_preservation_candidate(root, [old_candidate, (recovery_map, omitted, {})], "path")
        except ValueError:
            omitted_collision_candidate_rejected = True
        else:
            omitted_collision_candidate_rejected = False

        second_recovery = json.loads(json.dumps(recovery_preservation))
        try:
            _select_preservation_candidate(root, [old_candidate, (recovery_map, recovery_preservation, {}), (recovery_map, second_recovery, {})], "path")
        except ValueError:
            multiple_recoveries_rejected = True
        else:
            multiple_recoveries_rejected = False

        source_relative = "control/collision.json"
        archive_relative = f"artifacts/phase-4-prep/prep/work-items/{recovery_attempt}/collision-archive/control/collision.json"
        payload = b"collision-bytes"
        source = write(root, source_relative, payload)
        archive = write(root, archive_relative, payload)
        binding = {
            "path": source_relative, "sha256": _sha(source), "bytes": source.stat().st_size,
            "archive_path": archive_relative, "archive_sha256": _sha(archive), "archive_bytes": archive.stat().st_size,
        }
        receipt = {
            "inputs": [{"path": source_relative, "sha256": _sha(source)}],
            "outputs": [{"path": archive_relative, "sha256": _sha(archive), "bytes": archive.stat().st_size}],
        }
        try:
            _validate_collision_archive_binding(root, receipt, binding, f"artifacts/phase-4-prep/prep/work-items/{recovery_attempt}", label="test", require_live_source=True)
        except ValueError:
            exact_collision_archive_passes = False
        else:
            exact_collision_archive_passes = True
        source.write_bytes(b"tampered")
        try:
            _validate_collision_archive_binding(root, receipt, binding, f"artifacts/phase-4-prep/prep/work-items/{recovery_attempt}", label="test", require_live_source=True)
        except ValueError:
            current_collision_tamper_rejected = True
        else:
            current_collision_tamper_rejected = False
        source.write_bytes(payload)
        archive.write_bytes(b"tampered")
        try:
            _validate_collision_archive_binding(root, receipt, binding, f"artifacts/phase-4-prep/prep/work-items/{recovery_attempt}", label="test", require_live_source=True)
        except ValueError:
            archive_collision_tamper_rejected = True
        else:
            archive_collision_tamper_rejected = False

    return {
        "overwritten_original_pass_quarantined": overwritten_pass_quarantined,
        "wrong_quarantined_verdict_hash_rejected": wrong_verdict_hash_rejected,
        "omitted_collision_candidate_rejected": omitted_collision_candidate_rejected,
        "multiple_collision_recoveries_rejected": multiple_recoveries_rejected,
        "exact_collision_archive_binding_passes": exact_collision_archive_passes,
        "current_collision_source_tamper_rejected": current_collision_tamper_rejected,
        "collision_archive_tamper_rejected": archive_collision_tamper_rejected,
    }


def _derive_current_and_cumulative_sets(current_outputs: list[dict[str, Any]], historical_outputs: list[dict[str, Any]]) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    current_facts = [_fact(row, evidence_class=row.get("evidence_class", "work_item_output"), source_kind="current_receipt_output", default_path=row["path"], default_task=row.get("task_id")) for row in current_outputs]
    current = _derive_sets(current_facts)
    historical = _derive_sets(historical_outputs, historical=True)
    cumulative = {name: current[name] | historical[name] for name in SET_NAMES}
    return current, historical, cumulative


def _role_conflicts(assignment: dict[str, Any], current: dict[str, set[str]], historical: dict[str, set[str]], task: str) -> list[str]:
    conflicts: list[str] = []
    sets = {name: current[name] | historical[name] for name in SET_NAMES}
    product = sets["product"]
    power = _actors_for_role(assignment, "independent_power_operator")
    statistical = _actors_for_role(assignment, "statistical_owner")
    oracle = _actors_for_role(assignment, "independent_oracle_author")
    acceptance = _actors_for_role(assignment, "acceptance_engineer")
    delivery = _actors_for_role(assignment, "machine_delivery_statement")
    if power & product: conflicts.append("power_overlaps_product")
    if power & statistical: conflicts.append("power_overlaps_statistical")
    if power & oracle: conflicts.append("power_overlaps_oracle")
    if acceptance & product: conflicts.append("acceptance_overlaps_product")
    signed_names = ("all", "evidence", "unclassified", "product", "controller", "operator", "oracle", "development", "power", "formal", "e2e_readiness", "manifest", "replay", "validator", "reviewer", "delivery_statement")
    signed_universe = set().union(*(historical[name] if task == "T23" else sets[name] for name in signed_names))
    if delivery & signed_universe: conflicts.append("delivery_statement_overlaps_signed_producers_or_reviewer")
    if task in {"T16", "T17"} and _actors_for_role(assignment, "run_operator") & acceptance: conflicts.append("run_overlaps_acceptance")
    reviewer = _actors_for_role(assignment, "independent_reviewer")
    reviewer_forbidden_roles = set().union(*(_actors_for_role(assignment, role) for role in ("release_controller", "run_operator", "vps_operator", "independent_replay_operator", "independent_validator", "acceptance_engineer")))
    if task == "T22" and reviewer & (historical["all"] | reviewer_forbidden_roles): conflicts.append("reviewer_overlap")
    approver = _actors_for_role(assignment, "acceptance_approver")
    approver_forbidden_roles = reviewer_forbidden_roles | delivery | reviewer | _actors_for_role(assignment, "release_controller")
    if task == "T24" and approver & (historical["all"] | approver_forbidden_roles): conflicts.append("approver_overlap")
    return conflicts


def _acceptance_actor_conflict(acceptor: str, current: dict[str, set[str]], task: str) -> bool:
    return task != "T24" and acceptor in current["task"]


def _machine_delivery_statement_assignment_conflicts(assignment: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    signers = [row for row in assignment["assignments"] if "machine_delivery_statement" in row.get("roles", [])]
    if len(signers) != 1:
        conflicts.append("machine_delivery_statement_assignment_not_unique")
        return conflicts
    signer = signers[0]
    if not signer.get("actor_id"): conflicts.append("machine_delivery_statement_actor_id_missing")
    if signer.get("actor_type") != "codex_session": conflicts.append("machine_delivery_statement_not_machine")
    if signer.get("roles") != ["machine_delivery_statement"]: conflicts.append("machine_delivery_statement_mixed_role")
    if "T23" not in signer.get("task_ids", []): conflicts.append("machine_delivery_statement_missing_T23")
    for row in assignment["assignments"]:
        if row is not signer and row.get("actor_type") != "codex_session":
            conflicts.append("non_delivery_statement_human_assignment")
    return conflicts


def _machine_delivery_statement_mutation_self_tests() -> dict[str, bool]:
    def actor(actor_id: str, actor_type: str, roles: list[str], task_ids: list[str]) -> dict[str, Any]:
        return {"actor_id": actor_id, "actor_type": actor_type, "roles": roles, "task_ids": task_ids}

    human = actor("machine-delivery-statement", "codex_session", ["machine_delivery_statement"], ["T23"])
    machine = actor("machine", "codex_session", ["release_controller"], ["T00", "T23"])
    base = {"assignments": [human, machine]}
    machine_signer = {"assignments": [actor("machine-delivery-statement", "human_session", ["machine_delivery_statement"], ["T23"]), machine]}
    mixed_signer = {"assignments": [actor("machine-delivery-statement", "codex_session", ["machine_delivery_statement", "independent_reviewer"], ["T22", "T23"]), machine]}

    current = _empty_sets(); current["task"] = {"machine-delivery-statement"}; current["all"] = {"machine-delivery-statement"}; current["evidence"] = {"machine-delivery-statement"}; current["delivery_statement"] = {"machine-delivery-statement"}
    acknowledgement = _empty_sets()
    legitimate = not _machine_delivery_statement_assignment_conflicts(base) and "delivery_statement_overlaps_signed_producers_or_reviewer" not in _role_conflicts(base, current, acknowledgement, "T23")

    def prior_conflict(category: str) -> bool:
        prior = _empty_sets(); prior["all"] = {"machine-delivery-statement"}; prior["evidence"] = {"machine-delivery-statement"}; prior[category] = {"machine-delivery-statement"}
        return "delivery_statement_overlaps_signed_producers_or_reviewer" in _role_conflicts(base, current, prior, "T23")

    return {
        "machine_signer_rejected": bool(_machine_delivery_statement_assignment_conflicts(machine_signer)),
        "mixed_role_signer_rejected": bool(_machine_delivery_statement_assignment_conflicts(mixed_signer)),
        "prior_reviewer_rejected": prior_conflict("reviewer"),
        "prior_unclassified_producer_rejected": prior_conflict("unclassified"),
        "controller_reuse_rejected": prior_conflict("controller"),
        "operator_reuse_rejected": prior_conflict("operator"),
        "delivery_statement_evidence_reuse_rejected": prior_conflict("delivery_statement"),
        "machine_delivery_statement_independent_pass": legitimate,
    }


def _negative_role_tests() -> dict[str, bool]:
    def row(actor: str, *roles: str) -> dict[str, Any]: return {"actor_id": actor, "roles": list(roles)}
    base = {"assignments": [row("product", "implementation_author"), row("stats", "statistical_owner"), row("oracle", "independent_oracle_author"), row("power", "independent_power_operator"), row("accept", "acceptance_engineer"), row("run", "run_operator"), row("review", "independent_reviewer"), row("controller", "release_controller"), row("human", "machine_delivery_statement"), row("approver", "acceptance_approver")]}
    empty = _empty_sets()
    # T03 relabel does not matter: path-derived product membership still catches acceptance overlap.
    t03 = {k: set(v) for k, v in empty.items()}; t03["product"] = {"accept"}; t03["task"] = {"accept"}
    case1 = "acceptance_overlaps_product" in _role_conflicts(base, t03, empty, "T03")
    overlap = {"assignments": [row("same", "implementation_author", "statistical_owner", "independent_oracle_author", "independent_power_operator"), row("accept", "acceptance_engineer")]}
    historical_product = [{"path": "src/lottery_system/phase4/controller.py", "producer_actor_id": "same", "task_id": "T07", "role": "implementation_author", "evidence_class": "work_item_output"}]
    _, t13_history, _ = _derive_current_and_cumulative_sets([], historical_product)
    c2 = _role_conflicts(overlap, empty, t13_history, "T13"); case2 = all(value in c2 for value in ("power_overlaps_product", "power_overlaps_statistical", "power_overlaps_oracle"))
    run_overlap = {"assignments": [row("same", "run_operator", "acceptance_engineer")]}; case3 = "run_overlaps_acceptance" in _role_conflicts(run_overlap, empty, empty, "T16")

    t22_assignment = {"assignments": [row("review", "independent_reviewer"), row("controller", "release_controller"), row("validator", "acceptance_engineer")]}
    t22_current = _empty_sets(); t22_current["task"] = {"review"}; t22_current["all"] = {"review"}; t22_current["evidence"] = {"review"}; t22_current["reviewer"] = {"review"}
    case4 = "reviewer_overlap" not in _role_conflicts(t22_assignment, t22_current, empty, "T22")
    t22_prior = _empty_sets(); t22_prior["historical_task"] = {"review"}; t22_prior["all"] = {"review"}; t22_prior["evidence"] = {"review"}; t22_prior["unclassified"] = {"review"}
    case5 = "reviewer_overlap" in _role_conflicts(t22_assignment, t22_current, t22_prior, "T22")

    t24_assignment = {"assignments": [row("approver", "acceptance_approver"), row("review", "independent_reviewer"), row("controller", "release_controller")]}
    t24_current = _empty_sets(); t24_current["task"] = {"approver"}; t24_current["all"] = {"approver"}; t24_current["evidence"] = {"approver"}; t24_current["unclassified"] = {"approver"}
    case6 = "approver_overlap" not in _role_conflicts(t24_assignment, t24_current, empty, "T24") and not _acceptance_actor_conflict("approver", t24_current, "T24")
    t24_prior = _empty_sets(); t24_prior["historical_task"] = {"approver"}; t24_prior["all"] = {"approver"}; t24_prior["evidence"] = {"approver"}; t24_prior["unclassified"] = {"approver"}
    case7 = "approver_overlap" in _role_conflicts(t24_assignment, t24_current, t24_prior, "T24")
    return {"t03_relabel_product": case1, "t13_historical_product_overlap": case2, "t16_t17_run_overlap": case3, "t22_distinct_current_reviewer_pass": case4, "t22_standalone_unclassified_prior_overlap": case5, "t24_distinct_current_approver_pass": case6, "t24_prior_overlap": case7}


def validate(root: Path, receipt_path: Path, actor_path: Path, expected_task: str) -> dict[str, Any]:
    receipt, assignment = _load(receipt_path), _load(actor_path)
    if receipt.get("task_id") != expected_task:
        raise ValueError(f"expected {expected_task}, got {receipt.get('task_id')}")
    _validate_pass_terminal(receipt, expected_task)
    if receipt.get("actor_assignment_sha256") != _sha(actor_path):
        raise ValueError("actor-assignment hash mismatch")
    delivery_assignment_conflicts = _machine_delivery_statement_assignment_conflicts(assignment)
    if delivery_assignment_conflicts:
        raise ValueError("machine delivery actor assignment conflicts: " + ",".join(delivery_assignment_conflicts))
    _validate_task_records(root, assignment)
    actors = _actor_by_id(assignment)
    preserved_transition_links: list[dict[str, str]] = []
    for section in ("inputs", "outputs"):
        seen: set[str] = set()
        for row in receipt[section]:
            if row["path"] in seen: raise ValueError(f"duplicate {section} path: {row['path']}")
            seen.add(row["path"])
            path = _safe(root, row["path"])
            if not path.is_file(): raise ValueError(f"{section} path missing: {row['path']}")
            if _sha(path) != row["sha256"]:
                if section != "inputs":
                    raise ValueError(f"{section} hash mismatch: {row['path']}")
                _, transition_links = _resolve_preserved_input_transition(
                    root, row["path"], row["sha256"], path, receipt, require_provenance=False
                )
                preserved_transition_links.extend(transition_links)
            if section == "outputs":
                if path.stat().st_size != row["bytes"]: raise ValueError(f"output bytes mismatch: {row['path']}")
                if not _in_scope(expected_task, row["path"]): raise ValueError(f"output outside {expected_task} scope: {row['path']}")
                actor = actors.get(row["producer_actor_id"])
                if actor is None or row["session_id"] != actor["session_id"] or expected_task not in actor["task_ids"]: raise ValueError(f"output producer identity mismatch: {row['path']}")
                if row["task_id"] != expected_task or row["source_commit"] != receipt["source_commit"]: raise ValueError(f"output provenance mismatch: {row['path']}")
                if row["role"] not in actor["roles"]: raise ValueError(f"output producer role mismatch: {row['path']}")
    history_source = dict(receipt)
    history_source["inputs"] = list(receipt.get("inputs", [])) + preserved_transition_links
    historical_rows, historical_containers = _linked_historical_rows(root, history_source, actors)
    current_sets, historical_sets, sets = _derive_current_and_cumulative_sets(receipt["outputs"], historical_rows)
    if current_sets["task"] != set(receipt["task_producer_set"]): raise ValueError("task producer set does not derive from actual output paths")
    accept = receipt["acceptance_actor_provenance"]
    accept_actor = actors.get(accept["actor_id"])
    expected_role = TASK_ACCEPTANCE_ROLE[expected_task]
    if accept_actor is None or expected_role not in accept_actor["roles"] or accept["session_id"] != accept_actor["session_id"] or accept["task_record_path"] != accept_actor["task_record_path"] or accept["task_record_sha256"] != accept_actor["task_record_sha256"]: raise ValueError("acceptance actor provenance mismatch")
    if _acceptance_actor_conflict(accept["actor_id"], current_sets, expected_task): raise ValueError("task acceptance actor intersects producer set")
    conflicts = _role_conflicts(assignment, current_sets, historical_sets, expected_task)
    if conflicts: raise ValueError("role inequality conflicts: " + ",".join(conflicts))
    negative = _negative_role_tests()
    if not all(negative.values()): raise ValueError("checker role-conflict negative self-tests failed")
    terminal_negative = _terminal_mutation_self_tests()
    if not all(terminal_negative.values()): raise ValueError("checker terminal mutation self-tests failed")
    retry_scope_negative = _scope_retry_mutation_self_tests()
    if not all(retry_scope_negative.values()): raise ValueError("checker retry-scope mutation self-tests failed")
    history_transition_negative = _history_transition_mutation_self_tests()
    if not all(history_transition_negative.values()): raise ValueError("checker history-transition mutation self-tests failed")
    collision_recovery_negative = _collision_recovery_mutation_self_tests()
    if not all(collision_recovery_negative.values()): raise ValueError("checker collision-recovery mutation self-tests failed")
    delivery_negative = _machine_delivery_statement_mutation_self_tests()
    if not all(delivery_negative.values()): raise ValueError("checker machine delivery actor mutation self-tests failed")
    return {"status": "PASS", "task_id": expected_task, "receipt_sha256": _sha(receipt_path), "actor_assignment_sha256": _sha(actor_path), "input_count": len(receipt["inputs"]), "output_count": len(receipt["outputs"]), "current_task_producer_set": sorted(current_sets["task"]), "historical_actor_sets": {k: sorted(v) for k, v in historical_sets.items()}, "cumulative_derived_sets": {k: sorted(v) for k, v in sets.items()}, "historical_provenance_container_count": len(historical_containers), "historical_provenance_containers": sorted(historical_containers), "historical_actual_write_row_count": len(historical_rows), "acceptance_actor_id": accept["actor_id"], "machine_delivery_statement_assignment": {"actor_id": "machine-delivery-statement", "actor_type": "codex_session", "roles": ["machine_delivery_statement"], "status": "PASS"}, "role_conflict_negative_tests": negative, "machine_delivery_statement_mutation_tests": delivery_negative, "terminal_mutation_negative_tests": terminal_negative, "retry_scope_mutation_tests": retry_scope_negative, "history_transition_mutation_tests": history_transition_negative, "collision_recovery_mutation_tests": collision_recovery_negative, "scope_rule_count": len(TASK_SCOPES[expected_task])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    parser.add_argument("--expected-task", required=True, choices=sorted(TASK_ACCEPTANCE_ROLE))
    args = parser.parse_args()
    try:
        result = validate(Path.cwd().resolve(), args.receipt.resolve(), args.actor_assignments.resolve(), args.expected_task)
        sys.stdout.buffer.write(_canonical(result))
        return 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(_canonical({"status": "HOLD", "terminal": "HOLD_WORK_ITEM_RECEIPT", "error": str(exc)}))
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
