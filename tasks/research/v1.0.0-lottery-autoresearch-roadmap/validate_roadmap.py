from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


FORBIDDEN_TEXT = (
    "spec-executor",
    "spec_executor",
    "spec.yaml",
    "repo.url",
    "repo.branch",
    "UNAVAILABLE-NOT-A-GIT-REPOSITORY",
)

EXPECTED_PHASE0_PACKAGES = {f"P0-0{i}" for i in range(1, 8)}
EXPECTED_TASK_IDS = {f"TASK-00{i}" for i in range(1, 9)} | {"TASK-999"}
FINALIZER_INPUT_IDS = {f"TASK-00{i}" for i in range(1, 9)}
REQUIRED_SCOPE_FIELDS = {
    "active_games",
    "excluded_games",
    "per_game_outcome",
    "coverage_tier",
    "corroboration_tier",
    "evidence_ref",
}
EXPECTED_CORROBORATION_TIERS = {
    "corroborated_official",
    "shared_upstream",
    "primary_only",
}
REQUIRED_SKIP_FIELDS = {
    "run_id",
    "task_id",
    "trigger_task_id",
    "trigger_status",
    "reason",
    "evidence_refs",
    "actor",
    "recorded_at_utc",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot parse JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def add(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def json_type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return False


def resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported schema reference: {ref}")
    node: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"unresolvable schema reference: {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"schema reference is not an object: {ref}")
    return node


def schema_errors(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str = "$",
) -> list[str]:
    errors: list[str] = []
    if "$ref" in schema:
        try:
            target = resolve_ref(root, str(schema["$ref"]))
        except ValueError as exc:
            return [f"{path}: {exc}"]
        return schema_errors(instance, target, root, path)

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(json_type_matches(instance, item) for item in expected_types):
            return [f"{path}: expected type {expected_types}, got {type(instance).__name__}"]

    if isinstance(instance, str):
        if len(instance) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: string shorter than minLength")
        pattern = schema.get("pattern")
        if pattern and re.search(str(pattern), instance) is None:
            errors.append(f"{path}: string does not match pattern {pattern!r}")

    if isinstance(instance, list):
        if len(instance) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: array shorter than minItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items are not unique")
        prefix = schema.get("prefixItems", [])
        if isinstance(prefix, list):
            for index, item_schema in enumerate(prefix):
                if index >= len(instance):
                    errors.append(f"{path}: missing prefix item {index}")
                elif isinstance(item_schema, dict):
                    errors.extend(schema_errors(instance[index], item_schema, root, f"{path}[{index}]"))
        item_schema = schema.get("items")
        start = len(prefix) if isinstance(prefix, list) else 0
        if item_schema is False and len(instance) > start:
            errors.append(f"{path}: additional array items are forbidden")
        elif isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(schema_errors(item, item_schema, root, f"{path}[{index}]"))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required if isinstance(required, list) else []:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, value in instance.items():
                if key in properties and isinstance(properties[key], dict):
                    errors.extend(schema_errors(value, properties[key], root, f"{path}.{key}"))
                elif schema.get("additionalProperties") is False:
                    errors.append(f"{path}: additional property {key!r} is forbidden")
    return errors


def validate_schema(instance: dict[str, Any], schema_path: Path, errors: list[str]) -> None:
    try:
        schema = load_json(schema_path)
    except ValueError as exc:
        errors.append(str(exc))
        return
    add(
        errors,
        schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
        "roadmap schema must declare JSON Schema 2020-12",
    )
    errors.extend(f"JSON Schema validation failed: {item}" for item in schema_errors(instance, schema, schema))


def validate_dag(plan: dict[str, Any], errors: list[str]) -> None:
    tasks = plan.get("tasks", [])
    ids = [task.get("task_id") for task in tasks if isinstance(task, dict)]
    id_set = set(ids)
    add(errors, id_set == EXPECTED_TASK_IDS, f"task set mismatch: {sorted(id_set)}")
    add(errors, len(ids) == len(id_set), "task IDs must be unique")

    adjacency = {task_id: [] for task_id in id_set}
    edge_pairs: set[tuple[str, str]] = set()
    for edge in plan.get("dag", {}).get("edges", []):
        source = edge.get("from")
        target = edge.get("to")
        add(errors, source in id_set, f"unknown DAG source: {source}")
        add(errors, target in id_set, f"unknown DAG target: {target}")
        add(errors, target != "TASK-999", "TASK-999 must use finalizer_edges, not normal success-only edges")
        add(errors, source != target, f"self dependency: {source}")
        pair = (source, target)
        add(errors, pair not in edge_pairs, f"duplicate DAG edge: {source}->{target}")
        edge_pairs.add(pair)
        if source in adjacency and target in id_set:
            adjacency[source].append(target)

    for task in tasks:
        task_id = task.get("task_id")
        dependencies = set(task.get("dependencies", []))
        add(errors, dependencies <= id_set, f"{task_id} has unknown dependencies")
        incoming = {source for source, target in edge_pairs if target == task_id}
        add(errors, incoming == dependencies, f"{task_id} dependencies do not match normal DAG incoming edges")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            errors.append(f"DAG cycle detected at {node}")
            return
        if node in visited:
            return
        visiting.add(node)
        for child in adjacency.get(node, []):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for task_id in id_set:
        visit(task_id)

    finalizer_edges = plan.get("dag", {}).get("finalizer_edges", [])
    finalizer_pairs = {(edge.get("from"), edge.get("to")) for edge in finalizer_edges}
    expected_pairs = {(source, "TASK-999") for source in FINALIZER_INPUT_IDS}
    add(errors, finalizer_pairs == expected_pairs, "finalizer_edges must cover TASK-001 through TASK-008 exactly")
    for edge in finalizer_edges:
        add(errors, edge.get("mode") == "completion_or_terminal", "finalizer edge mode must be completion_or_terminal")

    flattened = [
        task_id
        for group in plan.get("dag", {}).get("execution_order", [])
        for task_id in group
    ]
    add(errors, len(flattened) == len(set(flattened)), "execution_order contains duplicates")
    add(errors, set(flattened) == id_set, "execution_order must contain every task exactly once")
    positions = {task_id: index for index, task_id in enumerate(flattened)}
    for source, target in edge_pairs:
        if source in positions and target in positions:
            add(errors, positions[source] < positions[target], f"execution_order violates {source}->{target}")
    add(errors, flattened and flattened[-1] == "TASK-999", "TASK-999 must be the final execution_order item")


def evidence_is_output(evidence: str, outputs: list[str]) -> bool:
    return any(
        evidence == output
        or evidence.startswith(output.rstrip("/") + "/")
        or (output.endswith("/") and evidence.startswith(output))
        for output in outputs
    )


def validate_tasks(plan: dict[str, Any], root: Path, errors: list[str]) -> None:
    acceptance_ids: set[str] = set()
    shared_scope_evidence = plan.get("game_scope_contract", {}).get("source_evidence")
    declared_outputs = {
        str(output)
        for planned_task in plan.get("tasks", [])
        for output in planned_task.get("output_paths", [])
    }
    for task in plan.get("tasks", []):
        task_id = task.get("task_id")
        outputs = [str(item) for item in task.get("output_paths", [])]
        add(errors, bool(outputs), f"{task_id} must declare output_paths")

        source_refs = [str(item) for item in task.get("source_refs", [])]
        add(errors, bool(source_refs), f"{task_id} must declare source_refs")
        for source in source_refs:
            if source.startswith("docs/"):
                add(
                    errors,
                    (root / source).exists() or source in declared_outputs,
                    f"{task_id} source ref is neither present nor a declared roadmap output: {source}",
                )

        scope = task.get("game_scope", {})
        if task_id == "TASK-001":
            add(errors, scope.get("mode") == "producer", "TASK-001 must produce game scope")
            add(errors, scope.get("output_evidence") == shared_scope_evidence, "TASK-001 scope evidence path mismatch")
            add(errors, set(scope.get("required_fields", [])) == REQUIRED_SCOPE_FIELDS, "TASK-001 game scope fields mismatch")
        else:
            add(errors, scope.get("mode") == "consumer", f"{task_id} must consume game scope")
            add(errors, scope.get("input_evidence") == shared_scope_evidence, f"{task_id} game scope input mismatch")
            scope_rule = str(scope.get("output_rule", ""))
            add(errors, "active_game" in scope_rule and "excluded_game" in scope_rule, f"{task_id} scope rule must address active and excluded games")

        review = task.get("review", {})
        attestation = str(review.get("attestation_path", ""))
        add(errors, bool(attestation), f"{task_id} must declare reviewer attestation")
        add(errors, evidence_is_output(attestation, outputs), f"{task_id} reviewer attestation is outside output_paths")
        add(errors, review.get("owner_role") != review.get("reviewer_role"), f"{task_id} owner and reviewer roles must differ")
        execution = task.get("execution", {})
        add(errors, "HOLD" in str(execution.get("timeout_or_hold_rule", "")), f"{task_id} timeout rule must produce HOLD")

        task_acceptance_ids: set[str] = set()
        task_evidence: set[str] = set()
        for acceptance in task.get("acceptance", []):
            acceptance_id = acceptance.get("id")
            add(errors, acceptance_id not in acceptance_ids, f"duplicate acceptance ID: {acceptance_id}")
            acceptance_ids.add(acceptance_id)
            task_acceptance_ids.add(acceptance_id)
            evidence = [str(item) for item in acceptance.get("evidence", [])]
            task_evidence.update(evidence)
            add(errors, bool(evidence), f"{acceptance_id} must declare evidence")
            for item in evidence:
                add(errors, evidence_is_output(item, outputs), f"{acceptance_id} evidence is outside task outputs: {item}")
        add(errors, attestation in task_evidence, f"{task_id} acceptance must cite reviewer attestation")

        verified_ids: set[str] = set()
        for verification in task.get("verification", []):
            command = str(verification.get("command", ""))
            add(errors, command.startswith("python "), f"{task_id} verification must be a direct python command")
            add(
                errors,
                not any(term.lower() in command.lower() for term in FORBIDDEN_TEXT),
                f"{task_id} verification contains forbidden task-runner coupling",
            )
            covered = set(verification.get("covers_acceptance_ids", []))
            add(errors, bool(covered), f"{task_id} verification must map acceptance IDs")
            add(errors, covered <= task_acceptance_ids, f"{task_id} verification maps unknown acceptance IDs")
            verified_ids.update(covered)
        add(errors, verified_ids == task_acceptance_ids, f"{task_id} verification coverage is incomplete")

    by_id = {task.get("task_id"): task for task in plan.get("tasks", [])}
    task_001 = by_id.get("TASK-001", {})
    add(errors, task_001.get("work_kind") == "data_feasibility_execution", "TASK-001 must be an execution task")
    required_paths = {
        "artifacts/phase-0/",
        "scripts/phase0/",
        "tests/phase0/",
        "docs/research/TASK-001-official-data-source-feasibility.md",
    }
    add(errors, required_paths <= set(task_001.get("output_paths", [])), "TASK-001 lacks executable phase-0 outputs")

    task_007 = by_id.get("TASK-007", {})
    add(errors, task_007.get("work_kind") == "benchmark_protocol", "TASK-007 must be a protocol")
    task_007_text = json.dumps(task_007, ensure_ascii=False)
    add(errors, "未执行" in task_007_text or "未来实现执行" in task_007_text, "TASK-007 must disclaim unexecuted results")

    finalizer = by_id.get("TASK-999", {})
    add(errors, finalizer.get("dependencies") == [], "TASK-999 normal dependencies must be empty")
    add(errors, set(finalizer.get("finalizer_dependencies", [])) == FINALIZER_INPUT_IDS, "TASK-999 finalizer_dependencies mismatch")
    add(
        errors,
        finalizer.get("dependency_policy", {}).get("mode") == "all_passed_or_terminal_short_circuit",
        "TASK-999 must support normal and terminal short-circuit entry",
    )


def decide(states: dict[str, str], hard_failure: bool = False) -> str:
    values = [states.get(task_id) for task_id in sorted(FINALIZER_INPUT_IDS)]
    if hard_failure or "stopped" in values:
        return "STOP"
    if values and all(value == "passed" for value in values):
        return "GO"
    if any(value in {"held", "skipped_terminal", "planned", "in_progress", None} for value in values):
        return "HOLD"
    raise ValueError(f"unclassifiable task states: {values}")


def validate_terminal_logic(plan: dict[str, Any], errors: list[str]) -> None:
    state_contract = plan.get("task_state_contract", {})
    states = set(state_contract.get("states", []))
    add(errors, {"passed", "held", "stopped", "skipped_terminal"} <= states, "task state contract lacks terminal states")
    allowed = state_contract.get("allowed_transitions", {})
    add(errors, "skipped_terminal" in allowed.get("planned", []), "planned tasks must allow terminal skip")
    for terminal in ("passed", "stopped", "skipped_terminal"):
        add(errors, allowed.get(terminal) == [], f"{terminal} must be terminal")

    terminal_flow = plan.get("terminal_flow", {})
    add(errors, terminal_flow.get("finalizer_task") == "TASK-999", "terminal finalizer must be TASK-999")
    add(
        errors,
        terminal_flow.get("state_record_path_template") == "artifacts/research/task-states/{run_id}/{task_id}.json",
        "terminal state records must use the frozen per-task path template",
    )
    add(errors, "新 run_id" in str(state_contract.get("resume_rule", "")), "post-finalizer remediation must start a new run_id")
    add(errors, set(terminal_flow.get("skip_record_required_fields", [])) == REQUIRED_SKIP_FIELDS, "skip record field contract mismatch")
    add(errors, "skipped_terminal" in str(plan.get("game_scope_contract", {}).get("zero_active_games_rule", "")), "zero active games must terminal-skip downstream work")

    decision = plan.get("final_acceptance", {}).get("decision_logic", {})
    add(errors, decision.get("evaluation_order") == ["STOP", "GO", "HOLD"], "decision order must be STOP, GO, HOLD")
    passed = {task_id: "passed" for task_id in FINALIZER_INPUT_IDS}
    held = {task_id: ("held" if task_id == "TASK-001" else "skipped_terminal") for task_id in FINALIZER_INPUT_IDS}
    stopped = {task_id: ("stopped" if task_id == "TASK-001" else "skipped_terminal") for task_id in FINALIZER_INPUT_IDS}
    add(errors, decide(passed) == "GO", "decision truth table failed normal GO path")
    add(errors, decide(held) == "HOLD", "decision truth table failed HOLD short-circuit path")
    add(errors, decide(stopped) == "STOP", "decision truth table failed STOP short-circuit path")
    add(errors, decide(passed, hard_failure=True) == "STOP", "hard failure must dominate GO")


def validate_phase0_coverage(plan: dict[str, Any], contract: dict[str, Any], errors: list[str]) -> None:
    rows = plan.get("phase_0_coverage", [])
    packages = [row.get("work_package") for row in rows]
    add(errors, set(packages) == EXPECTED_PHASE0_PACKAGES, "phase_0_coverage must cover P0-01 through P0-07 exactly")
    add(errors, len(packages) == len(set(packages)), "phase_0_coverage contains duplicate work packages")

    contract_artifacts = set(contract.get("planned_artifacts", {}))
    contract_gates = {gate.get("id") for gate in contract.get("hard_gates", [])}
    mapped_artifacts = {key for row in rows for key in row.get("artifact_keys", [])}
    mapped_gates = {gate for row in rows for gate in row.get("gate_ids", [])}
    add(
        errors,
        mapped_artifacts == contract_artifacts,
        f"phase-0 artifact coverage mismatch: missing={sorted(contract_artifacts - mapped_artifacts)}, extra={sorted(mapped_artifacts - contract_artifacts)}",
    )
    add(
        errors,
        mapped_gates == contract_gates,
        f"phase-0 gate coverage mismatch: missing={sorted(contract_gates - mapped_gates)}, extra={sorted(mapped_gates - contract_gates)}",
    )

    task_001 = next((task for task in plan.get("tasks", []) if task.get("task_id") == "TASK-001"), {})
    add(
        errors,
        set(task_001.get("required_artifact_keys", [])) == contract_artifacts,
        "TASK-001 required_artifact_keys must exactly match the phase-0 contract",
    )


def validate_phase0_contract_semantics(contract: dict[str, Any], errors: list[str]) -> None:
    add(errors, contract.get("version") == "1.3", "phase-0 contract version must be 1.3")

    artifacts = contract.get("planned_artifacts", {})
    add(
        errors,
        artifacts.get("reviewer_assignment") == "artifacts/phase-0/reviewer-assignment.json",
        "P0-01 reviewer assignment artifact is missing or has the wrong path",
    )
    add(
        errors,
        artifacts.get("reviewer_attestation") == "artifacts/phase-0/reviewer-attestation.json",
        "P0-07 reviewer attestation artifact is missing or has the wrong path",
    )
    schema_keys = set(contract.get("schema_policy", {}).get("machine_artifacts_requiring_schema", []))
    add(errors, "reviewer_assignment" in schema_keys, "reviewer assignment must have a frozen schema")
    add(errors, "reviewer_attestation" in schema_keys, "reviewer attestation must have a frozen schema")

    policy = contract.get("corroboration_policy", {})
    add(
        errors,
        set(policy.get("confidence_tiers", {})) == EXPECTED_CORROBORATION_TIERS,
        "corroboration confidence tiers must be corroborated_official, shared_upstream, and primary_only",
    )
    add(
        errors,
        policy.get("required_record_field") == "corroboration_tier",
        "corroboration_tier must be required on records",
    )

    gates = {str(gate.get("id")): gate for gate in contract.get("hard_gates", [])}
    scope_evidence = set(gates.get("G-SCOPE", {}).get("evidence", []))
    add(errors, "reviewer_assignment" in scope_evidence, "G-SCOPE must use the pre-execution reviewer assignment")
    add(errors, "reviewer_attestation" not in scope_evidence, "G-SCOPE must not require the post-execution reviewer attestation")

    authority_standard = str(gates.get("G-AUTHORITY", {}).get("standard", "")).lower()
    add(errors, "authoritative primary" in authority_standard, "G-AUTHORITY must require an authoritative primary source")
    add(
        errors,
        "at least one official corroborating channel" not in authority_standard,
        "G-AUTHORITY must not make a second official channel a hard feasibility prerequisite",
    )
    add(errors, "corroboration tier" in authority_standard, "G-AUTHORITY must require an explicit corroboration tier")

    reproducibility_evidence = set(gates.get("G-REPRODUCIBILITY", {}).get("evidence", []))
    add(errors, "reviewer_attestation" in reproducibility_evidence, "G-REPRODUCIBILITY must use the post-replay attestation")
    handoff_standard = str(gates.get("G-HANDOFF", {}).get("standard", ""))
    add(errors, "corroboration_tier" in handoff_standard, "G-HANDOFF must expose corroboration_tier")


def validate_task_cards(plan: dict[str, Any], roadmap_dir: Path, errors: list[str]) -> None:
    fixtures = roadmap_dir / "fixtures"
    for task in plan.get("tasks", []):
        task_id = str(task.get("task_id"))
        task_dir = fixtures / task_id
        prompt_path = task_dir / "PROMPT.md"
        add(errors, prompt_path.exists(), f"missing task card: {prompt_path}")
        if not prompt_path.exists():
            continue
        prompt = prompt_path.read_text(encoding="utf-8")
        for source in task.get("source_refs", []):
            add(errors, str(source) in prompt, f"{task_id} task card misses source ref: {source}")
        for output in task.get("output_paths", []):
            add(errors, str(output) in prompt, f"{task_id} task card misses output: {output}")
        for acceptance in task.get("acceptance", []):
            acceptance_id = str(acceptance.get("id"))
            criterion = str(acceptance.get("criterion"))
            add(errors, acceptance_id in prompt, f"{task_id} task card misses acceptance ID: {acceptance_id}")
            add(errors, criterion in prompt, f"{task_id} task card criterion drift: {acceptance_id}")
        for verification in task.get("verification", []):
            command = str(verification.get("command"))
            add(errors, command in prompt, f"{task_id} task card misses verification command")
        add(errors, str(task.get("review", {}).get("attestation_path", "")) in prompt, f"{task_id} task card misses review attestation")
        assignment_path = str(task.get("review", {}).get("assignment_path", ""))
        if assignment_path:
            add(errors, assignment_path in prompt, f"{task_id} task card misses review assignment")
        add(errors, "active_game" in prompt and "excluded_game" in prompt, f"{task_id} task card misses game-scope rule")
        for execution_value in task.get("execution", {}).values():
            add(errors, str(execution_value) in prompt, f"{task_id} task card misses execution/resource contract")
        for pointer_name in ("AGENTS.md", "CLAUDE.md"):
            pointer = task_dir / pointer_name
            add(errors, pointer.exists(), f"missing task pointer: {pointer}")
            if pointer.exists():
                add(errors, "PROMPT.md" in pointer.read_text(encoding="utf-8"), f"{pointer} must point to PROMPT.md")


def validate_no_task_runner_coupling(plan_path: Path, plan: dict[str, Any], errors: list[str]) -> None:
    text = plan_path.read_text(encoding="utf-8").lower()
    for term in FORBIDDEN_TEXT:
        add(errors, term.lower() not in text, f"roadmap contains forbidden coupling: {term}")
    add(errors, plan.get("tooling_independent") is True, "tooling_independent must be true")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roadmap", required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()

    roadmap_path = Path(args.roadmap)
    contract_path = Path(args.contract)
    errors: list[str] = []
    try:
        plan = load_json(roadmap_path)
        contract = load_json(contract_path)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)

    schema_path = roadmap_path.parent / "roadmap.schema.json"
    add(errors, schema_path.exists(), f"missing roadmap schema: {schema_path}")
    if schema_path.exists():
        validate_schema(plan, schema_path, errors)
    validate_no_task_runner_coupling(roadmap_path, plan, errors)
    validate_dag(plan, errors)
    validate_tasks(plan, Path.cwd(), errors)
    validate_terminal_logic(plan, errors)
    validate_phase0_contract_semantics(contract, errors)
    validate_phase0_coverage(plan, contract, errors)
    validate_task_cards(plan, roadmap_path.parent, errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        sys.exit(1)
    print("PASS: self-contained tooling-independent roadmap validation")


if __name__ == "__main__":
    main()
