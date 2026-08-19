#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lottery_system.phase4e5.metadata import canonical, coverage, parse_dlt_notice, read_captured_body, sha256


ROOT = Path(__file__).resolve().parents[2]
ACQUISITION = ROOT / "artifacts/phase-4e5/acquisition"
OUTPUT = ROOT / "artifacts/phase-4e5/metadata-audit"
ROLE_RECEIPT = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"


def load_eligible(game: str) -> list[dict[str, object]]:
    roles = json.loads(ROLE_RECEIPT.read_text(encoding="utf-8"))["games"][game]
    return [json.loads(line) for line in (ROOT / roles["eligible_source"]).read_text(encoding="utf-8").splitlines()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    raw_inventory = json.loads((ACQUISITION / "raw-inventory.json").read_text(encoding="utf-8"))
    records: list[dict[str, object]] = []
    parse_errors: list[dict[str, str]] = []
    for directory in sorted((ACQUISITION / "raw").glob("dlt_official_notice_*")):
        issue = directory.name.rsplit("_", 1)[-1]
        try:
            records.append(parse_dlt_notice(directory, issue))
        except Exception as exc:
            parse_errors.append({"issue": issue, "error": f"{type(exc).__name__}: {exc}"})
    records.sort(key=lambda row: (str(row["draw_date"]), str(row["issue"])))
    eligible_dlt = {str(row["issue"]): row for row in load_eligible("dlt")}
    date_conflicts = [
        row["issue"] for row in records
        if row["issue"] not in eligible_dlt or row["draw_date"] != eligible_dlt[row["issue"]]["draw_date"]
    ]
    payload = b"".join(canonical(row) for row in records)
    (args.output / "dlt-official-metadata.jsonl").write_bytes(payload)
    probe_status = {}
    for source_id in (
        "ssq_official_history_page", "ssq_official_history_api",
        "ssq_official_distribution_announcement", "dlt_official_history_page",
        "dlt_official_history_index",
    ):
        _, receipt = read_captured_body(ACQUISITION / "raw" / source_id)
        probe_status[source_id] = {
            "status": receipt["response"]["status"],
            "body_bytes": receipt["response"]["body_bytes"],
            "body_sha256": receipt["response"]["body_sha256"],
            "receipt": str((ACQUISITION / "raw" / source_id / "receipt.json").relative_to(ROOT)),
        }
    dlt_coverage = coverage(records)
    required = [
        "sales", "jackpot", "first_prize_count", "first_prize_amount",
        "second_prize_count", "second_prize_amount",
    ]
    audit = {
        "artifact_type": "phase4e5_official_metadata_feasibility_and_coverage_audit",
        "raw_inventory": str((ACQUISITION / "raw-inventory.json").relative_to(ROOT)),
        "raw_inventory_sha256": sha256((ACQUISITION / "raw-inventory.json").read_bytes()),
        "raw_request_count": raw_inventory["request_count"],
        "probes": probe_status,
        "games": {
            "ssq": {
                "eligible_issue_count": len(load_eligible("ssq")),
                "official_operational_rows": 0,
                "field_coverage": {field: 0.0 for field in required + ["province_first_prize_distribution"]},
                "comparable_official_per_draw_metadata": False,
                "promotion_authority": False,
                "finding": "Official history, API, and announcement requests all returned HTTP 403; fields remain missing and no unofficial substitute is used.",
            },
            "dlt": {
                "eligible_issue_count": len(eligible_dlt),
                "requested_recent_issue_count": raw_inventory["dlt_issue_count"],
                "parsed_official_rows": len(records),
                "parse_errors": parse_errors,
                "date_or_identity_conflicts": date_conflicts,
                **dlt_coverage,
                "required_operational_coverage_minimum": min(dlt_coverage["field_coverage"][field] for field in required),
                "comparable_official_per_draw_metadata": not parse_errors and not date_conflicts and len(records) == raw_inventory["dlt_issue_count"],
                "promotion_authority": not parse_errors and not date_conflicts and len(records) == raw_inventory["dlt_issue_count"],
            },
        },
        "provincial_distribution_rule": "Enabled only if official reproducible per-draw overall and selection coverage are each at least 0.95.",
        "unofficial_substitution_count": 0,
        "all_games_comparable_official_metadata": False,
        "promotion_gate_result": "FAIL_CLOSED_SSQ_OFFICIAL_METADATA_UNAVAILABLE",
    }
    (args.output / "coverage-audit.json").write_bytes(canonical(audit))
    print(json.dumps({
        "dlt_rows": len(records), "dlt_parse_errors": len(parse_errors),
        "dlt_min_required_coverage": audit["games"]["dlt"]["required_operational_coverage_minimum"],
        "ssq_official_rows": 0, "promotion_gate_result": audit["promotion_gate_result"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
