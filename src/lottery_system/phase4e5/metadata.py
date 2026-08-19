from __future__ import annotations

import gzip
import hashlib
import html
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable


OPERATIONAL_FIELDS = (
    "sales",
    "jackpot",
    "first_prize_count",
    "first_prize_amount",
    "second_prize_count",
    "second_prize_amount",
    "province_first_prize_distribution",
)

HOLIDAYS: dict[int, tuple[tuple[str, str], ...]] = {
    2021: (("01-01", "01-03"), ("02-11", "02-17"), ("04-03", "04-05"),
           ("05-01", "05-05"), ("06-12", "06-14"), ("09-19", "09-21"), ("10-01", "10-07")),
    2022: (("01-01", "01-03"), ("01-31", "02-06"), ("04-03", "04-05"),
           ("04-30", "05-04"), ("06-03", "06-05"), ("09-10", "09-12"), ("10-01", "10-07")),
    2023: (("01-01", "01-02"), ("01-21", "01-27"), ("04-05", "04-05"),
           ("04-29", "05-03"), ("06-22", "06-24"), ("09-29", "10-06")),
    2024: (("01-01", "01-01"), ("02-10", "02-17"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-08", "06-10"), ("09-15", "09-17"), ("10-01", "10-07")),
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_captured_body(directory: Path) -> tuple[bytes, dict[str, object]]:
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    response = receipt["response"]
    path = directory / str(response["body_path"])
    stored = path.read_bytes()
    if response.get("stored_body_sha256") and sha256(stored) != response["stored_body_sha256"]:
        raise ValueError(f"stored response digest mismatch: {directory}")
    raw = gzip.decompress(stored) if response.get("body_storage_encoding") == "gzip" else stored
    if len(raw) != response["body_bytes"] or sha256(raw) != response["body_sha256"]:
        raise ValueError(f"raw response digest mismatch: {directory}")
    return raw, receipt


def visible_lines(body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="strict")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    return [clean for line in text.splitlines() if (clean := re.sub(r"\s+", " ", line).strip())]


def _money(value: str) -> float:
    return float(value.replace(",", ""))


def _first(lines: list[str], pattern: str) -> re.Match[str] | None:
    regex = re.compile(pattern)
    return next((match for line in lines if (match := regex.search(line))), None)


def _prize(lines: list[str], label: str) -> tuple[int | None, float | None]:
    try:
        start = lines.index(label)
    except ValueError:
        return None, None
    count = _first(lines[start + 1 : start + 10], r"^([\d,]+)注$")
    amount = _first(lines[start + 1 : start + 12], r"^([\d,.]+)元$")
    return (
        int(count.group(1).replace(",", "")) if count else None,
        _money(amount.group(1)) if amount else None,
    )


def _province_distribution(lines: list[str]) -> dict[str, int] | None:
    line = next((item for item in lines if item.startswith("本期一等奖出自：")), None)
    if line is None:
        return None
    pairs = re.findall(r"([^：、，,。(]+)\(基本(\d+)注", line)
    if not pairs:
        return None
    return {province.strip(): int(count) for province, count in pairs}


def parse_dlt_notice(directory: Path, expected_issue: str) -> dict[str, object]:
    body, receipt = read_captured_body(directory)
    if receipt["response"]["status"] != 200:
        raise ValueError(f"non-200 DLT notice: {expected_issue}")
    lines = visible_lines(body)
    issue_match = _first(lines, r"第(\d{5})期开奖公告")
    date_match = _first(lines, r"开奖日期：(\d{4})年(\d{1,2})月(\d{1,2})日")
    sales_match = _first(lines, r"本期全国销售金额：([\d,]+)元")
    jackpot_match = _first(lines, r"([\d,.]+)元奖金滚入下期奖池")
    first_count, first_amount = _prize(lines, "一等奖")
    second_count, second_amount = _prize(lines, "二等奖")
    parsed_issue = "20" + issue_match.group(1) if issue_match else None
    if parsed_issue != expected_issue:
        raise ValueError(f"DLT notice issue mismatch: expected={expected_issue} parsed={parsed_issue}")
    draw_date = None
    if date_match:
        draw_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))).isoformat()
    distribution = _province_distribution(lines)
    response_headers = receipt["response"]["headers"]
    parts = directory.parts
    provenance_path = Path(*parts[parts.index("artifacts") :]) / "receipt.json" if "artifacts" in parts else directory / "receipt.json"
    result = {
        "game": "dlt",
        "issue": expected_issue,
        "draw_date": draw_date,
        "sales": _money(sales_match.group(1)) if sales_match else None,
        "jackpot": _money(jackpot_match.group(1)) if jackpot_match else None,
        "first_prize_count": first_count,
        "first_prize_amount": first_amount,
        "second_prize_count": second_count,
        "second_prize_amount": second_amount,
        "province_first_prize_distribution": distribution,
        "source_id": "gdlottery_official_per_draw_notice",
        "source_revision": response_headers.get("etag") or response_headers.get("last-modified"),
        "source_body_sha256": receipt["response"]["body_sha256"],
        "source_receipt": provenance_path.as_posix(),
    }
    result["field_missing"] = {field: result[field] is None for field in OPERATIONAL_FIELDS}
    result["block_missing_fraction"] = sum(result["field_missing"].values()) / len(OPERATIONAL_FIELDS)
    return result


def holiday_dates() -> set[date]:
    result: set[date] = set()
    for year, ranges in HOLIDAYS.items():
        for first, last in ranges:
            start = date.fromisoformat(f"{year}-{first}")
            end_year = year + (last < first)
            end = date.fromisoformat(f"{end_year}-{last}")
            cursor = start
            while cursor <= end:
                result.add(cursor)
                cursor += timedelta(days=1)
    return result


def calendar_fields(draw_date: str, prior_date: str | None, game: str) -> dict[str, object]:
    current = date.fromisoformat(draw_date)
    holidays = holiday_dates()
    known = current.year in HOLIDAYS
    distances = sorted(abs((current - holiday).days) for holiday in holidays) if holidays else [30]
    prior_holidays = [(current - holiday).days for holiday in holidays if holiday <= current]
    future_holidays = [(holiday - current).days for holiday in holidays if holiday >= current]
    gap = (current - date.fromisoformat(prior_date)).days if prior_date else None
    scheduled_gap = 2 if game == "ssq" and current.weekday() in (1, 6) else 3
    if game == "dlt":
        scheduled_gap = 2 if current.weekday() in (2, 5) else 3
    return {
        "month_sin": math.sin(2 * math.pi * current.month / 12),
        "month_cos": math.cos(2 * math.pi * current.month / 12),
        "day_of_year_sin": math.sin(2 * math.pi * current.timetuple().tm_yday / 366),
        "day_of_year_cos": math.cos(2 * math.pi * current.timetuple().tm_yday / 366),
        "scheduled_weekday": current.weekday(),
        "official_holiday": int(current in holidays) if known else None,
        "days_since_official_holiday_capped_30": min(30, min(prior_holidays)) if known and prior_holidays else None,
        "days_until_official_holiday_capped_30": min(30, min(future_holidays)) if known and future_holidays else None,
        "holiday_calendar_available": known,
        "days_since_prior_draw": gap,
        "scheduled_gap_days": scheduled_gap,
        "gap_anomaly": (gap - scheduled_gap) if gap is not None else None,
        "predeclared_schedule_regime": f"{game}_schedule_v1",
    }


def distribution_features(distribution: dict[str, int] | None) -> tuple[float | None, float | None, float | None]:
    if not distribution:
        return None, None, None
    total = sum(distribution.values())
    if total <= 0:
        return None, None, None
    shares = [count / total for count in distribution.values()]
    entropy = -sum(value * math.log(value) for value in shares)
    return max(shares), entropy, float(len(distribution))


def coverage(records: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(records)
    return {
        "row_count": len(rows),
        "field_coverage": {
            field: (sum(row.get(field) is not None for row in rows) / len(rows) if rows else 0.0)
            for field in OPERATIONAL_FIELDS
        },
    }
