from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, RefResolver


AUTHORITY_COMMIT = "bb25c5823e27ce81c40b6c73da0b77cfc906e4d7"
OWNER_ACTOR = "p4-contract-statistical-owner-i01"
OWNER_SESSION = "/root/contract_statistical"
ACCEPTANCE_ACTOR = "p4-acceptance-engineer-i01"
ACCEPTANCE_SESSION = "/root/acceptance_engineer"
EXPECTED_T00_INDEPENDENT_SHA256 = "7f087ba6f25ecf2593f02783f7ebeb09198c339f032d23d32f48fbb8e53c08a7"
EXPECTED_T01_I02_INDEPENDENT_SHA256 = "01b26ce7949a13200699a5b44ff1cd50072c120f59af2634302e5600aef13199"
EXPECTED_T01_I03_RECEIPT_SHA256 = "b77595f9f5dc429caf5fe29f6f7da315c0b15fb427bd6930e9f79ae08fc81852"
EXPECTED_T01_I03_INDEPENDENT_SHA256 = "008856c3f0bbcced0a119bb73ded665e2624e0b19e1fe60886ab8fc591e297ba"
EXPECTED_T01_I02_PRESERVATION_SHA256 = "63e4d5c5217c73394fb2f17cd5ab56e15c05f3e17e8d773206ab34b8ac5f779c"
EXPECTED_T01_I03_PRESERVATION_SHA256 = "de952e53a0cb5aed8e5c8734c5c361e5c49bb6cfaf7f4a3625bf026dd357abb8"
EXPECTED_T01_I04_RECEIPT_SHA256 = "d2bbafaa2724f0092669845b4882917b7a3cb36ca46db074f5505fcd23451e82"
EXPECTED_T01_I04_INDEPENDENT_SHA256 = "56c5e9cbf6e842ffe949abddb9bf950c99a82a776ac04308beac0cb907005fc5"
EXPECTED_T01_I04_PRESERVATION_SHA256 = "d9a5180bd705b0f2d25c4a38ead2e75d65a2cfa0cb1f60430f44400cfb11bd9d"
EXPECTED_T01_I05_RECEIPT_SHA256 = "0a20c0402095b77399f903ca22c693db851262112f65787290acc5ddce695864"
EXPECTED_T01_I05_INDEPENDENT_SHA256 = "a0c828ee8851a13793bd84171264aa315f3e3ec0e2fd566c865c626494ea46a9"
EXPECTED_T01_I05_PRESERVATION_SHA256 = "08bfb2239a4f50f8656c46b15414c38b808274d244548cb587932f216212dade"

REQUIRED_CONFIGS = {
    "acceptance-assertions.json", "acceptance-contract.json", "actor-inequality-contract.json",
    "alpha-contract.json", "calendar-contract.json", "cli-contract.json", "correction-policy-v1.json",
    "decision-contract.json", "delivery-coverage.json", "e2e-registry.json", "fault-contract.json",
    "feature-registry.json", "metric-contract.json", "model-registry.json", "power-controller-command.json", "scientific-power-controller-command.json",
    "probability-ranking-contract.json", "provenance-contract.json",
    "qualification-preregistration.json", "schedule-contract.json", "slo-contract.json",
    "source-review-contract.json", "state-contract.json", "time-contract.json",
}

REQUIRED_SCHEMAS = {
    "acceptance.schema.json", "actor-assignment.schema.json", "alert.schema.json",
    "alpha-wealth.schema.json", "calendar.schema.json", "candidate.schema.json",
    "champion-by-game.schema.json", "checkpoint.schema.json", "correction-closure.schema.json",
    "correction-impact.schema.json", "data-release.schema.json", "decision.schema.json",
    "e2e-receipt.schema.json", "experiment.schema.json", "forecast-diagnostic.schema.json",
    "forecast.schema.json", "ledger-event.schema.json", "manifest.schema.json",
    "model-status.schema.json", "provenance.schema.json", "ranking.schema.json",
    "result-revision.schema.json", "review.schema.json", "schedule.schema.json",
    "score.schema.json", "machine-delivery.schema.json", "source-observation.schema.json",
    "source-review.schema.json", "top-k-status.schema.json", "window-metric.schema.json",
    "work-item-receipt.schema.json",
}

EXPECTED_COMMANDS = {
    "contract validate", "data genesis", "data ingest", "data verify", "data release", "data current",
    "calendar build", "calendar validate", "schedule build", "schedule tick", "schedule audit",
    "forecast prepare", "forecast generate", "forecast lock", "forecast show", "result unlock",
    "score one", "score window", "score correct", "research decide", "research run", "research resume",
    "state project", "state show", "replay release", "validate unit", "validate e2e", "validate final",
    "release assemble", "release accept",
}
SCORE_CORRECT_REQUIRED_FLAGS = ["--fixture", "--oracle", "--runtime-root", "--clock"]
SCORE_CORRECT_FIXTURE_IDENTITY_REQUIREMENTS = {
    "correction_policy_path": "config/phase4/correction-policy-v1.json",
    "correction_policy_version": "correction-policy-v1",
    "correction_policy_sha256": "c544b5242d4b4fd6b4e065f857d89b22ccd60b281ab1e5e304452ebb73a10a06",
}
RESEARCH_DECIDE_REQUIRED_FLAGS = ["--fixture", "--runtime-root", "--clock"]
RESEARCH_DECIDE_PREREGISTRATION_IDENTITY_REQUIREMENTS = {
    "preregistration_path": "config/phase4/qualification-preregistration.json",
    "preregistration_sha256": "abb8d09dd5464d1eacc316b376ebad39eb885e52ed3c3de7d3de143cb1b76264",
    "preregistration_id": "qualification-preregistration-v1:abb8d09dd5464d1eacc316b376ebad39eb885e52ed3c3de7d3de143cb1b76264",
}
RESEARCH_RUN_REQUIRED_FLAGS = [
    "--mode", "--preregistration", "--feasibility", "--sequences-per-cell",
    "--output", "--seed-domain", "--clock",
]
RESEARCH_RUN_BINDING_REQUIREMENTS = {
    "mode": "development-design-selection",
    "preregistration_path": "config/phase4/qualification-preregistration.json",
    "preregistration_sha256": "abb8d09dd5464d1eacc316b376ebad39eb885e52ed3c3de7d3de143cb1b76264",
    "preregistration_id": "qualification-preregistration-v1:abb8d09dd5464d1eacc316b376ebad39eb885e52ed3c3de7d3de143cb1b76264",
    "feasibility_path": "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T10/feasibility/certificate.json",
    "feasibility_sha256": "e54a3b49faeff1507b727d4c6153b04160641d954f274334e0171c59ab4ac1ee",
    "feasibility_acceptance_path": "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T10/attempts/T10-I07/independent-validation.json",
    "feasibility_acceptance_sha256": "faa4ff88db457ce7fc25c6f10fdc59c2c80b1a9bc4a2405fad8182b82c4c8699",
    "sequences_per_cell": 2000,
    "output": "artifacts/phase-4-prep/p4-prep-controller-issued-i01/qualification-design/development",
    "seed_domain": "development",
    "clock": "fixture",
}
STATE_PROJECT_REQUIRED_FLAGS = ["--runtime-root", "--output"]
STATE_PROJECT_INSTALLED_CONTRACT_REQUIREMENTS = {
    "contract_path": "config/phase4/state-contract.json",
    "contract_sha256": "a2b241a40c761a0e1c25a432375734e33caadb485dd66971808c388d4b94388b",
    "contract_identity": "phase4-state-v1",
}
VALIDATE_E2E_REQUIRED_FLAGS = ["--registry", "--output", "--clock"]
VALIDATE_E2E_INSTALLED_REGISTRY_REQUIREMENTS = {
    "registry_path": "config/phase4/e2e-registry.json",
    "registry_sha256": "3a136a237f7b2c3693ab11792ac36bf90999320fcb1e35f7f2cc0e5a747b17b6",
    "registry_identity": "phase4-e2e-registry-v1:3a136a237f7b2c3693ab11792ac36bf90999320fcb1e35f7f2cc0e5a747b17b6",
}
POWER_CONTROLLER_COMMAND_SHA256 = "11fa45cbd2f1d43c5fea65e3df377e986d0a0603567fcbf21fa9288c49cef49d"
POWER_CONTROLLER_COMMAND = {
    "schema_version": "1.0.0",
    "artifact_type": "phase4_benchmark_controller_command",
    "argv": ["python3", "tests/phase4/fixtures/benchmark/controller_worker.py"],
    "protocol": "json_stdin_stdout_v1",
    "non_scientific": True,
    "qualification_seed_domain": None,
}
POWER_CONTROLLER_BENCHMARK_BINDING = {
    "benchmark_fixture_id": "benchmark-fixture-v1:5edefaa63c80c5c8c938d20dd48c87096250344ba3f533252b0a4344a11994c7",
    "benchmark_fixture_registry_sha256": "bb1cf44d495ee4890d8534193ed8d5dccf7702df6efa82b7bfd780b0bfbf0c6b",
    "controller_command_sha256": POWER_CONTROLLER_COMMAND_SHA256,
    "controller_source_sha256": "010905fa0f382fa49b6ee2619e9320fc73c3be40c12dda95c36c6635c5ea494b",
    "authoritative_benchmark_receipt_sha256": "d73256b6cd9cb8725c2541b10d9c30cc1a8176b5e53376e41b5d37662818926f",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_canonical_bytes(value))


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _schema_store(schema_root: Path) -> dict[str, Any]:
    store: dict[str, Any] = {}
    for path in schema_root.glob("*.schema.json"):
        schema = _load(path)
        store[path.resolve().as_uri()] = schema
        store[path.name] = schema
        if "$id" in schema:
            store[schema["$id"]] = schema
    return store


def _validator(schema_root: Path, schema_name: str) -> Draft202012Validator:
    path = schema_root / schema_name
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    resolver = RefResolver(base_uri=path.resolve().as_uri(), referrer=schema, store=_schema_store(schema_root))
    return Draft202012Validator(schema, resolver=resolver, format_checker=FormatChecker())


def _assert_valid(validator: Draft202012Validator, payload: Any, label: str) -> None:
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(map(str, first.absolute_path)) or "$"
        raise ValueError(f"{label} schema violation at {location}: {first.message}")


def _assert_invalid(validator: Draft202012Validator, payload: Any, label: str) -> None:
    if not list(validator.iter_errors(payload)):
        raise ValueError(f"negative contract case was accepted: {label}")


def _assignment_actor(assignment: dict[str, Any], actor_id: str) -> dict[str, Any]:
    rows = [row for row in assignment["assignments"] if row["actor_id"] == actor_id]
    if len(rows) != 1:
        raise ValueError(f"actor assignment does not uniquely bind {actor_id}")
    return rows[0]


def _validate_inputs(root: Path, authority_receipt: Path, actor_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _load(authority_receipt)
    if receipt.get("task_id") != "T00" or receipt.get("status") != "PASS" or receipt.get("source_commit") != AUTHORITY_COMMIT:
        raise ValueError("T00 PASS receipt or authority identity mismatch")
    independent = authority_receipt.parent / "independent-validation-I02.json"
    if not independent.is_file() or _sha256(independent) != EXPECTED_T00_INDEPENDENT_SHA256:
        raise ValueError("independent T00 validation identity mismatch")
    independent_payload = _load(independent)
    if independent_payload.get("status") != "PASS":
        raise ValueError("independent T00 validation did not PASS")
    assignment = _load(actor_path)
    _assert_valid(_validator(root / "schemas/phase4", "actor-assignment.schema.json"), assignment, "preparation actor assignment")
    owner = _assignment_actor(assignment, OWNER_ACTOR)
    acceptor = _assignment_actor(assignment, ACCEPTANCE_ACTOR)
    if set(owner["roles"]) != {"contract_owner", "statistical_owner"} or "T01" not in owner["task_ids"]:
        raise ValueError("T01 owner role/task binding mismatch")
    if "acceptance_engineer" not in acceptor["roles"] or "T01" not in acceptor["task_ids"]:
        raise ValueError("T01 acceptance actor binding mismatch")
    if owner["session_id"] != OWNER_SESSION or acceptor["session_id"] != ACCEPTANCE_SESSION:
        raise ValueError("T01 actor session mismatch")
    if OWNER_ACTOR == ACCEPTANCE_ACTOR:
        raise ValueError("T01 owner cannot self-accept")
    return assignment, independent_payload


def _validate_score_correct_provider_semantics(command: dict[str, Any], fixture: dict[str, Any]) -> None:
    if set(command) != {"verb", "required_flags", "allowed_exit_codes"} or command.get("verb") != "score correct":
        raise ValueError("score correct command record shape mismatch")
    if command.get("required_flags") != SCORE_CORRECT_REQUIRED_FLAGS:
        raise ValueError("score correct required flags drift from frozen T06 command")
    if command.get("allowed_exit_codes") != [0, 4, 5, 6, 20, 30]:
        raise ValueError("score correct allowed exits drift")
    if not isinstance(fixture, dict):
        raise ValueError("score correct fixture must be an explicit object")
    if not isinstance(fixture.get("new_result_revision_id"), str) or not fixture["new_result_revision_id"]:
        raise ValueError("score correct fixture missing explicit identity: new_result_revision_id")
    for identity, expected in SCORE_CORRECT_FIXTURE_IDENTITY_REQUIREMENTS.items():
        if fixture.get(identity) != expected:
            raise ValueError(f"score correct fixture correction-policy identity mismatch: {identity}")


def _score_correct_contract_mutation_tests(command: dict[str, Any]) -> dict[str, bool]:
    fixture = {"new_result_revision_id": "result-revision-v1:fixture", **SCORE_CORRECT_FIXTURE_IDENTITY_REQUIREMENTS}
    _validate_score_correct_provider_semantics(command, fixture)

    def rejected(candidate_command: dict[str, Any], candidate_fixture: Any) -> bool:
        try:
            _validate_score_correct_provider_semantics(candidate_command, candidate_fixture)
        except ValueError:
            return True
        return False

    missing_flags = all(
        rejected({**command, "required_flags": [flag for flag in SCORE_CORRECT_REQUIRED_FLAGS if flag != missing]}, fixture)
        for missing in SCORE_CORRECT_REQUIRED_FLAGS
    )
    unknown_flag = rejected({**command, "required_flags": [*SCORE_CORRECT_REQUIRED_FLAGS, "--unknown"]}, fixture)
    fixture_identity_fields = ("new_result_revision_id", *SCORE_CORRECT_FIXTURE_IDENTITY_REQUIREMENTS)
    missing_fixture_identities = all(
        rejected(command, {key: value for key, value in fixture.items() if key != missing})
        for missing in fixture_identity_fields
    )
    empty_fixture_identities = all(
        rejected(command, {**fixture, identity: ""}) for identity in fixture_identity_fields
    )
    tampered_policy_identities = all(
        rejected(command, {**fixture, identity: f"{expected}.tampered"})
        for identity, expected in SCORE_CORRECT_FIXTURE_IDENTITY_REQUIREMENTS.items()
    )
    return {
        "exact_frozen_flags_positive": True,
        "each_missing_required_flag_rejected": missing_flags,
        "unknown_flag_rejected": unknown_flag,
        "each_missing_fixture_identity_rejected": missing_fixture_identities,
        "empty_fixture_identity_rejected": empty_fixture_identities,
        "tampered_correction_policy_identity_rejected": tampered_policy_identities,
        "implicit_identity_defaults_forbidden": missing_fixture_identities and empty_fixture_identities,
    }


def _validate_research_decide_provider_semantics(command: dict[str, Any], fixture: dict[str, Any]) -> None:
    if set(command) != {"verb", "required_flags", "allowed_exit_codes"} or command.get("verb") != "research decide":
        raise ValueError("research decide command record shape mismatch")
    if command.get("required_flags") != RESEARCH_DECIDE_REQUIRED_FLAGS:
        raise ValueError("research decide required flags drift from frozen T07 command")
    if command.get("allowed_exit_codes") != [0, 4, 5, 6, 20, 30]:
        raise ValueError("research decide allowed exits drift")
    if not isinstance(fixture, dict):
        raise ValueError("research decide fixture must be an explicit object")
    if not isinstance(fixture.get("decision_id"), str) or not fixture["decision_id"]:
        raise ValueError("research decide fixture missing explicit identity: decision_id")
    for identity, expected in RESEARCH_DECIDE_PREREGISTRATION_IDENTITY_REQUIREMENTS.items():
        if fixture.get(identity) != expected:
            raise ValueError(f"research decide fixture preregistration identity mismatch: {identity}")


def _research_decide_contract_mutation_tests(command: dict[str, Any]) -> dict[str, bool]:
    fixture = {"decision_id": "decision-v1:fixture", **RESEARCH_DECIDE_PREREGISTRATION_IDENTITY_REQUIREMENTS}
    _validate_research_decide_provider_semantics(command, fixture)

    def rejected(candidate_command: dict[str, Any], candidate_fixture: Any) -> bool:
        try:
            _validate_research_decide_provider_semantics(candidate_command, candidate_fixture)
        except ValueError:
            return True
        return False

    missing_flags = all(
        rejected({**command, "required_flags": [flag for flag in RESEARCH_DECIDE_REQUIRED_FLAGS if flag != missing]}, fixture)
        for missing in RESEARCH_DECIDE_REQUIRED_FLAGS
    )
    unknown_flag = rejected({**command, "required_flags": [*RESEARCH_DECIDE_REQUIRED_FLAGS, "--unknown"]}, fixture)
    fixture_identity_fields = ("decision_id", *RESEARCH_DECIDE_PREREGISTRATION_IDENTITY_REQUIREMENTS)
    missing_fixture_identities = all(
        rejected(command, {key: value for key, value in fixture.items() if key != missing})
        for missing in fixture_identity_fields
    )
    empty_fixture_identities = all(
        rejected(command, {**fixture, identity: ""}) for identity in fixture_identity_fields
    )
    tampered_preregistration_identities = all(
        rejected(command, {**fixture, identity: f"{expected}.tampered"})
        for identity, expected in RESEARCH_DECIDE_PREREGISTRATION_IDENTITY_REQUIREMENTS.items()
    )
    return {
        "exact_frozen_flags_positive": True,
        "each_missing_required_flag_rejected": missing_flags,
        "unknown_flag_rejected": unknown_flag,
        "each_missing_fixture_identity_rejected": missing_fixture_identities,
        "empty_fixture_identity_rejected": empty_fixture_identities,
        "tampered_preregistration_identity_rejected": tampered_preregistration_identities,
        "implicit_identity_defaults_forbidden": missing_fixture_identities and empty_fixture_identities,
    }


def _validate_research_run_provider_semantics(command: dict[str, Any], binding: dict[str, Any]) -> None:
    if set(command) != {"verb", "required_flags", "allowed_exit_codes"} or command.get("verb") != "research run":
        raise ValueError("research run command record shape mismatch")
    if command.get("required_flags") != RESEARCH_RUN_REQUIRED_FLAGS:
        raise ValueError("research run required flags drift from frozen T12 command")
    if command.get("allowed_exit_codes") != [0, 4, 5, 6, 20, 30]:
        raise ValueError("research run allowed exits drift")
    if not isinstance(binding, dict) or set(binding) != set(RESEARCH_RUN_BINDING_REQUIREMENTS):
        raise ValueError("research run explicit execution binding shape mismatch")
    for identity, expected in RESEARCH_RUN_BINDING_REQUIREMENTS.items():
        if binding.get(identity) != expected:
            raise ValueError(f"research run binding mismatch: {identity}")


def _research_run_contract_mutation_tests(
    command: dict[str, Any], actual_binding: dict[str, Any]
) -> dict[str, bool]:
    _validate_research_run_provider_semantics(command, actual_binding)

    def rejected(candidate_command: dict[str, Any], candidate_binding: Any) -> bool:
        try:
            _validate_research_run_provider_semantics(candidate_command, candidate_binding)
        except ValueError:
            return True
        return False

    missing_flags = all(
        rejected(
            {**command, "required_flags": [flag for flag in RESEARCH_RUN_REQUIRED_FLAGS if flag != missing]},
            actual_binding,
        )
        for missing in RESEARCH_RUN_REQUIRED_FLAGS
    )
    unknown_flag = rejected(
        {**command, "required_flags": [*RESEARCH_RUN_REQUIRED_FLAGS, "--unknown"]}, actual_binding
    )
    missing_identities = all(
        rejected(command, {key: value for key, value in actual_binding.items() if key != missing})
        for missing in RESEARCH_RUN_BINDING_REQUIREMENTS
    )
    tampered_identities = all(
        rejected(command, {**actual_binding, identity: f"{expected}.tampered"})
        for identity, expected in RESEARCH_RUN_BINDING_REQUIREMENTS.items()
        if isinstance(expected, str)
    )
    invalid_seed_domains = all(
        rejected(command, {**actual_binding, "seed_domain": candidate})
        for candidate in ("", "power-confirmation", "formal-qualification", "latest")
    )
    invalid_counts = all(
        rejected(command, {**actual_binding, "sequences_per_cell": candidate})
        for candidate in (0, 1, 1999, 2001, "2000")
    )
    implicit_or_network_identity = all(
        rejected(command, {**actual_binding, identity: candidate})
        for identity in ("preregistration_path", "feasibility_path")
        for candidate in ("", "latest", "https://example.invalid/evidence.json")
    )
    return {
        "exact_frozen_flags_and_binding_positive": True,
        "each_missing_required_flag_rejected": missing_flags,
        "unknown_flag_rejected": unknown_flag,
        "each_missing_binding_identity_rejected": missing_identities,
        "tampered_identity_path_or_hash_rejected": tampered_identities,
        "nondevelopment_seed_domain_rejected": invalid_seed_domains,
        "nonfrozen_sequences_per_cell_rejected": invalid_counts,
        "implicit_latest_default_or_network_identity_rejected": implicit_or_network_identity,
    }


def _validate_state_project_provider_semantics(command: dict[str, Any], installed_contract: dict[str, Any]) -> None:
    if set(command) != {"verb", "required_flags", "allowed_exit_codes"} or command.get("verb") != "state project":
        raise ValueError("state project command record shape mismatch")
    if command.get("required_flags") != STATE_PROJECT_REQUIRED_FLAGS:
        raise ValueError("state project required flags drift from frozen T09 command")
    if command.get("allowed_exit_codes") != [0, 4, 5, 6, 20, 30]:
        raise ValueError("state project allowed exits drift")
    if not isinstance(installed_contract, dict) or set(installed_contract) != set(STATE_PROJECT_INSTALLED_CONTRACT_REQUIREMENTS):
        raise ValueError("state project installed contract binding shape mismatch")
    for identity, expected in STATE_PROJECT_INSTALLED_CONTRACT_REQUIREMENTS.items():
        if installed_contract.get(identity) != expected:
            raise ValueError(f"state project installed contract identity mismatch: {identity}")


def _state_project_contract_mutation_tests(command: dict[str, Any], actual_contract_sha256: str) -> dict[str, bool]:
    installed_contract = {
        **STATE_PROJECT_INSTALLED_CONTRACT_REQUIREMENTS,
        "contract_sha256": actual_contract_sha256,
    }
    _validate_state_project_provider_semantics(command, installed_contract)

    def rejected(candidate_command: dict[str, Any], candidate_contract: Any) -> bool:
        try:
            _validate_state_project_provider_semantics(candidate_command, candidate_contract)
        except ValueError:
            return True
        return False

    missing_flags = all(
        rejected({**command, "required_flags": [flag for flag in STATE_PROJECT_REQUIRED_FLAGS if flag != missing]}, installed_contract)
        for missing in STATE_PROJECT_REQUIRED_FLAGS
    )
    unknown_flag = rejected({**command, "required_flags": [*STATE_PROJECT_REQUIRED_FLAGS, "--unknown"]}, installed_contract)
    missing_contract_identities = all(
        rejected(command, {key: value for key, value in installed_contract.items() if key != missing})
        for missing in STATE_PROJECT_INSTALLED_CONTRACT_REQUIREMENTS
    )
    tampered_contract_identities = all(
        rejected(command, {**installed_contract, identity: f"{expected}.tampered"})
        for identity, expected in STATE_PROJECT_INSTALLED_CONTRACT_REQUIREMENTS.items()
    )
    return {
        "exact_frozen_flags_positive": True,
        "each_missing_required_flag_rejected": missing_flags,
        "unknown_flag_rejected": unknown_flag,
        "CLI_contract_id_forbidden": unknown_flag,
        "each_missing_installed_contract_identity_rejected": missing_contract_identities,
        "tampered_installed_contract_path_SHA_or_identity_rejected": tampered_contract_identities,
        "implicit_contract_defaults_forbidden": missing_contract_identities,
    }


def _validate_e2e_provider_semantics(command: dict[str, Any], installed_registry: dict[str, Any]) -> None:
    if set(command) != {"verb", "required_flags", "allowed_exit_codes"} or command.get("verb") != "validate e2e":
        raise ValueError("validate e2e command record shape mismatch")
    if command.get("required_flags") != VALIDATE_E2E_REQUIRED_FLAGS:
        raise ValueError("validate e2e required flags drift from frozen T11 command")
    if command.get("allowed_exit_codes") != [0, 4, 5, 6, 20, 30]:
        raise ValueError("validate e2e allowed exits drift")
    if not isinstance(installed_registry, dict) or set(installed_registry) != set(VALIDATE_E2E_INSTALLED_REGISTRY_REQUIREMENTS):
        raise ValueError("validate e2e installed registry binding shape mismatch")
    for identity, expected in VALIDATE_E2E_INSTALLED_REGISTRY_REQUIREMENTS.items():
        if installed_registry.get(identity) != expected:
            raise ValueError(f"validate e2e installed registry identity mismatch: {identity}")


def _validate_e2e_contract_mutation_tests(command: dict[str, Any], actual_registry_sha256: str) -> dict[str, bool]:
    installed_registry = {
        **VALIDATE_E2E_INSTALLED_REGISTRY_REQUIREMENTS,
        "registry_sha256": actual_registry_sha256,
    }
    _validate_e2e_provider_semantics(command, installed_registry)

    def rejected(candidate_command: dict[str, Any], candidate_registry: Any) -> bool:
        try:
            _validate_e2e_provider_semantics(candidate_command, candidate_registry)
        except ValueError:
            return True
        return False

    missing_flags = all(
        rejected({**command, "required_flags": [flag for flag in VALIDATE_E2E_REQUIRED_FLAGS if flag != missing]}, installed_registry)
        for missing in VALIDATE_E2E_REQUIRED_FLAGS
    )
    unknown_flag = rejected({**command, "required_flags": [*VALIDATE_E2E_REQUIRED_FLAGS, "--unknown"]}, installed_registry)
    missing_registry_identities = all(
        rejected(command, {key: value for key, value in installed_registry.items() if key != missing})
        for missing in VALIDATE_E2E_INSTALLED_REGISTRY_REQUIREMENTS
    )
    tampered_registry_identities = all(
        rejected(command, {**installed_registry, identity: f"{expected}.tampered"})
        for identity, expected in VALIDATE_E2E_INSTALLED_REGISTRY_REQUIREMENTS.items()
    )
    implicit_or_network_registry = all(
        rejected(command, {**installed_registry, "registry_path": invalid})
        for invalid in ("latest", "", "https://example.invalid/e2e-registry.json")
    )
    return {
        "exact_frozen_flags_positive": True,
        "each_missing_required_flag_rejected": missing_flags,
        "explicit_output_required": missing_flags,
        "unknown_flag_rejected": unknown_flag,
        "each_missing_installed_registry_identity_rejected": missing_registry_identities,
        "tampered_installed_registry_path_SHA_or_identity_rejected": tampered_registry_identities,
        "implicit_latest_default_or_network_registry_forbidden": implicit_or_network_registry,
    }


def _validate_power_controller_binding(
    command: Any,
    command_sha256: str,
    registry: Any,
    registry_sha256: str,
    source_sha256: str,
    benchmark_receipt: Any,
    benchmark_receipt_sha256: str,
) -> None:
    if command != POWER_CONTROLLER_COMMAND or command_sha256 != POWER_CONTROLLER_COMMAND_SHA256:
        raise ValueError("power controller command bytes or closed content drift")
    if not isinstance(registry, dict) or registry_sha256 != POWER_CONTROLLER_BENCHMARK_BINDING["benchmark_fixture_registry_sha256"]:
        raise ValueError("power controller benchmark registry hash drift")
    if registry.get("benchmark_fixture_id") != POWER_CONTROLLER_BENCHMARK_BINDING["benchmark_fixture_id"]:
        raise ValueError("power controller benchmark fixture identity drift")
    if registry.get("non_scientific") is not True or registry.get("qualification_seed_domain") is not None:
        raise ValueError("power controller benchmark isolation weakened")
    if registry.get("forbidden_seed_domains") != ["development", "power-confirmation", "formal-qualification"]:
        raise ValueError("power controller benchmark seed-domain separation drift")
    controller_fixture = registry.get("controller_fixture")
    if controller_fixture != {
        "path": "tests/phase4/fixtures/benchmark/controller-command.json",
        "sha256": POWER_CONTROLLER_COMMAND_SHA256,
    }:
        raise ValueError("power controller registry command binding drift")
    controller_source = registry.get("controller_source")
    if controller_source != {
        "path": "tests/phase4/fixtures/benchmark/controller_worker.py",
        "sha256": POWER_CONTROLLER_BENCHMARK_BINDING["controller_source_sha256"],
    } or source_sha256 != POWER_CONTROLLER_BENCHMARK_BINDING["controller_source_sha256"]:
        raise ValueError("power controller registry source binding drift")
    if benchmark_receipt_sha256 != POWER_CONTROLLER_BENCHMARK_BINDING["authoritative_benchmark_receipt_sha256"]:
        raise ValueError("authoritative T11 benchmark receipt hash drift")
    if not isinstance(benchmark_receipt, dict) or {
        key: benchmark_receipt.get(key)
        for key in ("benchmark_fixture_id", "benchmark_fixture_registry_sha256", "controller_command_sha256")
    } != {
        key: POWER_CONTROLLER_BENCHMARK_BINDING[key]
        for key in ("benchmark_fixture_id", "benchmark_fixture_registry_sha256", "controller_command_sha256")
    }:
        raise ValueError("authoritative T11 benchmark receipt binding drift")
    if benchmark_receipt.get("status") != "PASS" or benchmark_receipt.get("terminal") != "PREQUALIFICATION_BENCHMARK_PASS":
        raise ValueError("authoritative T11 benchmark receipt did not PASS")


def _power_controller_command_mutation_tests(root: Path, config_root: Path) -> dict[str, bool]:
    command_path = config_root / "power-controller-command.json"
    registry_path = root / "tests/phase4/fixtures/benchmark/registry.json"
    source_path = root / "tests/phase4/fixtures/benchmark/controller_worker.py"
    receipt_path = root / "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T11/benchmark-qualification-I02/receipt.json"
    command, registry, receipt = _load(command_path), _load(registry_path), _load(receipt_path)
    arguments = (_sha256(command_path), registry, _sha256(registry_path), _sha256(source_path), receipt, _sha256(receipt_path))
    _validate_power_controller_binding(command, *arguments)

    def rejected(candidate_command: Any, command_sha256: str, candidate_registry: Any, registry_sha256: str, source_sha256: str, candidate_receipt: Any, receipt_sha256: str) -> bool:
        try:
            _validate_power_controller_binding(candidate_command, command_sha256, candidate_registry, registry_sha256, source_sha256, candidate_receipt, receipt_sha256)
        except ValueError:
            return True
        return False

    missing_fields = all(
        rejected({key: value for key, value in command.items() if key != missing}, *arguments)
        for missing in POWER_CONTROLLER_COMMAND
    )
    unknown_field = rejected({**command, "unknown": True}, *arguments)
    command_semantics = all(
        rejected(candidate, *arguments)
        for candidate in (
            {**command, "argv": ["python3", "other.py"]},
            {**command, "protocol": "other"},
            {**command, "non_scientific": False},
            {**command, "qualification_seed_domain": "development"},
        )
    )
    command_hash = rejected(command, "0" * 64, *arguments[1:])
    registry_binding = all(
        rejected(command, arguments[0], candidate, arguments[2], arguments[3], receipt, arguments[5])
        for candidate in (
            {**registry, "benchmark_fixture_id": "stale"},
            {**registry, "controller_fixture": {**registry["controller_fixture"], "sha256": "0" * 64}},
            {**registry, "controller_source": {**registry["controller_source"], "sha256": "0" * 64}},
        )
    ) and rejected(command, arguments[0], registry, "0" * 64, arguments[3], receipt, arguments[5])
    source_hash = rejected(command, arguments[0], registry, arguments[2], "0" * 64, receipt, arguments[5])
    receipt_binding = all(
        rejected(command, arguments[0], registry, arguments[2], arguments[3], candidate, arguments[5])
        for candidate in (
            {**receipt, "benchmark_fixture_id": "stale"},
            {**receipt, "status": "HOLD"},
        )
    ) and rejected(command, arguments[0], registry, arguments[2], arguments[3], receipt, "0" * 64)
    stale_receipt_path = root / "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T11/benchmark-qualification-I01/receipt.json"
    stale_receipt = _load(stale_receipt_path)
    stale_I01 = rejected(command, arguments[0], registry, arguments[2], arguments[3], stale_receipt, _sha256(stale_receipt_path))
    return {
        "exact_I02_bound_command_positive": True,
        "missing_command_fields_rejected": missing_fields,
        "unknown_command_field_rejected": unknown_field,
        "command_semantic_tamper_rejected": command_semantics,
        "command_byte_hash_tamper_rejected": command_hash,
        "registry_identity_or_hash_tamper_rejected": registry_binding,
        "controller_source_hash_tamper_rejected": source_hash,
        "I02_receipt_tamper_and_stale_I01_rejected": receipt_binding and stale_I01,
    }


def _validate_contract_semantics(config_root: Path) -> dict[str, Any]:
    root = config_root.resolve().parents[1]
    cli = _load(config_root / "cli-contract.json")
    verbs = [row["verb"] for row in cli["commands"]]
    if set(verbs) != EXPECTED_COMMANDS or len(verbs) != len(EXPECTED_COMMANDS):
        raise ValueError("CLI command registry has missing, extra, or duplicate verbs")
    if any(not row["required_flags"] or row["allowed_exit_codes"] != [0, 4, 5, 6, 20, 30] for row in cli["commands"]):
        raise ValueError("CLI per-command flags/exits incomplete")
    score_correct_rows = [row for row in cli["commands"] if row["verb"] == "score correct"]
    if len(score_correct_rows) != 1:
        raise ValueError("score correct CLI contract must be unique")
    score_correct_mutations = _score_correct_contract_mutation_tests(score_correct_rows[0])
    if not all(score_correct_mutations.values()):
        raise ValueError("score correct CLI/fixture semantic mutation tests failed")
    research_decide_rows = [row for row in cli["commands"] if row["verb"] == "research decide"]
    if len(research_decide_rows) != 1:
        raise ValueError("research decide CLI contract must be unique")
    research_decide_mutations = _research_decide_contract_mutation_tests(research_decide_rows[0])
    if not all(research_decide_mutations.values()):
        raise ValueError("research decide CLI/fixture semantic mutation tests failed")
    research_run_rows = [row for row in cli["commands"] if row["verb"] == "research run"]
    if len(research_run_rows) != 1:
        raise ValueError("research run CLI contract must be unique")
    research_run_binding = {
        **RESEARCH_RUN_BINDING_REQUIREMENTS,
        "preregistration_sha256": _sha256(root / RESEARCH_RUN_BINDING_REQUIREMENTS["preregistration_path"]),
        "feasibility_sha256": _sha256(root / RESEARCH_RUN_BINDING_REQUIREMENTS["feasibility_path"]),
        "feasibility_acceptance_sha256": _sha256(
            root / RESEARCH_RUN_BINDING_REQUIREMENTS["feasibility_acceptance_path"]
        ),
    }
    research_run_mutations = _research_run_contract_mutation_tests(research_run_rows[0], research_run_binding)
    if not all(research_run_mutations.values()):
        raise ValueError("research run CLI/execution-binding semantic mutation tests failed")
    state_project_rows = [row for row in cli["commands"] if row["verb"] == "state project"]
    if len(state_project_rows) != 1:
        raise ValueError("state project CLI contract must be unique")
    state_project_mutations = _state_project_contract_mutation_tests(
        state_project_rows[0], _sha256(config_root / "state-contract.json")
    )
    if not all(state_project_mutations.values()):
        raise ValueError("state project CLI/installed-contract semantic mutation tests failed")
    validate_e2e_rows = [row for row in cli["commands"] if row["verb"] == "validate e2e"]
    if len(validate_e2e_rows) != 1:
        raise ValueError("validate e2e CLI contract must be unique")
    validate_e2e_mutations = _validate_e2e_contract_mutation_tests(
        validate_e2e_rows[0], _sha256(config_root / "e2e-registry.json")
    )
    if not all(validate_e2e_mutations.values()):
        raise ValueError("validate e2e CLI/installed-registry semantic mutation tests failed")
    power_controller_mutations = _power_controller_command_mutation_tests(root, config_root)
    if not all(power_controller_mutations.values()):
        raise ValueError("power controller command/I02 benchmark binding mutation tests failed")
    if cli["exit_codes"] != {"0": "PASS_OR_READY", "4": "IDENTITY_REUSE", "5": "CONTRACT_OR_EVIDENCE_MISMATCH", "6": "SECURITY_OR_CAUSALITY_FAILURE", "20": "HOLD", "30": "RETRYABLE_TERMINAL_RECORDED"}:
        raise ValueError("CLI exit code contract drift")

    probability = _load(config_root / "probability-ranking-contract.json")
    if probability["games"]["ssq"]["space_size"] != 17721088 or probability["games"]["dlt"]["space_size"] != 21425712:
        raise ValueError("full lottery space cardinality drift")
    if probability["top_k"] != [10, 100, 200, 1000] or probability["forecast_size"] != 1000:
        raise ValueError("Top-K/forecast count weakened")
    if probability["serialization"]["trailing_newline"] is not False or probability["serialization"]["non_finite_numbers"] is not False:
        raise ValueError("P4-CJSON-1 serialization weakened")
    if probability["normalization_tolerance"] != {"absolute": "1e-45", "relative": "1e-40"}:
        raise ValueError("probability tolerance drift")

    prereg = _load(config_root / "qualification-preregistration.json")
    expected = {
        "cycles_per_sequence": 150, "effect_ticks": [1536, 1792, 2048],
        "development_sequences_per_cell_design": 2000, "power_sequences_per_cell": 20000,
        "formal_sequences_per_cell": 1000, "uniform_max_false_proposals": 50,
        "positive_min_recoveries": 900,
    }
    for key, value in expected.items():
        if prereg[key] != value:
            raise ValueError(f"qualification constant drift: {key}")
    if prereg["formal_run_authorized"] is not False or len(prereg["seed_domains"]) != len(set(prereg["seed_domains"])):
        raise ValueError("T01 must remain results-blind with disjoint seed domains")
    if prereg["probability_family"] != {"name": "P4E1", "scale": 1024, "normalized_tick_bounds": [-4096, 4096], "decimal_precision": 80}:
        raise ValueError("qualification P4E1 contract incomplete")
    if prereg["sequential_test"]["family_initial_wealth"] != "0.006" or "product_" not in prereg["sequential_test"]["definition"]:
        raise ValueError("qualification LR/wealth contract incomplete")
    if set(prereg["aggregate_binomial_algorithms"]) != {"uniform", "positive"} or "term_(j+1)" not in prereg["aggregate_binomial_algorithms"]["uniform"]["recurrence"]:
        raise ValueError("G0/G+ recurrence not frozen")
    if any(value != 0 for value in prereg["seed_set_contract"]["pairwise_intersections_required"].values()) or prereg["seed_set_contract"]["all_set_hashes_required"] is not True:
        raise ValueError("seed set hash/intersection contract incomplete")

    alpha = _load(config_root / "alpha-contract.json")
    if (alpha["initial_wealth_per_game_family"], alpha["spending_formula"], alpha["reward"], alpha["maximum_look"]) != ("0.006", "W0/(t*(t+1))", "0", 150):
        raise ValueError("alpha/e-process contract drift")

    metric = _load(config_root / "metric-contract.json")
    if metric["minimum_observations"] != 30 or metric["reliability_bins"]["count"] != 10 or metric["numeric_tolerance"] != {"absolute": "1e-40", "relative": "1e-35"}:
        raise ValueError("metric/window contract drift")

    state = _load(config_root / "state-contract.json")
    if state["global_improved_field_allowed"] is not False or state["champion_by_game"] != {"ssq": "M0", "dlt": "M0"}:
        raise ValueError("scientific state contract weakened")
    if state["top_k"]["phase4_values"] != ["insufficient_observation"]:
        raise ValueError("Phase 4 real Top-K state weakened")

    assertions = _load(config_root / "acceptance-assertions.json")["assertions"]
    expected_a = [f"P4-MVP-A{i:02d}" for i in range(1, 22)]
    if [row["acceptance_id"] for row in assertions] != expected_a or any(not row["bottom_assertions"] for row in assertions):
        raise ValueError("A01-A21 bottom assertion coverage is incomplete")
    classes = _load(config_root / "delivery-coverage.json")["classes"]
    if [row["class_id"] for row in classes] != list(range(1, 7)):
        raise ValueError("six-class delivery coverage is incomplete")

    inequalities = _load(config_root / "actor-inequality-contract.json")["rules"]
    required_fragments = ["task_acceptance_actor", "independent_power_operator_not_in_product", "independent_power_operator_not_statistical", "T16_T17_run_operator", "independent_reviewer", "acceptance_engineer", "machine_delivery_statement", "acceptance_approver"]
    if any(not any(fragment in rule for rule in inequalities) for fragment in required_fragments):
        raise ValueError("actor inequality derivation is incomplete")
    cumulative = _load(config_root / "provenance-contract.json")["cumulative_actual_write_provenance"]
    if cumulative != {
        "source_links": "every recursively explicit receipt.inputs entry whose verified JSON payload is a receipt, manifest, closure, independent validation, preservation map, or standalone evidence container with actor provenance",
        "receipt_traversal": "recursive_explicit_links_only",
        "manifest_traversal": "explicit_files_with_producer_provenance",
        "standalone_evidence_traversal": "top-level provenance, producer_provenance, named *_provenance, and registered direct reviewer/signer identity fields",
        "unlinked_repository_discovery_allowed": False,
        "historical_output_live_path_rehash_required": False,
        "historical_container_hash_required": True,
        "classification_precedence": ["explicit provenance role", "explicit evidence_class or artifact_type", "registered product path prefix"],
        "conservative_unclassified_rule": "every historical actor enters all/evidence/historical_task and any actor without a typed category also enters unclassified; unclassified actors remain in T22/T24 prior universes",
        "historical_set_names": ["historical_task", "all", "evidence", "acknowledgement_only", "unclassified", "product", "controller", "operator", "oracle", "development", "power", "formal", "e2e_readiness", "manifest", "replay", "validator", "reviewer", "delivery_statement"],
        "derivation": "current task producer set derives only from current receipt outputs; historical sets retain the union of every actor fact from every recursively explicit prior container and are never reset to current producers",
        "normal_acceptance_rule": "acceptance actor is disjoint from current task producer set",
        "machine_delivery_statement_assignment_rule": "exactly one machine delivery statement row; actor_type codex_session; roles exactly [machine_delivery_statement]; T23 assigned; every actor is codex_session",
        "machine_delivery_statement_rule": "phase4_machine_delivery_statement enters delivery_statement production set and is not a human acknowledgement or T23 preapproval",
        "t23_rule": "current machine-only signer may produce only the current T23 machine delivery statement; signer must be disjoint from complete T00-T22 all/evidence/unclassified/product/controller/operator/oracle/development/power/formal/e2e_readiness/manifest/replay/validator/reviewer/delivery_statement sets",
        "t22_rule": "current T22 reviewer may produce T22 review output but must be disjoint from complete T00-T21 historical all set and controller/operator/validator roles",
        "t24_rule": "current T24 approver may directly produce T24 verdict and self-accept it but must be disjoint from complete T00-T23 historical all set and controller/operator/validator/reviewer/delivery_statement roles",
        "inequalities_use_cumulative_sets": True,
    }:
        raise ValueError("cumulative actual-write provenance contract drift")
    return {
        "cli_command_count": len(EXPECTED_COMMANDS), "cli_parameter_record_count": len(cli["commands"]), "acceptance_assertion_count": len(assertions),
        "delivery_class_count": len(classes), "actor_inequality_rule_count": len(inequalities),
        "formal_run_authorized": prereg["formal_run_authorized"],
        "score_correct_contract_mutations": score_correct_mutations,
        "research_decide_contract_mutations": research_decide_mutations,
        "research_run_contract_mutations": research_run_mutations,
        "state_project_contract_mutations": state_project_mutations,
        "validate_e2e_contract_mutations": validate_e2e_mutations,
        "power_controller_command_mutations": power_controller_mutations,
    }


def _resolve_schema_ref(schema_root: Path, ref: str, current: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if ref.startswith("#/"):
        value: Any = current
        for part in ref[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        return value, current
    filename, _, fragment = ref.partition("#")
    referenced = _load(schema_root / filename)
    value: Any = referenced
    if fragment.startswith("/"):
        for part in fragment[1:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
    return value, referenced


def _pattern_string(pattern: str, index: int) -> str:
    if "[0-9a-f]{64}" in pattern: return f"{index + 1:064x}"[-64:]
    if "[0-9a-f]{40}" in pattern: return f"{index + 1:040x}"[-40:]
    if "P4Q1024" in pattern: return f"P4Q1024-{min(57344, 28672 + index):05d}"
    if "[0-9]{50}" in pattern: return "0." + "0" * 49 + "1"
    if "P4-R" in pattern: return "P4-R01-0123456789ab-20260811-I01"
    if "I[0-9]" in pattern: return "I01"
    if "T(?:" in pattern: return "T01"
    if "(?:\\.5)" in pattern: return "1"
    if "0\\." in pattern: return "0.003"
    return f"value-{index + 1}"


def _sample(schema_root: Path, schema: dict[str, Any], root_schema: dict[str, Any], index: int = 0) -> Any:
    if "$ref" in schema:
        target, target_root = _resolve_schema_ref(schema_root, schema["$ref"], root_schema)
        return _sample(schema_root, target, target_root, index)
    if "const" in schema: return copy.deepcopy(schema["const"])
    if "enum" in schema: return copy.deepcopy(schema["enum"][0])
    if "oneOf" in schema: return _sample(schema_root, schema["oneOf"][0], root_schema, index)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((value for value in kind if value != "null"), "null")
    if kind == "object":
        return {name: _sample(schema_root, schema.get("properties", {}).get(name, {}), root_schema, index) for name in schema.get("required", [])}
    if kind == "array":
        count = schema.get("minItems", 0)
        return [_sample(schema_root, schema.get("items", {}), root_schema, ordinal) for ordinal in range(count)]
    if kind == "integer": return min(schema.get("maximum", schema.get("minimum", 0) + index), schema.get("minimum", 0) + index)
    if kind == "number": return schema.get("minimum", 0)
    if kind == "boolean": return True
    if kind == "null": return None
    if kind == "string" or "pattern" in schema or "format" in schema:
        if schema.get("format") == "date-time": return "2026-08-11T05:00:00Z"
        if schema.get("format") == "date": return "2026-08-11"
        if schema.get("format") == "uri": return "https://example.invalid/read"
        if "pattern" in schema: return _pattern_string(schema["pattern"], index)
        return f"value-{index + 1}"
    return None


def _positive_negative_schema_tests(root: Path, assignment: dict[str, Any]) -> dict[str, Any]:
    schemas = root / "schemas/phase4"
    samples: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for schema_name in sorted(REQUIRED_SCHEMAS):
        schema = _load(schemas / schema_name)
        sample = assignment if schema_name == "actor-assignment.schema.json" else _sample(schemas, schema, schema)
        # Cross-field branches require one of the explicitly legal states.
        if schema_name == "decision.schema.json": sample.update({"experiment_count": 0, "experiment_ids": [], "zero_experiment_reason": "no_eligible_hypothesis", "terminal": "no_change"})
        if schema_name == "window-metric.schema.json": sample.update({"observation_count": 0, "score_ids": [], "aggregate_state": "insufficient_observation", "values": None})
        if schema_name == "work-item-receipt.schema.json":
            sample["inputs"] = [{"path": "input.json", "sha256": "1" * 64}]
            sample["outputs"] = [{"path": "output.json", "sha256": "2" * 64, "bytes": 1, "producer_actor_id": OWNER_ACTOR, "task_id": "T01", "session_id": OWNER_SESSION, "source_commit": AUTHORITY_COMMIT, "role": "contract_owner"}]
            sample["task_producer_set"] = [OWNER_ACTOR]
            sample["acceptance_actor_provenance"] = {"actor_id": ACCEPTANCE_ACTOR, "session_id": ACCEPTANCE_SESSION, "task_record_path": "record.json", "task_record_sha256": "3" * 64}
            sample["role_inequalities"] = {"acceptor_disjoint": True}
            sample["command"] = ["validate"]
        validator = _validator(schemas, schema_name)
        _assert_valid(validator, sample, schema_name)
        samples[schema_name] = sample
        unknown = copy.deepcopy(sample); unknown["unknown_field"] = True
        _assert_invalid(validator, unknown, f"{schema_name}:unknown-field")
        missing = copy.deepcopy(sample); missing.pop(schema["required"][0])
        _assert_invalid(validator, missing, f"{schema_name}:missing-dimension")
        results.append({"schema": schema_name, "positive": "PASS", "unknown_field_negative": "PASS", "missing_required_negative": "PASS", "offline_reference_store": "PASS"})

    targeted: dict[str, tuple[str, dict[str, Any]]] = {}
    ranking = copy.deepcopy(samples["ranking.schema.json"]); ranking["numbers"] = {"unknown": True}; targeted["ranking_numbers_shape"] = ("ranking.schema.json", ranking)
    revision = copy.deepcopy(samples["result-revision.schema.json"]); revision["numbers"] = {"unknown": True}; targeted["result_numbers_shape"] = ("result-revision.schema.json", revision)
    review = copy.deepcopy(samples["review.schema.json"]); review["independence_audit"] = {"unknown": True}; targeted["review_independence_shape"] = ("review.schema.json", review)
    candidate = copy.deepcopy(samples["candidate.schema.json"]); candidate["canonical_diff"] = [{"unknown": True}]; targeted["candidate_diff_closed"] = ("candidate.schema.json", candidate)
    candidate_value = copy.deepcopy(samples["candidate.schema.json"]); candidate_value["canonical_diff"] = [{"op": "replace", "path": "/P01/shrinkage", "value": {"nonsense": 1}}]; targeted["candidate_value_registered_scalar_or_list"] = ("candidate.schema.json", candidate_value)
    candidate_family = copy.deepcopy(samples["candidate.schema.json"]); candidate_family["hypothesis_family"] = "context_feature"; candidate_family["canonical_diff"] = [{"op": "replace", "path": "/P01/shrinkage", "value": 5}]; targeted["candidate_path_family_match"] = ("candidate.schema.json", candidate_family)
    acceptance = copy.deepcopy(samples["acceptance.schema.json"]); acceptance["a01_a21"] = {f"wrong-{i}": "PASS" for i in range(21)}; targeted["acceptance_exact_a01_a21"] = ("acceptance.schema.json", acceptance)
    review_keys = copy.deepcopy(samples["review.schema.json"]); review_keys["a01_a21_disposition"] = {f"wrong-{i}": "PASS" for i in range(21)}; targeted["review_exact_a01_a21"] = ("review.schema.json", review_keys)
    window = copy.deepcopy(samples["window-metric.schema.json"]); window.update({"aggregate_state": "available", "observation_count": 0, "score_ids": [], "values": None}); targeted["window_available_condition"] = ("window-metric.schema.json", window)
    window_nonsense = copy.deepcopy(samples["window-metric.schema.json"]); window_nonsense.update({"aggregate_state": "available", "observation_count": 30, "score_ids": [f"score-{i}" for i in range(30)], "values": {"nonsense": 1}}); targeted["window_available_values_metric_shape"] = ("window-metric.schema.json", window_nonsense)
    decision = copy.deepcopy(samples["decision.schema.json"]); decision.update({"experiment_count": 1, "experiment_ids": [], "zero_experiment_reason": "no_eligible_hypothesis"}); targeted["decision_count_condition"] = ("decision.schema.json", decision)
    forecast = copy.deepcopy(samples["forecast.schema.json"]); forecast.update({"game": "ssq", "rule_id": "dlt-ns-35c5-12c2-v1"}); forecast["tickets"][0]["numbers"] = {}; targeted["forecast_game_rule_numbers"] = ("forecast.schema.json", forecast)
    actor_machine_signer = copy.deepcopy(samples["actor-assignment.schema.json"])
    actor_machine_row = next(row for row in actor_machine_signer["assignments"] if row["actor_id"] == "machine-delivery-statement"); actor_machine_row["actor_type"] = "codex_session"
    targeted["machine_delivery_statement_human_rejected"] = ("actor-assignment.schema.json", actor_machine_signer)
    actor_mixed_signer = copy.deepcopy(samples["actor-assignment.schema.json"])
    actor_mixed_row = next(row for row in actor_mixed_signer["assignments"] if row["actor_id"] == "machine-delivery-statement"); actor_mixed_row["roles"].append("independent_reviewer")
    targeted["machine_delivery_statement_mixed_role_rejected"] = ("actor-assignment.schema.json", actor_mixed_signer)
    for label, (schema_name, payload) in targeted.items():
        _assert_invalid(_validator(schemas, schema_name), payload, label)

    candidate_registered = [
        ("static_parameter", "/P01/shrinkage", 5),
        ("static_parameter", "/P02/training_window", "expanding"),
        ("slow_drift_parameter", "/P03/recency_half_life", 52),
        ("slow_drift_parameter", "/P04/tick_group", [-64, 0]),
        ("context_feature", "/F01/enabled", True),
        ("context_feature", "/F02/config", ["weekday", "holiday"]),
    ]
    candidate_validator = _validator(schemas, "candidate.schema.json")
    for family, path, value in candidate_registered:
        candidate_positive = copy.deepcopy(samples["candidate.schema.json"])
        candidate_positive["hypothesis_family"] = family
        candidate_positive["canonical_diff"] = [{"op": "replace", "path": path, "value": value}]
        _assert_valid(candidate_validator, candidate_positive, f"candidate registered patch {path}")

    interval = {"lower": "0", "upper": "1"}
    window_values = {
        "mean_joint_log_score": "0", "mean_skill": "0", "mean_inclusion_brier": "0", "mean_rank_percentile": "0",
        "cumulative_hit_rate": {key: "0" for key in ("10", "100", "200", "1000")},
        "wilson_95": {key: dict(interval) for key in ("10", "100", "200", "1000")},
        "reliability": [{"bin_index": index, "lower": str(index / 10), "upper": str((index + 1) / 10), "count": 3, "mean_predicted_probability": "0", "observed_rate": "0"} for index in range(10)],
        "ece": "0", "stability": "0",
    }
    window_positive = copy.deepcopy(samples["window-metric.schema.json"])
    window_positive.update({"aggregate_state": "available", "observation_count": 30, "score_ids": [f"score-{i}" for i in range(30)], "values": window_values})
    _assert_valid(_validator(schemas, "window-metric.schema.json"), window_positive, "window available strict metric values")

    model_future = copy.deepcopy(samples["model-status.schema.json"]); model_future["status"] = "prospective_improvement_confirmed"
    _assert_invalid(_validator(schemas, "model-status.schema.json"), model_future, "future model state")
    topk_future = copy.deepcopy(samples["top-k-status.schema.json"]); topk_future["status"] = "confirmed_lift"
    _assert_invalid(_validator(schemas, "top-k-status.schema.json"), topk_future, "future top-k state")
    champion_bypass = copy.deepcopy(samples["champion-by-game.schema.json"]); champion_bypass["champion_by_game"]["ssq"] = "P4E1"
    _assert_invalid(_validator(schemas, "champion-by-game.schema.json"), champion_bypass, "champion bypass")

    checker_path = root / "scripts/phase4_independent/validate_work_item.py"
    spec = importlib.util.spec_from_file_location("phase4_independent_receipt_checker", checker_path)
    if spec is None or spec.loader is None: raise ValueError("cannot load independent receipt checker")
    checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    role_negatives = checker._negative_role_tests()
    if not all(role_negatives.values()): raise ValueError("mandatory provenance conflict negative cases did not all reject")
    terminal_negatives = checker._terminal_mutation_self_tests()
    if not all(terminal_negatives.values()): raise ValueError("mandatory receipt terminal mutation cases did not all reject")
    human_negatives = checker._machine_delivery_statement_mutation_self_tests()
    if not all(human_negatives.values()): raise ValueError("mandatory machine delivery actor mutation cases did not all pass")

    e2e = set(_load(root / "config/phase4/e2e-registry.json")["negative_cases"])
    required_semantic = {"time_class_mixed", "global_improved", "implicit_external_service", "lax_probability_contract", "direct_champion_change", "future_phase_state", "cross_game_state", "cross_game_alpha", "prohibited_scientific_wording"}
    # Three names are represented by their exact frozen registry equivalents.
    aliases = {"implicit_external_service": "source_use_mismatch", "lax_probability_contract": "invalid_probability_or_order_key", "future_phase_state": "cross_game_state"}
    missing_semantic = [name for name in required_semantic if name not in e2e and aliases.get(name) not in e2e]
    if missing_semantic: raise ValueError(f"semantic negative registry incomplete: {missing_semantic}")
    return {"tested_schema_count": len(results), "cases": results, "targeted_nested_cross_field_negatives": sorted(targeted), "candidate_registered_patch_positive_count": len(candidate_registered), "window_available_strict_positive": "PASS", "mandatory_role_conflict_negatives": role_negatives, "mandatory_machine_delivery_statement_mutations": human_negatives, "mandatory_receipt_terminal_mutation_negatives": terminal_negatives, "semantic_negative_cases": sorted(required_semantic | {"future_model_state", "future_top_k_state", "champion_bypass"}), "cross_file_reference_schemas_exercised": True, "offline_local_reference_store": True, "status": "PASS"}


def _file_row(root: Path, path: Path, role: str) -> dict[str, Any]:
    return {
        "path": _relative(root, path), "sha256": _sha256(path), "bytes": path.stat().st_size,
        "producer_actor_id": OWNER_ACTOR, "task_id": "T01", "session_id": OWNER_SESSION,
        "source_commit": AUTHORITY_COMMIT, "role": role,
    }


def main(argv: list[str] | None = None) -> int:
    started = _utc_now()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--schemas", required=True, type=Path)
    parser.add_argument("--authority-receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    config_root, schema_root = args.config.resolve(), args.schemas.resolve()
    output, actor_path, authority_receipt = args.output.resolve(), args.actor_assignments.resolve(), args.authority_receipt.resolve()
    if output.name != "T01-I06":
        raise ValueError("I06 validator requires immutable output identity T01-I06")
    if output.exists():
        raise FileExistsError(f"immutable T01 output already exists: {output}")
    assignment, independent = _validate_inputs(root, authority_receipt, actor_path)

    missing_configs = sorted(REQUIRED_CONFIGS - {path.name for path in config_root.glob("*.json")})
    missing_schemas = sorted(REQUIRED_SCHEMAS - {path.name for path in schema_root.glob("*.schema.json")})
    if missing_configs or missing_schemas:
        raise ValueError(f"contract bundle incomplete: configs={missing_configs}, schemas={missing_schemas}")
    schema_rows = []
    for path in sorted(schema_root.glob("*.schema.json")):
        schema = _load(path)
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False or not schema.get("required"):
            raise ValueError(f"schema root is not strict: {path.name}")
        schema_rows.append({"path": _relative(root, path), "sha256": _sha256(path), "bytes": path.stat().st_size})
    for path in sorted(config_root.glob("*.json")):
        _load(path)
    semantics = _validate_contract_semantics(config_root)
    tests = _positive_negative_schema_tests(root, assignment)

    output.mkdir(parents=True, exist_ok=False)
    contract_files = [config_root / name for name in sorted(REQUIRED_CONFIGS)]
    t01_schema_files = [schema_root / name for name in sorted(REQUIRED_SCHEMAS)]
    allowed_static = contract_files + t01_schema_files + [
        root / "docs/runbooks/phase-4-mvp-runtime.md", root / "requirements/phase4.in", root / "scripts/phase4/validate_contract_bundle.py",
        root / "scripts/phase4_independent/validate_work_item.py",
    ]
    inventory_rows = [_file_row(root, path, "statistical_owner" if path.name in {"alpha-contract.json", "qualification-preregistration.json"} else "contract_owner") for path in allowed_static]
    inventory = {
        "schema_version": "1.0.0", "artifact_type": "phase4_contract_bundle_inventory", "task_id": "T01",
        "authority_commit": AUTHORITY_COMMIT, "authority_receipt_sha256": _sha256(authority_receipt),
        "t00_independent_validation_sha256": EXPECTED_T00_INDEPENDENT_SHA256,
        "t01_i02_independent_hold_sha256": EXPECTED_T01_I02_INDEPENDENT_SHA256,
        "t01_i03_receipt_sha256": EXPECTED_T01_I03_RECEIPT_SHA256,
        "t01_i03_independent_hold_sha256": EXPECTED_T01_I03_INDEPENDENT_SHA256,
        "t01_i02_preservation_map_sha256": EXPECTED_T01_I02_PRESERVATION_SHA256,
        "t01_i03_preservation_map_sha256": EXPECTED_T01_I03_PRESERVATION_SHA256,
        "t01_i04_receipt_sha256": EXPECTED_T01_I04_RECEIPT_SHA256,
        "t01_i04_independent_hold_sha256": EXPECTED_T01_I04_INDEPENDENT_SHA256,
        "t01_i04_preservation_map_sha256": EXPECTED_T01_I04_PRESERVATION_SHA256,
        "t01_i05_receipt_sha256": EXPECTED_T01_I05_RECEIPT_SHA256,
        "t01_i05_independent_hold_sha256": EXPECTED_T01_I05_INDEPENDENT_SHA256,
        "t01_i05_preservation_map_sha256": EXPECTED_T01_I05_PRESERVATION_SHA256,
        "files": inventory_rows, "file_count": len(inventory_rows),
        "inventory_sha256": hashlib.sha256(_canonical_bytes(inventory_rows)).hexdigest(), "status": "PASS",
    }
    _write_new(output / "contract-inventory.json", inventory)
    _write_new(output / "schema-validation.json", {"schema_version": "1.0.0", "artifact_type": "phase4_schema_validation", "schemas": schema_rows, "schema_count": len(schema_rows), "status": "PASS"})
    _write_new(output / "positive-negative-validation.json", {"schema_version": "1.0.0", "artifact_type": "phase4_positive_negative_contract_validation", **tests})
    _write_new(output / "acceptance-traceability.json", {"schema_version": "1.0.0", "artifact_type": "phase4_acceptance_traceability", **semantics, "acceptance_ids": [f"P4-MVP-A{i:02d}" for i in range(1, 22)], "delivery_classes": list(range(1, 7)), "status": "PASS"})
    _write_new(output / "role-audit.json", {
        "schema_version": "1.0.0", "artifact_type": "phase4_t01_role_audit", "task_id": "T01",
        "task_producer_set": [OWNER_ACTOR], "acceptance_actor_id": ACCEPTANCE_ACTOR,
        "acceptance_actor_not_in_task_producer_set": ACCEPTANCE_ACTOR != OWNER_ACTOR,
        "combined_non_independent_roles": {OWNER_ACTOR: ["contract_owner", "statistical_owner"]},
        "machine_delivery_statement_assignment": {"actor_id": "machine-delivery-statement", "actor_type": "codex_session", "roles": ["machine_delivery_statement"], "t23_new_decision_required": True},
        "formal_actor_assignment_required_before": "T15", "role_label_substitution_allowed": False, "status": "PASS",
    })
    prior_receipt = output.parent / "T01" / "receipt.json"
    i02_receipt = output.parent / "T01-I02" / "receipt.json"
    i02_independent = output.parent / "T01-I02" / "independent-validation.json"
    i03_receipt = output.parent / "T01-I03" / "receipt.json"
    i03_independent = output.parent / "T01-I03" / "independent-validation.json"
    i02_preservation = output.parent / "T01-I02" / "preserved-shared-outputs" / "preservation-map.json"
    i03_preservation = output.parent / "T01-I03" / "preserved-shared-outputs" / "preservation-map.json"
    i04_receipt = output.parent / "T01-I04" / "receipt.json"
    i04_independent = output.parent / "T01-I04" / "independent-validation.json"
    i04_preservation = output.parent / "T01-I04" / "preserved-shared-outputs" / "preservation-map.json"
    i05_receipt = output.parent / "T01-I05" / "receipt.json"
    i05_independent = output.parent / "T01-I05" / "independent-validation.json"
    i05_preservation = output.parent / "T01-I05" / "preserved-shared-outputs" / "preservation-map.json"
    prior_paths = [prior_receipt, i02_receipt, i02_independent, i03_receipt, i03_independent, i02_preservation, i03_preservation, i04_receipt, i04_independent, i04_preservation, i05_receipt, i05_independent, i05_preservation]
    if not all(path.is_file() for path in prior_paths):
        raise ValueError("I06 prior attempt or preservation binding missing")
    expected_prior_hashes = {i02_independent: EXPECTED_T01_I02_INDEPENDENT_SHA256, i03_receipt: EXPECTED_T01_I03_RECEIPT_SHA256, i03_independent: EXPECTED_T01_I03_INDEPENDENT_SHA256, i02_preservation: EXPECTED_T01_I02_PRESERVATION_SHA256, i03_preservation: EXPECTED_T01_I03_PRESERVATION_SHA256, i04_receipt: EXPECTED_T01_I04_RECEIPT_SHA256, i04_independent: EXPECTED_T01_I04_INDEPENDENT_SHA256, i04_preservation: EXPECTED_T01_I04_PRESERVATION_SHA256, i05_receipt: EXPECTED_T01_I05_RECEIPT_SHA256, i05_independent: EXPECTED_T01_I05_INDEPENDENT_SHA256, i05_preservation: EXPECTED_T01_I05_PRESERVATION_SHA256}
    for path, expected_hash in expected_prior_hashes.items():
        if _sha256(path) != expected_hash:
            raise ValueError(f"I06 prior attempt identity mismatch: {_relative(root, path)}")
    _write_new(output / "attempt-disposition.json", {
        "schema_version": "1.0.0", "artifact_type": "phase4_attempt_disposition", "task_id": "T01",
        "attempts": [
            {"path": _relative(root, prior_receipt.parent), "receipt_sha256": _sha256(prior_receipt), "disposition": "REJECTED_PROVENANCE_OVERCLAIM"},
            {"path": _relative(root, i02_receipt.parent), "receipt_sha256": _sha256(i02_receipt), "independent_validation_sha256": EXPECTED_T01_I02_INDEPENDENT_SHA256, "preservation_map_sha256": EXPECTED_T01_I02_PRESERVATION_SHA256, "disposition": "HOLD_MACHINE_CONTRACT"},
            {"path": _relative(root, i03_receipt.parent), "receipt_sha256": EXPECTED_T01_I03_RECEIPT_SHA256, "independent_validation_sha256": EXPECTED_T01_I03_INDEPENDENT_SHA256, "preservation_map_sha256": EXPECTED_T01_I03_PRESERVATION_SHA256, "disposition": "HOLD_MACHINE_CONTRACT"},
            {"path": _relative(root, i04_receipt.parent), "receipt_sha256": EXPECTED_T01_I04_RECEIPT_SHA256, "independent_validation_sha256": EXPECTED_T01_I04_INDEPENDENT_SHA256, "preservation_map_sha256": EXPECTED_T01_I04_PRESERVATION_SHA256, "disposition": "HOLD_MACHINE_CONTRACT"},
            {"path": _relative(root, i05_receipt.parent), "receipt_sha256": EXPECTED_T01_I05_RECEIPT_SHA256, "independent_validation_sha256": EXPECTED_T01_I05_INDEPENDENT_SHA256, "preservation_map_sha256": EXPECTED_T01_I05_PRESERVATION_SHA256, "disposition": "HOLD_MACHINE_CONTRACT"}
        ],
        "scope_resolution": "Only the two exact command-provider paths scripts/phase4/validate_contract_bundle.py and scripts/phase4_independent/validate_work_item.py are implicit executable T01 deliverables; authority bytes are unchanged.",
        "i06_finding_scope": ["F05_human_only_delivery_statement_assignment_and_T23_prior_signed_universe"],
        "replacement_attempt_path": _relative(root, output), "status": "PASS",
    })
    generated = sorted(path for path in output.iterdir() if path.is_file())
    outputs = inventory_rows + [_file_row(root, path, "contract_owner") for path in generated]
    acceptor = _assignment_actor(assignment, ACCEPTANCE_ACTOR)
    receipt = {
        "schema_version": "1.0.0", "artifact_type": "phase4_work_item_receipt", "task_id": "T01",
        "identity": output.parents[1].name, "source_commit": AUTHORITY_COMMIT,
        "actor_assignment_sha256": _sha256(actor_path), "task_producer_set": [OWNER_ACTOR],
        "acceptance_actor_provenance": {"actor_id": ACCEPTANCE_ACTOR, "session_id": ACCEPTANCE_SESSION, "task_record_path": acceptor["task_record_path"], "task_record_sha256": acceptor["task_record_sha256"]},
        "role_inequalities": {"t01_acceptor_not_t01_producer": True, "acceptance_not_product": True, "power_not_product": True, "power_not_statistical": True, "power_not_oracle": True, "formal_actor_assignment_deferred_to_T15": True},
        "inputs": [
            {"path": _relative(root, authority_receipt), "sha256": _sha256(authority_receipt)},
            {"path": _relative(root, authority_receipt.parent / "independent-validation-I02.json"), "sha256": EXPECTED_T00_INDEPENDENT_SHA256},
            {"path": _relative(root, actor_path), "sha256": _sha256(actor_path)},
        ] + ([
            {"path": _relative(root, prior_receipt), "sha256": _sha256(prior_receipt)},
            {"path": _relative(root, i02_receipt), "sha256": _sha256(i02_receipt)},
            {"path": _relative(root, i02_independent), "sha256": EXPECTED_T01_I02_INDEPENDENT_SHA256},
            {"path": _relative(root, i03_receipt), "sha256": EXPECTED_T01_I03_RECEIPT_SHA256},
            {"path": _relative(root, i03_independent), "sha256": EXPECTED_T01_I03_INDEPENDENT_SHA256},
            {"path": _relative(root, i02_preservation), "sha256": EXPECTED_T01_I02_PRESERVATION_SHA256},
            {"path": _relative(root, i03_preservation), "sha256": EXPECTED_T01_I03_PRESERVATION_SHA256},
            {"path": _relative(root, i04_receipt), "sha256": EXPECTED_T01_I04_RECEIPT_SHA256},
            {"path": _relative(root, i04_independent), "sha256": EXPECTED_T01_I04_INDEPENDENT_SHA256},
            {"path": _relative(root, i04_preservation), "sha256": EXPECTED_T01_I04_PRESERVATION_SHA256},
            {"path": _relative(root, i05_receipt), "sha256": EXPECTED_T01_I05_RECEIPT_SHA256},
            {"path": _relative(root, i05_independent), "sha256": EXPECTED_T01_I05_INDEPENDENT_SHA256},
            {"path": _relative(root, i05_preservation), "sha256": EXPECTED_T01_I05_PRESERVATION_SHA256},
        ]),
        "outputs": outputs, "command": [sys.executable, *sys.argv], "started_at_utc": started, "ended_at_utc": _utc_now(),
        "process_exit_code": 0, "status": "PASS", "terminal": "T01_RESULT_BLIND_MACHINE_CONTRACT_FROZEN",
    }
    _assert_valid(_validator(schema_root, "work-item-receipt.schema.json"), receipt, "T01 receipt")
    _write_new(output / "receipt.json", receipt)
    sys.stdout.buffer.write(_canonical_bytes({"status": "PASS", "terminal": receipt["terminal"], "receipt": _relative(root, output / "receipt.json"), "schema_count": len(schema_rows), "contract_file_count": len(contract_files)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileExistsError as exc:
        sys.stdout.buffer.write(_canonical_bytes({"status": "FAIL", "terminal": "IDENTITY_REUSE", "error": str(exc)}))
        raise SystemExit(4)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        sys.stdout.buffer.write(_canonical_bytes({"status": "HOLD", "terminal": "HOLD_MACHINE_CONTRACT", "error": str(exc)}))
        raise SystemExit(20)
