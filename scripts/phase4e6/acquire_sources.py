#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from lottery_system.phase4e6.consensus import canonical, sha256


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e6"
ROLE = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def capture(source_id: str, method: str, url: str, body: bytes, headers: dict[str, str], directory: Path) -> dict[str, object]:
    if directory.is_dir():
        return json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    request = urllib.request.Request(url, data=body if method == "POST" else None, headers=headers, method=method)
    started = utc_now(); response_body = b""; response_headers: dict[str, str] = {}; status = None; error = None
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = int(response.status); response_body = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        status = int(exc.code); response_body = exc.read()
        response_headers = {key.lower(): value for key, value in exc.headers.items()}; error = f"HTTPError: {exc}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finished = utc_now()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "request-body.bin").write_bytes(body)
    stored = gzip.compress(response_body, compresslevel=9, mtime=0)
    (directory / "response-body.bin.gz").write_bytes(stored)
    request_record = {"method": method, "url": url, "headers": headers, "body_path": "request-body.bin", "body_bytes": len(body), "body_sha256": sha256(body)}
    response_record = {"status": status, "headers": response_headers, "body_path": "response-body.bin.gz", "body_bytes": len(response_body), "body_sha256": sha256(response_body), "body_storage_encoding": "gzip", "stored_body_bytes": len(stored), "stored_body_sha256": sha256(stored), "error": error}
    receipt = {"source_id": source_id, "request": request_record, "response": response_record, "request_started_at_utc": started, "response_finished_at_utc": finished}
    (directory / "request.json").write_bytes(canonical(request_record))
    (directory / "response-headers.json").write_bytes(canonical(response_headers))
    (directory / "receipt.json").write_bytes(canonical(receipt))
    return receipt


def jobs(output: Path) -> list[tuple[str, str, str, bytes, dict[str, str], Path]]:
    roles = json.loads(ROLE.read_text(encoding="utf-8"))
    common = {"Accept": "text/html,*/*;q=0.1", "Accept-Language": "zh-CN,zh;q=0.9", "User-Agent": "phase4e6-consensus-audit/1.0 (+https://github.com/innuama-coder/lottery-prediction)"}
    result = []
    for game in ("ssq", "dlt"):
        source = ROOT / roles["games"][game]["eligible_source"]
        draws = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        issues = [str(row["issue"]) for row in draws[-120:]]
        if game == "ssq":
            issues.extend(f"2026{value:03d}" for value in range(86, 96))
        for issue in issues:
            form = urllib.parse.urlencode({"lotid": game, "page": 1, "limit": 30, "ish": 0, "issue": issue}).encode()
            h17500 = {**common, "Content-Type": "application/x-www-form-urlencoded", "Referer": f"https://{game}.17500.cn/win/list.html"}
            result.append((f"17500_{game}_{issue}", "POST", f"https://{game}.17500.cn/win/getlist.html", form, h17500, output / "raw" / f"17500_{game}_{issue}"))
            url38 = f"https://www.00038.cn/kjh/{game}/{issue}.htm"
            result.append((f"00038_{game}_{issue}", "GET", url38, b"", {**common, "Referer": f"https://www.00038.cn/kjh/{game}/"}, output / "raw" / f"00038_{game}_{issue}"))
    blocked = [
        ("500_ssq_access_probe", "GET", "https://kaijiang.500.com/shtml/ssq/2024008.shtml", b"", common, output / "raw" / "500_ssq_access_probe"),
        ("zhcw_ssq_api_probe", "GET", "https://jc.zhcw.com/port/client_json.php?transactionType=10001002&lotteryId=1&issue=2024008&callback=p4e6", b"", {**common, "Referer": "https://www.zhcw.com/"}, output / "raw" / "zhcw_ssq_api_probe"),
        ("cwl_ssq_api_probe", "GET", "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&issueCount=&issueStart=2024008&issueEnd=2024008&dayStart=&dayEnd=&pageNo=1&pageSize=30&week=", b"", {**common, "Referer": "https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/"}, output / "raw" / "cwl_ssq_api_probe"),
    ]
    return result + blocked


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, default=BASE / "acquisition"); parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    work = jobs(args.output); receipts = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(capture, *item): item[0] for item in work}
        for future in as_completed(futures):
            receipts.append(future.result())
    receipts.sort(key=lambda row: str(row["source_id"]))
    inventory = {"artifact_type": "phase4e6_raw_acquisition_inventory", "design_checkpoint": "e4e118bb", "design_on_remote_before_acquisition": True, "request_count": len(receipts), "requests": receipts}
    inventory["inventory_sha256"] = sha256(canonical(inventory))
    (args.output / "raw-inventory.json").write_bytes(canonical(inventory))
    print(json.dumps({"request_count": len(receipts), "status_counts": {str(status): sum(row["response"]["status"] == status for row in receipts) for status in sorted({row["response"]["status"] for row in receipts}, key=str)}, "inventory_sha256": inventory["inventory_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
