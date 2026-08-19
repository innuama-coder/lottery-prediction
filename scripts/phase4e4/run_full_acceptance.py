#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from lottery_system.phase4e4.data import canonical, sha256_bytes


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/phase-4e4/acceptance-20260819/full-current-phase4-tests.json"


def main() -> int:
    commands = [
        ["python3", "-m", "compileall", "-q", "src/lottery_system/phase4e4", "scripts/phase4e4"],
        ["python3", "-m", "unittest", "discover", "-s", "tests/phase4", "-p", "test*.py"],
    ]
    results = []
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        transcript = completed.stdout + completed.stderr
        match = re.search(r"Ran (\d+) tests", transcript)
        results.append({"command": command, "exit_code": completed.returncode,
                        "transcript_sha256": sha256_bytes(transcript.encode()),
                        "test_count": int(match.group(1)) if match else None,
                        "tail": transcript.splitlines()[-8:]})
    payload = {"artifact_type": "phase4e4_full_current_phase4_test_receipt", "results": results,
               "status": "PASS" if all(row["exit_code"] == 0 for row in results) else "FAIL"}
    payload["receipt_sha256"] = sha256_bytes(canonical(payload))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(canonical(payload))
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
