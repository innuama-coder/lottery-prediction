from __future__ import annotations

import gzip
import html
import json
import re
from datetime import date
from pathlib import Path

from lottery_system.phase4e5.metadata import parse_dlt_notice, visible_lines

from .consensus import OPERATIONAL_FIELDS, sha256


def read_capture(directory: Path) -> tuple[bytes, dict[str, object]]:
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    response = receipt["response"]
    stored = (directory / str(response["body_path"])).read_bytes()
    if sha256(stored) != response["stored_body_sha256"]:
        raise ValueError(f"stored body digest mismatch: {directory}")
    raw = gzip.decompress(stored) if response["body_storage_encoding"] == "gzip" else stored
    if len(raw) != response["body_bytes"] or sha256(raw) != response["body_sha256"]:
        raise ValueError(f"raw body digest mismatch: {directory}")
    return raw, receipt


def _receipt_path(directory: Path) -> str:
    parts = directory.parts
    return Path(*parts[parts.index("artifacts") :], "receipt.json").as_posix() if "artifacts" in parts else str(directory / "receipt.json")


def _money(value: str) -> float:
    return float(value.replace(",", ""))


def _prizes(text: str) -> tuple[int | None, float | None, int | None, float | None]:
    rows = re.findall(r"<tr[^>]*>\s*<td>\s*(一等奖|二等奖)\s*</td>\s*<td>([\d,]+)(?:\s*元)?</td>\s*<td>([\d,]+)(?:\s*注)?</td>", text, re.I)
    by_label = {label: (int(count.replace(",", "")), _money(amount)) for label, amount, count in rows}
    first = by_label.get("一等奖", (None, None))
    second = by_label.get("二等奖", (None, None))
    return first[0], first[1], second[0], second[1]


def parse_17500(directory: Path, game: str, expected_issue: str) -> dict[str, object]:
    raw, receipt = read_capture(directory)
    if receipt["response"]["status"] != 200:
        raise ValueError("17500 capture is inaccessible")
    text = raw.decode("utf-8", errors="strict")
    issue_match = re.search(r"<b>\s*(\d{7})期\s*</b>", text)
    date_match = re.search(r"<h3><i>\s*(\d{2})-(\d{2})", text)
    balls_match = re.search(r'<p class="t">(.*?)</p>', text, re.S)
    if not issue_match or issue_match.group(1) != expected_issue or not date_match or not balls_match:
        raise ValueError(f"17500 identity parse failure: {expected_issue}")
    red = [int(value) for value in re.findall(r'class="rb">(\d{2})</b>', balls_match.group(1))]
    blue = [int(value) for value in re.findall(r'class="bb">(\d{2})</b>', balls_match.group(1))]
    sales = re.search(r"(?:投注总额|本期销售额)：([\d,]+)元", text)
    jackpot = re.search(r"(?:奖池金额|滚入下期奖金)：([\d,.]+)元", text)
    prize_rows = re.findall(r"<tr[^>]*>\s*<td>\s*(一等奖|二等奖)\s*</td>\s*<td>\s*([\d,]+)\s*</td>\s*<td>\s*([\d,]+)\s*</td>", text, re.I)
    prizes: dict[str, tuple[int, float]] = {}
    for label, count, amount in prize_rows:
        prizes.setdefault(label, (int(count.replace(",", "")), _money(amount)))
    first_count, first_amount = prizes.get("一等奖", (None, None))
    second_count, second_amount = prizes.get("二等奖", (None, None))
    return {
        "game": game,
        "issue": expected_issue,
        "draw_date": f"{expected_issue[:4]}-{date_match.group(1)}-{date_match.group(2)}",
        "front": red,
        "back": blue,
        "sales": _money(sales.group(1)) if sales else None,
        "jackpot": _money(jackpot.group(1)) if jackpot else None,
        "first_prize_count": first_count,
        "first_prize_amount": first_amount,
        "second_prize_count": second_count,
        "second_prize_amount": second_amount,
        "source_id": "17500_per_issue",
        "capture_group": "17500_direct_capture",
        "accessible": True,
        "lineage": "17500 public per-issue result endpoint; archive states results should be checked against lottery-center announcements",
        "suspected_common_upstream": "national lottery draw announcement; independently rendered/transcribed archive",
        "raw_receipt": _receipt_path(directory),
        "raw_body_sha256": receipt["response"]["body_sha256"],
    }


def parse_00038(directory: Path, game: str, expected_issue: str) -> dict[str, object]:
    raw, receipt = read_capture(directory)
    if receipt["response"]["status"] != 200:
        raise ValueError("00038 capture is inaccessible")
    text = raw.decode("utf-8", errors="strict")
    issue_match = re.search(rf"var\s+s_qi\s*=\s*{re.escape(expected_issue)}\s*;", text)
    date_match = re.search(r"开奖时间：(\d{4}-\d{2}-\d{2})", text)
    balls_match = re.search(r'<div class="ballBox ball40.*?">(.*?)</div>', text, re.S)
    if not issue_match or not date_match or not balls_match:
        raise ValueError(f"00038 identity parse failure: {expected_issue}")
    all_balls = [("blue" in attrs, int(value)) for attrs, value in re.findall(r'<span class="ball([^"]*)">(\d{2})</span>', balls_match.group(1))]
    front = [value for blue, value in all_balls if not blue]
    back = [value for blue, value in all_balls if blue]
    first_count, first_amount, second_count, second_amount = _prizes(text)
    return {
        "game": game,
        "issue": expected_issue,
        "draw_date": date_match.group(1),
        "front": front,
        "back": back,
        "sales": None,
        "jackpot": None,
        "first_prize_count": first_count,
        "first_prize_amount": first_amount,
        "second_prize_count": second_count,
        "second_prize_amount": second_amount,
        "source_id": "00038_per_issue",
        "capture_group": "00038_direct_capture",
        "accessible": True,
        "lineage": "00038 public per-issue archive; exact balls and prize table retained; displayed sales and jackpot are rounded and deliberately treated as missing",
        "suspected_common_upstream": "national lottery draw announcement; independently rendered archive",
        "raw_receipt": _receipt_path(directory),
        "raw_body_sha256": receipt["response"]["body_sha256"],
    }


def parse_official_dlt(directory: Path, expected_issue: str) -> dict[str, object]:
    parsed = parse_dlt_notice(directory, expected_issue)
    raw, receipt = read_capture(directory)
    lines = visible_lines(raw)
    try:
        start = lines.index("本期开奖号码：")
        front = [int(value) for value in lines[start + 1].split()]
        back = [int(value) for value in lines[start + 2].split()]
    except (ValueError, IndexError):
        raise ValueError(f"official DLT ball parse failure: {expected_issue}") from None
    return {
        **{field: parsed.get(field) for field in ("game", "issue", "draw_date", *OPERATIONAL_FIELDS)},
        "front": front,
        "back": back,
        "source_id": "gdlottery_official_per_draw_notice",
        "capture_group": "gdlottery_official_direct_capture",
        "accessible": True,
        "lineage": "Guangdong Sports Lottery official per-draw national DLT announcement",
        "suspected_common_upstream": None,
        "raw_receipt": _receipt_path(directory),
        "raw_body_sha256": receipt["response"]["body_sha256"],
        "regional_distribution": parsed.get("province_first_prize_distribution"),
    }
