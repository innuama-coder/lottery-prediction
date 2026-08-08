#!/usr/bin/env python3
"""Bounded, read-only point-in-time collection reconnaissance.

This script probes an independent archive (the Internet Archive Wayback Machine
availability API) for the official SSQ and DLT result endpoints.  Its ONLY
purpose is to record, with preserved HTTP receipts, whether any auditable
archived publication exists that could bind (game, issue, numbers) to a
historical availability time for the frozen Phase 3 draws.

It derives NO eligibility.  Homepage snapshots (current view) and missing
per-issue result snapshots are both insufficient under the Phase 3 binding rule.
Failures, timeouts and empty results are preserved as receipts, never dropped.

Usage:
  python3 scripts/phase3/pit_collect_recon.py --output <receipts-dir>
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


TARGETS = [
    {"name": "ssq-official-home", "url": "https://www.cwl.gov.cn/",
     "note": "official SSQ (welfare lottery) homepage; a homepage snapshot is current view and cannot bind a per-issue result"},
    {"name": "ssq-result-api", "url": "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq",
     "note": "official SSQ per-issue result API; the only endpoint that could bind issue+numbers"},
    {"name": "dlt-official-home", "url": "https://www.lottery.gov.cn/",
     "note": "official DLT (sports lottery) homepage; current view, cannot bind a per-issue result"},
    {"name": "dlt-history-api", "url": "https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry?gameNo=851&pageNo=1&pageSize=1",
     "note": "official DLT per-issue history API; the only endpoint that could bind issue+numbers"},
]

QUERY_TIMESTAMP = "20250401"


def probe(target: dict, timeout: float = 12.0) -> dict:
    api = "http://archive.org/wayback/available?" + urllib.parse.urlencode(
        {"url": target["url"], "timestamp": QUERY_TIMESTAMP}
    )
    receipt = {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_recon_receipt",
        "name": target["name"],
        "target_url": target["url"],
        "target_note": target["note"],
        "query_timestamp": QUERY_TIMESTAMP,
        "availability_api_url": api,
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "eligibility_derived": False,
    }
    try:
        request = urllib.request.Request(api, headers={"User-Agent": "phase3-pit-recon/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        snapshot = (payload.get("archived_snapshots") or {}).get("closest") or {}
        receipt.update({
            "status": "OK",
            "http_status": response.status,
            "snapshot_available": bool(snapshot.get("available")),
            "snapshot_timestamp": snapshot.get("timestamp"),
            "snapshot_url": snapshot.get("url"),
            "snapshot_status": snapshot.get("status"),
        })
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        receipt.update({"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)[:300]})
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 scripts/phase3/pit_collect_recon.py")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"schema_version": "3.0.0", "artifact_type": "phase3_pit_recon_summary",
               "query_timestamp": QUERY_TIMESTAMP, "read_only": True, "eligibility_derived": False,
               "targets": []}
    for target in TARGETS:
        receipt = probe(target)
        (args.output / f"{target['name']}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        summary["targets"].append({
            "name": target["name"],
            "status": receipt["status"],
            "snapshot_available": receipt.get("snapshot_available"),
            "binds_per_issue_result": False,
        })
    eligible = sum(1 for t in summary["targets"] if t["snapshot_available"] and t["binds_per_issue_result"])
    summary["snapshots_that_bind_per_issue_result"] = eligible
    summary["conclusion"] = "no archived snapshot binds (game, issue, numbers, availability time) for any frozen draw"
    (args.output / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
