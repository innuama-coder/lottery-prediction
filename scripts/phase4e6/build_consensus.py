#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

from lottery_system.phase4e6.consensus import IDENTITY_FIELDS, OPERATIONAL_FIELDS, canonical, consensus_issue, sha256
from lottery_system.phase4e6.sources import parse_00038, parse_17500, parse_official_dlt, read_capture


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e6"
ROLES = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"


def targets() -> dict[str, list[str]]:
    roles = json.loads(ROLES.read_text(encoding="utf-8")); result = {}
    for game in ("ssq", "dlt"):
        source = ROOT / roles["games"][game]["eligible_source"]
        draws = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        result[game] = [str(row["issue"]) for row in draws[-120:]]
    result["ssq"].extend(f"2026{value:03d}" for value in range(86, 96))
    return result


def verify_inventory() -> tuple[dict[str, object], list[str]]:
    inventory = json.loads((BASE / "acquisition/raw-inventory.json").read_text(encoding="utf-8")); failures = []
    for item in inventory["requests"]:
        directory = BASE / "acquisition/raw" / str(item["source_id"])
        try:
            raw, receipt = read_capture(directory)
            request_body = (directory / "request-body.bin").read_bytes()
            if sha256(request_body) != receipt["request"]["body_sha256"]:
                failures.append(f"request:{item['source_id']}")
            if sha256(raw) != receipt["response"]["body_sha256"]:
                failures.append(f"response:{item['source_id']}")
        except Exception as exc:
            failures.append(f"{item['source_id']}:{type(exc).__name__}")
    return inventory, failures


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, default=BASE / "consensus")
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    inventory, provenance_failures = verify_inventory(); observations = []; parse_failures = []
    for game, issues in targets().items():
        for issue in issues:
            for source, parser_fn in (("17500", parse_17500), ("00038", parse_00038)):
                directory = BASE / "acquisition/raw" / f"{source}_{game}_{issue}"
                try:
                    observations.append(parser_fn(directory, game, issue))
                except Exception as exc:
                    parse_failures.append({"game": game, "issue": issue, "source": source, "error": f"{type(exc).__name__}: {exc}"})
            if game == "dlt":
                directory = ROOT / "artifacts/phase-4e5/acquisition/raw" / f"dlt_official_notice_{issue}"
                try:
                    observations.append(parse_official_dlt(directory, issue))
                except Exception as exc:
                    parse_failures.append({"game": game, "issue": issue, "source": "gdlottery_official", "error": f"{type(exc).__name__}: {exc}"})
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in observations:
        grouped[(str(row["game"]), str(row["issue"]))].append(row)
    consensus_rows = []
    for game, issues in targets().items():
        for issue in issues:
            rows = grouped.get((game, issue), [])
            if rows:
                consensus_rows.append(consensus_issue(rows))
    observations.sort(key=lambda row: (str(row["game"]), str(row["issue"]), str(row["source_id"])))
    consensus_rows.sort(key=lambda row: (str(row["game"]), str(row["issue"])))
    (args.output / "source-observations.jsonl").write_bytes(b"".join(canonical(row) for row in observations))
    (args.output / "consensus-rows.jsonl").write_bytes(b"".join(canonical(row) for row in consensus_rows))
    games = {}
    for game in ("ssq", "dlt"):
        rows = [row for row in consensus_rows if row["game"] == game]
        historical = rows[:120]
        games[game] = {
            "issue_count": len(rows), "historical_audit_issue_count": len(historical),
            "identity_consensus_fraction": sum(bool(row["identity_consensus"]) for row in historical) / len(historical),
            "operational_consensus_fraction": sum(bool(row["operational_consensus"]) for row in historical) / len(historical),
            "accepted_fraction": sum(not bool(row["quarantined"]) for row in historical) / len(historical),
            "conflict_fraction": sum(bool(row["conflict"]) for row in historical) / len(historical),
            "field_consensus_fraction": {field: sum(row.get(field) is not None for row in historical) / len(historical) for field in (*IDENTITY_FIELDS, *OPERATIONAL_FIELDS)},
            "regional_distribution_consensus_fraction": 0.0,
            "regional_distribution_enabled": False,
        }
    probe_receipts = {source: json.loads((BASE / "acquisition/raw" / source / "receipt.json").read_text(encoding="utf-8")) for source in ("500_ssq_access_probe", "zhcw_ssq_api_probe", "cwl_ssq_api_probe")}
    body500, _ = read_capture(BASE / "acquisition/raw/500_ssq_access_probe")
    coverage = {
        "artifact_type": "phase4e6_coverage_report", "games": games,
        "promotion_minimum": 0.95,
        "both_games_coverage_gate_pass": all(game["accepted_fraction"] >= 0.95 for game in games.values()),
        "raw_provenance_complete": not provenance_failures,
        "access_probes": {
            "500": {"status": probe_receipts["500_ssq_access_probe"]["response"]["status"], "bot_challenge_detected": b"EO_Bot_Ssid" in body500, "bypassed": False},
            "zhcw_api": {"status": probe_receipts["zhcw_ssq_api_probe"]["response"]["status"], "error": probe_receipts["zhcw_ssq_api_probe"]["response"]["error"], "bypassed": False},
            "cwl_api": {"status": probe_receipts["cwl_ssq_api_probe"]["response"]["status"], "error": probe_receipts["cwl_ssq_api_probe"]["response"]["error"], "bypassed": False}
        },
    }
    coverage["receipt_sha256"] = sha256(canonical(coverage)); (args.output / "coverage-report.json").write_bytes(canonical(coverage))
    conflicts = {"artifact_type": "phase4e6_conflict_report", "quarantined": [{"game": row["game"], "issue": row["issue"], "conflict_fields": row["conflict_fields"], "missing_fields": row["missing_fields"], "source_count": row["source_count"]} for row in consensus_rows if row["quarantined"]], "parse_failures": parse_failures}
    conflicts["receipt_sha256"] = sha256(canonical(conflicts)); (args.output / "conflict-report.json").write_bytes(canonical(conflicts))
    lineage = {"artifact_type": "phase4e6_source_lineage", "sources": sorted({(row["source_id"], row["capture_group"], row["lineage"], str(row["suspected_common_upstream"])) for row in observations}), "p4e5_official_raw_reused_read_only": True, "p4e5_bytes_modified": False}
    lineage["sources"] = [{"source_id": a, "capture_group": b, "lineage": c, "suspected_common_upstream": d} for a, b, c, d in lineage["sources"]]
    lineage["receipt_sha256"] = sha256(canonical(lineage)); (args.output / "source-lineage.json").write_bytes(canonical(lineage))
    replay = {"artifact_type": "phase4e6_deterministic_replay_receipt", "raw_request_count": inventory["request_count"], "raw_provenance_failures": provenance_failures, "parse_failures": parse_failures, "observation_count": len(observations), "consensus_count": len(consensus_rows), "observations_sha256": sha256((args.output / "source-observations.jsonl").read_bytes()), "consensus_sha256": sha256((args.output / "consensus-rows.jsonl").read_bytes()), "pass": not provenance_failures and not parse_failures}
    replay["receipt_sha256"] = sha256(canonical(replay)); (args.output / "replay-receipt.json").write_bytes(canonical(replay))
    print(json.dumps({"coverage": games, "parse_failure_count": len(parse_failures), "provenance_failure_count": len(provenance_failures)}, sort_keys=True))
    return 0 if not provenance_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
