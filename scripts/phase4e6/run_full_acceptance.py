#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lottery_system.phase4e6.consensus import canonical, sha256

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/phase4e6/acceptance"

def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    command = ["python3", "-m", "unittest", "discover", "-s", "tests/phase4", "-p", "test_*.py"]
    started = datetime.now(timezone.utc).isoformat(); run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env={**__import__("os").environ, "PYTHONPATH": "src"})
    output = run.stdout + run.stderr
    receipt = {"artifact_type": "phase4e6_full_phase4_test_receipt", "command": command, "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(), "returncode": run.returncode, "status": "PASS" if run.returncode == 0 else "FAIL", "output_sha256": sha256(output.encode()), "output_tail": output[-4000:]}
    receipt["receipt_sha256"] = sha256(canonical(receipt)); (OUT / "full-phase4-tests.json").write_bytes(canonical(receipt))
    print(json.dumps({"status": receipt["status"], "returncode": run.returncode, "output_tail": output[-1200:]}, sort_keys=True)); return run.returncode

if __name__ == "__main__": raise SystemExit(main())
