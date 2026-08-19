#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lottery_system.phase4e5.metadata import canonical, sha256


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase-4e5"


def role_coverage() -> dict[str, object]:
    roles = json.loads((BASE / "roles/role-boundary-receipt.json").read_text(encoding="utf-8"))
    metadata = {
        row["issue"]: row
        for row in map(json.loads, (BASE / "metadata-audit/dlt-official-metadata.jsonl").read_text(encoding="utf-8").splitlines())
    }
    source = ROOT / roles["games"]["dlt"]["eligible_source"]
    draws = list(map(json.loads, source.read_text(encoding="utf-8").splitlines()))
    blocks = {"selection": draws[-240:-120], "report": draws[-120:]}
    dlt = {
        role: sum(metadata.get(row["issue"], {}).get("province_first_prize_distribution") is not None for row in rows) / len(rows)
        for role, rows in blocks.items()
    }
    return {
        "artifact_type": "phase4e5_role_specific_metadata_coverage_supplement",
        "dlt": {
            "province_first_prize_distribution": dlt,
            "minimum_required": 0.95,
            "conditional_enablement_pass": all(value >= 0.95 for value in dlt.values()),
        },
        "ssq": {"official_operational_coverage": 0.0, "conditional_enablement_pass": False},
        "unofficial_substitution_count": 0,
    }


def main() -> int:
    tests = json.loads((BASE / "acceptance/full-current-phase4-tests.json").read_text(encoding="utf-8"))
    independent = json.loads((BASE / "acceptance/independent-acceptance.json").read_text(encoding="utf-8"))
    decision = json.loads((BASE / "delivery/decision.json").read_text(encoding="utf-8"))
    if tests["status"] != "PASS" or independent["status"] != "PASS":
        raise SystemExit("acceptance is not green")
    supplement = role_coverage()
    (BASE / "metadata-audit/role-coverage-supplement.json").write_bytes(canonical(supplement))
    excluded = {"final-evidence-manifest.json", "final-closure.json"}
    files = [
        {"path": str(path.relative_to(BASE)), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
        for path in sorted(BASE.rglob("*")) if path.is_file() and path.name not in excluded
    ]
    manifest = {
        "artifact_type": "phase4e5_final_evidence_manifest", "file_count": len(files),
        "files": files, "manifest_sha256": sha256(canonical(files)),
    }
    (BASE / "final-evidence-manifest.json").write_bytes(canonical(manifest))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    closure = {
        "artifact_type": "phase4e5_final_closure", "terminal_state": decision["terminal_state"],
        "serving_release": "P4-P4E2-20260815-r12", "serving_release_changed": False,
        "p4e4_terminal_state": "FEATURE_ENGINEERING_DELIVERED_PROSPECTIVE_ONLY",
        "p4e4_and_prior_release_bytes_unchanged": True,
        "official_comparable_metadata_both_games": False,
        "scientific_promotion_gates_all_games": False,
        "release_allocation": "FORBIDDEN", "no_pull_request": True,
        "full_phase4_test_count": tests["results"][1]["test_count"],
        "full_phase4_tests_receipt_sha256": tests["receipt_sha256"],
        "independent_acceptance_receipt_sha256": independent["receipt_sha256"],
        "final_evidence_manifest_sha256": manifest["manifest_sha256"],
        "delivery_checkpoint": head,
    }
    closure["receipt_sha256"] = sha256(canonical(closure))
    (BASE / "final-closure.json").write_bytes(canonical(closure))
    print(json.dumps(closure, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
