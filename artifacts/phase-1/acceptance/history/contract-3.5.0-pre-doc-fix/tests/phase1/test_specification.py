from __future__ import annotations

import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # fail closed: G1 cannot silently lose schema validation
    raise RuntimeError("G1 requires jsonschema[format]==4.26.0") from exc


REPO = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO / "schemas" / "phase1"
FIXTURE_ROOT = REPO / "tests" / "phase1" / "fixtures" / "spec"
CONTRACT_PATH = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
PHASE0_ROOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
SCHEMA_FILES = (
    "source-observation.schema.json",
    "draw-record.schema.json",
    "dataset-release.schema.json",
    "run-manifest.schema.json",
    "run-event.schema.json",
    "run-result.schema.json",
)
FREEZE_PATH = FIXTURE_ROOT / "spec-bundle-freeze.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def core_fact_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "game": value["game"],
        "issue_id": value["issue_id"],
        "draw_date": value.get("draw_date_local", value.get("draw_date")),
        "front_numbers": value["front_numbers"],
        "back_numbers": value["back_numbers"],
    }


def deterministic_id_projection(kind: str, value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if kind == "observation":
        return "obs-v1:", {
            "source_id": value["source_id"], "game": value["game"], "issue_id": value["issue_id"],
            "raw_sha256": value["raw_sha256"], "parser_version": value["parser_version"],
        }
    if kind == "revision":
        return "rev-v1:", {
            "game": value["game"], "issue_id": value["issue_id"],
            "core_fact_sha256": value["core_fact_sha256"],
            "supersedes_revision_id": value["supersedes_revision_id"],
        }
    if kind == "event":
        return "evt-v1:", {
            "run_id": value["run_id"], "sequence": value["sequence"], "event_type": value["event_type"],
            "request_id": value["request_id"], "attempt": value["attempt"],
        }
    raise KeyError(f"unknown deterministic ID kind: {kind}")


def semantic_errors(schema_name: str, value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema_name in {"source-observation.schema.json", "draw-record.schema.json"}:
        game = value.get("game")
        expected = {"ssq": (6, 1, 33, 16), "dlt": (5, 2, 35, 12)}.get(game)
        if expected is None:
            errors.append("unknown game")
        else:
            fc, bc, fm, bm = expected
            front, back = value.get("front_numbers", []), value.get("back_numbers", [])
            if len(front) != fc or front != sorted(set(front)) or any(not isinstance(n, int) or not 1 <= n <= fm for n in front):
                errors.append("invalid front numbers")
            if len(back) != bc or back != sorted(set(back)) or any(not isinstance(n, int) or not 1 <= n <= bm for n in back):
                errors.append("invalid back numbers")
        if value.get("core_fact_sha256") != canonical_sha256(core_fact_projection(value)):
            errors.append("core fact hash mismatch")
    if schema_name == "source-observation.schema.json":
        identity = {
            "game": value.get("game"),
            "issue_id": value.get("issue_id"),
            "parser_version": value.get("parser_version"),
            "raw_sha256": value.get("raw_sha256"),
            "source_id": value.get("source_id"),
        }
        if value.get("observation_id") != "obs-v1:" + canonical_sha256(identity):
            errors.append("observation id mismatch")
    if schema_name == "draw-record.schema.json":
        links = value.get("evidence_links", [])
        if len(links) != 2 or len({link.get("publisher_id") for link in links}) != 2:
            errors.append("draw requires exactly two distinct publishers")
        prefix, identity = deterministic_id_projection("revision", value)
        if value.get("revision_id") != prefix + canonical_sha256(identity):
            errors.append("revision id mismatch")
    if schema_name == "run-event.schema.json":
        prefix, identity = deterministic_id_projection("event", value)
        if value.get("event_id") != prefix + canonical_sha256(identity):
            errors.append("event id mismatch")
    return errors


def frozen_spec_bundle() -> tuple[list[dict[str, str]], str]:
    freeze = load_json(FREEZE_PATH)
    paths = [entry["path"] for entry in freeze["files"]]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("freeze paths must be unique and sorted")
    if FREEZE_PATH.relative_to(REPO).as_posix() in paths:
        raise ValueError("freeze manifest must not include itself")
    actual = []
    for relative in paths:
        path = REPO / relative
        if not path.is_file() or path.resolve() == FREEZE_PATH.resolve():
            raise ValueError(f"invalid frozen path: {relative}")
        actual.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return actual, canonical_sha256(actual)


def validate_request_events(events: list[dict[str, Any]], planned: set[str]) -> list[str]:
    errors: list[str] = []
    sequences = [event.get("sequence") for event in events]
    if any(not isinstance(sequence, int) or isinstance(sequence, bool) for sequence in sequences):
        errors.append("sequence must be integer")
    elif sequences != list(range(1, len(events) + 1)):
        errors.append("sequence must be unique, contiguous, and in file order")
    states = {request_id: "not_started" for request_id in planned}
    for event in events:
        event_type = event.get("event_type")
        request_id = event.get("request_id")
        if event_type not in {"request_started", "request_succeeded", "request_failed"}:
            errors.append(f"unsupported request event: {event_type}")
            continue
        if request_id not in planned:
            errors.append(f"unknown request id: {request_id}")
            continue
        state = states[request_id]
        if event_type == "request_started":
            if state == "not_started":
                states[request_id] = "started"
            elif state == "started":
                errors.append(f"{request_id}: duplicate start")
            else:
                errors.append(f"{request_id}: event after terminal")
        elif state == "not_started":
            errors.append(f"{request_id}: terminal before start")
            states[request_id] = "terminal"
        elif state == "started":
            states[request_id] = "terminal"
        else:
            errors.append(f"{request_id}: terminal after terminal")
    for request_id, state in states.items():
        if state != "terminal":
            errors.append(f"{request_id}: expected exactly one terminal after start")
    return errors


def fixture_coverage_errors(expectations: dict[str, Any]) -> list[str]:
    requirements = expectations.get("coverage_requirements", {})
    fixtures = expectations.get("fixtures", {})
    errors: list[str] = []
    required_schemas = {value.get("schema") for value in requirements.values()}
    if required_schemas != set(SCHEMA_FILES):
        errors.append(f"coverage schemas differ: {sorted(required_schemas ^ set(SCHEMA_FILES))}")
    actual_paths = {
        path.relative_to(FIXTURE_ROOT).as_posix()
        for directory in (FIXTURE_ROOT / "valid", FIXTURE_ROOT / "invalid")
        for path in directory.glob("*.json")
    }
    if set(fixtures) != actual_paths:
        errors.append(f"fixture inventory differs: {sorted(set(fixtures) ^ actual_paths)}")
    counts: Counter[tuple[str, bool]] = Counter()
    for relative, metadata in fixtures.items():
        object_type = metadata.get("object_type")
        requirement = requirements.get(object_type)
        if requirement is None or metadata.get("schema") != requirement.get("schema"):
            errors.append(f"{relative}: object/schema coverage mapping invalid")
        valid = metadata.get("valid")
        schema_valid = metadata.get("schema_valid")
        semantic_valid = metadata.get("semantic_valid")
        if not isinstance(valid, bool) or not isinstance(schema_valid, bool) or semantic_valid not in {True, False, None}:
            errors.append(f"{relative}: validation expectations are incomplete")
            continue
        if semantic_valid is None and schema_valid:
            errors.append(f"{relative}: semantic_valid may be null only after schema rejection")
        if valid != (schema_valid and semantic_valid is True):
            errors.append(f"{relative}: overall validity disagrees with schema/semantic expectations")
        if not metadata.get("covers") or not all(isinstance(item, str) and item for item in metadata["covers"]):
            errors.append(f"{relative}: covers metadata missing")
        if not relative.startswith("valid/" if valid else "invalid/"):
            errors.append(f"{relative}: validity directory mismatch")
        counts[(object_type, valid)] += 1
    for object_type, requirement in requirements.items():
        if counts[(object_type, True)] < requirement.get("minimum_valid", 1):
            errors.append(f"{object_type}: valid coverage below minimum")
        if counts[(object_type, False)] < requirement.get("minimum_invalid", 1):
            errors.append(f"{object_type}: invalid coverage below minimum")
    return errors


def g1_assertion(assertion: str) -> tuple[bool, str]:
    if assertion == "six_schema_files_exist":
        missing = [name for name in SCHEMA_FILES if not (SCHEMA_ROOT / name).is_file()]
        return not missing, f"missing={missing}"
    if assertion == "six_schema_meta_validation_passes":
        try:
            for name in SCHEMA_FILES:
                Draft202012Validator.check_schema(load_json(SCHEMA_ROOT / name))
        except Exception as exc:  # jsonschema exposes several schema-error subclasses
            return False, str(exc)
        return True, "six Draft 2020-12 schemas valid"
    if assertion == "valid_and_invalid_fixtures_have_expected_results":
        expectations = load_json(FIXTURE_ROOT / "fixture-expectations.json")
        mismatches = fixture_coverage_errors(expectations)
        for relative, expected in expectations.get("fixtures", {}).items():
            value = load_json(FIXTURE_ROOT / relative)
            schema = load_json(SCHEMA_ROOT / expected["schema"])
            if schema.get("title") != expected.get("object_type"):
                mismatches.append(relative + ":schema-title")
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            schema_actual = not list(validator.iter_errors(value))
            if schema_actual is not expected["schema_valid"]:
                mismatches.append(relative + ":schema")
                continue
            if schema_actual:
                semantic_actual = not semantic_errors(expected["schema"], value)
                if semantic_actual is not expected["semantic_valid"]:
                    mismatches.append(relative + ":semantic")
                if (schema_actual and semantic_actual) is not expected["valid"]:
                    mismatches.append(relative + ":overall")
        return not mismatches, f"mismatches={mismatches}"
    if assertion == "run_and_request_state_machine_negative_cases_pass":
        contract = load_json(CONTRACT_PATH)
        allowed = {tuple(edge) for edge in contract["object_contract"]["run_transitions"]}
        cases = load_json(FIXTURE_ROOT / "state-machine-cases.json")
        illegal = {(case["from"], case["to"]) for case in cases["run_transition_negative_cases"]}
        positive_errors = {
            case["case_id"]: validate_request_events(case["events"], set(case["planned_request_ids"]))
            for case in cases["request_history_positive_cases"]
        }
        accepted_negative = [
            case["case_id"]
            for case in cases["request_history_negative_cases"]
            if not validate_request_events(case["events"], set(case["planned_request_ids"]))
        ]
        failures = []
        if allowed & illegal:
            failures.append(f"accepted illegal transitions={sorted(allowed & illegal)}")
        failures.extend(f"positive {case_id}: {errors}" for case_id, errors in positive_errors.items() if errors)
        failures.extend(f"negative accepted: {case_id}" for case_id in accepted_negative)
        return not failures, f"cases={len(illegal) + len(positive_errors) + len(cases['request_history_negative_cases'])}, failures={failures}"
    if assertion == "three_real_phase0_hash_vectors_match":
        vector_document = load_json(FIXTURE_ROOT / "hash-vectors.json")
        vectors = vector_document["vectors"]
        phase0 = {(row["game"], row["issue_id"]): row for row in load_jsonl(PHASE0_ROOT / "consensus" / "canonical-records.jsonl")}
        mismatches = []
        for vector in vectors:
            fact = vector["phase0_fact"]
            actual = phase0.get((fact["game"], fact["issue_id"]))
            if actual is None or core_fact_projection(actual) != fact:
                mismatches.append(vector["case_id"] + ":source")
            if canonical_json_bytes(fact).decode("utf-8") != vector["canonical_phase0_json_lf"]:
                mismatches.append(vector["case_id"] + ":bytes")
            if canonical_sha256(fact) != vector["expected_core_fact_sha256"]:
                mismatches.append(vector["case_id"] + ":hash")
        id_vectors = vector_document.get("deterministic_id_vectors", [])
        for vector in id_vectors:
            fixture = load_json(REPO / vector["fixture_ref"])
            prefix, identity = deterministic_id_projection(vector["kind"], fixture)
            expected_id = prefix + canonical_sha256(identity)
            if identity != vector["identity"]:
                mismatches.append(vector["case_id"] + ":identity")
            if canonical_json_bytes(identity).decode("utf-8") != vector["canonical_identity_json_lf"]:
                mismatches.append(vector["case_id"] + ":bytes")
            if expected_id != vector["expected_id"] or fixture.get(vector["fixture_id_field"]) != expected_id:
                mismatches.append(vector["case_id"] + ":id")
        return len(vectors) == 3 and len(id_vectors) == 3 and not mismatches, f"core_vectors={len(vectors)}, id_vectors={len(id_vectors)}, mismatches={mismatches}"
    if assertion == "spec_bundle_hash_is_frozen":
        freeze = load_json(FREEZE_PATH)
        expected_files = freeze.get("files", [])
        actual_files, actual = frozen_spec_bundle()
        expected = freeze.get("expected_bundle_sha256")
        required_paths = {"docs/data/lottery-data-spec-v1.md", "tests/phase1/fixtures/spec/hash-vectors.json"} | {
            f"schemas/phase1/{name}" for name in SCHEMA_FILES
        }
        frozen_paths = {entry.get("path") for entry in expected_files}
        ok = frozen_paths == required_paths and actual_files == expected_files and expected == actual
        return ok, f"files={len(actual_files)}, expected={expected}, actual={actual}"
    raise KeyError(f"unknown G1 assertion: {assertion}")


class SpecificationAcceptanceTests(unittest.TestCase):
    def test_every_contract_g1_assertion(self) -> None:
        contract = load_json(CONTRACT_PATH)
        gate = next(item for item in contract["gates"] if item["id"] == "G1")
        for assertion in gate["assertions"]:
            with self.subTest(assertion=assertion):
                passed, detail = g1_assertion(assertion)
                self.assertTrue(passed, detail)

    def test_unknown_assertion_fails_closed(self) -> None:
        with self.assertRaises(KeyError):
            g1_assertion("future_check_not_implemented")


if __name__ == "__main__":
    unittest.main()
