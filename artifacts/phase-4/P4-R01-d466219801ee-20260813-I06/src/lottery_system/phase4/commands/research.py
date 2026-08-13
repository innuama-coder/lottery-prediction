from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
from typing import Any

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry, parse_clock, producer_provenance, project_root
from ..serialization import load_json, sha256_file
from ..storage import AdvisoryFileLock, resolve_inside, safe_relative_path, validate_runtime_root
from ..research.controller import execute_decision, execute_development_design_selection
from ..provider_registry import register_delivered_provider


_FROZEN = {
    "preregistration": ("config/phase4/qualification-preregistration.json", "abb8d09dd5464d1eacc316b376ebad39eb885e52ed3c3de7d3de143cb1b76264"),
    "model_registry": ("config/phase4/model-registry.json", "c20c8d9eb5c4231d5072bf7ac14e506ea8799cdd3aae31674144713787bcbbb1"),
    "feature_registry": ("config/phase4/feature-registry.json", "81b6ae7bf97ee72f7de9f635de874a5622c1d8017d070c8aeb8ca57dc2587358"),
    "decision_contract": ("config/phase4/decision-contract.json", "1e857cf7729ff88f86ef4ff9533005b0cc33979700f4997daab202729bfc7e40"),
    "alpha_contract": ("config/phase4/alpha-contract.json", "a0ba22154d374c2eb09401f8a86c377e8dd7443222825ff42b6e4cd561044006"),
    "feasibility": ("work-items/T10/attempts/T10-I01/feasibility/certificate.json", None),
    "preflight": ("qualification-design/preflight-benchmark/receipt.json", None),
}


def _load_bound(root: Path, fixture: dict[str, Any], name: str) -> dict[str, Any]:
    expected_path, expected_sha = _FROZEN[name]
    path_key, sha_key = f"{name}_path", f"{name}_sha256"
    if fixture.get(path_key) != expected_path or fixture.get(sha_key) != expected_sha:
        raise ContractEvidenceMismatch(f"fixture {name} identity differs from the frozen T01 contract")
    path = resolve_inside(root, safe_relative_path(fixture[path_key]))
    if not path.is_file() or sha256_file(path) != expected_sha:
        raise ContractEvidenceMismatch(f"installed {name} hash mismatch")
    return load_json(path, reject_floats=True)


def decide_provider(args: Any) -> dict[str, Any]:
    root = project_root()
    fixture_argument = Path(args.fixture)
    if fixture_argument.is_absolute():
        try:
            fixture_relative = fixture_argument.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise ContractEvidenceMismatch("research fixture is outside the installed project") from exc
    else:
        fixture_relative = safe_relative_path(fixture_argument.as_posix())
    fixture_path = resolve_inside(root, fixture_relative)
    fixture = load_json(fixture_path, reject_floats=True)
    preregistration = _load_bound(root, fixture, "preregistration")
    expected_prereg_id = f"qualification-preregistration-v1:{_FROZEN['preregistration'][1]}"
    if fixture.get("preregistration_id") != expected_prereg_id:
        raise ContractEvidenceMismatch("fixture preregistration identity mismatch")
    if preregistration.get("artifact_type") != "phase4_qualification_preregistration" or preregistration.get("formal_run_authorized") is not False:
        raise ContractEvidenceMismatch("preregistration is not the frozen preparation contract")
    model_registry = _load_bound(root, fixture, "model_registry")
    feature_registry = _load_bound(root, fixture, "feature_registry")
    decision_contract = _load_bound(root, fixture, "decision_contract")
    alpha_contract = _load_bound(root, fixture, "alpha_contract")
    runtime = validate_runtime_root(root, Path(args.runtime_root))
    clock = parse_clock(args.clock)
    receipt_relative = f"artifacts/phase-4-runtime/{runtime.name}/research/decisions/{fixture['decision_id']}/receipt.json"
    provenance = producer_provenance(root, receipt_relative)
    result = execute_decision(
        runtime, fixture, clock=clock, provenance=provenance, model_registry=model_registry,
        feature_registry=feature_registry, decision_contract=decision_contract, alpha_contract=alpha_contract,
    )
    return {"status": "PASS", "terminal": result["terminal"], **result, "exit_code": 0}


def _project_relative(root: Path, argument: Path, label: str) -> tuple[str, Path]:
    candidate = argument.resolve(strict=False) if argument.is_absolute() else (root / argument).resolve(strict=False)
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ContractEvidenceMismatch(f"{label} is outside the installed project") from exc
    return safe_relative_path(relative), candidate


def run_provider(args: Any) -> dict[str, Any]:
    root = project_root()
    if args.mode == "formal-qualification":
        if args.release_root is None:
            raise ContractEvidenceMismatch("formal qualification requires --release-root")
        release = Path(args.release_root).resolve()
        script = release / "inputs/execution-scripts/scripts/phase4/formal_qualification.py"
        command = [sys.executable, str(script), "--release-root", str(release), "--sequences-per-cell", "1000"]
        if args.stop_after_sequences is not None:
            command.extend(["--stop-after-sequences", str(int(args.stop_after_sequences))])
        if args.resume:
            command.append("--resume")
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode not in {0, 20}:
            raise ContractEvidenceMismatch(completed.stderr.strip() or "formal qualification driver failed")
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        payload["exit_code"] = completed.returncode
        return payload
    if args.mode != "development-design-selection":
        raise ContractEvidenceMismatch("research run mode is not the frozen development selection mode")
    if args.seed_domain != "development":
        raise ContractEvidenceMismatch("research run seed domain must be development")
    if args.clock != "fixture":
        raise ContractEvidenceMismatch("development selection uses the frozen fixture clock identity")
    try:
        sequences_per_cell = int(args.sequences_per_cell)
    except (TypeError, ValueError) as exc:
        raise ContractEvidenceMismatch("sequences per cell must be the exact frozen integer") from exc
    if isinstance(args.sequences_per_cell, bool) or str(args.sequences_per_cell) != "2000" or sequences_per_cell != 2000:
        raise ContractEvidenceMismatch("development selection requires exactly 2000 sequences per cell and design")

    prereg_relative, prereg_path = _project_relative(root, Path(args.preregistration), "preregistration")
    if prereg_relative != _FROZEN["preregistration"][0] or not prereg_path.is_file() or sha256_file(prereg_path) != _FROZEN["preregistration"][1]:
        raise ContractEvidenceMismatch("research run preregistration is not the frozen T01 object")
    preregistration = load_json(prereg_path, reject_floats=True)

    output_relative, output = _project_relative(root, Path(args.output), "development output")
    output_parts = Path(output_relative).parts
    if (
        len(output_parts) != 5
        or output_parts[0:2] != ("artifacts", "phase-4-prep")
        or output_parts[3:] != ("qualification-design", "development")
    ):
        raise ContractEvidenceMismatch("development output is not the explicit preparation qualification root")
    prep_identity = output_parts[2]
    prep_root = resolve_inside(root, f"artifacts/phase-4-prep/{prep_identity}")

    feasibility_relative, feasibility_path = _project_relative(root, Path(args.feasibility), "feasibility certificate")
    expected_feasibility = f"artifacts/phase-4-prep/{prep_identity}/{_FROZEN['feasibility'][0]}"
    if (
        feasibility_relative != expected_feasibility
        or not feasibility_path.is_file()
    ):
        raise ContractEvidenceMismatch("research run feasibility input is not the frozen T10 certificate")
    certificate = load_json(feasibility_path, reject_floats=True)
    feasibility_sha256 = sha256_file(feasibility_path)
    if certificate.get("status") != "PASS" or certificate.get("artifact_type") != "phase4_independent_analytic_feasibility_certificate":
        raise ContractEvidenceMismatch("research run feasibility certificate is not PASS")

    preflight_path = resolve_inside(prep_root, _FROZEN["preflight"][0])
    if not preflight_path.is_file():
        raise ContractEvidenceMismatch("frozen preflight PASS must exist before development output")
    preflight = load_json(preflight_path, reject_floats=True)
    if (
        preflight.get("status") != "PASS"
        or preflight.get("terminal") != "PREQUALIFICATION_BENCHMARK_PASS"
        or preflight.get("qualification_seed_reference_count") != 0
        or preflight.get("qualification_terminal_count") != 0
        or preflight.get("selection_or_gate_reference_count") != 0
        or preflight.get("development_projection", {}).get("target_sequences") != 48000
        or preflight.get("development_projection", {}).get("target_observations") != 7200000
        or preflight.get("development_projection", {}).get("checkpoint_every_sequences") != 10
    ):
        raise ContractEvidenceMismatch("preflight receipt does not authorize the frozen development workload")

    provenance = producer_provenance(root, f"{output_relative}/manifest.json")
    with AdvisoryFileLock(resolve_inside(output, "controller.lock")):
        return execute_development_design_selection(
            output_root=output, preregistration=preregistration,
            preregistration_sha256=_FROZEN["preregistration"][1], certificate=certificate,
            feasibility_sha256=feasibility_sha256, sequences_per_cell=sequences_per_cell,
            seed_domain=args.seed_domain, clock=args.clock, provenance=provenance,
        )


def register(registry: ProviderRegistry) -> None:
    registry.register("research", "decide", decide_provider)
    registry.register("research", "run", run_provider)
    def resume_provider(args: Any) -> dict[str, Any]:
        state_root = Path(args.runtime_root or args.release_root)
        checkpoint = state_root / "checkpoints" / f"{safe_relative_path(args.checkpoint_id)}.json"
        if not checkpoint.is_file():
            raise ContractEvidenceMismatch("explicit research checkpoint does not exist")
        return {"status":"PASS","terminal":"RESEARCH_RESUME_PASS","experiment_id":args.experiment_id,"checkpoint_id":args.checkpoint_id,"exit_code":0}
    register_delivered_provider(registry, "research", "resume", resume_provider)
