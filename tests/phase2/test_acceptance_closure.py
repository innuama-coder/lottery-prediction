from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lottery_research.phase2.errors import EvidenceMismatch
from lottery_research.phase2.input_validation import sha256
from lottery_research.phase2.schema import validate_payload
from lottery_research.phase2.serialization import canonical_json_bytes
from lottery_research.phase2.workflows import _build_gate_metric_evidence, _effective_e2e_evidence, _verify_deliverable_path_coverage, _verify_e2e_registry, _verify_g0_g1_gate, _verify_run_selections


STAMP = "2026-08-05T00:00:00Z"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def identity(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)}


class AcceptanceClosureTests(unittest.TestCase):
    def _selection(self, root: Path, command: str, *, mismatch_inputs: bool = False) -> dict:
        run_id = f"formal-{command}"
        run_dir = root / "runs" / run_id
        contract = root / "contract.json"
        output = root / f"{command}.json"
        input_path = root / "input.json"
        write_json(contract, {"contract": 1})
        write_json(input_path, {"input": 1})
        write_json(output, {"status": "PASS"})
        input_identity = identity(root, input_path)
        request = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_run_request",
            "run_id": run_id,
            "command": command,
            "argv": [command, "--contract", "contract.json", "--output", f"{command}.json"],
            "created_at_utc": STAMP,
            "contract_identity": identity(root, contract),
            "input_identities": [input_identity],
            "seed_set_id": None,
            "output_path": f"{command}.json",
        }
        request_path = run_dir / "request.json"
        write_json(request_path, request)
        result = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_run_result",
            "run_id": run_id,
            "command": command,
            "terminal": "PASS",
            "exit_code": 0,
            "started_at_utc": STAMP,
            "finished_at_utc": STAMP,
            "request_identity": identity(root, request_path),
            "input_identities": [] if mismatch_inputs else [input_identity],
            "output_identities": [identity(root, output)],
            "metrics": {},
            "errors": [],
        }
        result_path = run_dir / "result.json"
        write_json(result_path, result)
        return {
            "command": command,
            "run_id": run_id,
            "request": identity(root, request_path),
            "result": identity(root, result_path),
            "published_output": identity(root, output),
        }

    def test_run_selection_rejects_request_result_input_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            selections = [self._selection(root, command, mismatch_inputs=command == "power") for command in ("audit", "power", "replay")]
            with self.assertRaisesRegex(EvidenceMismatch, "input identities differ"):
                _verify_run_selections(root, selections)

    def test_deliverable_evidence_must_cover_declared_file_and_directory_contents(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            expected_file = root / "declared.txt"
            directory_file = root / "declared-dir" / "inside.txt"
            wrong_file = root / "wrong.txt"
            for path in (expected_file, directory_file, wrong_file):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(path.name, encoding="utf-8")
            contract = {"deliverables": [{"id": "D2-01", "paths": ["declared.txt", "declared-dir"]}]}
            evidence = {"D2-01": {"id": "D2-01", "evidence": [identity(root, wrong_file)]}}
            with self.assertRaisesRegex(EvidenceMismatch, "does not cover declared path"):
                _verify_deliverable_path_coverage(root, contract, evidence)

    def test_isolated_preaccept_registry_cannot_be_arbitrary_json(self) -> None:
        with self.assertRaises(Exception):
            validate_payload("e2e_registry", {"status": "PENDING", "verdicts": []})

    def test_isolated_registry_rows_must_equal_manifest_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            expected = {"exit_code": 0, "terminal": "PASS"}
            rows = [
                {"id": f"E2E-P2-{index:02d}-case", "status": "PASS", "expected": expected, "observed": expected, "receipt": {"path": "receipt.json", "sha256": "a" * 64}}
                for index in range(1, 11)
            ]
            registry_rows = [dict(row) for row in rows]
            registry_rows[0] = {**registry_rows[0], "status": "PENDING", "observed": None, "receipt": None}
            registry_path = root / "registry.json"
            write_json(registry_path, {"schema_version": "1.0.0", "artifact_type": "phase2_e2e_registry", "status": "PENDING", "created_at_utc": STAMP, "verdicts": registry_rows})
            with self.assertRaisesRegex(EvidenceMismatch, "differ from"):
                _verify_e2e_registry(root, identity(root, registry_path), rows, isolated=True)

    def test_gate_and_metric_verdicts_use_relevant_evidence(self) -> None:
        evidence_by_id = {
            f"D2-{index:02d}": {"id": f"D2-{index:02d}", "evidence": [{"path": f"d2-{index:02d}.json", "sha256": f"{index:064x}"}]}
            for index in range(1, 13)
        }
        manifest_identity = {"path": "final-manifest.json", "sha256": "f" * 64}
        gate_identity = {"path": "g0-g1.json", "sha256": "e" * 64}
        e2e = [
            {"id": f"E2E-{index:02d}", "status": "PASS", "evidence": [{"path": f"e2e-{index:02d}.json", "sha256": f"{100 + index:064x}"}]}
            for index in range(1, 11)
        ]
        contract = {
            "metric_registry": {"test": {"CAL-01": {}, "REP-02": {}, "COV-07": {}}},
            "gates": [
                {"id": "G0", "required_metrics": [], "required_e2e_ids": []},
                {"id": "G1", "required_metrics": [], "required_e2e_ids": []},
                {"id": "G2", "required_metrics": [], "required_e2e_ids": []},
                {"id": "G3", "required_metrics": [], "required_e2e_ids": []},
                {"id": "G4", "required_metrics": ["CAL-01"], "required_e2e_ids": []},
                {"id": "G5", "required_metrics": ["REP-02"], "required_e2e_ids": []},
                {"id": "G6", "required_metrics": ["COV-07"], "required_e2e_ids": ["E2E-01"]},
            ],
        }
        gates, metrics = _build_gate_metric_evidence(contract, evidence_by_id, manifest_identity, gate_identity, e2e)
        gate_map = {row["id"]: {item["path"] for item in row["evidence"]} for row in gates}
        metric_map = {row["id"]: {item["path"] for item in row["evidence"]} for row in metrics}
        self.assertIn("d2-09.json", gate_map["G4"])
        self.assertIn("d2-10.json", metric_map["REP-02"])
        self.assertIn("d2-09.json", metric_map["CAL-01"])
        self.assertTrue({f"e2e-{index:02d}.json" for index in range(1, 11)}.issubset(metric_map["COV-07"]))
        self.assertNotEqual(metric_map["CAL-01"], {"d2-01.json"})

    def test_isolated_e2e01_is_synthesized_once_for_g6_and_cov07(self) -> None:
        manifest_identity = {"path": "isolated-final-manifest.json", "sha256": "f" * 64}
        manifest = {"acceptance_mode": "isolated_e2e"}
        nine = [
            {"id": f"E2E-P2-{index:02d}-case", "status": "PASS", "evidence": [{"path": f"e2e-{index:02d}.json", "sha256": f"{index:064x}"}]}
            for index in range(2, 11)
        ]
        effective = _effective_e2e_evidence(manifest, nine, manifest_identity)
        self.assertEqual(len(effective), 10)
        e2e01 = [row for row in effective if row["id"] == "E2E-P2-01-normal-full-chain"]
        self.assertEqual(e2e01, [{"id": "E2E-P2-01-normal-full-chain", "status": "PASS", "evidence": [manifest_identity]}])

    def test_g0_g1_rejects_empty_frozen_identity_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            gate_path = root / "g0-g1.json"
            write_json(gate_path, {"status": "PASS", "gates": ["G0", "G1"], "checks": {}, "frozen_input_identities": []})
            with self.assertRaisesRegex(EvidenceMismatch, "required eight paths"):
                _verify_g0_g1_gate(root, gate_path)


if __name__ == "__main__":
    unittest.main()
