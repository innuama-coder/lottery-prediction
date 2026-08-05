from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
sys.path.insert(0, str(SCRIPTS))

from phase0lib import (  # noqa: E402
    ValidationError,
    canonical_json_bytes,
    canonical_sha256,
    load_json,
    load_jsonl,
    validate_schema_instance,
)
from verify_phase0 import verify_command, verify_observation, verify_reviewer, verify_scope  # noqa: E402


class CanonicalizationTests(unittest.TestCase):
    def test_key_order_is_deterministic(self) -> None:
        left = {"z": [3, 2, 1], "a": "中文", "n": 7}
        right = {"n": 7, "a": "中文", "z": [3, 2, 1]}
        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))

    def test_binary_float_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "floats are forbidden"):
            canonical_json_bytes({"money": 0.1})


class SchemaSubsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "state"],
            "properties": {
                "schema_version": {"const": "1.0.0"},
                "state": {"enum": ["frozen"]},
            },
        }

    def test_valid_instance(self) -> None:
        validate_schema_instance({"schema_version": "1.0.0", "state": "frozen"}, self.schema)

    def test_missing_required_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "missing required"):
            validate_schema_instance({"schema_version": "1.0.0"}, self.schema)

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown property"):
            validate_schema_instance({"schema_version": "1.0.0", "state": "frozen", "surprise": 1}, self.schema)

    def test_version_and_enum_drift_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_schema_instance({"schema_version": "2.0.0", "state": "frozen"}, self.schema)
        with self.assertRaises(ValidationError):
            validate_schema_instance({"schema_version": "1.0.0", "state": "open"}, self.schema)

    def test_unsupported_assertion_keyword_rejected(self) -> None:
        bad = dict(self.schema)
        bad["if"] = {"type": "object"}
        with self.assertRaisesRegex(ValidationError, "unsupported keywords"):
            validate_schema_instance({}, bad)


class JsonlTests(unittest.TestCase):
    def test_blank_line_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "records.jsonl"
            path.write_text('{"a":1}\n\n', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "blank JSONL"):
                load_jsonl(path)

    def test_line_number_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "records.jsonl"
            path.write_text('{"a":1}\n{bad}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, ":2:"):
                load_jsonl(path)


@unittest.skipUnless((REPO / "artifacts/phase-0/scope-freeze.json").is_file(), "P0-01 fixtures not frozen")
class FreezeSemanticFailureInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract_path = REPO / "docs/roadmap/phase-0-acceptance-contract.json"
        cls.scope = load_json(REPO / "artifacts/phase-0/scope-freeze.json")
        cls.observation = load_json(REPO / "artifacts/phase-0/observation-plan.json")
        cls.reviewer = load_json(REPO / "artifacts/phase-0/reviewer-assignment.json")

    def test_frozen_scope_replays(self) -> None:
        verify_scope(self.scope, self.contract_path)

    def test_sample_selection_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.scope)
        selected = tampered["corroboration_sample"]["games"][0]["strata"][0]["selected_issue_ids"]
        selected[0] = "2024001"
        tampered["corroboration_sample"]["games"][0]["strata"][0]["selected_issue_ids_sha256"] = canonical_sha256(selected)
        with self.assertRaisesRegex(ValidationError, "does not replay"):
            verify_scope(tampered, self.contract_path)

    def test_candidate_universe_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.scope)
        tampered["corroboration_sample"]["games"][0]["strata"][0]["candidate_issue_ids"].pop()
        with self.assertRaisesRegex(ValidationError, "candidate universe.*mismatch"):
            verify_scope(tampered, self.contract_path)

    def test_schedule_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.observation)
        tampered["request_schedule"][0]["scheduled_at_utc"] = "2026-08-01T00:00:00Z"
        with self.assertRaisesRegex(ValidationError, "request schedule.*mismatch"):
            verify_observation(self.scope, tampered)

    def test_schedule_order_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.observation)
        tampered["request_schedule"][0], tampered["request_schedule"][1] = tampered["request_schedule"][1], tampered["request_schedule"][0]
        for sequence, item in enumerate(tampered["request_schedule"], 1):
            item["sequence"] = sequence
        tampered["request_schedule_sha256"] = canonical_sha256(tampered["request_schedule"])
        with self.assertRaisesRegex(ValidationError, "globally sorted"):
            verify_observation(self.scope, tampered)

    def test_retry_budget_underflow_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.observation)
        tampered["budgets"]["total_retry_limit"] = 0
        with self.assertRaisesRegex(ValidationError, "retry budget"):
            verify_observation(self.scope, tampered)

    def test_role_overlap_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.reviewer)
        tampered["role_separation"]["reviewer_ids"] = tampered["role_separation"]["executor_ids"]
        with self.assertRaisesRegex(ValidationError, "not pairwise disjoint"):
            verify_reviewer(self.scope, tampered)


@unittest.skipUnless((REPO / "artifacts/phase-0/verification-command.json").is_file(), "verification command not frozen")
class VerificationCommandFailureInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.command = load_json(REPO / "artifacts/phase-0/verification-command.json")
        cls.schema = load_json(REPO / "artifacts/phase-0/schemas/verification-command.schema.json")

    def test_missing_full_replay_command_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.command)
        tampered.pop("full_replay_command", None)
        with self.assertRaisesRegex(ValidationError, "missing required"):
            validate_schema_instance(tampered, self.schema)

    def test_p0_01_command_cannot_masquerade_as_full_replay(self) -> None:
        tampered = copy.deepcopy(self.command)
        tampered["command"] = tampered.get("bootstrap_gate_command", tampered["command"])
        with self.assertRaisesRegex(ValidationError, "expected const"):
            validate_schema_instance(tampered, self.schema)

    def test_missing_verifier_hash_inventory_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.command)
        tampered.pop("verifier_file_hashes", None)
        with self.assertRaisesRegex(ValidationError, "missing required"):
            validate_schema_instance(tampered, self.schema)

    def test_verifier_file_hash_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.command)
        tampered["verifier_file_hashes"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "verification tool hash mismatch"):
            verify_command(
                REPO,
                REPO / "docs/roadmap/phase-0-acceptance-contract.json",
                REPO / "artifacts/phase-0",
                REPO / "artifacts/phase-0/schemas",
                tampered,
            )

    def test_interpreter_hash_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.command)
        tampered["interpreter_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "interpreter executable hash mismatch"):
            verify_command(
                REPO,
                REPO / "docs/roadmap/phase-0-acceptance-contract.json",
                REPO / "artifacts/phase-0",
                REPO / "artifacts/phase-0/schemas",
                tampered,
            )

    def test_running_interpreter_substitution_is_rejected(self) -> None:
        fake_executable = REPO / "docs/roadmap/phase-0-acceptance-contract.json"
        with patch("verify_phase0.sys.executable", str(fake_executable)):
            with self.assertRaisesRegex(ValidationError, "running interpreter executable hash differs"):
                verify_command(
                    REPO,
                    REPO / "docs/roadmap/phase-0-acceptance-contract.json",
                    REPO / "artifacts/phase-0",
                    REPO / "artifacts/phase-0/schemas",
                    self.command,
                )


@unittest.skipUnless((REPO / "artifacts/phase-0/verification-command.json").is_file(), "verification command not frozen")
class EndToEndP001Tests(unittest.TestCase):
    def test_frozen_p0_01_passes_through_launcher(self) -> None:
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(SCRIPTS / "verify_phase0.ps1"), "--contract",
                "docs/roadmap/phase-0-acceptance-contract.json", "--artifacts",
                "artifacts/phase-0", "--stage", "p0-01",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('"status":"PASS"', completed.stdout)


if __name__ == "__main__":
    unittest.main()
