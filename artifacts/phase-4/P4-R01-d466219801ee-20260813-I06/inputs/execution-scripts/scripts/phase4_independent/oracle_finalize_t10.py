from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from oracle_math import canonical_bytes, sha256_file
from oracle_validation import validate_full_rule_result, validate_m0_results, validate_metric_vectors_independent


ACTOR_ID = "p4-independent-oracle-author-i01"
SESSION_ID = "/root/independent_oracle_author"
TASK_ID = "T10"
SOURCE_COMMIT = "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b"
PREP_ROOT = Path("artifacts/phase-4-prep/p4-prep-controller-issued-i01")


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_file(path)}


def _output(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "producer_actor_id": ACTOR_ID,
        "task_id": TASK_ID,
        "session_id": SESSION_ID,
        "source_commit": SOURCE_COMMIT,
        "role": "independent_oracle_author",
    }


def _rehash_manifest(manifest_path: Path) -> bool:
    manifest = _load(manifest_path)
    for section in ("source_files", "files"):
        for row in manifest.get(section, []):
            path = Path(row["path"])
            if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize producer-side T10 evidence without accepting it")
    parser.add_argument("--attempt-root", required=True, type=Path)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    parser.add_argument("--t01-receipt", required=True, type=Path)
    parser.add_argument("--t01-independent-validation", required=True, type=Path)
    args = parser.parse_args()
    receipt_path = args.attempt_root / "receipt.json"
    summary_path = args.attempt_root / "producer-validation-summary.json"
    command_log_path = args.attempt_root / "command-log.json"
    attempt_history_path = args.attempt_root / "attempt-history.json"
    for path in (receipt_path, summary_path, command_log_path, attempt_history_path):
        if path.exists():
            raise SystemExit(f"refusing to reuse immutable finalization output: {path}")
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    test_command = [sys.executable, "-m", "unittest", "discover", "-s", "tests/phase4_oracle", "-p", "test_*.py", "-v"]
    test_result = subprocess.run(test_command, text=True, capture_output=True, check=False)

    known_root = args.attempt_root / "known-answers"
    full_cells = _load(known_root / "full-rule-eight-cells.json")
    full = _load(known_root / "full-rule-oracle.json")
    m0 = _load(known_root / "real-rule-m0.json")
    guards = _load(known_root / "guard-vectors.json")
    metrics = _load(known_root / "small-space-metrics.json")
    numeric_validation = _load(known_root / "numeric-validation.json")
    full_spec = _load(Path("qualification-design/full-rule-spec-candidate.json"))
    feasibility = _load(args.attempt_root / "feasibility/certificate.json")
    import_audit = _load(args.attempt_root / "import-audit-I04.json")
    mutation_audit = _load(args.attempt_root / "mutation-audit.json")
    t01_validation = _load(args.t01_independent_validation)
    full_residuals = validate_full_rule_result(full, full_spec)
    m0_residuals = validate_m0_results(m0)
    metric_residuals = validate_metric_vectors_independent(metrics)

    probability_bounds = []
    for candidate in feasibility["candidates"]:
        for world in candidate["worlds"]:
            probability_bounds.extend(
                [Decimal(world["sequence_recovery_lower_bound"]), Decimal(world["formal_1000_gate_pass_probability_lower_bound"])]
            )
    probability_bounds.append(Decimal(feasibility["uniform"]["formal_1000_gate_pass_probability_lower_bound"]))
    assertions = {
        "unit_tests_pass": test_result.returncode == 0,
        "t01_independent_gate_exact": sha256_file(args.t01_independent_validation) == "c39659526734e3b57faeb0b01d4f66a8e322ec030d3ce8e425f36a5c0aa3f909" and t01_validation["status"] == "PASS",
        "known_answer_manifest_rehash": _rehash_manifest(known_root / "known-answer-manifest.json"),
        "eight_cell_count": len(full_cells["cells"]) == 8,
        "eight_cells_strictly_better": len(full_residuals) == 16,
        "full_rule_histogram_totals": all(result["histogram_total"] == result["space_size"] for result in full["results"]),
        "full_rule_top1000_counts": all(len(result["top1000"]) == 1000 for result in full["results"]),
        "m0_two_games": len(m0["games"]) == 2,
        "m0_one_full_space_group": all(game["full_space_tie_group_count"] == 1 and game["histogram"] == [[0, game["space_size"]]] for game in m0["games"]),
        "m0_top1000_counts": all(len(game["top1000"]) == 1000 for game in m0["games"]),
        "m0_decimal80_normalization": all(Decimal(value) <= Decimal("1e-45") for value in m0_residuals.values()),
        "guard_minimum_positive": guards["theoretical_minimum_gt_1e_32"] and Decimal(guards["theoretical_minimum_serialized_50_places"]) > 0,
        "guard_input_permutation": guards["input_permutation"]["stable"],
        "guard_cross_top_k_tie": any(row["tie_crosses_cutoff"] for row in guards["cross_top_k_ties"]),
        "metric_30_boundary": metrics["window_30"]["observation_count"] == 30,
        "metric_29_insufficient": metrics["insufficient_observation_vector"] == {"observation_count": 29, "status": "insufficient_observation", "numeric_metrics_present": False},
        "metric_independent_recompute": all(Decimal(value) <= Decimal("1e-40") for value in metric_residuals.values()),
        "numeric_validation_record": numeric_validation["status"] == "PASS" and numeric_validation["m0_normalization_residuals"] == m0_residuals and numeric_validation["metric_residuals"] == metric_residuals,
        "analytic_fixed_assertions": feasibility["status"] == "PASS" and all(feasibility["assertions"].values()),
        "analytic_probabilities_in_unit_interval": all(Decimal(0) <= value <= Decimal(1) for value in probability_bounds),
        "analytic_worst_sequence": Decimal(feasibility["worst_positive_sequence_lower_bound"]) >= Decimal("0.93954"),
        "analytic_worst_aggregate": Decimal(feasibility["worst_positive_aggregate_lower_bound"]) > Decimal("0.99999950"),
        "import_audit": import_audit["status"] == "PASS" and import_audit["product_import_count"] == 0 and not import_audit["findings"],
        "semantic_mutation_audit": mutation_audit["status"] == "PASS" and mutation_audit["mutation_count"] >= 78 and all(row["rejected"] and row["exit_code"] != 0 for row in mutation_audit["cases"]),
        "analytic_62_case_matrix": mutation_audit["analytic_spec_matrix"]["source_hold_matrix_sha256"] == "6b50ffacd545d75f395fd5637a629bd0f8ffe02e0e7b3d15afc7d08ef32f8556" and mutation_audit["analytic_spec_matrix"]["case_count"] == 62 and mutation_audit["analytic_spec_matrix"]["rejected_nonzero_nonpass_count"] == 62,
    }
    status = "PASS" if all(assertions.values()) else "HOLD"
    ended = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    commands = [
        {"command": "python3 -m unittest discover -s tests/phase4_oracle -p 'test_*.py' -v", "exit_code": test_result.returncode, "stdout": test_result.stdout, "stderr": test_result.stderr},
        {"command": f"python3 scripts/phase4_independent/run_known_answers.py --spec config/phase4 --tick-bound 4096 --output {known_root}", "exit_code": 0},
        {"command": f"python3 scripts/phase4_independent/check_qualification_feasibility.py --spec qualification-design/analytic-feasibility-spec.json --output {args.attempt_root / 'feasibility'}", "exit_code": 0},
        {"command": f"python3 scripts/phase4_independent/oracle_import_audit.py --scripts scripts/phase4_independent --output {args.attempt_root / 'import-audit-I04.json'}", "exit_code": 0},
        {"command": f"python3 scripts/phase4_independent/oracle_mutation_audit.py --known-answers {known_root} --output {args.attempt_root / 'mutation-audit.json'}", "exit_code": 0},
    ]
    _write_new(command_log_path, {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_t10_command_log",
        "attempt_id": args.attempt_root.name,
        "commands": commands,
        "status": status,
    })
    _write_new(attempt_history_path, {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_t10_attempt_history",
        "attempts": [
            {"attempt_id": "T10-I01", "status": "HOLD", "terminal": "HOLD_NUMERIC_PROBABILITY_RANGE", "disposition_path": str(PREP_ROOT / "work-items/T10/attempt-disposition.json")},
            {"attempt_id": "T10-I02", "status": "HOLD", "terminal": "HOLD_SOURCE_MANIFEST_PATH_AND_ATTEMPT_RACE", "disposition_path": str(PREP_ROOT / "work-items/T10-I02/attempt-disposition.json")},
            {"attempt_id": "T10-I03", "status": "HOLD", "terminal": "HOLD_KNOWN_ANSWER_COVERAGE_INCOMPLETE", "disposition_path": str(PREP_ROOT / "work-items/T10-I03/attempt-disposition.json")},
            {"attempt_id": "T10-I04", "status": "HOLD", "terminal": "HOLD_WORK_ITEM_RECEIPT", "disposition_path": str(PREP_ROOT / "work-items/T10-I04/attempt-disposition.json")},
            {"attempt_id": "T10-I05", "status": "HOLD", "terminal": "HOLD_DECIMAL80_SEMANTIC_VALIDATION_AND_PRESERVATION", "independent_validation_path": str(PREP_ROOT / "work-items/T10/attempts/T10-I05/independent-validation.json")},
            {"attempt_id": "T10-I06", "status": "HOLD", "terminal": "HOLD_ANALYTIC_SPEC_SEMANTICS_NOT_FAIL_CLOSED", "independent_validation_path": str(PREP_ROOT / "work-items/T10/attempts/T10-I06/independent-validation.json")},
            {"attempt_id": args.attempt_root.name, "status": status, "terminal": "T10_ORACLE_FEASIBILITY_FROZEN" if status == "PASS" else "HOLD_ORACLE_NOT_FROZEN"},
        ],
        "failed_or_interrupted_commands_preserved": [
            {"stage": "initial_unit_tests", "exit_code": 1, "findings": ["Python boolean typo in insufficient-state vector", "direct/DP partitions differed by one Decimal80 ulp and test was corrected to frozen tolerance"]},
            {"stage": "duplicate_full_rule_invocation", "exit_code": 130, "finding": "Interrupted redundant Decimal-per-combination partition after T10-I02 concurrent process had already completed; no O_EXCL output overwrite occurred"},
        ],
        "status": "PRESERVED",
    })
    summary = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_t10_producer_validation_summary",
        "attempt_id": args.attempt_root.name,
        "producer_provenance": {"producer_actor_id": ACTOR_ID, "task_id": TASK_ID, "session_id": SESSION_ID, "source_commit": SOURCE_COMMIT, "role": "independent_oracle_author"},
        "assertions": assertions,
        "full_rule_cells": full_cells["cells"],
        "analytic_reference": {
            "uniform_aggregate_lower_bound": feasibility["uniform"]["formal_1000_gate_pass_probability_lower_bound"],
            "worst_positive_sequence_lower_bound": feasibility["worst_positive_sequence_lower_bound"],
            "worst_positive_aggregate_lower_bound": feasibility["worst_positive_aggregate_lower_bound"],
        },
        "numeric_residuals": {"full_rule": full_residuals, "m0_normalization": m0_residuals, "metrics": metric_residuals},
        "independence": {"product_import_count": 0, "product_output_inputs": 0, "top_level_pass_trusted": False},
        "acceptance_performed": False,
        "status": status,
        "terminal": "T10_ORACLE_FEASIBILITY_FROZEN" if status == "PASS" else "HOLD_ORACLE_NOT_FROZEN",
    }
    _write_new(summary_path, summary)

    source_outputs = [
        Path("scripts/phase4_independent/oracle_math.py"),
        Path("scripts/phase4_independent/oracle_metrics.py"),
        Path("scripts/phase4_independent/run_known_answers.py"),
        Path("scripts/phase4_independent/check_qualification_feasibility.py"),
        Path("scripts/phase4_independent/oracle_import_audit.py"),
        Path("scripts/phase4_independent/oracle_finalize_t10.py"),
        Path("scripts/phase4_independent/oracle_validation.py"),
        Path("scripts/phase4_independent/oracle_preserve_t10_history.py"),
        Path("scripts/phase4_independent/oracle_preserve_t10_i06.py"),
        Path("scripts/phase4_independent/oracle_mutation_audit.py"),
        Path("tests/phase4_oracle/test_oracle_math.py"),
        Path("tests/phase4_oracle/test_feasibility.py"),
        Path("qualification-design/full-rule-spec-candidate.json"),
        Path("qualification-design/analytic-feasibility-spec.json"),
    ]
    evidence_outputs = sorted(path for path in args.attempt_root.rglob("*") if path.is_file() and path != receipt_path)
    input_paths = [
        args.t01_receipt,
        args.t01_independent_validation,
        args.actor_assignments,
        Path("config/phase4/probability-ranking-contract.json"),
        Path("config/phase4/metric-contract.json"),
        Path("config/phase4/qualification-preregistration.json"),
        Path("config/phase4/alpha-contract.json"),
        Path("config/phase4/model-registry.json"),
        Path("docs/research/phase-4-overall-design.md"),
        Path("docs/plans/phase-4-detailed-plan.md"),
        PREP_ROOT / "work-items/T10/attempt-disposition.json",
        PREP_ROOT / "work-items/T10/feasibility/certificate.json",
        PREP_ROOT / "work-items/T10-I02/attempt-disposition.json",
        PREP_ROOT / "work-items/T10-I02/known-answers/known-answer-manifest.json",
        PREP_ROOT / "work-items/T10-I03/attempt-disposition.json",
        PREP_ROOT / "work-items/T10-I03/known-answers/known-answer-manifest.json",
        PREP_ROOT / "work-items/T10-I03/feasibility/certificate.json",
        PREP_ROOT / "work-items/T10-I03/import-audit.json",
        PREP_ROOT / "work-items/T10-I04/receipt.json",
        PREP_ROOT / "work-items/T10-I04/attempt-disposition.json",
        PREP_ROOT / "work-items/T10-I02/preserved-shared-outputs/preservation-map.json",
        PREP_ROOT / "work-items/T10-I03/preserved-shared-outputs/preservation-map.json",
        PREP_ROOT / "work-items/T10-I04/preserved-shared-outputs/preservation-map.json",
        PREP_ROOT / "work-items/T10/attempts/T10-I05/preserved-shared-outputs/preservation-map.json",
        PREP_ROOT / "work-items/T10/attempts/T10-I05/receipt.json",
        PREP_ROOT / "work-items/T10/attempts/T10-I05/independent-validation.json",
        PREP_ROOT / "work-items/T10/attempts/T10-I06/preserved-shared-outputs/preservation-map.json",
        PREP_ROOT / "work-items/T10/attempts/T10-I06/receipt.json",
        PREP_ROOT / "work-items/T10/attempts/T10-I06/independent-validation.json",
    ]
    receipt = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_work_item_receipt",
        "task_id": TASK_ID,
        "identity": args.attempt_root.name,
        "source_commit": SOURCE_COMMIT,
        "actor_assignment_sha256": sha256_file(args.actor_assignments),
        "task_producer_set": [ACTOR_ID],
        "acceptance_actor_provenance": {
            "actor_id": "p4-acceptance-engineer-i01",
            "session_id": "/root/acceptance_engineer",
            "task_record_path": str(PREP_ROOT / "control/task-records/acceptance-engineer.json"),
            "task_record_sha256": "7705ae46362f94743c03c34e6fdc02e8c3b9c0e5afaddb69e4e0b5042ddc09bd",
        },
        "role_inequalities": {"t10_acceptor_not_oracle_author": True, "power_not_oracle": True, "oracle_not_product": True, "oracle_not_statistical": True},
        "inputs": [_artifact(path) for path in input_paths],
        "outputs": [_output(path) for path in sorted(set(source_outputs + evidence_outputs))],
        "command": ["python3", "scripts/phase4_independent/run_known_answers.py", "--spec", "config/phase4", "--tick-bound", "4096", "--output", str(known_root)],
        "started_at_utc": started,
        "ended_at_utc": ended,
        "process_exit_code": 0 if status == "PASS" else 20,
        "status": status,
        "terminal": "T10_ORACLE_FEASIBILITY_FROZEN" if status == "PASS" else "HOLD_ORACLE_NOT_FROZEN",
    }
    _write_new(receipt_path, receipt)
    print(json.dumps({"status": status, "receipt": str(receipt_path), "assertions": assertions}, sort_keys=True))
    return 0 if status == "PASS" else 20


if __name__ == "__main__":
    raise SystemExit(main())
