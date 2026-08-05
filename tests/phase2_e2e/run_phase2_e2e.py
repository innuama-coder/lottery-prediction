"""Independent Phase 2 end-to-end runner and evidence constructors.

The runner never mutates the source project inputs.  Every command executes in a
copied project-shaped root and every aggregate verdict is backed by the immutable
request/result records produced by :mod:`lottery_research.phase2.run_protocol`.

Heavy Monte Carlo cases may share one isolated full-chain run.  Their receipts
must then name the same immutable run-result identity explicitly.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from lottery_research.phase2.input_validation import sha256
from lottery_research.phase2.run_protocol import execute_recorded
from lottery_research.phase2.schema import load_json, validate_payload
from lottery_research.phase2.serialization import canonical_json_bytes


SOURCE_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_REL = Path("docs/roadmap/phase-2-acceptance-contract.json")
INPUT_MANIFEST_REL = Path("artifacts/phase-2/contracts/input-manifest.json")
PREREGISTRATION_REL = Path("artifacts/phase-2/contracts/preregistration.json")
REVIEWER_ASSIGNMENT_REL = Path("artifacts/phase-2/contracts/reviewer-assignment.json")
RULE_DOCUMENT_REL = Path("docs/research/phase-2-input-rule-and-time-contract.md")


# Only files needed by validate-input and the G0/G1 preflight are copied.  Formal
# result paths and the formal G0/G1 gate are intentionally absent.
BASE_ISOLATION_FILES = (
    CONTRACT_REL,
    Path("docs/roadmap/phase-1-acceptance-contract.json"),
    RULE_DOCUMENT_REL,
    Path("docs/research/lottery-autoresearch-technical-strategy.md"),
    Path("artifacts/phase-1/acceptance/phase1-acceptance.json"),
    Path("artifacts/phase-1/baseline-v1/draws.jsonl"),
    Path("artifacts/phase-1/baseline-v1/observations.jsonl"),
    Path("artifacts/phase-1/baseline-v1/manifest.json"),
    Path("tests/phase1/fixtures/spec/spec-bundle-freeze.json"),
    Path("artifacts/phase-2/readiness/p2-00a-readiness.json"),
    Path("artifacts/phase-2/contracts/pre-g0-contract-amendment.json"),
    INPUT_MANIFEST_REL,
    PREREGISTRATION_REL,
    REVIEWER_ASSIGNMENT_REL,
    Path("artifacts/phase-2/contracts/environment-lock.json"),
)

HEAVY_G0_FILES = (Path("artifacts/phase-2/reviews/method-review.json"),)
HEAVY_QUALIFICATION_FILES = (
    Path("artifacts/phase-2/qualification/harness-qualification.json"),
    Path("artifacts/phase-2/qualification/reference-null.bin"),
    Path("artifacts/phase-2/qualification/evaluation-null.bin"),
    Path("artifacts/phase-2/qualification/effect-interval-calibration.json"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _identity(base: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        label = resolved.relative_to(base.resolve()).as_posix()
    except ValueError:
        label = resolved.as_posix()
    return {"path": label, "sha256": sha256(resolved)}


def _copy_file(source_root: Path, destination_root: Path, relative: Path) -> None:
    source = source_root / relative
    if not source.is_file():
        raise FileNotFoundError(f"required isolation input is missing: {relative.as_posix()}")
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _outcome(row: dict[str, Any]) -> dict[str, Any]:
    return {"exit_code": int(row["expected_exit_code"]), "terminal": row["expected_terminal"]}


def _assertion(identifier: str, expected: Any, observed: Any) -> dict[str, Any]:
    if observed != expected:
        raise AssertionError(f"{identifier}: expected {expected!r}, observed {observed!r}")
    return {"id": identifier, "status": "PASS", "expected": expected, "observed": observed}


class Phase2E2ERunner:
    """Create isolated workspaces and produce schema-valid E2E evidence."""

    def __init__(self, source_root: Path, execution_root: Path, *, evidence_root: Path | None = None) -> None:
        self.source_root = source_root.resolve()
        self.execution_root = execution_root.resolve()
        self.evidence_root = (evidence_root or self.source_root).resolve()
        self.contract = load_json(self.source_root / CONTRACT_REL)
        self.case_rows = {row["id"]: row for row in self.contract["required_e2e_cases"]}
        if len(self.case_rows) != 10:
            raise ValueError("Phase 2 contract must register exactly ten unique E2E cases")

    def prepare_isolated_root(self, case_id: str, *, suffix: str = "workspace") -> Path:
        if case_id not in self.case_rows:
            raise KeyError(case_id)
        root = self.execution_root / case_id / suffix
        if root.exists():
            raise FileExistsError(f"isolated E2E root already exists: {root}")
        root.mkdir(parents=True)
        for relative in BASE_ISOLATION_FILES:
            _copy_file(self.source_root, root, relative)
        return root

    @staticmethod
    def validate_argv(root: Path) -> list[str]:
        return [
            "validate-input",
            "--contract", str(root / CONTRACT_REL),
            "--input-rule-contract", str(root / RULE_DOCUMENT_REL),
            "--input-manifest", str(root / INPUT_MANIFEST_REL),
            "--preregistration", str(root / PREREGISTRATION_REL),
            "--reviewer-assignment", str(root / REVIEWER_ASSIGNMENT_REL),
        ]

    def _recorded(self, root: Path, run_id: str, argv: Sequence[str]) -> tuple[dict[str, Any], Path, Path]:
        code, cli_result, request_path, result_path = execute_recorded(
            root,
            run_id,
            argv,
            root / "artifacts/phase-2/runs",
        )
        if code != int(cli_result["exit_code"]):
            raise AssertionError("run protocol returned inconsistent exit codes")
        return cli_result, request_path, result_path

    def _receipt(
        self,
        case_id: str,
        isolated_root: Path,
        *,
        started_at: str,
        finished_at: str,
        observed: dict[str, Any],
        result_paths: Sequence[Path],
        input_paths: Sequence[Path],
        output_paths: Sequence[Path],
        assertions: Sequence[dict[str, Any]],
        receipt_path: Path | None = None,
    ) -> Path:
        registered = self.case_rows[case_id]
        expected = _outcome(registered)
        if observed != expected:
            raise AssertionError(f"{case_id}: aggregate outcome does not match contract")
        outcome_assertions = [
            _assertion("observed-exit-code", expected["exit_code"], observed["exit_code"]),
            _assertion("observed-terminal", expected["terminal"], observed["terminal"]),
        ]
        all_assertions = [*outcome_assertions, *assertions]
        assertion_ids = [row["id"] for row in all_assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError(f"duplicate assertion id in {case_id}")
        payload = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_e2e_receipt",
            "case_id": case_id,
            "owner": registered["owner"],
            "gate": registered["gate"],
            "status": "PASS",
            "isolated_root": isolated_root.resolve().as_posix(),
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "expected": expected,
            "observed": observed,
            "run_identities": [_identity(self.evidence_root, path) for path in result_paths],
            "assertions": all_assertions,
            "input_identities": [_identity(self.evidence_root, path) for path in input_paths],
            "output_identities": [_identity(self.evidence_root, path) for path in output_paths],
        }
        validate_payload("e2e_receipt", payload)
        destination = receipt_path or isolated_root / "artifacts/phase-2/e2e/receipt.json"
        _write_json(destination, payload)
        return destination

    def run_validate_fault(
        self,
        case_id: str,
        mutator: Callable[[dict[str, Any]], None],
    ) -> Path:
        """Run one of E2E02..05 against a controlled manifest copy."""

        allowed = {
            "E2E-P2-02-input-tamper",
            "E2E-P2-03-observation-count-inflation",
            "E2E-P2-04-rule-segment-mixing",
            "E2E-P2-05-point-in-time-leakage",
        }
        if case_id not in allowed:
            raise ValueError(f"not a validate-input fault case: {case_id}")
        root = self.prepare_isolated_root(case_id)
        manifest_path = root / INPUT_MANIFEST_REL
        manifest = load_json(manifest_path)
        mutator(manifest)
        _write_json(manifest_path, manifest)
        run_id = case_id.lower().replace("e2e-p2-", "e2e-")
        cli, request_path, result_path = self._recorded(root, run_id, self.validate_argv(root))
        result = load_json(result_path)
        expected = _outcome(self.case_rows[case_id])
        assertions = [
            _assertion("process-exit-code", expected["exit_code"], result["exit_code"]),
            _assertion("cli-terminal", expected["terminal"], result["terminal"]),
            _assertion("formal-audit-output-count", 0, int((root / "artifacts/phase-2/results/historical-audit.json").exists())),
            _assertion("formal-power-output-count", 0, int((root / "artifacts/phase-2/results/power-envelope.json").exists())),
        ]
        return self._receipt(
            case_id,
            root,
            started_at=result["started_at_utc"],
            finished_at=result["finished_at_utc"],
            observed={"exit_code": cli["exit_code"], "terminal": cli["terminal"]},
            result_paths=[result_path],
            input_paths=[root / CONTRACT_REL, manifest_path, request_path],
            output_paths=[result_path, result_path.parent / "stdout.json", result_path.parent / "stderr.txt"],
            assertions=assertions,
        )

    def run_light_validation_faults(self) -> list[Path]:
        def input_tamper(payload: dict[str, Any]) -> None:
            payload["upstream"]["draws"]["sha256"] = "0" * 64

        def inflate_observations(payload: dict[str, Any]) -> None:
            payload["statistical_unit"] = "one DrawRecord and each SourceObservation are independent statistical units"

        def mix_rule_segment(payload: dict[str, Any]) -> None:
            segment = copy.deepcopy(payload["game_rule_maps"][0]["documented_draw_process_segments"][0])
            segment.update(id="injected-one-issue-overlap", issue_end=segment["issue_start"])
            payload["game_rule_maps"][0]["documented_draw_process_segments"].append(segment)

        def remove_leakage_guard(payload: dict[str, Any]) -> None:
            payload["forbidden_statistical_matrix_fields"].remove("future_draw_numbers")

        cases = (
            ("E2E-P2-02-input-tamper", input_tamper),
            ("E2E-P2-03-observation-count-inflation", inflate_observations),
            ("E2E-P2-04-rule-segment-mixing", mix_rule_segment),
            ("E2E-P2-05-point-in-time-leakage", remove_leakage_guard),
        )
        return [self.run_validate_fault(case_id, mutator) for case_id, mutator in cases]

    @staticmethod
    def _stage_replay_tamper_preconditions(root: Path) -> Path:
        """Stage only the PASS sentinels needed to reach replay hash preflight.

        The case deliberately fails before statistical replay.  These sentinels
        are confined to the fault-injection workspace and cannot be selected as
        formal research outputs.
        """

        for relative, artifact_type in (
            (Path("artifacts/phase-2/qualification/harness-qualification.json"), "phase2_harness_qualification"),
            (Path("artifacts/phase-2/results/historical-audit.json"), "phase2_historical_audit"),
            (Path("artifacts/phase-2/results/power-envelope.json"), "phase2_power_envelope"),
        ):
            _write_json(root / relative, {"schema_version": "1.0.0", "artifact_type": artifact_type, "status": "PASS"})
        preregistration = root / PREREGISTRATION_REL
        evidence_manifest = root / "artifacts/phase-2/contracts/e2e06-replay-evidence.json"
        _write_json(
            evidence_manifest,
            {
                "schema_version": "1.0.0",
                "artifact_type": "phase2_replay_evidence_manifest",
                "status": "frozen",
                "evidence": [_identity(root, preregistration)],
            },
        )
        return evidence_manifest

    def run_preregistration_tamper(self) -> Path:
        """Execute all seven registered E2E06 tamper scopes, aggregate once."""

        case_id = "E2E-P2-06-post-result-preregistration-tamper"

        def alpha(row: dict[str, Any]) -> None:
            row["global_alpha"] = 0.051

        def joint_null(row: dict[str, Any]) -> None:
            row["joint_null"] += " [tampered]"

        def tests(row: dict[str, Any]) -> None:
            row["test_registry"][0]["statistic"] += " [tampered]"

        def practical(row: dict[str, Any]) -> None:
            row["practical_effect_registry"][0]["practical_null_upper"] += 0.001

        def effect_grid(row: dict[str, Any]) -> None:
            row["effect_grids"]["marginal_inclusion"][1] += 0.001

        def sample_grid(row: dict[str, Any]) -> None:
            row["sample_size_grid"][-1] += 1

        def monte_carlo(row: dict[str, Any]) -> None:
            row["monte_carlo_design"]["interval_method"] += " [tampered]"

        mutations = {
            "alpha": alpha,
            "joint-null": joint_null,
            "test-registry": tests,
            "practical-effect-registry": practical,
            "effect-grid": effect_grid,
            "sample-size-grid": sample_grid,
            "monte-carlo-method": monte_carlo,
        }
        expected = _outcome(self.case_rows[case_id])
        results: list[Path] = []
        requests: list[Path] = []
        preregistrations: list[Path] = []
        assertions: list[dict[str, Any]] = []
        start_times: list[str] = []
        finish_times: list[str] = []
        case_root = self.execution_root / case_id
        for name, mutate in mutations.items():
            root = self.prepare_isolated_root(case_id, suffix=name)
            evidence_manifest = self._stage_replay_tamper_preconditions(root)
            preregistration_path = root / PREREGISTRATION_REL
            preregistration = load_json(preregistration_path)
            mutate(preregistration)
            _write_json(preregistration_path, preregistration)
            output = root / "artifacts/phase-2/replay/should-not-exist.json"
            cli, request_path, result_path = self._recorded(
                root,
                f"e2e06-{name}",
                [
                    "replay",
                    "--contract", str(root / CONTRACT_REL),
                    "--evidence-manifest", str(evidence_manifest),
                    "--output", str(output),
                    "--seed-set", "reserved-replay",
                ],
            )
            result = load_json(result_path)
            assertions.extend(
                [
                    _assertion(f"{name}-exit-code", expected["exit_code"], result["exit_code"]),
                    _assertion(f"{name}-terminal", expected["terminal"], result["terminal"]),
                    _assertion(f"{name}-no-replay-output", False, output.exists()),
                ]
            )
            if cli["terminal"] != expected["terminal"]:
                raise AssertionError(f"{name}: CLI terminal mismatch")
            results.append(result_path)
            requests.append(request_path)
            preregistrations.append(preregistration_path)
            start_times.append(result["started_at_utc"])
            finish_times.append(result["finished_at_utc"])
        assertions.append(_assertion("tamper-scope-coverage", 7, len(results)))
        return self._receipt(
            case_id,
            case_root,
            started_at=min(start_times),
            finished_at=max(finish_times),
            observed=expected,
            result_paths=results,
            input_paths=[self.source_root / CONTRACT_REL, *requests, *preregistrations],
            output_paths=results,
            assertions=assertions,
            receipt_path=case_root / "receipt.json",
        )

    def consume_shared_pass_runs(
        self,
        case_id: str,
        isolated_root: Path,
        result_paths: Sequence[Path],
        *,
        assertions: Sequence[dict[str, Any]],
        input_paths: Sequence[Path],
        output_paths: Sequence[Path],
        receipt_path: Path | None = None,
    ) -> Path:
        """Create one heavy-case receipt from already executed shared runs.

        This is the only supported reuse path for E2E01/07/08/09.  Every shared
        run must be an immutable schema-valid PASS result, and the receipt names
        each shared result identity.
        """

        if case_id not in {
            "E2E-P2-01-normal-full-chain",
            "E2E-P2-07-uniform-calibration",
            "E2E-P2-08-injected-bias-recovery",
            "E2E-P2-09-independent-seed-replay",
        }:
            raise ValueError("shared PASS runs are only valid for heavy E2E cases")
        rows = [load_json(path) for path in result_paths]
        for row in rows:
            validate_payload("run_result", row)
            if row["exit_code"] != 0 or row["terminal"] != "PASS":
                raise AssertionError(f"shared run is not PASS: {row['run_id']}")
        expected = _outcome(self.case_rows[case_id])
        return self._receipt(
            case_id,
            isolated_root,
            started_at=min(row["started_at_utc"] for row in rows),
            finished_at=max(row["finished_at_utc"] for row in rows),
            observed=expected,
            result_paths=result_paths,
            input_paths=input_paths,
            output_paths=output_paths,
            assertions=[*assertions, _assertion("shared-run-pass-count", len(rows), len(result_paths))],
            receipt_path=receipt_path,
        )

    def _resolve_evidence_identity(self, item: dict[str, str]) -> Path:
        value = Path(item["path"])
        path = value if value.is_absolute() else self.evidence_root / value
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"receipt evidence identity mismatch: {item['path']}")
        return path

    def verify_receipt_evidence(self, receipt: dict[str, Any]) -> None:
        """Rehash every identity embedded in one receipt before aggregation."""

        run_result_count = 0
        matching_outcomes = 0
        for group in ("run_identities", "input_identities", "output_identities"):
            for item in receipt[group]:
                path = self._resolve_evidence_identity(item)
                if group == "run_identities":
                    payload = load_json(path)
                    validate_payload("run_result", payload)
                    run_result_count += 1
                    matching_outcomes += int(
                        payload["exit_code"] == receipt["observed"]["exit_code"]
                        and payload["terminal"] == receipt["observed"]["terminal"]
                    )
        if run_result_count == 0:
            raise ValueError(f"receipt contains no validated run result: {receipt['case_id']}")
        if matching_outcomes == 0:
            raise ValueError(f"receipt has no run result matching its aggregate outcome: {receipt['case_id']}")

    def materialize_receipt_for_root(
        self,
        receipt_path: Path,
        isolated_root: Path,
        destination: Path,
    ) -> Path:
        """Copy a receipt into an isolated root with explicit evidence paths.

        Existing formal receipts commonly use paths relative to the source
        project.  Final acceptance resolves receipt identities relative to the
        isolated project root, so an unmodified copy would ambiguously point at
        files that do not exist there.  This method first rehashes every source
        identity and then emits a schema-equivalent receipt whose identities are
        absolute, immutable paths to those same verified files.
        """

        receipt = load_json(receipt_path)
        validate_payload("e2e_receipt", receipt)
        self.verify_receipt_evidence(receipt)
        materialized = copy.deepcopy(receipt)
        for group in ("run_identities", "input_identities", "output_identities"):
            for item in materialized[group]:
                verified = self._resolve_evidence_identity(item)
                item["path"] = verified.resolve().as_posix()
        materialized["isolated_root"] = receipt["isolated_root"]
        validate_payload("e2e_receipt", materialized)
        _write_json(destination, materialized)
        return destination

    @staticmethod
    def build_replay_evidence_manifest(
        isolated_root: Path,
        *,
        audit_request: Path,
        audit_result: Path,
        power_request: Path,
        power_result: Path,
        output_path: Path,
    ) -> Path:
        """Freeze the exact isolated inputs consumed by the replay command."""

        relatives = (
            CONTRACT_REL,
            RULE_DOCUMENT_REL,
            Path("artifacts/phase-2/contracts/pre-g0-contract-amendment.json"),
            INPUT_MANIFEST_REL,
            PREREGISTRATION_REL,
            REVIEWER_ASSIGNMENT_REL,
            Path("artifacts/phase-2/reviews/method-review.json"),
            Path("artifacts/phase-2/reviews/nonstatistical-patch-review.json"),
            Path("artifacts/phase-2/contracts/environment-lock.json"),
            Path("artifacts/phase-2/gates/g0-g1.json"),
            Path("artifacts/phase-2/qualification/harness-qualification.json"),
            Path("artifacts/phase-2/qualification/reference-null.bin"),
            Path("artifacts/phase-2/qualification/evaluation-null.bin"),
            Path("artifacts/phase-2/qualification/effect-interval-calibration.json"),
            Path("artifacts/phase-2/results/historical-audit.json"),
            Path("artifacts/phase-2/results/power-envelope.json"),
        )
        evidence_paths = [isolated_root / relative for relative in relatives]
        evidence_paths.extend((audit_request, audit_result, power_request, power_result))
        payload = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_replay_evidence_manifest",
            "status": "frozen",
            "evidence": [_identity(isolated_root, path) for path in evidence_paths],
        }
        _write_json(output_path, payload)
        return output_path

    def collect_deliverable_evidence(self, isolated_root: Path) -> dict[str, list[Path]]:
        """Enumerate complete D2-01..D2-11 path coverage from the contract."""

        evidence: dict[str, list[Path]] = {}
        for deliverable in self.contract["deliverables"]:
            identifier = deliverable["id"]
            if identifier not in {f"D2-{index:02d}" for index in range(1, 12)}:
                continue
            paths: list[Path] = []
            for declared in deliverable["paths"]:
                target = isolated_root / declared
                if target.is_file():
                    paths.append(target)
                elif target.is_dir():
                    paths.extend(
                        path
                        for path in sorted(target.rglob("*"))
                        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
                    )
                else:
                    raise FileNotFoundError(f"missing isolated deliverable path: {identifier} {declared}")
            evidence[identifier] = paths
        return evidence

    def build_isolated_preaccept_manifest(
        self,
        isolated_root: Path,
        *,
        run_selections: dict[str, tuple[str, Path, Path, Path]],
        deliverable_evidence: dict[str, Sequence[Path]],
        completed_receipts: Sequence[Path],
        signal_status: str,
        output_path: Path,
        signer_id: str = "phase2-e2e-runner",
    ) -> Path:
        """Build the only valid pre-accept form for isolated E2E01.

        E2E01 is PENDING with no observed outcome or receipt.  The other nine
        cases must already have schema-valid, fully rehashed PASS receipts.
        """

        expected_commands = {"audit", "power", "replay"}
        if set(run_selections) != expected_commands:
            raise ValueError("isolated preaccept requires audit, power and replay selections")
        expected_deliverables = {f"D2-{index:02d}" for index in range(1, 12)}
        if set(deliverable_evidence) != expected_deliverables:
            raise ValueError("isolated preaccept requires evidence for D2-01 through D2-11")
        by_case: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path in completed_receipts:
            receipt = load_json(path)
            validate_payload("e2e_receipt", receipt)
            self.verify_receipt_evidence(receipt)
            if receipt["case_id"] in by_case:
                raise ValueError(f"duplicate completed receipt: {receipt['case_id']}")
            by_case[receipt["case_id"]] = (path, receipt)
        e2e01 = "E2E-P2-01-normal-full-chain"
        expected_completed = set(self.case_rows) - {e2e01}
        if set(by_case) != expected_completed:
            raise ValueError(f"isolated preaccept receipt coverage mismatch: missing={sorted(expected_completed - set(by_case))}")

        verdicts: list[dict[str, Any]] = []
        for case_id in sorted(self.case_rows):
            expected = _outcome(self.case_rows[case_id])
            if case_id == e2e01:
                verdicts.append({"id": case_id, "status": "PENDING", "expected": expected, "observed": None, "receipt": None})
            else:
                path, receipt = by_case[case_id]
                if receipt["observed"] != expected:
                    raise ValueError(f"completed receipt outcome mismatch: {case_id}")
                verdicts.append(
                    {
                        "id": case_id,
                        "status": "PASS",
                        "expected": expected,
                        "observed": receipt["observed"],
                        "receipt": _identity(isolated_root, path),
                    }
                )
        preaccept_index = isolated_root / "artifacts/phase-2/e2e/preaccept-index.json"
        _write_json(
            preaccept_index,
            {
                "schema_version": "1.0.0",
                "artifact_type": "phase2_e2e_registry",
                "status": "PENDING",
                "created_at_utc": _now(),
                "verdicts": verdicts,
            },
        )
        validate_payload("e2e_registry", load_json(preaccept_index))
        selections = []
        for command in sorted(run_selections):
            run_id, request, result, published = run_selections[command]
            selections.append(
                {
                    "command": command,
                    "run_id": run_id,
                    "request": _identity(isolated_root, request),
                    "result": _identity(isolated_root, result),
                    "published_output": _identity(isolated_root, published),
                }
            )
        deliverables = [
            {
                "id": deliverable_id,
                "evidence": [_identity(isolated_root, path) for path in deliverable_evidence[deliverable_id]],
            }
            for deliverable_id in sorted(expected_deliverables)
        ]
        deliverables.append({"id": "D2-12", "evidence": []})
        timestamp = _now()
        payload = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_final_evidence_manifest",
            "status": "frozen",
            "acceptance_mode": "isolated_e2e",
            "frozen_at_utc": timestamp,
            "contract_identity": _identity(isolated_root, isolated_root / CONTRACT_REL),
            "signal_status": signal_status,
            "run_selections": selections,
            "deliverables": deliverables,
            "e2e_registry_identity": _identity(isolated_root, preaccept_index),
            "e2e_verdicts": verdicts,
            "blocking_findings": [],
            "signature": {"signer_id": signer_id, "signed": True, "signed_at_utc": timestamp},
        }
        validate_payload("final_evidence_manifest", payload)
        _write_json(output_path, payload)
        return output_path

    def build_registry(self, receipt_paths: Sequence[Path], output_path: Path) -> Path:
        if len(receipt_paths) != 10:
            raise ValueError("E2E registry requires exactly ten receipts")
        verdicts = []
        seen: set[str] = set()
        for receipt_path in receipt_paths:
            receipt = load_json(receipt_path)
            validate_payload("e2e_receipt", receipt)
            self.verify_receipt_evidence(receipt)
            case_id = receipt["case_id"]
            if case_id in seen:
                raise ValueError(f"duplicate aggregate E2E receipt: {case_id}")
            seen.add(case_id)
            registered = self.case_rows.get(case_id)
            if registered is None or receipt["expected"] != _outcome(registered):
                raise ValueError(f"receipt does not match the frozen contract: {case_id}")
            verdicts.append(
                {
                    "id": case_id,
                    "status": "PASS",
                    "expected": receipt["expected"],
                    "observed": receipt["observed"],
                    "receipt": _identity(self.evidence_root, receipt_path),
                }
            )
        if seen != set(self.case_rows):
            raise ValueError(f"E2E registry coverage mismatch: missing={sorted(set(self.case_rows) - seen)}")
        payload = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_e2e_registry",
            "status": "PASS",
            "created_at_utc": _now(),
            "verdicts": sorted(verdicts, key=lambda row: row["id"]),
        }
        validate_payload("e2e_registry", payload)
        _write_json(output_path, payload)
        return output_path

    def run_power_interruption_resume(self) -> Path:
        """Execute the formal E2E10 power interruption/resume/control chain.

        This method is intentionally not used by lightweight unit tests.  It
        consumes the frozen full Monte Carlo budget and is the only method here
        allowed to emit an E2E10 PASS receipt.
        """

        case_id = "E2E-P2-10-interruption-and-idempotent-resume"
        root = self.prepare_isolated_root(case_id)
        for relative in HEAVY_G0_FILES:
            _copy_file(self.source_root, root, relative)
        for relative in HEAVY_QUALIFICATION_FILES:
            _copy_file(self.source_root, root, relative)
        checkpoint_root = root / "artifacts/phase-2/e2e10/checkpoints"
        interrupted_output = root / "artifacts/phase-2/e2e10/resumed-power.json"
        common = [
            "power",
            "--contract", str(root / CONTRACT_REL),
            "--input-manifest", str(root / INPUT_MANIFEST_REL),
            "--preregistration", str(root / PREREGISTRATION_REL),
            "--output", str(interrupted_output),
            "--checkpoint-root", str(checkpoint_root),
        ]
        raw_dir = root / "artifacts/phase-2/runs/e2e10-controlled-interruption"
        raw_dir.mkdir(parents=True)
        started = _now()
        process = subprocess.run(
            [sys.executable, "-m", "lottery_research.phase2", *common, "--interrupt-after-batches", "1"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        finished = _now()
        stdout_path = raw_dir / "stdout.bin"
        stderr_path = raw_dir / "stderr.bin"
        stdout_path.write_bytes(process.stdout)
        stderr_path.write_bytes(process.stderr)
        normal_cli_terminal: str | None = None
        try:
            normal_cli_terminal = json.loads(process.stdout.decode("utf-8")).get("terminal")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        interruption_record = raw_dir / "interruption.json"
        _write_json(
            interruption_record,
            {
                "artifact_type": "phase2_controlled_interruption",
                "started_at_utc": started,
                "finished_at_utc": finished,
                "native_return_code": process.returncode,
                "normal_cli_terminal": normal_cli_terminal,
                "stdout": _identity(self.evidence_root, stdout_path),
                "stderr": _identity(self.evidence_root, stderr_path),
            },
        )
        checkpoint_batches = list(checkpoint_root.rglob("batch-*.json"))
        if process.returncode == 0 or interrupted_output.exists() or normal_cli_terminal is not None:
            raise AssertionError(
                f"controlled interruption did not interrupt computation: "
                f"return_code={process.returncode}, terminal={normal_cli_terminal}"
            )
        if not checkpoint_batches:
            raise AssertionError("controlled interruption wrote no checkpoint batch")

        resume_cli, resume_request, resume_result = self._recorded(root, "e2e10-resume", common)
        if resume_cli["exit_code"] != 0 or resume_cli["terminal"] != "PASS":
            raise AssertionError(f"resume run did not PASS: {resume_cli}")
        control_output = root / "artifacts/phase-2/e2e10/uninterrupted-power.json"
        control_checkpoint = root / "artifacts/phase-2/e2e10/control-checkpoints"
        control_argv = [*common]
        control_argv[control_argv.index(str(interrupted_output))] = str(control_output)
        control_argv[control_argv.index(str(checkpoint_root))] = str(control_checkpoint)
        control_cli, control_request, control_result = self._recorded(root, "e2e10-uninterrupted", control_argv)
        if control_cli["exit_code"] != 0 or control_cli["terminal"] != "PASS":
            raise AssertionError(f"uninterrupted control run did not PASS: {control_cli}")
        resumed = load_json(interrupted_output)
        control = load_json(control_output)
        checkpoint = resumed["checkpoint_resume"]
        assertions = [
            _assertion("first-process-normal-terminal", None, None),
            _assertion("resume-exit", 0, load_json(resume_result)["exit_code"]),
            _assertion("control-exit", 0, load_json(control_result)["exit_code"]),
            _assertion("missing-batches", 0, checkpoint["missing_batches"]),
            _assertion("duplicate-batches", 0, checkpoint["duplicate_batches"]),
            _assertion("reused-batches-positive", True, checkpoint["reused_batches"] > 0),
            _assertion("normalized-hash-match", control["normalized_artifact"]["sha256"], resumed["normalized_artifact"]["sha256"]),
        ]
        expected = _outcome(self.case_rows[case_id])
        return self._receipt(
            case_id,
            root,
            started_at=started,
            finished_at=load_json(control_result)["finished_at_utc"],
            observed=expected,
            result_paths=[resume_result, control_result],
            input_paths=[root / CONTRACT_REL, resume_request, control_request],
            output_paths=[interrupted_output, control_output, interruption_record],
            assertions=assertions,
        )

    def build_g4_receipts_from_e2e10(self, isolated_root: Path) -> tuple[Path, Path]:
        """Derive independent E2E07 and E2E08 receipts from one immutable control run."""

        qualification = isolated_root / "artifacts/phase-2/qualification/harness-qualification.json"
        power_output = isolated_root / "artifacts/phase-2/e2e10/uninterrupted-power.json"
        request_path = isolated_root / "artifacts/phase-2/runs/e2e10-uninterrupted/request.json"
        result_path = isolated_root / "artifacts/phase-2/runs/e2e10-uninterrupted/result.json"
        environment = isolated_root / "artifacts/phase-2/contracts/environment-lock.json"
        for path in (qualification, power_output, request_path, result_path, environment):
            if not path.is_file():
                raise FileNotFoundError(f"E2E10 shared G4 evidence is missing: {path}")
        qualification_payload = load_json(qualification)
        power = load_json(power_output)
        environment_payload = load_json(environment)
        if environment_payload["source_schema_bundle"]["sha256"] != "fcec05d06b19cba2914dabba74d16bbdcb06f32ca42f1d2a726b7c05e9dc31ae":
            raise AssertionError("shared G4 run is not bound to the final source/schema bundle")
        if qualification_payload["status"] != "PASS" or power["status"] != "PASS":
            raise AssertionError("shared qualification or power artifact is not PASS")
        metrics = power["metrics"]
        common_inputs = [isolated_root / CONTRACT_REL, environment, qualification, request_path]
        common_outputs = [power_output, result_path]
        result_paths = [result_path]

        e2e07_assertions = [
            _assertion("CAL-01-upper-at-most-0.06", True, metrics["CAL-01"] <= 0.06),
            _assertion("CAL-02-half-width-at-most-0.005", True, metrics["CAL-02"] <= 0.005),
            _assertion("CAL-03-lower-at-least-0.93", True, metrics["CAL-03"] >= 0.93),
            _assertion("CAL-04-promotions-zero", 0, metrics["CAL-04"]),
            _assertion("power-terminal-status", "PASS", power["status"]),
            _assertion("final-source-schema-bundle", "fcec05d06b19cba2914dabba74d16bbdcb06f32ca42f1d2a726b7c05e9dc31ae", environment_payload["source_schema_bundle"]["sha256"]),
        ]
        e2e08_assertions = [
            _assertion("QUAL-01-recovery-rate", 1.0, qualification_payload["metrics"]["QUAL-01"]),
            _assertion("qualification-positive-direction-match", 1.0, metrics["QUAL-01"]),
            _assertion("qualification-scenarios-all-pass", True, all(row["status"] == "PASS" for row in qualification_payload["scenarios"])),
            _assertion("registered-power-grid-points", 240, len(power["grid"])),
            _assertion("required-n-result-count", 40, len(power["required_n"])),
            _assertion("required-n-or-not-identified-coverage", 1.0, metrics["POW-06"]["coverage"]),
            _assertion("qualifying-target-grid-point-exists", True, any(row["simultaneous_95_lower"] >= 0.80 for row in power["grid"])),
            _assertion("unsimulated-interpolation-count", 0, metrics["POW-06"]["unsimulated_interpolation"]),
            _assertion("cross-game-pooling-count", 0, metrics["POW-06"]["cross_game_pooling"]),
            _assertion("final-source-schema-bundle", "fcec05d06b19cba2914dabba74d16bbdcb06f32ca42f1d2a726b7c05e9dc31ae", environment_payload["source_schema_bundle"]["sha256"]),
        ]
        receipt_root = isolated_root / "artifacts/phase-2/e2e"
        e2e07 = self.consume_shared_pass_runs(
            "E2E-P2-07-uniform-calibration",
            isolated_root,
            result_paths,
            assertions=e2e07_assertions,
            input_paths=common_inputs,
            output_paths=common_outputs,
            receipt_path=receipt_root / "e2e07-receipt.json",
        )
        e2e08 = self.consume_shared_pass_runs(
            "E2E-P2-08-injected-bias-recovery",
            isolated_root,
            result_paths,
            assertions=e2e08_assertions,
            input_paths=common_inputs,
            output_paths=common_outputs,
            receipt_path=receipt_root / "e2e08-receipt.json",
        )
        return e2e07, e2e08


def _protocol_interrupt_probe(checkpoint: Path) -> int:
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("batch-00000-complete", encoding="utf-8")
    raise KeyboardInterrupt("controlled test-only protocol probe")


def _protocol_resume_probe(checkpoint: Path) -> int:
    if checkpoint.read_text(encoding="utf-8") != "batch-00000-complete":
        return 2
    return 0


def run_interruption_protocol_probe(directory: Path) -> dict[str, Any]:
    """Lightweight process-semantic probe; never emits an E2E10 receipt."""

    checkpoint = directory / "probe-checkpoint.txt"
    first = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "_interrupt-probe", str(checkpoint)],
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "_resume-probe", str(checkpoint)],
        capture_output=True,
        check=False,
    )
    return {
        "artifact_type": "phase2_e2e10_protocol_probe_not_acceptance_evidence",
        "first_native_return_code": first.returncode,
        "first_normal_cli_terminal": None,
        "checkpoint_exists": checkpoint.is_file(),
        "resume_return_code": second.returncode,
        "eligible_for_e2e10_receipt": False,
    }


def _parse_paths(values: Iterable[str]) -> list[Path]:
    return [Path(value).resolve() for value in values]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    light = subparsers.add_parser("light", help="run E2E02..06 in isolated roots")
    light.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    light.add_argument("--execution-root", type=Path, required=True)

    registry = subparsers.add_parser("registry", help="build the unique ten-case registry")
    registry.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    registry.add_argument("--execution-root", type=Path, required=True)
    registry.add_argument("--output", type=Path, required=True)
    registry.add_argument("receipts", nargs=10)

    e2e10 = subparsers.add_parser("e2e10", help="run the full heavy interruption/resume case")
    e2e10.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    e2e10.add_argument("--execution-root", type=Path, required=True)

    g4_receipts = subparsers.add_parser("g4-receipts", help="derive E2E07/08 receipts from a completed E2E10 control run")
    g4_receipts.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    g4_receipts.add_argument("--execution-root", type=Path, required=True)
    g4_receipts.add_argument("--isolated-root", type=Path, required=True)

    probe = subparsers.add_parser("probe", help="run the non-acceptance interruption protocol probe")
    probe.add_argument("--directory", type=Path, required=True)

    internal_interrupt = subparsers.add_parser("_interrupt-probe")
    internal_interrupt.add_argument("checkpoint", type=Path)
    internal_resume = subparsers.add_parser("_resume-probe")
    internal_resume.add_argument("checkpoint", type=Path)

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "_interrupt-probe":
        return _protocol_interrupt_probe(args.checkpoint)
    if args.command == "_resume-probe":
        return _protocol_resume_probe(args.checkpoint)
    if args.command == "probe":
        payload = run_interruption_protocol_probe(args.directory)
        sys.stdout.buffer.write(canonical_json_bytes(payload))
        return 0
    runner = Phase2E2ERunner(args.source_root, args.execution_root)
    if args.command == "light":
        paths = [*runner.run_light_validation_faults(), runner.run_preregistration_tamper()]
        sys.stdout.buffer.write(canonical_json_bytes({"status": "PASS", "receipts": [path.as_posix() for path in paths]}))
        return 0
    if args.command == "e2e10":
        path = runner.run_power_interruption_resume()
        sys.stdout.buffer.write(canonical_json_bytes({"status": "PASS", "receipt": path.as_posix()}))
        return 0
    if args.command == "g4-receipts":
        e2e07_path, e2e08_path = runner.build_g4_receipts_from_e2e10(args.isolated_root.resolve())
        sys.stdout.buffer.write(
            canonical_json_bytes(
                {"status": "PASS", "receipts": [e2e07_path.as_posix(), e2e08_path.as_posix()]}
            )
        )
        return 0
    runner.build_registry(_parse_paths(args.receipts), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
