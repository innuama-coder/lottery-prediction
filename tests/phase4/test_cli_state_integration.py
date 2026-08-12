from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from lottery_system.phase4.cli_kernel import ProviderRegistry, _load_providers, build_parser
from lottery_system.phase4.identity import content_id
from lottery_system.phase4.ledger import AppendOnlyLedger
from lottery_system.phase4.provider_registry import explicit_hold_commands
from lottery_system.phase4.serialization import canonical_json_bytes, load_json
from lottery_system.phase4.state_projection import (
    StateProjectionViolation,
    reduce_state_events,
    state_object_id,
)
from lottery_system.phase4.commands.validation import (
    E2E_REGISTRY_ID,
    E2E_REGISTRY_SHA256,
    run_e2e_harness,
    validate_e2e_outputs,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = load_json(ROOT / "config/phase4/state-contract.json", reject_floats=True)
PROVENANCE = {
    "producer_actor_id": "p4-implementation-author-i01", "task_id": "T09",
    "session_id": "/root/implementation_author", "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
    "path": "tests/phase4/test_cli_state_integration.py", "role": "implementation_author",
}


def state_records() -> list[dict]:
    rows = [{
        "schema_version": "1.0.0", "artifact_type": "phase4_engineering_status",
        "system_release_id": "P4-MVP-fixture-v1", "status": "READY_FOR_HUMAN_ACCEPTANCE",
    }]
    for game in ("ssq", "dlt"):
        model = {
            "schema_version": "1.0.0", "artifact_type": "phase4_model_status", "game": game,
            "model_id": "M0", "comparator_champion_id": "M0", "model_release_id": "baseline-v1",
            "window_id": "phase4-window-v1", "status": "baseline_only",
        }
        rows.append(model)
        for K in (10, 100, 200, 1000):
            rows.append({
                "schema_version": "1.0.0", "artifact_type": "phase4_top_k_status", "game": game, "K": K,
                "model_id": "M0", "comparator_champion_id": "M0", "model_release_id": "baseline-v1",
                "window_id": "phase4-window-v1", "status": "insufficient_observation",
            })
    return rows


def seed_runtime(runtime: Path) -> str:
    ledger = AppendOnlyLedger(runtime, "state-events")
    head = None
    for ordinal, row in enumerate(state_records(), start=1):
        event = ledger.append_event(
            object_id=state_object_id(row, CONTRACT), event_type="state_recorded",
            event_at_utc=f"2026-01-03T00:{ordinal:02d}:00Z", payload=row,
            producer_provenance=PROVENANCE, expected_head_sha256=head,
        )
        head = event["event_sha256"]
    assert head is not None
    return head


def parser_arguments(specification: dict) -> list[str]:
    verb, action = specification["verb"].split(" ", 1)
    argv = [verb, action]
    for raw in specification["required_flags"]:
        flag = raw.split("|")[0]
        value = {
            "--clock": "fixture:2026-01-03T00:00:00Z", "--mode": "fixture", "--phase": "4",
            "--iteration": "I01", "--scope": "smoke", "--seed-domain": "fixture",
        }.get(flag, "fixture-value")
        argv.extend((flag, value))
    return argv


class CliStateIntegrationTests(unittest.TestCase):
    def test_registry_and_parser_are_bidirectionally_equal_with_explicit_holds(self) -> None:
        _parser, specifications = build_parser(ROOT)
        registry = ProviderRegistry()
        _load_providers(registry)
        self.assertEqual(registry.registered, frozenset(specifications))
        self.assertEqual(len(registry.registered), 30)
        self.assertEqual(explicit_hold_commands(registry), {
            ("research", "resume"), ("replay", "release"),
            ("validate", "final"),
            ("release", "assemble"), ("release", "accept"),
        })
        for key in explicit_hold_commands(registry):
            result = registry.provider(*key)(object())
            self.assertEqual((result["status"], result["terminal"], result["exit_code"]), ("HOLD", "HOLD_COMMAND_NOT_IMPLEMENTED", 20))

    def test_all_thirty_help_and_parser_smoke_paths(self) -> None:
        parser, specifications = build_parser(ROOT)
        contract = load_json(ROOT / "config/phase4/cli-contract.json", reject_floats=True)
        self.assertEqual(len(contract["commands"]), 30)
        for row in contract["commands"]:
            verb, action = row["verb"].split(" ", 1)
            with self.subTest(command=row["verb"]), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                parser.parse_args([verb, action, "--help"])
            self.assertEqual(raised.exception.code, 0)
            parsed = parser.parse_args(parser_arguments(row))
            self.assertEqual((parsed.verb, parsed.action), (verb, action))
            self.assertIn((verb, action), specifications)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as rejected:
            parser.parse_args([
                "state", "project", "--runtime-root", "fixture-runtime", "--output", "fixture-output",
                "--contract-id", "phase4-state-v1",
            ])
        self.assertEqual(rejected.exception.code, 2)

        exact_e2e = parser.parse_args([
            "validate", "e2e", "--registry", "config/phase4/e2e-registry.json",
            "--output", "artifacts/phase-4-prep/e2e-fixture", "--clock", "fixture",
        ])
        self.assertEqual((exact_e2e.verb, exact_e2e.action), ("validate", "e2e"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as extra_root:
            parser.parse_args([
                "validate", "e2e", "--registry", "config/phase4/e2e-registry.json",
                "--output", "artifacts/phase-4-prep/e2e-fixture", "--clock", "fixture",
                "--runtime-root", "artifacts/phase-4-runtime/e2e-fixture",
            ])
        self.assertEqual(extra_root.exception.code, 2)

    def _write_e2e_fixture(self, output: Path) -> None:
        registry_path = ROOT / "config/phase4/e2e-registry.json"
        registry = load_json(registry_path, reject_floats=True)
        cases = [(case, "positive") for case in registry["positive_cases"]]
        cases += [(case, "negative") for case in registry["negative_cases"]]
        references = []
        for ordinal, (case_id, polarity) in enumerate(cases, start=1):
            guard = "PASS" if polarity == "positive" else f"GUARD_{case_id.upper()}"
            receipt = {
                "schema_version": "1.0.0", "artifact_type": "phase4_e2e_case_receipt",
                "case_id": case_id, "polarity": polarity, "guard_code": guard,
                "expected_guard_code": guard, "guard_exit_code": 0 if polarity == "positive" else 5,
                "status": "PASS", "terminal": "E2E_CASE_PASS" if polarity == "positive" else "REGISTERED_GUARD_REJECTED_MUTATION",
                "exit_code": 0, "mutation_count": 0 if polarity == "positive" else 1,
                "unrelated_exception": False, "validator_pid": 1000 + ordinal,
                "validator_execution_id": f"validator-execution-{ordinal:03d}",
                "command": ["fixture-worker", case_id], "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64,
            }
            relative = f"case-receipts/{case_id}/receipt.json"
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded = canonical_json_bytes(receipt)
            path.write_bytes(encoded)
            references.append({"case_id": case_id, "path": relative, "sha256": hashlib.sha256(encoded).hexdigest()})
        manifest = {
            "schema_version": "1.0.0", "artifact_type": "phase4_e2e_manifest",
            "registry_sha256": E2E_REGISTRY_SHA256, "guard_map_sha256": "2" * 64,
            "positive_case_count": len(registry["positive_cases"]),
            "negative_case_count": len(registry["negative_cases"]),
            "expected_case_count": len(cases), "observed_case_count": len(cases),
            "expected_guard_hit_count": len(cases), "case_count": len(cases),
            "guard_hit_rate": "100%", "unrelated_exception_count": 0,
            "mutation_count": len(registry["negative_cases"]),
            "distinct_validator_process_count": len(cases), "case_receipts": references,
            "status": "PASS", "terminal": "E2E_VALIDATION_PASS",
        }
        (output / "e2e-manifest.json").write_bytes(canonical_json_bytes(manifest))

    def test_validate_e2e_runs_fixed_harness_and_recomputes_closure(self) -> None:
        prep = ROOT / "artifacts/phase-4-prep"
        prep.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=prep, prefix="t09-e2e-") as temporary:
            output = Path(temporary)

            def runner(command, **kwargs):
                self.assertEqual(command[1], str(ROOT / "scripts/phase4/validate_bottom_up.py"))
                self.assertNotIn("--runtime-root", command)
                self.assertNotIn("--release-root", command)
                self.assertEqual(kwargs, {"cwd": ROOT, "check": False, "capture_output": True})
                self._write_e2e_fixture(output)
                return subprocess.CompletedProcess(command, 0, b"fixture-stdout", b"")

            args = type("Args", (), {
                "registry": Path("config/phase4/e2e-registry.json"), "output": output, "clock": "fixture",
            })()
            result = run_e2e_harness(args, runner=runner)
            self.assertEqual((result["status"], result["terminal"], result["exit_code"]), ("PASS", "E2E_VALIDATION_PASS", 0))
            self.assertEqual((result["registry_id"], result["registry_sha256"]), (E2E_REGISTRY_ID, E2E_REGISTRY_SHA256))
            self.assertEqual((result["case_count"], result["mutation_count"], result["guard_hit_rate"]), (53, 43, "100%"))

    def test_validate_e2e_rejects_tampered_or_incomplete_closure(self) -> None:
        prep = ROOT / "artifacts/phase-4-prep"
        prep.mkdir(parents=True, exist_ok=True)
        mutations = (
            ("registry_hash", lambda manifest, receipt: manifest.__setitem__("registry_sha256", "f" * 64)),
            ("guard_rate", lambda manifest, receipt: manifest.__setitem__("guard_hit_rate", "52/53")),
            ("mutation_count", lambda manifest, receipt: manifest.__setitem__("mutation_count", 42)),
            ("process_count", lambda manifest, receipt: manifest.__setitem__("distinct_validator_process_count", 52)),
            ("case_duplicate", lambda manifest, receipt: manifest["case_receipts"].__setitem__(1, manifest["case_receipts"][0])),
            ("guard_miss", lambda manifest, receipt: receipt.__setitem__("guard_code", "UNRELATED")),
            ("unrelated_exception", lambda manifest, receipt: receipt.__setitem__("unrelated_exception", True)),
            ("wrong_terminal", lambda manifest, receipt: receipt.__setitem__("terminal", "E2E_CASE_PASS")),
            ("zero_guard_exit", lambda manifest, receipt: receipt.__setitem__("guard_exit_code", 0)),
            ("duplicate_execution", lambda manifest, receipt: receipt.__setitem__("validator_execution_id", "validator-execution-001")),
        )
        for name, mutate in mutations:
            with self.subTest(mutation=name), tempfile.TemporaryDirectory(dir=prep, prefix="t09-e2e-neg-") as temporary:
                output = Path(temporary)
                self._write_e2e_fixture(output)
                manifest_path = output / "e2e-manifest.json"
                manifest = load_json(manifest_path, reject_floats=True)
                negative_reference = next(row for row in manifest["case_receipts"] if row["case_id"] == "time_class_mixed")
                receipt_path = output / negative_reference["path"]
                receipt = load_json(receipt_path, reject_floats=True)
                mutate(manifest, receipt)
                receipt_path.write_bytes(canonical_json_bytes(receipt))
                negative_reference["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                manifest_path.write_bytes(canonical_json_bytes(manifest))
                with self.assertRaises((ValueError, KeyError)):
                    validate_e2e_outputs(registry_path=ROOT / "config/phase4/e2e-registry.json", output_root=output)

    def test_projection_reduces_complete_exact_keys_and_schemas(self) -> None:
        projection = reduce_state_events(state_records(), contract=CONTRACT, ledger_head_sha256="a" * 64)
        self.assertEqual(set(projection), {
            "schema_version", "artifact_type", "contract_id", "ledger_head_sha256", "engineering_status",
            "champion_by_game", "model_status", "top_k_status", "projection_id",
        })
        self.assertEqual(projection["engineering_status"]["status"], "READY_FOR_HUMAN_ACCEPTANCE")
        self.assertEqual(projection["champion_by_game"], {"ssq": "M0", "dlt": "M0"})
        self.assertEqual((len(projection["model_status"]), len(projection["top_k_status"])), (2, 8))
        model_schema = load_json(ROOT / "schemas/phase4/model-status.schema.json", reject_floats=True)
        top_schema = load_json(ROOT / "schemas/phase4/top-k-status.schema.json", reject_floats=True)
        for row in projection["model_status"]:
            Draft202012Validator(model_schema).validate(row)
        for row in projection["top_k_status"]:
            Draft202012Validator(top_schema).validate(row)
        self.assertEqual(projection["projection_id"], content_id("state-projection", projection, excluded_fields=("projection_id",)))

    def test_runtime_projection_and_show_use_only_explicit_identity(self) -> None:
        runtime_parent = ROOT / "artifacts/phase-4-runtime"
        runtime_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_parent, prefix="t09-state-") as temporary:
            runtime = Path(temporary)
            seed_runtime(runtime)
            registry = ProviderRegistry(); _load_providers(registry)
            output = runtime / "explicit-output"
            args = type("Args", (), {"runtime_root": runtime, "output": output})()
            environment = {
                "P4_ACTOR_ID": "p4-implementation-author-i01", "P4_SESSION_ID": "/root/implementation_author",
                "P4_TASK_ID": "T09", "P4_ROLE": "implementation_author",
                "P4_ACTOR_ASSIGNMENTS": "artifacts/phase-4-prep/p4-prep-controller-issued-i01/control/actor-assignments-preparation.json",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                first = registry.provider("state", "project")(args)
                second = registry.provider("state", "project")(args)
            self.assertFalse(first["idempotent_resume"])
            self.assertTrue(second["idempotent_resume"])
            shown = registry.provider("state", "show")(type("Args", (), {"runtime_root": runtime, "object_id": first["projection_id"]})())
            self.assertEqual(shown["projection"]["projection_id"], first["projection_id"])
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes({**second, "idempotent_resume": False}))

    def test_missing_dimensions_future_states_global_and_cross_game_reject(self) -> None:
        base = state_records()
        mutations = []
        for key in ("game", "model_id", "comparator_champion_id", "model_release_id", "window_id"):
            changed = copy.deepcopy(base); del changed[1][key]; mutations.append(changed)
        changed = copy.deepcopy(base); del changed[2]["K"]; mutations.append(changed)
        changed = copy.deepcopy(base); changed[1]["status"] = "prospective_improvement_confirmed"; mutations.append(changed)
        changed = copy.deepcopy(base); changed[2]["status"] = "confirmed_lift"; mutations.append(changed)
        changed = copy.deepcopy(base); changed[0]["improved"] = True; mutations.append(changed)
        changed = copy.deepcopy(base); changed[2]["comparator_champion_id"] = "dlt-M0"; mutations.append(changed)
        changed = copy.deepcopy(base); changed[2]["window_id"] = "other-window"; mutations.append(changed)
        changed = copy.deepcopy(base); changed[2]["game"] = "dlt"; mutations.append(changed)
        for value in mutations:
            with self.subTest(mutation=len(value)), self.assertRaises((StateProjectionViolation, KeyError)):
                reduce_state_events(value, contract=CONTRACT, ledger_head_sha256="a" * 64)

    def test_no_implicit_selector_network_or_dependency_cycle(self) -> None:
        state_source = (ROOT / "src/lottery_system/phase4/state_projection.py").read_text()
        command_source = (ROOT / "src/lottery_system/phase4/commands/state.py").read_text()
        for forbidden in (".glob(", ".rglob(", "getmtime", "urlopen", "requests", "socket."):
            self.assertNotIn(forbidden, state_source + command_source)
        modules = {
            "provider_registry": ROOT / "src/lottery_system/phase4/provider_registry.py",
            "state_projection": ROOT / "src/lottery_system/phase4/state_projection.py",
            "commands.state": ROOT / "src/lottery_system/phase4/commands/state.py",
        }
        graph = {name: set() for name in modules}
        for name, path in modules.items():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    normalized = node.module.lstrip(".")
                    for target in modules:
                        if normalized.endswith(target): graph[name].add(target)
        def visit(node: str, active: set[str], done: set[str]) -> None:
            if node in active: raise AssertionError("T09 component import cycle")
            if node in done: return
            active.add(node)
            for child in graph[node]: visit(child, active, done)
            active.remove(node); done.add(node)
        done: set[str] = set()
        for node in graph: visit(node, set(), done)


if __name__ == "__main__":
    unittest.main()
