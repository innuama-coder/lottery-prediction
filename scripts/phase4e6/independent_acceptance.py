#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import math
import subprocess
from pathlib import Path

from lottery_system.phase4e6.consensus import canonical, sha256

ROOT = Path(__file__).resolve().parents[2]; BASE = ROOT / "artifacts/phase4e6"; DELIVERY = BASE / "delivery"; ACCEPT = BASE / "acceptance"
RULES = {"ssq": ((33, 6), (16, 1)), "dlt": ((35, 5), (12, 2))}

def rows(path: Path): return [json.loads(line) for line in path.read_text().splitlines()]
def elementary(weights, k):
    result = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1): result[order] += weight * result[order - 1]
    return result[k]
def weights(draws, key, n):
    counts = [0] * n
    for draw in draws:
        for value in draw[key]: counts[int(value) - 1] += 1
    rates = [(count + 0.5) / (len(draws) + 1.0) for count in counts]
    raw = [rate / (1 - rate) for rate in rates]; maximum = max(raw); return [value / maximum for value in raw]

def main() -> int:
    failures = []; inventory = json.loads((BASE / "acquisition/raw-inventory.json").read_text())
    for item in inventory["requests"]:
        directory = BASE / "acquisition/raw" / item["source_id"]; receipt = json.loads((directory / "receipt.json").read_text())
        stored = (directory / receipt["response"]["body_path"]).read_bytes(); raw = gzip.decompress(stored)
        if hashlib.sha256(raw).hexdigest() != receipt["response"]["body_sha256"]: failures.append(f"raw:{item['source_id']}")
    replay = {}
    consensus = rows(BASE / "consensus/consensus-rows.jsonl")
    for game in ("ssq", "dlt"):
        draws = rows(ROOT / f"artifacts/phase-4e4/data-20260819/canonical/{game}.jsonl")
        if game == "ssq":
            known = {row["issue"] for row in draws}
            draws += [{"issue": row["issue"], "draw_date": row["draw_date"], "front": row["front"], "back": row["back"]} for row in consensus if row["game"] == game and row["identity_consensus"] and row["issue"] not in known]
            draws.sort(key=lambda row: (row["draw_date"], row["issue"]))
        zone_weights = [weights(draws, key, spec[0]) for key, spec in zip(("front", "back"), RULES[game])]
        norms = [elementary(zone_weights[z], RULES[game][z][1]) for z in range(2)]
        expected = rows(DELIVERY / f"top1000/{game}-top1000-shadow.jsonl"); errors = []
        for ticket in expected:
            probability = math.prod(zone_weights[0][v - 1] for v in ticket["front"]) / norms[0] * math.prod(zone_weights[1][v - 1] for v in ticket["back"]) / norms[1]
            errors.append(abs(probability - ticket["joint_probability"]))
        ok = len(expected) == 1000 and max(errors) <= 1e-18 and all(a["joint_probability"] >= b["joint_probability"] for a, b in zip(expected, expected[1:]))
        replay[game] = {"row_count": len(expected), "maximum_probability_absolute_error": max(errors), "ordered": ok, "pass": ok}
        if not ok: failures.append(f"top1000:{game}")
    tests = json.loads((ACCEPT / "full-phase4-tests.json").read_text())
    if tests["status"] != "PASS": failures.append("full_tests")
    if subprocess.run(["git", "diff", "--quiet", "40afa230", "--", "artifacts/phase-4", "artifacts/phase-4e3", "artifacts/phase-4e4", "artifacts/phase-4e5"], cwd=ROOT).returncode: failures.append("prior_bytes")
    decision = json.loads((DELIVERY / "decision.json").read_text())
    if decision["terminal_status"] != "PROSPECTIVE_ONLY" or decision["serving_release_changed"]: failures.append("decision")
    payload = {"artifact_type": "phase4e6_independent_acceptance", "status": "PASS" if not failures else "FAIL", "blocking_findings": failures, "independent_top1000_replay": replay, "raw_provenance_request_count": inventory["request_count"], "consensus_rows_sha256": sha256((BASE / "consensus/consensus-rows.jsonl").read_bytes()), "report_labels_read": False, "report_evaluations": 0, "serving_release": "P4-P4E2-20260815-r12", "terminal_status": "PROSPECTIVE_ONLY"}
    payload["receipt_sha256"] = sha256(canonical(payload)); (ACCEPT / "independent-acceptance.json").write_bytes(canonical(payload)); print(json.dumps(payload, sort_keys=True)); return 0 if not failures else 1

if __name__ == "__main__": raise SystemExit(main())
