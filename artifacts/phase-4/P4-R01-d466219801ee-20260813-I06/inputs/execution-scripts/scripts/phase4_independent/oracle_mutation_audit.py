from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from oracle_math import canonical_bytes
from oracle_validation import validate_full_rule_result


ANALYTIC_MATRIX_SHA256 = "6b50ffacd545d75f395fd5637a629bd0f8ffe02e0e7b3d15afc7d08ef32f8556"
ANALYTIC_FIELDS = (
    "schema_version", "artifact_type", "spec_id", "result_blind",
    "small_space.N", "small_space.k", "small_space.space_size", "scale", "cycles",
    "effect_vector", "effect_ticks", "slow_drift_ramp_cycles", "feature_context",
    "family_initial_wealth", "alpha_first", "uniform_sequence_upper_bound", "formal_sequences",
    "uniform_max_false_proposals", "positive_min_recoveries", "positive_bound", "threshold_h",
    "rounding", "decimal_precision", "selection_minima.uniform_aggregate",
    "selection_minima.positive_aggregate", "selection_minima.positive_sequence",
    "required_reference_minima.weakest_uniform_aggregate",
    "required_reference_minima.worst_positive_sequence",
    "required_reference_minima.worst_positive_aggregate",
    "t01_qualification_contract_sha256", "t01_alpha_contract_sha256",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))


def _runner(config: Path, spec: Path, output: Path) -> int:
    command = [
        sys.executable,
        "scripts/phase4_independent/run_known_answers.py",
        "--spec", str(config),
        "--tick-bound", "4096",
        "--full-rule-spec", str(spec),
        "--output", str(output),
    ]
    return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode


def _feasibility_runner(spec: Path, output: Path) -> tuple[int, str | None]:
    command = [sys.executable, "scripts/phase4_independent/check_qualification_feasibility.py", "--spec", str(spec), "--output", str(output)]
    code = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode
    certificate = output / "certificate.json"
    status = _load(certificate).get("status") if certificate.is_file() else None
    return code, status


def _parent_and_key(value: dict[str, Any], dotted: str) -> tuple[dict[str, Any], str]:
    parts = dotted.split(".")
    parent = value
    for part in parts[:-1]:
        parent = parent[part]
    return parent, parts[-1]


def _tampered(value: Any) -> Any:
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is str:
        return value + "-tampered"
    if type(value) is list:
        return value + [None]
    raise TypeError(f"unsupported analytic mutation type: {type(value).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed deletion/change mutation audit for the independent oracle")
    parser.add_argument("--known-answers", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite mutation audit")
    original_spec = _load(Path("qualification-design/full-rule-spec-candidate.json"))
    analytic_spec = _load(Path("qualification-design/analytic-feasibility-spec.json"))
    original_result = _load(args.known_answers / "full-rule-oracle.json")
    cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="p4-oracle-mutations-") as temp_name:
        temp = Path(temp_name)
        base_config = temp / "config"
        shutil.copytree("config/phase4", base_config)
        probability_path = base_config / "probability-ranking-contract.json"
        probability = _load(probability_path)
        for name, mutation in (
            ("delete_probability_normalization_tolerance", lambda value: value.pop("normalization_tolerance")),
            ("change_probability_absolute_tolerance", lambda value: value["normalization_tolerance"].__setitem__("absolute", "1e-20")),
        ):
            value = copy.deepcopy(probability)
            mutation(value)
            _write(probability_path, value)
            code = _runner(base_config, Path("qualification-design/full-rule-spec-candidate.json"), temp / f"out-{name}")
            cases.append({"case": name, "exit_code": code, "rejected": code != 0})
            _write(probability_path, probability)

        for field in ("games", "top_k", "absolute_error_bound", "decimal_precision", "scale"):
            value = copy.deepcopy(original_spec)
            del value[field]
            spec_path = temp / f"spec-delete-{field}.json"
            _write(spec_path, value)
            code = _runner(base_config, spec_path, temp / f"out-delete-{field}")
            cases.append({"case": f"delete_full_rule_{field}", "exit_code": code, "rejected": code != 0})
        changed_spec = copy.deepcopy(original_spec)
        changed_spec["games"]["ssq"]["space_size"] -= 1
        changed_spec_path = temp / "spec-change-count.json"
        _write(changed_spec_path, changed_spec)
        code = _runner(base_config, changed_spec_path, temp / "out-change-count")
        cases.append({"case": "change_full_rule_count", "exit_code": code, "rejected": code != 0})

        analytic_cases = []
        for field in ANALYTIC_FIELDS:
            for operation in ("delete", "tamper"):
                value = copy.deepcopy(analytic_spec)
                parent, key = _parent_and_key(value, field)
                if operation == "delete":
                    del parent[key]
                else:
                    parent[key] = _tampered(parent[key])
                spec_path = temp / f"analytic-{len(analytic_cases):02d}.json"
                output_path = temp / f"analytic-output-{len(analytic_cases):02d}"
                _write(spec_path, value)
                code, emitted_status = _feasibility_runner(spec_path, output_path)
                row = {
                    "case": f"analytic_{operation}_{field}", "field": field, "operation": operation,
                    "exit_code": code, "emitted_certificate_status": emitted_status,
                    "mutated_spec_sha256": __import__("hashlib").sha256(spec_path.read_bytes()).hexdigest(),
                    "rejected": code != 0 and emitted_status != "PASS",
                }
                analytic_cases.append(row)
                cases.append(row)

    result_mutations = {
        "delete_result_K": lambda value: value["eight_cells"][0].pop("K"),
        "delete_result_error": lambda value: value["eight_cells"][0].pop("absolute_error_bound"),
        "delete_result_numeric": lambda value: value["eight_cells"][0].pop("candidate_coverage"),
        "change_result_K": lambda value: value["eight_cells"][0].__setitem__("K", 11),
        "change_result_count": lambda value: value["results"][0].__setitem__("front_combination_count", 1),
        "change_result_error": lambda value: value["eight_cells"][0].__setitem__("absolute_error_bound", "1e-20"),
        "change_result_numeric": lambda value: value["eight_cells"][0].__setitem__("candidate_coverage", "not-a-number"),
        "change_result_strict_boolean": lambda value: value["eight_cells"][0].__setitem__("strictly_better", False),
    }
    for name, mutation in result_mutations.items():
        value = copy.deepcopy(original_result)
        mutation(value)
        try:
            validate_full_rule_result(value, original_spec)
        except (ValueError, KeyError, TypeError):
            rejected = True
            code = 1
        else:
            rejected = False
            code = 0
        cases.append({"case": name, "exit_code": code, "rejected": rejected})

    status = "PASS" if cases and all(row["rejected"] and row["exit_code"] != 0 for row in cases) else "HOLD"
    report = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_oracle_mutation_audit",
        "mutation_count": len(cases),
        "full_rule_and_result_mutation_count": len(cases) - len(analytic_cases),
        "analytic_spec_matrix": {
            "source_hold_matrix_sha256": ANALYTIC_MATRIX_SHA256,
            "field_count": len(ANALYTIC_FIELDS),
            "case_count": len(analytic_cases),
            "rejected_nonzero_nonpass_count": sum(row["rejected"] for row in analytic_cases),
            "cases": analytic_cases,
        },
        "cases": cases,
        "status": status,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_bytes(report))
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"status": status, "mutations": len(cases), "output": str(args.output)}, sort_keys=True))
    return 0 if status == "PASS" else 20


if __name__ == "__main__":
    raise SystemExit(main())
