from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from lottery_system.phase4.real_common import digest
from lottery_system.phase4.real_model import write_once


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/phase-4e3/delivery-20260819/acceptance/full-test-receipt.json"


def main() -> int:
    before = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines()
    if before:
        raise ValueError(f"FAIL_DIRTY_PRE_FULL_ACCEPTANCE: {before}")
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"]
    environment = {**os.environ, "PYTHONPATH": "src:."}
    completed = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    combined = completed.stdout + completed.stderr
    if completed.returncode:
        print(combined)
        return completed.returncode
    match = re.search(r"Ran (\d+) tests in ([0-9.]+)s", combined)
    skipped = re.search(r"skipped=(\d+)", combined)
    receipt = {
        "artifact_type": "phase4e3_full_test_acceptance_receipt", "status": "PASS",
        "command": command, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "return_code": completed.returncode,
        "test_count": int(match.group(1)) if match else None,
        "elapsed_seconds_reported": float(match.group(2)) if match else None,
        "skipped_count": int(skipped.group(1)) if skipped else 0,
        "summary_output": [line for line in combined.splitlines() if line.strip()][-20:],
        "clean_worktree_before_run": True,
    }
    receipt["receipt_sha256"] = digest(receipt)
    write_once(OUTPUT, receipt)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
