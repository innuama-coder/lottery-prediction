#!/usr/bin/env python3
"""Collect official DLT notices and calculate observed payout EV.

Only public, static Guangdong Sports Lottery pages are requested. Requests are
serial and the start of adjacent requests is separated by at least three
seconds. No user-agent or other identity header is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from lottery_system.phase4.parimutuel import (
    DLT_PRIZE_PARIMUTUEL_v1,
    expected_ticket_value,
    tier_win_probability,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "artifacts/phase4e25_b1_dlt_pool_data"
URL_TEMPLATE = "https://www.gdlottery.cn/f_html/kjgg/P085_{issue}.html"
FIXED_EV = 0.881670
TARGET_EV = 2.0
REQUEST_INTERVAL_SECONDS = 3.0


class _TextExtractor(HTMLParser):
    """Produce readable text while retaining table-cell boundaries."""

    BREAK_TAGS = {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            cell = "\n".join(v.strip() for v in "".join(self._cell).splitlines() if v.strip())
            self._row.append(cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\xa0", " ")
        return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def decode_html(raw: bytes) -> str:
    """Decode the two encodings used by the static notice pages."""
    head = raw[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", head, re.I)
    candidates = ([match.group(1)] if match else []) + ["utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError("official page is neither UTF-8 nor GBK/GB18030")


def _integer(value: str) -> int:
    return int(value.replace(",", ""))


def _amount_or_zero(value: str) -> int:
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*元", value)
    if not match:
        return 0
    # The output contract is whole yuan; official rollover cents are floored.
    return int(float(match.group(1).replace(",", "")))


def parse_draw_html(raw: bytes, *, url: str, fetched_at_utc: str) -> dict[str, object]:
    """Parse one official notice. Raises ValueError rather than emitting partial data."""
    parser = _TextExtractor()
    parser.feed(decode_html(raw))
    text = parser.text()

    issue_match = re.search(r"第\s*(26\d{3})\s*期", text)
    date_match = re.search(r"开奖日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    sales_match = re.search(r"本期全国销售金额[：:]\s*([\d,]+)\s*元", text)
    numbers_match = re.search(
        r"本期开奖号码[：:]?\s*\n?((?:\d{2}\s+){4}\d{2})\s*\n\s*(\d{2}\s+\d{2})",
        text,
    )
    if not all((issue_match, date_match, sales_match, numbers_match)):
        raise ValueError("missing issue/date/sales/winning-number field")

    date_parts = tuple(int(date_match.group(i)) for i in range(1, 4))
    draw_date = f"{date_parts[0]:04d}-{date_parts[1]:02d}-{date_parts[2]:02d}"
    front = sorted(int(v) for v in numbers_match.group(1).split())
    back = sorted(int(v) for v in numbers_match.group(2).split())
    if len(front) != 5 or len(set(front)) != 5 or len(back) != 2 or len(set(back)) != 2:
        raise ValueError("invalid winning-number cardinality")

    # "应派奖金合计" occurs in the header, so only treat a standalone
    # line as the total-row boundary.  The live pages use ul/li rather than a
    # table, while the offline fixture intentionally exercises table markup.
    table_match = re.search(r"本期中奖情况(.*?)(?:\n合计\n|本期一等奖出自)", text, re.S)
    if not table_match:
        raise ValueError("missing prize table")
    lines = [line.strip() for line in table_match.group(1).splitlines() if line.strip()]
    lines = [line for line in lines if line not in {"奖级", "中奖注数", "单注奖金", "应派奖金合计"}]
    tier_label = re.compile(r"^(?:[一二三四五六七八九十]+等奖)(?:派奖)?$")
    tiers: list[dict[str, object]] = []
    # Normal official markup: one prize row, with sub-values stacked in cells.
    for cells in parser.rows:
        if len(cells) < 3:
            continue
        labels = [v for v in cells[0].splitlines() if v]
        base_label = next((v for v in labels if tier_label.match(v)), None)
        if not base_label:
            continue
        subtypes = [v for v in labels if v in {"基本", "追加"}]
        winners = re.findall(r"([\d,]+)注", cells[1])
        prizes = re.findall(r"(?:[\d,]+(?:\.\d+)?\s*元|---)", cells[2])
        for index, winner in enumerate(winners):
            if index >= len(prizes):
                break
            subtype = subtypes[index] if index < len(subtypes) else ""
            tiers.append({
                "tier": base_label + subtype,
                "winners": _integer(winner),
                "prize_per_ticket_yuan": _amount_or_zero(prizes[index]),
            })
    # Fallback for unusually unstructured notices.
    current: str | None = None
    subtype_queue: list[str] = []
    i = 0
    while i < len(lines):
        token = lines[i]
        if tier_label.match(token):
            current = token
            subtype_queue = []
            i += 1
            continue
        if token in {"基本", "追加"}:
            subtype_queue.append(token)
            i += 1
            continue
        winners_match = re.fullmatch(r"([\d,]+)注", token)
        if current and winners_match and i + 2 < len(lines):
            prize_token = lines[i + 1]
            total_token = lines[i + 2]
            if prize_token == "---" or re.fullmatch(r"[\d,]+元", prize_token):
                subtype = subtype_queue.pop(0) if subtype_queue else None
                candidate = {
                    "tier": current + (subtype or ""),
                    "winners": _integer(winners_match.group(1)),
                    "prize_per_ticket_yuan": _amount_or_zero(prize_token),
                }
                if candidate["tier"] not in {row["tier"] for row in tiers}:
                    tiers.append(candidate)
                i += 3
                continue
        i += 1
    required = {"一等奖基本", "一等奖追加", "二等奖基本", "二等奖追加"}
    if not required.issubset({row["tier"] for row in tiers}):
        raise ValueError("missing first/second basic or additional tier")

    rollover_matches = re.findall(r"([\d,]+(?:\.\d+)?)\s*元奖金滚入下期奖池", text)
    rollover = int(float(rollover_matches[-1].replace(",", ""))) if rollover_matches else 0
    return {
        "issue_id": issue_match.group(1),
        "draw_date_local": draw_date,
        "front_numbers": front,
        "back_numbers": back,
        "national_sales_yuan": _integer(sales_match.group(1)),
        "pool_rollover_yuan": rollover,
        "tiers": tiers,
        "provenance": {
            "url": url,
            "fetched_at_utc": fetched_at_utc,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "issue_id": issue_match.group(1),
            "draw_date_local": draw_date,
        },
    }


def observed_ev(draw: dict[str, object]) -> float:
    paid = sum(row["winners"] * row["prize_per_ticket_yuan"] for row in draw["tiers"])
    return paid / (draw["national_sales_yuan"] / 2.0)


def _tier(draw: dict[str, object], name: str) -> dict[str, object]:
    return next(row for row in draw["tiers"] if row["tier"] == name)


def required_tier1_pool(draw: dict[str, object]) -> dict[str, object]:
    """Invert the frozen B0 model at popularity_weight=1.0."""
    tier1 = _tier(draw, "一等奖基本")
    tier2 = _tier(draw, "二等奖基本")
    bets = max(1, draw["national_sales_yuan"] // 2)
    tier2_pool = tier2["winners"] * tier2["prize_per_ticket_yuan"]
    without = expected_ticket_value(
        "dlt", DLT_PRIZE_PARIMUTUEL_v1, tier1_pool=0, tier2_pool=tier2_pool,
        total_bets=bets, popularity_weight=1.0,
    )
    p1 = tier_win_probability("dlt", 1)
    denominator = (bets - 1) * p1 + 1
    threshold = math.ceil(max(0.0, (TARGET_EV - without["total_ev"]) * denominator / p1))
    check = expected_ticket_value(
        "dlt", DLT_PRIZE_PARIMUTUEL_v1, tier1_pool=threshold,
        tier2_pool=tier2_pool, total_bets=bets, popularity_weight=1.0,
    )
    real_pool = tier1["winners"] * tier1["prize_per_ticket_yuan"]
    return {
        "tier1_pool_yuan": real_pool,
        "tier2_pool_yuan": tier2_pool,
        "required_tier1_pool_yuan": threshold,
        "required_to_real_tier1_pool_multiple": None if real_pool == 0 else threshold / real_pool,
        "required_to_reported_rollover_multiple": (
            None if draw["pool_rollover_yuan"] == 0 else threshold / draw["pool_rollover_yuan"]
        ),
        "b0_total_bets_proxy_floor_sales_div_2": bets,
        "b0_total_ev_at_threshold_yuan": check["total_ev"],
    }


def build_summary(draws: list[dict[str, object]], missing: list[dict[str, object]]) -> dict[str, object]:
    rows = []
    for draw in draws:
        ev = observed_ev(draw)
        first = _tier(draw, "一等奖基本")
        second = _tier(draw, "二等奖基本")
        rows.append({
            "issue_id": draw["issue_id"],
            "real_ev_per_ticket_yuan": ev,
            "first_prize_per_ticket_yuan": first["prize_per_ticket_yuan"],
            "second_prize_per_ticket_yuan": second["prize_per_ticket_yuan"],
            "difference_from_frozen_fixed_ev_yuan": ev - FIXED_EV,
            "gap_to_2_yuan": TARGET_EV - ev,
            "pool_rollover_yuan": draw["pool_rollover_yuan"],
            "b0_threshold": required_tier1_pool(draw),
        })
    evs = [row["real_ev_per_ticket_yuan"] for row in rows]
    return {
        "schema_version": "phase4e25-b1-v1",
        "method": "observed announced payout / (national sales / 2 yuan)",
        "frozen_fixed_ev_yuan": FIXED_EV,
        "target_ev_yuan": TARGET_EV,
        "draw_count": len(draws),
        "missing_issues": missing,
        "real_ev_range_yuan": {"min": min(evs), "max": max(evs)} if evs else None,
        "draws": rows,
        "caveat": "Observed accounting and conditional B0 model output; no return or winning guarantee.",
    }


def collect(start_issue: int, count: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    draws: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []
    last_start: float | None = None
    for issue_number in range(start_issue, start_issue - count, -1):
        if last_start is not None:
            time.sleep(max(0.0, REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_start)))
        issue = str(issue_number)
        url = URL_TEMPLATE.format(issue=issue)
        last_start = time.monotonic()
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            with urlopen(url, timeout=30) as response:  # no identity/header spoofing
                raw = response.read()
            draw = parse_draw_html(raw, url=url, fetched_at_utc=fetched_at)
            if draw["issue_id"] != issue:
                raise ValueError(f"URL issue {issue} returned issue {draw['issue_id']}")
            draws.append(draw)
            print(f"collected {issue} {len(raw)} bytes")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            missing.append({"issue_id": issue, "url": url, "reason": f"{type(exc).__name__}: {exc}"})
            print(f"skipped {issue}: {exc}")
    return draws, missing


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--start-issue", type=int, default=26070)
    cli.add_argument("--count", type=int, default=20, choices=range(1, 21))
    args = cli.parse_args()
    draws, missing = collect(args.start_issue, args.count)
    if not draws:
        raise SystemExit("no official draw page could be collected; existing artifacts were not overwritten")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    draw_path = OUTPUT_DIR / "dlt-draws.jsonl"
    draw_path.write_text("".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in draws), encoding="utf-8")
    summary = build_summary(draws, missing)
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
