from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from tests.phase1 import run_acceptance as runner


REPO = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
PRODUCT_FORMAL = REPO / "artifacts" / "phase-1"


class AcceptanceG3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.acceptance_depth = patch.dict(
            runner.os.environ, {"LOTTERY_ACCEPTANCE_DEPTH": "0"},
        )
        self.acceptance_depth.start()
        self.addCleanup(self.acceptance_depth.stop)
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.contract_sha = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
        self.runtime_argv = [
            [runner.sys.executable if token == "{python}" else token for token in row]
            for row in runner.EXPECTED_G3_ARGV
        ]
        self.unittest_command_count = sum(
            any(argv[index:index + 2] == ["-m", "unittest"] for index in range(len(argv) - 1))
            for argv in self.runtime_argv
        )

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(runner.canonical_bytes(value))

    def _g1(self) -> dict:
        assertions = next(g for g in self.contract["gates"] if g["id"] == "G1")["assertions"]
        return {
            "schema_version": "1.0.0",
            "artifact_type": "phase1_gate_acceptance",
            "contract_ref": "docs/roadmap/phase-1-acceptance-contract.json",
            "contract_sha256": self.contract_sha,
            "contract_version": self.contract["contract_version"],
            "gate": "G1",
            "status": "PASS",
            "spec_bundle_freeze_ref": runner.FREEZE_PATH.relative_to(REPO).as_posix(),
            "spec_bundle_freeze_sha256": runner.sha256_file(runner.FREEZE_PATH),
            "commands": [],
            "assertions": [{"id": value, "status": "PASS"} for value in assertions],
        }

    def _g2(self, g1: dict) -> dict:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "phase1_gate_acceptance",
            "contract_ref": "docs/roadmap/phase-1-acceptance-contract.json",
            "contract_sha256": self.contract_sha,
            "contract_version": self.contract["contract_version"],
            "gate": "G2",
            "status": "PASS",
            "dependency": copy.deepcopy(g1),
            "commands": [],
            "assertions": [{"id": value, "status": "PASS"} for value in runner.EXPECTED_G2_ASSERTIONS],
            "oracle_assertions": [{"id": f"oracle-{index}", "status": "PASS"} for index in range(18)],
        }

    def _review(self, scope: str) -> dict:
        relative = "config/phase1/live-source-policy.json"
        return {
            "artifact_type": "phase1_independent_review",
            "review_scope": scope,
            "contract_version": self.contract["contract_version"],
            "contract_sha256": self.contract_sha,
            "reviewer": f"independent-{scope}-reviewer",
            "reviewed_artifact_hashes": {relative: runner.sha256_file(REPO / relative)},
            "blocking_findings": [],
        }

    def _install(self, formal: Path, *, reviews: bool = True) -> None:
        formal.mkdir(parents=True)
        shutil.copy2(PRODUCT_FORMAL / "current-release.json", formal / "current-release.json")
        for relative in ("releases/baseline-v1", "baseline-v1", "runs/p1-baseline-v1"):
            shutil.copytree(PRODUCT_FORMAL / relative, formal / relative)
        g1 = self._g1()
        self._write_json(formal / "acceptance" / "g1.json", g1)
        self._write_json(formal / "acceptance" / "g2.json", self._g2(g1))
        if reviews:
            for filename, scope in runner.REVIEW_SCOPES.items():
                self._write_json(formal / "reviews" / filename, self._review(scope))

    def _child(self, case: str, *, status: str = "PASS") -> dict:
        declared = next(item for item in self.contract["e2e_cases"] if item["id"] == case)["assertions"]
        return {
            "schema_version": "1.0.0",
            "artifact_type": "phase1_gate_acceptance",
            "contract_version": self.contract["contract_version"],
            "contract_sha256": self.contract_sha,
            "case": case,
            "status": status,
            "assertions": [
                {"id": assertion_id, "status": "PASS", "expected": True, "actual": True}
                for assertion_id in declared
            ],
        }

    @staticmethod
    def _required_unit_ids(index: int) -> list[str]:
        return sorted({
            test_id
            for command_index, test_ids in runner.G3_TEST_REQUIREMENTS.values()
            if command_index == index
            for test_id in test_ids
        })

    def _unit_stderr(self, index: int, *, mode: str = "valid") -> str:
        required = self._required_unit_ids(index)
        ids = required or [f"tests.phase1.mock.Command{index}Tests.test_smoke"]
        if mode == "missing_target":
            ids = [f"tests.phase1.mock.Command{index}Tests.test_wrong_target"]
        if mode == "duplicate":
            ids = [ids[0], ids[0]]
        status = "ok"
        if mode == "skip":
            status = "skipped 'not acceptable'"
        elif mode == "expected_failure":
            status = "expected failure"
        elif mode == "unexpected_success":
            status = "unexpected success"
        rows = [f"test_contract ({test_id}) ... {status}" for test_id in ids]
        return "\n".join([*rows, "", f"Ran {len(rows)} tests in 0.001s", "", "OK", ""])

    def _completed(self, argv: list[str], *, hold_case: str | None = None) -> subprocess.CompletedProcess[str]:
        index = self.runtime_argv.index(argv)
        if index < self.unittest_command_count:
            return subprocess.CompletedProcess(argv, 0, "", self._unit_stderr(index))
        case = argv[-1]
        hold = case == hold_case
        child = self._child(case, status="HOLD" if hold else "PASS")
        return subprocess.CompletedProcess(argv, runner.HOLD if hold else runner.PASS, json.dumps(child), "")

    def _run(
        self,
        formal: Path,
        *,
        hold_case: str | None = None,
        transform: Callable[[int, subprocess.CompletedProcess[str]], subprocess.CompletedProcess[str]] | None = None,
        mutate_during_run: Callable[[], None] | None = None,
    ) -> tuple[int, dict]:
        mutated = False

        def side_effect(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal mutated
            index = self.runtime_argv.index(argv)
            completed = self._completed(argv, hold_case=hold_case)
            if transform is not None:
                completed = transform(index, completed)
            if mutate_during_run is not None and not mutated:
                mutate_during_run()
                mutated = True
            return completed

        with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run", side_effect=side_effect):
            return runner.run_g3(self.contract, CONTRACT_PATH)

    def _write_g3(self, formal: Path, report: dict) -> None:
        self._write_json(formal / "acceptance" / "g3.json", report)

    def test_01_valid_g3_has_frozen_commands_assertions_typed_children_and_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            code, report = self._run(formal)
            self.assertEqual((code, report["status"]), (runner.PASS, "PASS"), report)
            self.assertEqual(len(report["commands"]), len(self.runtime_argv))
            self.assertEqual([row["argv"] for row in report["commands"]], self.runtime_argv)
            self.assertEqual(len(report["assertions"]), len(runner.EXPECTED_G3_ASSERTIONS))
            self.assertEqual([row["id"] for row in report["assertions"]], runner.EXPECTED_G3_ASSERTIONS)
            self.assertTrue(all(row["status"] == "PASS" and row["evidence"] for row in report["assertions"]))
            self.assertEqual(len(report["input_inventory"]), 8)
            self.assertTrue(all(item["type"] in {"file", "tree"} for item in report["input_inventory"].values()))
            for command in report["commands"][:self.unittest_command_count]:
                self.assertEqual(command["kind"], "unittest")
                self.assertTrue(command["unittest"]["valid"])
                self.assertGreater(command["unittest"]["ran"], 0)
            for case, child in report["e2e_children"].items():
                self.assertTrue(runner._validate_child(case, child, self.contract, CONTRACT_PATH))
            self._write_g3(formal, report)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                all_code, all_report = runner.run_all(self.contract, CONTRACT_PATH)
            execute.assert_not_called()
            self.assertEqual((all_code, all_report["status"]), (runner.PASS, "PASS"), all_report)

    def test_02_missing_review_is_hold_without_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal, reviews=False)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                code, report = runner.run_g3(self.contract, CONTRACT_PATH)
            self.assertEqual((code, report["status"]), (runner.HOLD, "HOLD"))
            self.assertEqual(report["review_states"], ["missing", "missing"])
            execute.assert_not_called()

    def test_03_missing_dependency_is_hold_without_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            (formal / "acceptance" / "g1.json").unlink()
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                code, report = runner.run_g3(self.contract, CONTRACT_PATH)
            self.assertEqual((code, report["status"]), (runner.HOLD, "HOLD"))
            execute.assert_not_called()

    def test_04_old_contract_dependency_is_hold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            path = formal / "acceptance" / "g1.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["contract_version"] = "3.3.0"
            value["contract_sha256"] = "0" * 64
            self._write_json(path, value)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                code, report = runner.run_g3(self.contract, CONTRACT_PATH)
            self.assertEqual((code, report["status"]), (runner.HOLD, "HOLD"))
            execute.assert_not_called()

    def test_05_current_contract_failed_dependency_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            path = formal / "acceptance" / "g1.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["status"] = "FAIL"
            self._write_json(path, value)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                code, report = runner.run_g3(self.contract, CONTRACT_PATH)
            self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"))
            execute.assert_not_called()

    def test_06_only_e2e05_hold20_can_map_g3_to_hold(self) -> None:
        for hold_case, expected in (("E2E-05", (runner.HOLD, "HOLD")), ("E2E-03", (runner.FAIL, "FAIL"))):
            with self.subTest(hold_case=hold_case), tempfile.TemporaryDirectory() as directory:
                formal = Path(directory) / "phase-1"
                self._install(formal)
                code, report = self._run(formal, hold_case=hold_case)
                self.assertEqual((code, report["status"]), expected, report)

    def test_07_contract_command_or_assertion_profile_weakening_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            for mutate in (
                lambda value: value["gates"][-1]["verification"][0]["argv"].pop(),
                lambda value: value["gates"][-1]["assertions"].pop(),
            ):
                candidate = copy.deepcopy(self.contract)
                mutate(candidate)
                with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                    code, report = runner.run_g3(candidate, CONTRACT_PATH)
                self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"))
                execute.assert_not_called()

    def test_08_main_rejects_arbitrary_output_and_recursive_g3_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory) / "nested" / "report.json"
            for gate in ("G3", "ALL"):
                with self.subTest(gate=gate), patch.object(runner, "FORMAL_ROOT", Path(directory) / "formal"):
                    code = runner.main(["--contract", str(CONTRACT_PATH), "--gate", gate, "--output", str(forbidden)])
                self.assertEqual(code, runner.FAIL)
                self.assertFalse(forbidden.exists())
            with patch.dict(runner.os.environ, {"LOTTERY_ACCEPTANCE_DEPTH": "1"}), patch.object(runner, "FORMAL_ROOT", Path(directory) / "formal"):
                with self.assertRaisesRegex(RuntimeError, "recursive G3"):
                    runner.run_g3(self.contract, CONTRACT_PATH)
                with self.assertRaisesRegex(RuntimeError, "recursive ALL"):
                    runner.run_all(self.contract, CONTRACT_PATH)

    def test_09_each_running_input_inventory_mutation_forces_fail(self) -> None:
        targets = {
            "g1": "acceptance/g1.json",
            "g2": "acceptance/g2.json",
            "pointer": "current-release.json",
            "release_tree": "releases/baseline-v1/draws.jsonl",
            "review": "reviews/data-review.json",
        }
        for label, relative in targets.items():
            with self.subTest(target=label), tempfile.TemporaryDirectory() as directory:
                formal = Path(directory) / "phase-1"
                self._install(formal)
                target = formal / relative
                code, report = self._run(formal, mutate_during_run=lambda target=target: target.write_bytes(target.read_bytes() + b" "))
                self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"), report)
                self.assertFalse(report["input_inventory_stable"])

    def test_10_strict_unittest_parser_rejects_empty_missing_skip_expected_failure_and_duplicate(self) -> None:
        successful_failed_name = (
            "tests.phase1.test_acceptance_g3.AcceptanceG3Tests."
            "test_05_current_contract_failed_dependency_is_fail"
        )
        successful_stderr = "\n".join([
            f"test_contract ({successful_failed_name}) ... ok",
            "", "Ran 1 test in 0.001s", "", "OK", "",
        ])
        parsed = runner._parse_unittest_verbose(successful_stderr)
        self.assertTrue(parsed["valid"], parsed)
        self.assertEqual(
            parsed["tests"],
            [{"id": successful_failed_name, "status": "ok"}],
        )

        modes = (
            "empty", "missing_target", "skip", "expected_failure",
            "unexpected_success", "duplicate",
        )
        for mode in modes:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                formal = Path(directory) / "phase-1"
                self._install(formal)

                def transform(index: int, completed: subprocess.CompletedProcess[str], mode: str = mode) -> subprocess.CompletedProcess[str]:
                    if index != 0:
                        return completed
                    stderr = "" if mode == "empty" else self._unit_stderr(index, mode=mode)
                    return subprocess.CompletedProcess(completed.args, 0, "", stderr)

                code, report = self._run(formal, transform=transform)
                self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"), report)

    def test_11_e2e_child_contract_case_assertions_or_typed_evidence_tamper_is_fail(self) -> None:
        def mutate_child(mode: str, child: dict) -> None:
            if mode == "contract":
                child["contract_sha256"] = "0" * 64
            elif mode == "case":
                child["case"] = "E2E-04"
            elif mode == "assertions":
                child["assertions"].pop()
            elif mode == "evidence":
                child["assertions"][0]["expected"] = None

        for mode in ("contract", "case", "assertions", "evidence"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                formal = Path(directory) / "phase-1"
                self._install(formal)

                def transform(index: int, completed: subprocess.CompletedProcess[str], mode: str = mode) -> subprocess.CompletedProcess[str]:
                    if index != self.unittest_command_count:
                        return completed
                    child = json.loads(completed.stdout)
                    mutate_child(mode, child)
                    return subprocess.CompletedProcess(completed.args, completed.returncode, json.dumps(child), completed.stderr)

                code, report = self._run(formal, transform=transform)
                self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"), report)

    def test_12_g2_embedded_g1_canonical_sha_mismatch_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            path = formal / "acceptance" / "g2.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["dependency"]["commands"] = [{"forged": True}]
            self._write_json(path, value)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                code, report = runner.run_g3(self.contract, CONTRACT_PATH)
            self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"))
            self.assertFalse(report["g2_embedded_g1_hash_match"])
            execute.assert_not_called()

    def test_13_all_rejects_expected_actual_or_evidence_tamper_in_g3_assertion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            code, report = self._run(formal)
            self.assertEqual(code, runner.PASS)
            for field, replacement in (("expected", False), ("actual", False), ("evidence", {"kind": "forged"})):
                with self.subTest(field=field):
                    candidate = copy.deepcopy(report)
                    candidate["assertions"][0][field] = replacement
                    self._write_g3(formal, candidate)
                    with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                        all_code, all_report = runner.run_all(self.contract, CONTRACT_PATH)
                    self.assertEqual((all_code, all_report["status"]), (runner.FAIL, "FAIL"), all_report)
                    execute.assert_not_called()

    def test_14_old_g3_and_all_do_not_block_fresh_g3_resign_then_valid_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            self._write_json(formal / "acceptance" / "g3.json", {"status": "old"})
            self._write_json(formal / "acceptance" / "all.json", {"status": "old"})
            code, report = self._run(formal)
            self.assertEqual((code, report["status"]), (runner.PASS, "PASS"), report)
            self._write_g3(formal, report)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                all_code, all_report = runner.run_all(self.contract, CONTRACT_PATH)
            self.assertEqual((all_code, all_report["status"]), (runner.PASS, "PASS"), all_report)
            execute.assert_not_called()

    def test_15_all_maps_current_fail_and_hold_without_subprocess(self) -> None:
        for mode, expected in (("fail", (runner.FAIL, "FAIL")), ("hold", (runner.HOLD, "HOLD"))):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                formal = Path(directory) / "phase-1"
                self._install(formal)
                if mode == "hold":
                    code, report = self._run(formal, hold_case="E2E-05")
                else:
                    def transform(index: int, completed: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
                        if index != 0:
                            return completed
                        return subprocess.CompletedProcess(completed.args, 0, "", self._unit_stderr(index, mode="missing_target"))
                    code, report = self._run(formal, transform=transform)
                self.assertEqual((code, report["status"]), expected, report)
                self._write_g3(formal, report)
                with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                    all_code, all_report = runner.run_all(self.contract, CONTRACT_PATH)
                self.assertEqual((all_code, all_report["status"]), expected, all_report)
                execute.assert_not_called()

    def test_16_malformed_or_forged_review_is_fail_without_commands(self) -> None:
        for mode in ("malformed", "forged"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                formal = Path(directory) / "phase-1"
                self._install(formal)
                path = formal / "reviews" / "data-review.json"
                if mode == "malformed":
                    path.write_text("{", encoding="utf-8")
                else:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["reviewed_artifact_hashes"]["config/phase1/live-source-policy.json"] = "f" * 64
                    self._write_json(path, value)
                with patch.object(runner, "FORMAL_ROOT", formal), patch.object(runner.subprocess, "run") as execute:
                    code, report = runner.run_g3(self.contract, CONTRACT_PATH)
                self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"))
                execute.assert_not_called()

    def test_17_static_assertion_evidence_gap_forces_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            with patch.object(runner, "FORMAL_ROOT", formal), patch.object(
                runner, "_configuration_hashes_close", return_value=False,
            ), patch.object(runner.subprocess, "run", side_effect=lambda argv, **kwargs: self._completed(argv)):
                code, report = runner.run_g3(self.contract, CONTRACT_PATH)
            self.assertEqual((code, report["status"]), (runner.FAIL, "FAIL"))
            assertion = next(row for row in report["assertions"] if row["id"] == "configuration_input_hashes_match_contract")
            self.assertEqual((assertion["expected"], assertion["actual"], assertion["status"]), (True, False, "FAIL"))
            self.assertTrue(assertion["evidence"])

    def test_18_real_g1_is_deterministic_and_closes_controlled_g2_binding(self) -> None:
        first_code, first = runner.run_g1(self.contract, CONTRACT_PATH)
        second_code, second = runner.run_g1(self.contract, CONTRACT_PATH)
        self.assertEqual((first_code, second_code), (runner.PASS, runner.PASS))
        self.assertEqual(first, second)
        self.assertIn("Ran 2 tests in <elapsed>s", first["commands"][0]["stderr"])
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "phase-1"
            self._install(formal)
            self._write_json(formal / "acceptance" / "g1.json", first)
            self._write_json(formal / "acceptance" / "g2.json", self._g2(second))
            with patch.object(runner, "FORMAL_ROOT", formal):
                states, _dependencies, review_states, binding_ok = runner._g3_inputs(
                    self.contract, CONTRACT_PATH,
                )
            self.assertEqual(states, ["valid", "valid"])
            self.assertEqual(review_states, ["valid", "valid"])
            self.assertTrue(binding_ok)

    def test_19_unittest_elapsed_normalization_is_exact_and_preserves_failure_output(self) -> None:
        raw = (
            "Traceback: operation took 9.999s\n"
            "Ran 1 test in 2.500s\n"
            "FAILED (failures=1, duration=2.500s)\n"
            "prefix Ran 1 test in 3.000s\n"
        )
        self.assertEqual(
            runner._normalize_unittest_elapsed(raw),
            raw.replace("Ran 1 test in 2.500s\n", "Ran 1 test in <elapsed>s\n"),
        )

    def test_20_final_acceptance_contract_drives_all_output_and_derived_inventory(self) -> None:
        final = self.contract["final_acceptance"]
        declared_argv = final["runner_argv"]
        required_report = Path(final["required_report"])
        self.assertEqual(declared_argv[-2:], ["--output", required_report.as_posix()])
        self.assertEqual(declared_argv[4:6], ["--gate", "ALL"])

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            copied_contract = workspace / "docs" / "roadmap" / CONTRACT_PATH.name
            copied_contract.parent.mkdir(parents=True)
            shutil.copy2(CONTRACT_PATH, copied_contract)
            formal = workspace / "artifacts" / "phase-1"
            expected_report = {"schema_version": "1.0.0", "gate": "ALL", "status": "PASS"}
            main_argv = declared_argv[2:]
            previous_cwd = Path.cwd()
            try:
                runner.os.chdir(workspace)
                with self.subTest(case="declared output is accepted and written"), patch.object(
                    runner, "FORMAL_ROOT", formal,
                ), patch.object(runner, "run_all", return_value=(runner.PASS, expected_report)) as execute:
                    code = runner.main(main_argv)
                    self.assertEqual(code, runner.PASS)
                    execute.assert_called_once()
                    written = workspace / required_report
                    self.assertTrue(written.is_file())
                    self.assertEqual(written.read_bytes(), runner.canonical_bytes(expected_report))
                    self.assertFalse((formal / "acceptance" / "all.json").exists())

                wrong_report = required_report.with_name("wrong-final.json")
                wrong_argv = [*main_argv[:-1], wrong_report.as_posix()]
                with self.subTest(case="wrong output remains rejected"), patch.object(
                    runner, "FORMAL_ROOT", formal,
                ), patch.object(runner, "run_all") as execute:
                    code = runner.main(wrong_argv)
                    self.assertEqual(code, runner.FAIL)
                    execute.assert_not_called()
                    self.assertFalse((workspace / wrong_report).exists())

                required_label = required_report.relative_to("artifacts/phase-1").as_posix()
                old_label = "acceptance/all.json"
                (formal / "acceptance").mkdir(parents=True, exist_ok=True)
                self._write_json(formal / "acceptance" / "g3.json", {"status": "derived-g3"})
                self._write_json(formal / required_label, {"status": "derived-final"})
                self._write_json(formal / old_label, {"status": "ordinary-old-name"})
                with self.subTest(case="derived inventory follows required_report"), patch.object(
                    runner, "FORMAL_ROOT", formal,
                ):
                    inventory = runner._formal_state(exclude=runner.G3_DERIVED_OUTPUTS)
                    self.assertNotIn("acceptance/g3.json", inventory)
                    self.assertNotIn(required_label, inventory)
                    self.assertIn(old_label, inventory)
            finally:
                runner.os.chdir(previous_cwd)

    def test_21_acceptance_report_paths_reject_unsafe_refs_collisions_and_argv_mismatch(self) -> None:
        unsafe_refs = {
            "absolute": "/tmp/phase1-acceptance.json",
            "parent traversal": "artifacts/phase-1/acceptance/../escape.json",
            "backslash": "artifacts\\phase-1\\acceptance\\phase1-acceptance.json",
            "non-json": "artifacts/phase-1/acceptance/phase1-acceptance.txt",
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            runner, "FORMAL_ROOT", Path(directory) / "artifacts" / "phase-1",
        ):
            for case, ref in unsafe_refs.items():
                with self.subTest(case=case, ref=ref):
                    with self.assertRaises(ValueError):
                        runner._validated_acceptance_report_ref(ref)

        report_refs = {
            "G1": "artifacts/phase-1/acceptance/g1.json",
            "G2": "artifacts/phase-1/acceptance/g2.json",
            "G3": "artifacts/phase-1/acceptance/g3.json",
            "ALL": "artifacts/phase-1/acceptance/phase1-acceptance.json",
        }

        def replace_report_ref(candidate: dict, gate_id: str, ref: str) -> None:
            if gate_id == "ALL":
                candidate["final_acceptance"]["required_report"] = ref
                candidate["final_acceptance"]["runner_argv"][-1] = ref
                return
            gate = next(item for item in candidate["gates"] if item["id"] == gate_id)
            gate["required_evidence"] = [
                ref if value == report_refs[gate_id] else value
                for value in gate["required_evidence"]
            ]

        for left, right in (
            ("G1", "G2"),
            ("G1", "G3"),
            ("G1", "ALL"),
            ("G2", "G3"),
            ("G2", "ALL"),
            ("G3", "ALL"),
        ):
            with self.subTest(case="report collision", left=left, right=right):
                candidate = copy.deepcopy(self.contract)
                replace_report_ref(candidate, right, report_refs[left])
                with self.assertRaisesRegex(ValueError, "collide"):
                    runner._formal_report_map(candidate)

        for case, mutate in (
            (
                "required_report changed without runner output",
                lambda final: final.__setitem__(
                    "required_report", "artifacts/phase-1/acceptance/alternate-final.json",
                ),
            ),
            (
                "runner output changed without required_report",
                lambda final: final["runner_argv"].__setitem__(
                    -1, "artifacts/phase-1/acceptance/alternate-final.json",
                ),
            ),
        ):
            with self.subTest(case=case):
                candidate = copy.deepcopy(self.contract)
                mutate(candidate["final_acceptance"])
                with self.assertRaisesRegex(ValueError, "does not match"):
                    runner._formal_report_map(candidate)

    def test_22_current_contract_report_map_and_g3_derived_outputs_are_exact(self) -> None:
        reports = runner._formal_report_map(self.contract)
        expected_labels = {
            "G1": "acceptance/g1.json",
            "G2": "acceptance/g2.json",
            "G3": "acceptance/g3.json",
            "ALL": "acceptance/phase1-acceptance.json",
        }
        self.assertEqual({gate: value[0] for gate, value in reports.items()}, expected_labels)
        self.assertEqual(
            {gate: value[1] for gate, value in reports.items()},
            {
                gate: (PRODUCT_FORMAL / Path(label)).resolve()
                for gate, label in expected_labels.items()
            },
        )
        self.assertEqual(
            runner.G3_DERIVED_OUTPUTS,
            frozenset({"acceptance/g3.json", "acceptance/phase1-acceptance.json"}),
        )
        for ordinary_or_legacy in ("acceptance/g1.json", "acceptance/g2.json", "acceptance/all.json"):
            with self.subTest(not_derived=ordinary_or_legacy):
                self.assertNotIn(ordinary_or_legacy, runner.G3_DERIVED_OUTPUTS)

    def test_23_acceptance_directory_symlink_escape_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "artifacts" / "phase-1"
            outside = root / "outside"
            formal.mkdir(parents=True)
            outside.mkdir()
            try:
                (formal / "acceptance").symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks are unavailable on this platform: {exc}")
            with patch.object(runner, "FORMAL_ROOT", formal):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    runner._validated_acceptance_report_ref(
                        "artifacts/phase-1/acceptance/g1.json",
                    )
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
