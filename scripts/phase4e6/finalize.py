#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lottery_system.phase4e6.consensus import canonical, sha256

ROOT = Path(__file__).resolve().parents[2]; BASE = ROOT / "artifacts/phase4e6"

def main() -> int:
    tests = json.loads((BASE / "acceptance/full-phase4-tests.json").read_text())
    independent = json.loads((BASE / "acceptance/independent-acceptance.json").read_text())
    decision = json.loads((BASE / "delivery/decision.json").read_text())
    coverage = json.loads((BASE / "consensus/coverage-report.json").read_text())
    if tests["status"] != "PASS" or independent["status"] != "PASS": raise SystemExit("acceptance not green")
    excluded = {"final-evidence-manifest.json", "final-closure.json"}
    files = [{"path": str(path.relative_to(BASE)), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())} for path in sorted(BASE.rglob("*")) if path.is_file() and path.name not in excluded]
    manifest = {"artifact_type": "phase4e6_final_evidence_manifest", "file_count": len(files), "files": files, "manifest_sha256": sha256(canonical(files))}
    (BASE / "final-evidence-manifest.json").write_bytes(canonical(manifest))
    closure = {"artifact_type": "phase4e6_final_closure", "terminal_status": "PROSPECTIVE_ONLY", "serving_release": "P4-P4E2-20260815-r12", "serving_release_changed": False, "release_allocation": "FORBIDDEN", "valid_untouched_report_window": False, "report_labels_read": False, "report_evaluations": 0, "dlt_consensus_coverage": coverage["games"]["dlt"]["accepted_fraction"], "ssq_consensus_coverage": coverage["games"]["ssq"]["accepted_fraction"], "both_games_data_quality_gate_pass": False, "statistical_gate_pass": False, "prior_bytes_unchanged": subprocess.run(["git", "diff", "--quiet", "40afa230", "--", "artifacts/phase-4", "artifacts/phase-4e3", "artifacts/phase-4e4", "artifacts/phase-4e5"], cwd=ROOT).returncode == 0, "full_tests_status": tests["status"], "full_tests_receipt_sha256": tests["receipt_sha256"], "independent_acceptance_status": independent["status"], "independent_acceptance_receipt_sha256": independent["receipt_sha256"], "manifest_sha256": manifest["manifest_sha256"], "prospective_checkpoint": "348339ab", "probability_spread_adjustment": "none", "no_pull_request": True}
    closure["receipt_sha256"] = sha256(canonical(closure)); (BASE / "final-closure.json").write_bytes(canonical(closure)); print(json.dumps(closure, sort_keys=True)); return 0

if __name__ == "__main__": raise SystemExit(main())
