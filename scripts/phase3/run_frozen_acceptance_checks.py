from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and retain the frozen Phase 3 regression/receipt checks.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prep-root", required=True, type=Path)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--through-work-item", type=int, choices=range(1, 14), default=11)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    prep = args.prep_root.resolve()
    release = args.release_root.resolve() if args.release_root else None
    prep_actors = prep / "control/actor-assignments-preparation.json"
    formal_actors = release / "control/actor-assignments-formal.json" if release else None
    python = sys.executable
    commands: list[tuple[str, list[str], dict[str, str]]] = [
        ("compileall", [python, "-m", "compileall", "-q", "-f", "src/lottery_research/phase3", "scripts/phase3"], {}),
        ("git-diff-check", ["git", "diff", "--check"], {}),
        ("phase3-tests", [python, "-m", "unittest", "discover", "-s", "tests/phase3", "-p", "test_*.py", "-v"], {"TMPDIR": "/private/tmp", "PYTHONPATH": "src"}),
        ("phase2-1-tests", [python, "-m", "unittest", "discover", "-s", "tests/phase2_1", "-p", "test_*.py", "-v"], {"TMPDIR": "/private/tmp", "PYTHONPATH": "src"}),
        ("phase2-tests", [python, "-m", "unittest", "discover", "-s", "tests/phase2", "-p", "test_*.py", "-v"], {"PYTHONPATH": "src"}),
    ]
    for ordinal in range(1, args.through_work_item + 1):
        work_item = f"W{ordinal:02d}"
        if ordinal <= 6:
            receipt = prep / f"work-items/{work_item}/receipt.json"
            actors = prep_actors
        else:
            if release is None or formal_actors is None:
                raise ValueError("W07-W13 validation requires --release-root")
            receipt = release / f"work-items/{work_item}/receipt.json"
            actors = formal_actors
        commands.append((
            f"receipt-{work_item}",
            [python, "scripts/phase3/validate_work_item_receipt.py", "--receipt", str(receipt), "--actor-assignments", str(actors), "--expected-work-item", work_item],
            {"PYTHONPATH": "src"},
        ))
    rows = []
    for index, (name, command, additions) in enumerate(commands, start=1):
        environment = os.environ.copy()
        environment.update(additions)
        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        completed = subprocess.run(command, cwd=ROOT, env=environment, check=False, capture_output=True)
        ended = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        stdout_path = output / f"{index:02d}-{name}.stdout.log"
        stderr_path = output / f"{index:02d}-{name}.stderr.log"
        stdout_path.write_bytes(completed.stdout)
        stderr_path.write_bytes(completed.stderr)
        rows.append({
            "name": name, "command": command, "environment_overrides": additions,
            "started_at_utc": started, "ended_at_utc": ended, "exit_code": completed.returncode,
            "stdout": stdout_path.name, "stderr": stderr_path.name,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
        })
    summary = {
        "schema_version": "3.0.0", "artifact_type": "phase3_frozen_acceptance_command_log",
        "status": "PASS" if all(row["exit_code"] == 0 for row in rows) else "FAIL",
        "command_count": len(rows), "through_work_item": f"W{args.through_work_item:02d}", "commands": rows,
    }
    (output / "summary.json").write_bytes(canonical(summary))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
