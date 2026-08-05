from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from lottery_research.phase2 import EVIDENCE_MISMATCH, HOLD, INVALID_CONTRACT, PASS, REJECTED
from lottery_research.phase2.benchmark import _partition
from lottery_research.phase2.cli import COMMANDS
from lottery_research.phase2.environment import installed_packages_for_lock, source_schema_bundle
from lottery_research.phase2.input_validation import sha256
from lottery_research.phase2.schema import SCHEMA_FILES, load_schema, validate_payload


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "roadmap" / "phase-2-acceptance-contract.json"
ENVIRONMENT_LOCK = ROOT / "artifacts" / "phase-2" / "contracts" / "environment-lock.json"
DRAFT_ROOT = ROOT / "artifacts" / "phase-2" / "readiness" / "drafts"
RULE_DOCUMENT = DRAFT_ROOT / "input-rule-and-time-contract.draft.md"
FROZEN_AT = "2026-08-05T00:30:00Z"


def formal_manifest() -> dict:
    value = json.loads((DRAFT_ROOT / "input-manifest.draft.json").read_text(encoding="utf-8"))
    value.pop("created_at_utc")
    value.update(
        schema_version="1.0.0",
        artifact_type="phase2_input_manifest",
        status="frozen",
        frozen_at_utc=FROZEN_AT,
    )
    return value


def formal_preregistration() -> dict:
    return json.loads((ROOT / "artifacts/phase-2/contracts/preregistration.json").read_text(encoding="utf-8"))


def formal_assignment() -> dict:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase2_reviewer_assignment",
        "status": "frozen",
        "frozen_at_utc": FROZEN_AT,
        "minimum_independence_level": "procedural_agent_independence",
        "assignments": [
            {
                "role": role,
                "identity": f"identity-{index}-{role}",
                "responsibility": f"formal responsibility for {role}",
                "independence_level": "procedural_agent_independence" if "review" in role else "not_applicable",
                "conflict_declaration": "no forbidden conflict declared",
                "authored_artifact_paths": [],
                "signature": {"signed": True, "signed_at_utc": FROZEN_AT, "signature_type": "procedural_attestation"},
            }
            for index, role in enumerate(contract["role_separation"]["roles"])
        ],
    }


def auxiliary_schema_samples() -> dict[str, dict]:
    identity = {"path": "x.json", "sha256": "a" * 64}
    verdict = {"id": "X", "status": "PASS", "evidence": [identity]}
    outcome = {"exit_code": 0, "terminal": "PASS"}
    e2e_verdicts = [{"id": f"E2E-P2-{index:02d}-case", "status": "PASS", "expected": outcome, "observed": outcome, "receipt": identity} for index in range(1, 11)]
    return {
        "run_request": {"schema_version": "1.0.0", "artifact_type": "phase2_run_request", "run_id": "run-1", "command": "audit", "argv": ["audit", "--contract", "contract.json"], "created_at_utc": FROZEN_AT, "contract_identity": identity, "input_identities": [identity], "seed_set_id": "s1", "output_path": "out.json"},
        "run_result": {"schema_version": "1.0.0", "artifact_type": "phase2_run_result", "run_id": "run-1", "command": "audit", "terminal": "PASS", "exit_code": 0, "started_at_utc": FROZEN_AT, "finished_at_utc": FROZEN_AT, "request_identity": identity, "input_identities": [identity], "output_identities": [identity], "metrics": {}, "errors": []},
        "replay_review": {"schema_version": "1.0.0", "artifact_type": "phase2_replay_review", "status": "PASS", "reviewer_identity": "reviewer", "independence_level": "procedural_agent_independence", "source_run_identities": [identity], "replay_run_identities": [identity], "deterministic_match_rate": 1.0, "independent_reference_match_rate": 1.0, "different_seed_verdict": "compatible", "blocking_findings": [], "signature": {"signed": True, "signed_at_utc": FROZEN_AT}},
        "acceptance": {"schema_version": "1.0.0", "artifact_type": "phase2_acceptance", "delivery_status": "GO", "signal_status": "indeterminate", "accepted_at_utc": FROZEN_AT, "contract_identity": identity, "deliverable_verdicts": [{**verdict, "id": f"D2-{index:02d}"} for index in range(1, 14)], "gate_verdicts": [{**verdict, "id": f"G{index}"} for index in range(7)], "metric_verdicts": [verdict], "e2e_verdicts": [{**verdict, "id": f"E2E-{index}"} for index in range(10)], "blocking_findings": [], "reviewer_signature": {"reviewer_id": "reviewer", "signed": True, "signed_at_utc": FROZEN_AT}},
        "environment_lock": {"schema_version": "1.0.0", "artifact_type": "phase2_environment_lock", "created_at_utc": FROZEN_AT, "python": {"version": "3.12.13", "implementation": "CPython", "executable": "python"}, "platform": {"system": "Windows", "release": "11", "version": "x", "machine": "AMD64"}, "hardware": {"logical_processors": 4, "processor": "unknown"}, "dependency_lock": identity, "installed_packages": {"jsonschema": "4.26.0"}, "p2_01_start_authorization": identity, "source_schema_bundle": {"algorithm": "sha256", "profile": "sorted path/hash JSON", "file_count": 1, "sha256": "b" * 64}, "benchmark": {"status": "PASS", "synthetic_only": True, "seed": 1, "worlds_per_scenario": 1000, "draws_per_world": 200, "repeats": 1, "null_wall_seconds_per_1000_worlds": 1.0, "bias_wall_seconds_per_1000_worlds": 1.0, "peak_memory_bytes": 1, "memory_metric": "coordinator_process_peak_working_set_bytes", "artifact_bytes": 1, "parallel_workers": 1, "parallel_efficiency_factor": 0.5, "formal_estimate_allowed": True, "diagnostic_checksum": 1.0}},
        "method_review": {"schema_version": "1.0.0", "artifact_type": "phase2_method_review", "status": "PASS", "reviewer_identity": "independent-method-reviewer", "independence_level": "procedural_agent_independence", "reviewed_identities": [identity] * 5, "test_verdicts": [{"id": f"T-{i}", "status": "PASS", "rationale": "complete item review passed"} for i in range(7)], "effect_parameter_verdicts": [{"id": f"E-{i}", "status": "PASS", "rationale": "complete effect review passed"} for i in range(10)], "method_verdicts": [{"id": f"M-{i}", "status": "PASS", "rationale": "complete method review passed"} for i in range(8)], "registered_test_review_coverage": 1.0, "registered_effect_parameter_review_coverage": 1.0, "blocking_findings": [], "unresolved_nonblocking_findings": [], "prompt_summary_sha256": "c" * 64, "signature": {"signed": True, "signed_at_utc": FROZEN_AT}},
        "e2e_receipt": {"schema_version": "1.0.0", "artifact_type": "phase2_e2e_receipt", "case_id": "E2E-P2-01-case", "owner": "owner", "gate": "G6", "status": "PASS", "isolated_root": "isolated/case-01", "started_at_utc": FROZEN_AT, "finished_at_utc": FROZEN_AT, "expected": outcome, "observed": outcome, "run_identities": [identity], "assertions": [{"id": "exit", "status": "PASS", "expected": 0, "observed": 0}], "input_identities": [identity], "output_identities": [identity]},
        "e2e_registry": {"schema_version": "1.0.0", "artifact_type": "phase2_e2e_registry", "status": "PASS", "created_at_utc": FROZEN_AT, "verdicts": e2e_verdicts},
        "final_evidence_manifest": {"schema_version": "1.0.0", "artifact_type": "phase2_final_evidence_manifest", "status": "frozen", "acceptance_mode": "formal", "frozen_at_utc": FROZEN_AT, "contract_identity": identity, "signal_status": "indeterminate", "run_selections": [{"command": command, "run_id": f"run-{command}", "request": identity, "result": identity, "published_output": identity} for command in ("audit", "power", "replay")], "deliverables": [{"id": f"D2-{index:02d}", "evidence": [] if index == 12 else [identity]} for index in range(1, 13)], "e2e_registry_identity": identity, "e2e_verdicts": e2e_verdicts, "blocking_findings": [], "signature": {"signer_id": "reviewer", "signed": True, "signed_at_utc": FROZEN_AT}}
    }


class Phase2CliContractTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "lottery_research.phase2", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_formal_inputs(self, directory: Path, *, overlap: bool = False, tamper_hash: bool = False, observation_inflation: bool = False, leakage_guard_tamper: bool = False) -> tuple[Path, Path, Path]:
        manifest = formal_manifest()
        if overlap:
            segment = copy.deepcopy(manifest["game_rule_maps"][0]["documented_draw_process_segments"][0])
            segment["id"] = "injected-overlap"
            manifest["game_rule_maps"][0]["documented_draw_process_segments"].append(segment)
        if tamper_hash:
            manifest["upstream"]["draws"]["sha256"] = "0" * 64
        if observation_inflation:
            manifest["statistical_unit"] = "one DrawRecord and each SourceObservation are independent statistical units"
        if leakage_guard_tamper:
            manifest["forbidden_statistical_matrix_fields"].remove("future_draw_numbers")
        files = (
            directory / "input-manifest.json",
            directory / "preregistration.json",
            directory / "reviewer-assignment.json",
        )
        for path, payload in zip(files, (manifest, formal_preregistration(), formal_assignment()), strict=True):
            path.write_text(json.dumps(payload), encoding="utf-8")
        return files

    def test_exact_six_command_contract(self) -> None:
        self.assertEqual(COMMANDS, ("validate-input", "qualify-harness", "audit", "power", "replay", "accept"))

    def test_all_eight_schemas_are_valid_and_reject_missing_required_field(self) -> None:
        samples = {"input_manifest": formal_manifest(), "preregistration": formal_preregistration(), "reviewer_assignment": formal_assignment(), **auxiliary_schema_samples()}
        self.assertEqual(set(samples), set(SCHEMA_FILES))
        for kind, payload in samples.items():
            with self.subTest(kind=kind):
                load_schema(kind)
                validate_payload(kind, payload)
                invalid = copy.deepcopy(payload)
                invalid.pop("schema_version")
                with self.assertRaises(Exception):
                    validate_payload(kind, invalid)

    def test_preregistration_schema_freezes_core_method_fields(self) -> None:
        payload = json.loads((ROOT / "artifacts/phase-2/contracts/preregistration.json").read_text(encoding="utf-8"))
        for field in ("effect_grids", "alternative_generator_registry", "power_execution_rule", "power_prefix_policy", "power_calendar_policy", "sensitivity_registry", "seed_derivation", "replay_artifact_profile", "replay_grid_tolerance", "historical_effect_interval"):
            with self.subTest(field=field):
                invalid = copy.deepcopy(payload)
                invalid.pop(field)
                with self.assertRaises(Exception):
                    validate_payload("preregistration", invalid)

    def test_validate_input_passes_on_complete_frozen_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest, preregistration, assignment = self.write_formal_inputs(Path(raw))
            result = self.run_cli("validate-input", "--contract", str(CONTRACT), "--input-rule-contract", str(RULE_DOCUMENT), "--input-manifest", str(manifest), "--preregistration", str(preregistration), "--reviewer-assignment", str(assignment))
        self.assertEqual(result.returncode, PASS, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["terminal"], "PASS")
        self.assertEqual(payload["checks"]["draw_count"], 400)
        self.assertFalse(payload["output_written"])

    def test_practical_effect_registry_grid_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest, preregistration_path, assignment = self.write_formal_inputs(Path(raw))
            preregistration = json.loads(preregistration_path.read_text(encoding="utf-8"))
            preregistration["practical_effect_registry"][0]["effect_grid"] = [0, 0.005, 0.01]
            preregistration_path.write_text(json.dumps(preregistration), encoding="utf-8")
            result = self.run_cli("validate-input", "--contract", str(CONTRACT), "--input-rule-contract", str(RULE_DOCUMENT), "--input-manifest", str(manifest), "--preregistration", str(preregistration_path), "--reviewer-assignment", str(assignment))
        self.assertEqual(result.returncode, REJECTED, result.stdout + result.stderr)
        self.assertIn("practical-effect grid differs", result.stdout)

    def test_tampered_input_hash_returns_evidence_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest, preregistration, assignment = self.write_formal_inputs(Path(raw), tamper_hash=True)
            result = self.run_cli("validate-input", "--contract", str(CONTRACT), "--input-rule-contract", str(RULE_DOCUMENT), "--input-manifest", str(manifest), "--preregistration", str(preregistration), "--reviewer-assignment", str(assignment))
        self.assertEqual(result.returncode, EVIDENCE_MISMATCH, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["terminal"], "EVIDENCE_MISMATCH")

    def test_rule_overlap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest, preregistration, assignment = self.write_formal_inputs(Path(raw), overlap=True)
            result = self.run_cli("validate-input", "--contract", str(CONTRACT), "--input-rule-contract", str(RULE_DOCUMENT), "--input-manifest", str(manifest), "--preregistration", str(preregistration), "--reviewer-assignment", str(assignment))
        self.assertEqual(result.returncode, REJECTED, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["terminal"], "REJECTED")

    def test_observation_sample_inflation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest, preregistration, assignment = self.write_formal_inputs(Path(raw), observation_inflation=True)
            result = self.run_cli("validate-input", "--contract", str(CONTRACT), "--input-rule-contract", str(RULE_DOCUMENT), "--input-manifest", str(manifest), "--preregistration", str(preregistration), "--reviewer-assignment", str(assignment))
        self.assertEqual(result.returncode, REJECTED, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["terminal"], "REJECTED")

    def test_point_in_time_leakage_guard_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest, preregistration, assignment = self.write_formal_inputs(Path(raw), leakage_guard_tamper=True)
            result = self.run_cli("validate-input", "--contract", str(CONTRACT), "--input-rule-contract", str(RULE_DOCUMENT), "--input-manifest", str(manifest), "--preregistration", str(preregistration), "--reviewer-assignment", str(assignment))
        self.assertEqual(result.returncode, REJECTED, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["terminal"], "REJECTED")

    def test_invalid_arguments_return_code_4_as_json(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                result = self.run_cli(command)
                self.assertEqual(result.returncode, INVALID_CONTRACT, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["terminal"], "INVALID_CONTRACT")

    def test_future_commands_hold_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "isolated-project"
            contract = project / "docs/roadmap/phase-2-acceptance-contract.json"
            contract.parent.mkdir(parents=True)
            contract.write_bytes(CONTRACT.read_bytes())
            cases = {
                "qualify-harness": ["--input-manifest", "not-read.json", "--preregistration", "not-read.json"],
                "audit": ["--input-manifest", "not-read.json", "--preregistration", "not-read.json"],
                "power": ["--input-manifest", "not-read.json", "--preregistration", "not-read.json"],
                "replay": ["--evidence-manifest", "not-read.json", "--seed-set", "reserved-replay"],
                "accept": ["--evidence-manifest", "not-read.json"],
            }
            for command, command_args in cases.items():
                with self.subTest(command=command):
                    output = project / "artifacts/phase-2/test-outputs" / f"{command}.json"
                    result = self.run_cli(command, "--contract", str(contract), *command_args, "--output", str(output))
                    self.assertFalse(output.exists())
                    self.assertEqual(result.returncode, HOLD, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["terminal"], "HOLD")

    def test_accept_rejects_output_outside_project_root_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            project = temporary / "isolated-project"
            contract = project / "docs/roadmap/phase-2-acceptance-contract.json"
            contract.parent.mkdir(parents=True)
            contract.write_bytes(CONTRACT.read_bytes())
            replay_review = project / "artifacts/phase-2/reviews/replay-review.json"
            replay_review.parent.mkdir(parents=True)
            replay_review.write_text('{"status":"PASS"}', encoding="utf-8")
            external_output = temporary / "escaped-acceptance.json"
            result = self.run_cli(
                "accept",
                "--contract", str(contract),
                "--evidence-manifest", str(project / "not-read.json"),
                "--output", str(external_output),
            )
            self.assertEqual(result.returncode, INVALID_CONTRACT, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["terminal"], "INVALID_CONTRACT")
            self.assertFalse(external_output.exists())

    def test_partition_is_complete_and_balanced(self) -> None:
        values = _partition(1000, 4)
        self.assertEqual(sum(values), 1000)
        self.assertLessEqual(max(values) - min(values), 1)

    def test_phase1_dependency_contract_is_unchanged_and_results_require_g2(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["dependencies"], ["jsonschema[format]==4.26.0"])
        results = [ROOT / "artifacts" / "phase-2" / "results" / name for name in ("historical-audit.json", "power-envelope.json")]
        if any(path.exists() for path in results):
            qualification = json.loads((ROOT / "artifacts" / "phase-2" / "qualification" / "harness-qualification.json").read_text(encoding="utf-8"))
            self.assertEqual(qualification["status"], "PASS")

    def test_checked_in_environment_lock_is_recomputable(self) -> None:
        payload = json.loads(ENVIRONMENT_LOCK.read_text(encoding="utf-8"))
        validate_payload("environment_lock", payload)
        dependency_path = ROOT / payload["dependency_lock"]["path"]
        start_path = ROOT / payload["p2_01_start_authorization"]["path"]
        self.assertEqual(payload["dependency_lock"]["sha256"], sha256(dependency_path))
        self.assertEqual(payload["p2_01_start_authorization"]["sha256"], sha256(start_path))
        self.assertEqual(payload["installed_packages"], installed_packages_for_lock(dependency_path))
        self.assertEqual(payload["source_schema_bundle"], source_schema_bundle(ROOT))
        self.assertTrue(payload["benchmark"]["synthetic_only"])
        self.assertGreaterEqual(payload["benchmark"]["worlds_per_scenario"], 1000)


if __name__ == "__main__":
    unittest.main()
