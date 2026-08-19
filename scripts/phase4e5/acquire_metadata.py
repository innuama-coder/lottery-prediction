#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/phase-4e5/acquisition"
AUTHORITY_COMMIT = "4766af6d"
REMOTE_BRANCH = "origin/codex/phase4e5-exogenous-metadata-20260820-r01"
ROLE_RECEIPT = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"

PROBES = (
    (
        "ssq_official_history_page",
        "https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/",
        "https://www.cwl.gov.cn/",
    ),
    (
        "ssq_official_history_api",
        "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&issueCount=&issueStart=&issueEnd=&dayStart=2003-01-01&dayEnd=2024-11-05&pageNo=1&pageSize=100&week=",
        "https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/",
    ),
    (
        "ssq_official_distribution_announcement",
        "https://www.cwl.gov.cn/c/2025/09/09/627575.shtml",
        "https://www.cwl.gov.cn/",
    ),
    (
        "dlt_official_history_page",
        "https://m.lottery.gov.cn/zst/dlt/?tt_force_outside=1",
        "https://m.lottery.gov.cn/",
    ),
    (
        "dlt_official_history_index",
        "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
        "https://www.gdlottery.cn/html/dlt/",
    ),
    (
        "holiday_2021_official",
        "https://www.gov.cn/zhengce/content/2020-11/25/content_5564127.htm",
        "https://www.gov.cn/",
    ),
    (
        "holiday_2022_official",
        "https://www.gov.cn/zhengce/content/2021-10/25/content_5644835.htm",
        "https://www.gov.cn/",
    ),
    (
        "holiday_2023_official",
        "https://www.gov.cn/zhengce/content/2022-12/08/content_5730844.htm",
        "https://www.gov.cn/",
    ),
    (
        "holiday_2024_official",
        "https://www.gov.cn/zhengce/content/202310/content_6911527.htm",
        "https://www.gov.cn/",
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def assert_authority() -> None:
    if not ROLE_RECEIPT.is_file():
        raise SystemExit("role-boundary receipt must exist before acquisition")
    subprocess.run(["git", "fetch", "origin", REMOTE_BRANCH.removeprefix("origin/")], cwd=ROOT, check=True)
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", AUTHORITY_COMMIT, REMOTE_BRANCH],
        cwd=ROOT,
        check=True,
    )


def issue_ids(game: str) -> list[str]:
    role = json.loads(ROLE_RECEIPT.read_text(encoding="utf-8"))["games"][game]
    source = ROOT / role["eligible_source"]
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    if game == "dlt":
        rows = rows[-480:]
    return [str(row["issue"]) for row in rows]


def fetch(source_id: str, url: str, referer: str, output: Path) -> dict[str, object]:
    directory = output / "raw" / source_id
    if directory.is_dir():
        receipt_path = directory / "receipt.json"
        if not receipt_path.is_file():
            raise RuntimeError(f"incomplete pre-existing capture: {directory}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        raw_path = directory / "response-body.bin"
        gzip_path = directory / "response-body.bin.gz"
        if gzip_path.is_file() and receipt["response"]["body_path"] == "response-body.bin":
            stored = gzip_path.read_bytes()
            receipt["response"].update({
                "body_path": "response-body.bin.gz",
                "body_storage_encoding": "gzip",
                "stored_body_bytes": len(stored),
                "stored_body_sha256": sha256(stored),
            })
            receipt_path.write_bytes(canonical(receipt))
        elif raw_path.is_file():
            raw = raw_path.read_bytes()
            stored = gzip.compress(raw, compresslevel=9, mtime=0)
            gzip_path.write_bytes(stored)
            raw_path.unlink()
            receipt["response"].update({
                "body_path": "response-body.bin.gz",
                "body_storage_encoding": "gzip",
                "stored_body_bytes": len(stored),
                "stored_body_sha256": sha256(stored),
            })
            receipt_path.write_bytes(canonical(receipt))
        return receipt
    request_body = b""
    request_headers = {
        "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "Referer": referer,
        "User-Agent": "phase4e5-official-metadata-audit/1.0 (+https://github.com/innuama-coder/lottery-prediction)",
    }
    request = urllib.request.Request(url, data=None, headers=request_headers, method="GET")
    started = utc_now()
    response_body = b""
    response_headers: dict[str, str] = {}
    error: str | None = None
    status: int | None = None
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = int(response.status)
            response_body = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_body = exc.read()
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
        error = f"HTTPError: {exc}"
    except Exception as exc:  # the failed attempt is itself required audit evidence
        error = f"{type(exc).__name__}: {exc}"
    finished = utc_now()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "request-body.bin").write_bytes(request_body)
    stored_response_body = gzip.compress(response_body, compresslevel=9, mtime=0)
    (directory / "response-body.bin.gz").write_bytes(stored_response_body)
    request_record = {
        "method": "GET",
        "url": url,
        "headers": request_headers,
        "body_path": "request-body.bin",
        "body_bytes": len(request_body),
        "body_sha256": sha256(request_body),
    }
    response_record = {
        "status": status,
        "headers": response_headers,
        "body_path": "response-body.bin.gz",
        "body_bytes": len(response_body),
        "body_sha256": sha256(response_body),
        "body_storage_encoding": "gzip",
        "stored_body_bytes": len(stored_response_body),
        "stored_body_sha256": sha256(stored_response_body),
        "error": error,
    }
    (directory / "request.json").write_bytes(canonical(request_record))
    (directory / "response-headers.json").write_bytes(canonical(response_headers))
    receipt = {
        "source_id": source_id,
        "request": request_record,
        "response": response_record,
        "request_started_at_utc": started,
        "response_finished_at_utc": finished,
    }
    (directory / "receipt.json").write_bytes(canonical(receipt))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--dlt-limit", type=int, default=480)
    parser.add_argument("--delay", type=float, default=0.03)
    args = parser.parse_args()
    assert_authority()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    receipts: list[dict[str, object]] = []
    for source_id, url, referer in PROBES:
        receipts.append(fetch(source_id, url, referer, output))
    dlt_ids = issue_ids("dlt")[-args.dlt_limit :]
    for index, issue in enumerate(dlt_ids, start=1):
        short_issue = issue[2:]
        source_id = f"dlt_official_notice_{issue}"
        url = f"https://www.gdlottery.cn/f_html/kjgg/P085_{short_issue}.html"
        receipts.append(fetch(source_id, url, "https://www.gdlottery.cn/html/dlt/", output))
        if args.delay and index != len(dlt_ids):
            time.sleep(args.delay)
    inventory = {
        "artifact_type": "phase4e5_raw_acquisition_inventory",
        "authority_commit": AUTHORITY_COMMIT,
        "authority_commit_on_remote_before_acquisition": True,
        "role_receipt_sha256": sha256(ROLE_RECEIPT.read_bytes()),
        "request_count": len(receipts),
        "requests": receipts,
        "dlt_issue_count": len(dlt_ids),
        "dlt_first_issue": dlt_ids[0] if dlt_ids else None,
        "dlt_last_issue": dlt_ids[-1] if dlt_ids else None,
    }
    (output / "raw-inventory.json").write_bytes(canonical(inventory))
    print(json.dumps({
        "request_count": len(receipts),
        "status_counts": {
            str(status): sum(1 for receipt in receipts if receipt["response"]["status"] == status)
            for status in sorted({receipt["response"]["status"] for receipt in receipts}, key=lambda value: str(value))
        },
        "inventory_sha256": sha256((output / "raw-inventory.json").read_bytes()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
