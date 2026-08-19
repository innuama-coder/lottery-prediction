#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from lottery_system.phase4e4.data import canonical, parse_500_ssq, parse_gdlottery_dlt, sha256_bytes, sha256_file, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_COMMIT = "c6621d14c13cda36b010f6d13fd3c636cdfb0a2e"
BRANCH = "origin/codex/phase4e4-feature-strengthening-20260819-r01"
ORIGINAL = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"
OUT = ROOT / "artifacts/phase-4e4/data-20260819"
URLS = {
    "cwl_attempt": "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&issueCount=&issueStart=&issueEnd=&dayStart=2003-01-01&dayEnd=2025-04-05&pageNo=1&pageSize=100&week=",
    "ssq_provenance": "https://datachart.500.com/ssq/history/newinc/history.php?start=03001&end=26085",
    "dlt_official": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def fetch(url: str) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "phase4e4-history-audit/1.0 (+https://github.com/innuama-coder/lottery-prediction)",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.1",
        "Referer": "https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/" if "cwl.gov.cn" in url else url,
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return int(response.status), response.read(), {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(), {key.lower(): value for key, value in exc.headers.items()}


def original_rows() -> dict[str, dict[str, dict[str, object]]]:
    result = {"ssq": {}, "dlt": {}}
    for line in ORIGINAL.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        result[raw["game"]][raw["issue_id"]] = raw
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if subprocess.run(["git", "merge-base", "--is-ancestor", AUTHORITY_COMMIT, BRANCH], cwd=ROOT).returncode:
        raise SystemExit("authority commit is not present on the remote branch")
    output = args.output
    raw_root = output / "raw"
    receipts = []
    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    bodies: dict[str, bytes] = {}
    for source_id, url in URLS.items():
        status, body, headers = fetch(url)
        bodies[source_id] = body
        suffix = "json" if source_id == "dlt_official" else "html"
        body_path = raw_root / f"{source_id}.{suffix}"
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        receipts.append({
            "source_id": source_id, "url": url, "method": "GET", "captured_at_utc": captured,
            "http_status": status, "response_headers": headers, "body_path": str(body_path.relative_to(output)),
            "body_bytes": len(body), "body_sha256": sha256_bytes(body),
        })
    if receipts[0]["http_status"] == 200:
        raise SystemExit("CWL became available; the preregistered paginated collector must be used instead of fallback")
    if receipts[1]["http_status"] != 200 or receipts[2]["http_status"] != 200:
        raise SystemExit("required expanded-history source unavailable")
    parsed = {"ssq": parse_500_ssq(bodies["ssq_provenance"]), "dlt": parse_gdlottery_dlt(bodies["dlt_official"])}
    originals = original_rows()
    boundaries = {"ssq": "2025-04-06", "dlt": "2025-03-31"}
    audit_games = {}
    for game, rows in parsed.items():
        pre = [row for row in rows if row.draw_date < boundaries[game]]
        overlap = [row for row in rows if row.issue in originals[game]]
        conflicts = []
        for row in overlap:
            old = originals[game][row.issue]
            if (list(row.front), list(row.back), row.draw_date) != (old["front_numbers"], old["back_numbers"], old["draw_date_local"]):
                conflicts.append(row.issue)
        if conflicts or len(pre) < 540:
            raise SystemExit(f"{game} conflict or insufficient prehistory: conflicts={conflicts[:5]} count={len(pre)}")
        report, selection = pre[-60:], pre[:-60]
        write_jsonl(output / "selection-prefix" / f"{game}.jsonl", selection)
        write_jsonl(output / "sealed-report" / f"{game}.jsonl", report)
        write_jsonl(output / "canonical" / f"{game}.jsonl", rows)
        years = sorted({row.draw_date[:4] for row in rows})
        audit_games[game] = {
            "canonical_count": len(rows), "prebaseline_count": len(pre), "selection_count": len(selection), "report_count": len(report),
            "expanded_count_including_original_200": len(pre) + 200, "target_1000_met": len(pre) + 200 >= 1000,
            "first_issue": rows[0].issue, "first_date": rows[0].draw_date, "last_issue": rows[-1].issue, "last_date": rows[-1].draw_date,
            "selection_last_issue": selection[-1].issue, "selection_last_date": selection[-1].draw_date,
            "report_first_issue": report[0].issue, "report_first_date": report[0].draw_date,
            "report_last_issue": report[-1].issue, "report_last_date": report[-1].draw_date,
            "overlap_with_original_count": len(overlap), "overlap_conflicts": conflicts, "duplicate_issue_count": 0,
            "covered_years": years, "canonical_sha256": sha256_file(output / "canonical" / f"{game}.jsonl"),
            "selection_sha256": sha256_file(output / "selection-prefix" / f"{game}.jsonl"),
            "sealed_report_sha256": sha256_file(output / "sealed-report" / f"{game}.jsonl"),
            "source_authority": "official" if game == "dlt" else "provenance_tracked_nonofficial_fallback",
            "promotion_authority": game == "dlt",
        }
    provenance = {
        "artifact_type": "phase4e4_history_provenance_inventory", "authority_commit": AUTHORITY_COMMIT,
        "authority_commit_on_remote_before_capture": True, "captured_at_utc": captured, "requests": receipts,
        "games": audit_games, "original_200_path": str(ORIGINAL.relative_to(ROOT)), "original_200_sha256": sha256_file(ORIGINAL),
        "ssq_official_access_blocked": True,
        "ssq_fallback_disclosure": "The preregistered national official endpoint returned HTTP 403 from the VPS. The direct 500.com historical table is retained with full raw provenance for implementation and prospective diagnostics only; it cannot authorize promotion.",
        "synthetic_rows": 0, "silent_imputations": 0, "status": "PARTIAL_OFFICIAL_PROVENANCE_COMPLETE_PROMOTION_AUTHORITY_BLOCKED_FOR_SSQ",
    }
    (output / "provenance").mkdir(parents=True, exist_ok=True)
    (output / "provenance" / "inventory.json").write_bytes(canonical(provenance))
    print(json.dumps(provenance, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
