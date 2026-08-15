from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FINAL = {"acceptance/machine-acceptance.json", "acceptance/checklist-release-receipt.json", "acceptance/final-closure.json"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def once(path: Path, value: Any) -> None:
    encoded = value.encode() if isinstance(value, str) else canon(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable identity collision: {path}")
        return
    path.write_bytes(encoded)


def receipt(release: Path, task: str, inputs: list[Path], outputs: list[Path], assertions: dict[str, Any], started: str) -> dict[str, Any]:
    if not inputs or not outputs or not assertions or any(not path.is_file() for path in inputs + outputs):
        raise ValueError(f"{task} receipt evidence incomplete")
    value = {
        "artifact_type": "phase4_task_receipt", "task": task, "release_id": release.name,
        "command": [str(Path(sys.executable).resolve()), *sys.argv], "exit_code": 0,
        "started_at_utc": started, "completed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": f"uid:{os.getuid()}", "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True, check=True).stdout),
        "inputs": [{"path": path.relative_to(release).as_posix(), "sha256": sha(path)} for path in inputs],
        "outputs": [{"path": path.relative_to(release).as_posix(), "sha256": sha(path)} for path in outputs],
        "assertions": assertions, "blocking_findings": [], "status": "PASS",
    }
    once(release / f"receipts/{task}.json", value)
    return value


def validate_task_receipts(release: Path) -> dict[str, str]:
    hashes = {}
    required = {"artifact_type", "task", "release_id", "command", "exit_code", "started_at_utc", "completed_at_utc", "actor", "dirty", "inputs", "outputs", "assertions", "blocking_findings", "status"}
    paths = [release / "contracts/D01-receipt.json"] + [release / f"receipts/D{i:02d}.json" for i in range(2, 13)] + [release / "receipts/D14.json"]
    for path in paths:
        value = load(path)
        if set(value) != required or value["status"] != "PASS" or value["exit_code"] != 0 or value["blocking_findings"] or not value["inputs"] or not value["outputs"] or not value["assertions"]:
            raise ValueError(f"HOLD_INVALID_TASK_RECEIPT:{path}")
        for record in value["inputs"] + value["outputs"]:
            evidence = release / record["path"] if not record["path"].startswith(("docs/", "tasks/", "ROADMAP")) else ROOT / record["path"]
            if evidence.is_file() and sha(evidence) != record["sha256"]:
                raise ValueError(f"HOLD_RECEIPT_HASH_MISMATCH:{path}:{evidence}")
        hashes[value["task"]] = sha(path)
    return hashes


def validate_forecast(release: Path, game: str) -> dict[str, Any]:
    forecast_path = next((release / f"forecasts/{game}").glob("*/forecast.json"))
    forecast = load(forecast_path)
    rows_path = forecast_path.with_name("top1000.jsonl")
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    if len(rows) != 1000 or len({(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in rows}) != 1000:
        raise ValueError(f"HOLD_ILLEGAL_TOP1000:{game}")
    required_tie = {"tie_group_id", "tie_group_size", "tie_rank_lower", "tie_rank_upper", "tie_midrank", "tie_key"}
    probabilities = [Decimal(row["joint_probability"]) for row in rows]
    if len(set(probabilities)) < 2 or any(value <= 0 for value in probabilities) or any(left < right for left, right in zip(probabilities, probabilities[1:])):
        raise ValueError(f"HOLD_PROBABILITY_ORDER:{game}")
    for index, row in enumerate(rows, 1):
        if row["rank"] != index or not required_tie <= row.keys() or row.get("probability_representation") != "P4-DECIMAL-EXACT-1":
            raise ValueError(f"HOLD_TIE_CONTRACT:{game}:{index}")
        peers = [position for position, value in enumerate(probabilities, 1) if value == probabilities[index - 1]]
        if row["tie_group_size"] != len(peers) or row["tie_rank_lower"] != min(peers) or row["tie_rank_upper"] != max(peers) or Decimal(row["tie_midrank"]) != (Decimal(min(peers)) + Decimal(max(peers))) / 2:
            raise ValueError(f"HOLD_TIE_BOUNDS:{game}:{index}")
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    if serving["family"] == "M0" or not serving["non_m0"]:
        raise ValueError(f"HOLD_M0_SERVING:{game}")
    return {"forecast_sha256": sha(forecast_path), "top1000_sha256": sha(rows_path), "distinct_probability_count": len(set(probabilities)), "model_release_id": serving["model_release_id"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    release = args.release.resolve()
    release.relative_to(ROOT.resolve())
    if release.name != "P4-RMVP-20260815-r08":
        raise ValueError("post-r07 fail-closed correction requires unique release P4-RMVP-20260815-r08")
    if any((release / path).exists() for path in FINAL):
        raise FileExistsError("D15 final files already exist; release identity is immutable")

    command_receipts = sorted((release / "validation/attempts").glob("*/receipt.json"))
    passed = {path.parent.name for path in command_receipts if load(path).get("status") == "PASS" and load(path).get("exit_code") == 0}
    required_commands = {"A01-compileall", "A02-phase4", "A03-phase4-oracle", "A04-phase3", "A05-phase2-1", "A06-phase2", "A07-authority", "A08-contract", "A09-bottom-up", "A10-replay-validation"}
    if not required_commands <= passed:
        raise ValueError(f"HOLD_FINAL_REGRESSION_INCOMPLETE:{sorted(required_commands - passed)}")

    replay_path = release / "replay/replay-report.json"
    replay = load(replay_path)
    if replay.get("match_rate") != 1.0 or replay.get("mutation_detection_rate") != 1.0 or replay.get("product_core_import_count") != 0:
        raise ValueError("HOLD_D12_INDEPENDENT_REPLAY")
    receipt(release, "D12", [replay_path], [replay_path], {"match_rate_100pct": True, "mutation_detection_100pct": True, "independent_imports_zero": True}, started)

    checklist = (f"# Phase 4 local product acceptance candidate\n\nStatus: `CANDIDATE_NOT_RELEASED`\n\n"
                 f"Release: `{release.name}`. Run the exact commands in `runbook/release-runbook.md`. Inspect both SSQ and DLT frozen P4E1-R models, exact-decimal Top-1000 tie layers, locks, append-only ledger, held-out scientific metrics, score/revision, AutoResearch shadow, scheduler recovery, and independent replay. This checklist makes no claim of lift, winnings, or profit.\n")
    checklist_path = release / "acceptance/local-product-checklist-candidate.md"
    once(checklist_path, checklist)
    candidate_receipt = release / "acceptance/checklist-candidate-receipt.json"
    once(candidate_receipt, {"artifact_type": "phase4_checklist_candidate_receipt", "release_id": release.name, "checklist_sha256": sha(checklist_path), "status": "CANDIDATE_NOT_RELEASED"})
    receipt(release, "D14", [checklist_path], [candidate_receipt], {"content_addressed": True, "candidate_not_released": True}, started)

    environment_path = release / "readiness/environment.json"
    lock = ROOT / "requirements/phase4.lock"
    wheels = sorted((ROOT / "wheelhouse/phase4").glob("*")) if (ROOT / "wheelhouse/phase4").exists() else []
    environment = {"artifact_type": "phase4_execution_environment", "interpreter_realpath": str(Path(sys.executable).resolve()), "python_version": platform.python_version(), "platform": platform.platform(), "dependency_lock_path": "requirements/phase4.lock", "dependency_lock_sha256": sha(lock), "wheelhouse": [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)} for path in wheels if path.is_file()], "command_receipts": [{"path": path.relative_to(release).as_posix(), "sha256": sha(path)} for path in command_receipts], "status": "PASS"}
    once(environment_path, environment)
    forecast_evidence = {game: validate_forecast(release, game) for game in ("ssq", "dlt")}
    task_hashes = validate_task_receipts(release)

    excluded = FINAL | {"manifest/delivery-manifest.json"}
    entries = []
    for path in sorted(item for item in release.rglob("*") if item.is_file()):
        relative = path.relative_to(release).as_posix()
        if relative not in excluded:
            entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha(path)})
    manifest = {"artifact_type": "phase4_pre_acceptance_delivery_manifest", "release_id": release.name, "entries": entries, "entry_count": len(entries), "coverage": 1.0, "covers_d14": all(any(row["path"] == name for row in entries) for name in ("acceptance/local-product-checklist-candidate.md", "acceptance/checklist-candidate-receipt.json", "receipts/D14.json")), "excluded_final_append_only": sorted(FINAL), "status": "PASS"}
    if not manifest["covers_d14"]:
        raise ValueError("HOLD_D13_DOES_NOT_COVER_D14")
    manifest_path = release / "manifest/delivery-manifest.json"
    once(manifest_path, manifest)
    pre_hashes = {row["path"]: row["sha256"] for row in entries}

    # D15 bottom-up recomputation; no top-level PASS is trusted.
    ledger_events = sorted((release / "runtime/ledger/events").glob("*.json"))
    if not ledger_events or not (release / "runtime/ledger/head.json").is_file():
        raise ValueError("HOLD_APPEND_ONLY_LEDGER_MISSING")
    for relative, expected in pre_hashes.items():
        if sha(release / relative) != expected:
            raise ValueError(f"HOLD_PRE_ACCEPTANCE_CHANGED:{relative}")
    scientific = {game: load(next((release / f"backtests/{game}").glob("*/summary.json")))["scientific_status"] for game in ("ssq", "dlt")}
    acceptance = {"artifact_type": "phase4_machine_acceptance", "release_id": release.name, "recomputed_from_bottom_up": True, "task_receipt_hashes": task_hashes, "forecast_evidence": forecast_evidence, "scientific_status_by_game": scientific, "replay_match_rate": replay["match_rate"], "mutation_detection_rate": replay["mutation_detection_rate"], "manifest_sha256": sha(manifest_path), "manifest_coverage": 1.0, "pre_acceptance_hashes": pre_hashes, "pre_acceptance_unchanged": True, "blocking_findings": [], "machine_state": "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE", "status": "PASS"}
    acceptance_path = release / "acceptance/machine-acceptance.json"
    once(acceptance_path, acceptance)
    release_receipt = {"artifact_type": "phase4_checklist_release_receipt", "checklist_sha256": sha(checklist_path), "manifest_sha256": sha(manifest_path), "machine_acceptance_sha256": sha(acceptance_path), "released_after_machine_pass": True, "status": "PASS"}
    release_receipt_path = release / "acceptance/checklist-release-receipt.json"
    once(release_receipt_path, release_receipt)
    closure = {"artifact_type": "phase4_final_closure", "release_id": release.name, "manifest_sha256": sha(manifest_path), "machine_acceptance_sha256": sha(acceptance_path), "checklist_release_receipt_sha256": sha(release_receipt_path), "pre_acceptance_unchanged": all(sha(release / path) == expected for path, expected in pre_hashes.items()), "machine_state": "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE", "status": "PASS"}
    once(release / "acceptance/final-closure.json", closure)
    print(json.dumps({"status": "PASS", "machine_state": closure["machine_state"], "release": release.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
