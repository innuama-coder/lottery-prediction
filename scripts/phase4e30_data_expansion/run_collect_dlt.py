#!/usr/bin/env python3
"""Serial, resumable collector for official Guangdong DLT draw notices."""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "artifacts/phase4e30_data_expansion"
DRAW_PATH = OUTPUT_DIR / "dlt-draws-full.jsonl"
SUMMARY_PATH = OUTPUT_DIR / "collection-summary.json"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
URL_TEMPLATE = "https://www.gdlottery.cn/f_html/kjgg/P085_{issue}.html"
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_MAX_TIME_SECONDS = 20.0


class _TextExtractor(HTMLParser):
    BREAK_TAGS = {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\xa0", " ")
        return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def decode_html(raw: bytes) -> str:
    head = raw[:4096].decode("ascii", errors="ignore")
    declared = re.search(r"charset\s*=\s*['\"]?([\w-]+)", head, re.I)
    candidates = ([declared.group(1)] if declared else []) + ["utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    raise ValueError("official page is neither UTF-8 nor GBK/GB18030")


def _int(value: str) -> int:
    return int(value.replace(",", ""))


def _money(value: str) -> int:
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*元", value)
    return int(float(match.group(1).replace(",", ""))) if match else 0


def _number_pair(text: str, label: str) -> tuple[list[int], list[int]]:
    match = re.search(
        rf"{re.escape(label)}[：:]?\s*\n?((?:\d{{2}}\s+){{4}}\d{{2}})\s*\n\s*(\d{{2}}\s+\d{{2}})",
        text,
    )
    if not match:
        raise ValueError(f"missing {label}")
    return ([int(v) for v in match.group(1).split()], [int(v) for v in match.group(2).split()])


def _parse_tiers(text: str) -> list[dict[str, object]]:
    if "总奖金（元）" in text and "全国中奖注数" in text:
        tiers = []
        # Issue 17030 omits the zero total-prize cell when tier one has no
        # winner; insert that structural zero before applying the row parser.
        text = re.sub(r"(^一等奖\n)(0\n0\n滚入下期一等奖$)", r"\g<1>0\n\g<2>", text, flags=re.M)
        old_row = re.compile(
            r"^([一二三四五六七八九十]+等奖)(\(追加\))?\n"
            r"([\d,]+)\n([\d,]+)\n([\d,]+)\n([^\n]+)$",
            re.M,
        )
        for match in old_row.finditer(text):
            prize = re.match(r"[\d,]+", match.group(6))
            tiers.append({
                "tier": match.group(1) + ("追加" if match.group(2) else "基本" if match.group(1) in {"一等奖", "二等奖"} else ""),
                "winners": _int(match.group(4)),
                "prize_per_ticket_yuan": _int(prize.group(0)) if prize else 0,
            })
        required = {"一等奖基本", "二等奖基本"}
        if not required.issubset({row["tier"] for row in tiers}):
            raise ValueError("missing old-format first/second basic or additional tier")
        return tiers
    section = re.search(r"本期中奖情况(.*?)(?:\n合计\n|本期一等奖出自|奖金滚入下期奖池)", text, re.S)
    if not section:
        raise ValueError("missing prize table")
    lines = [line for line in section.group(1).splitlines() if line not in {
        "奖级", "中奖注数", "单注奖金", "应派奖金合计"
    }]
    tier_re = re.compile(r"^[一二三四五六七八九十]+等奖(?:派奖)?$")
    tiers: list[dict[str, object]] = []
    current: str | None = None
    subtypes: list[str] = []
    index = 0
    while index < len(lines):
        token = lines[index]
        if tier_re.fullmatch(token):
            current, subtypes = token, []
            index += 1
            continue
        if token in {"基本", "追加", "派奖"}:
            subtypes.append(token)
            index += 1
            continue
        winner = re.fullmatch(r"([\d,]+)注", token)
        if current and winner and index + 1 < len(lines):
            prize = lines[index + 1]
            if prize in {"---", "---元"} or re.fullmatch(r"[\d,]+(?:\.\d+)?元", prize):
                subtype = subtypes.pop(0) if subtypes else ""
                tiers.append({
                    "tier": current + subtype,
                    "winners": _int(winner.group(1)),
                    "prize_per_ticket_yuan": _money(prize),
                })
                # Official rows also contain a total after each prize. Fixtures may omit it.
                index += 3 if index + 2 < len(lines) and re.fullmatch(r"(?:[\d,]+(?:\.\d+)?元|---)", lines[index + 2]) else 2
                continue
        index += 1
    required = {"一等奖基本", "二等奖基本"}
    if not required.issubset({row["tier"] for row in tiers}):
        raise ValueError("missing first/second basic or additional tier")
    return tiers


def parse_draw_html(raw: bytes, *, url: str, fetched_at_utc: str) -> dict[str, object]:
    parser = _TextExtractor()
    parser.feed(decode_html(raw))
    text = parser.text()
    issue = re.search(r"第\s*(\d{5})\s*期", text)
    date = re.search(r"开奖日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    sales = re.search(r"本期全国销售金额[：:]\s*([\d,]+)\s*元", text)
    old_date = re.search(r"(\d{4})年第\d{5}期(\d{1,2})月(\d{1,2})日在北京开奖", text)
    old_sales = re.search(r"全国销售量\((?:超级|体彩)大乐透\)[：:]\s*([\d,]+)元", text)
    date = date or old_date
    sales = sales or old_sales
    if not issue or not date or not sales:
        raise ValueError("missing issue/date/sales field")
    if "本期开奖号码" in text:
        front, back = _number_pair(text, "本期开奖号码")
    else:
        old_numbers = re.search(
            r"前区号码\n后区号码\n(\d{2})\n(\d{2})\n(\d{2})\n(\d{2})\n(\d{2})\n(\d{2})\n(\d{2})",
            text,
        )
        if not old_numbers:
            raise ValueError("missing old-format winning numbers")
        values = [int(old_numbers.group(i)) for i in range(1, 8)]
        front, back = values[:5], values[5:]
    try:
        front_order, back_order = _number_pair(text, "本期出球顺序")
    except ValueError:
        # The 2017 legacy notices publish only sorted winning numbers.  Null is
        # deliberate: treating display order as physical draw order would fabricate data.
        front_order, back_order = None, None
    if not (len(front) == 5 and len(set(front)) == 5 and all(1 <= n <= 35 for n in front)):
        raise ValueError("invalid front numbers")
    if not (len(back) == 2 and len(set(back)) == 2 and all(1 <= n <= 12 for n in back)):
        raise ValueError("invalid back numbers")
    if front_order is not None and (sorted(front_order) != sorted(front) or sorted(back_order) != sorted(back)):
        raise ValueError("draw order does not match winning numbers")
    ball_set = re.search(r"本期使用第\s*(\d+)\s*套摇奖球", text)
    rollover = re.findall(r"([\d,]+(?:\.\d+)?)\s*元奖金滚入下期奖池", text)
    if not rollover:
        rollover = re.findall(r"滚入\d{5}期奖池金额为\s*([\d,]+(?:\.\d+)?)", text)
    draw_date = f"{int(date.group(1)):04d}-{int(date.group(2)):02d}-{int(date.group(3)):02d}"
    return {
        "issue_id": issue.group(1),
        "draw_date_local": draw_date,
        "front_numbers": sorted(front),
        "back_numbers": sorted(back),
        "front_draw_order": front_order,
        "back_draw_order": back_order,
        "ball_set_id": int(ball_set.group(1)) if ball_set else None,
        "national_sales_yuan": _int(sales.group(1)),
        "pool_rollover_yuan": int(float(rollover[-1].replace(",", ""))) if rollover else 0,
        "tiers": _parse_tiers(text),
        "provenance": {
            "url": url,
            "fetched_at_utc": fetched_at_utc,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }


def _read_draws(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    draws = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        draw = json.loads(line)
        issue = str(draw["issue_id"])
        if issue in seen:
            raise ValueError(f"duplicate issue_id {issue} at existing line {number}")
        seen.add(issue)
        draws.append(draw)
    return draws


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_draws(path: Path, draws: list[dict[str, object]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for draw in sorted(draws, key=lambda row: str(row["issue_id"])):
            handle.write(json.dumps(draw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(path)


def build_summary(draws: list[dict[str, object]], *, run: dict[str, object]) -> dict[str, object]:
    by_year: dict[str, int] = {}
    for draw in draws:
        year = str(draw["draw_date_local"])[:4]
        by_year[year] = by_year.get(year, 0) + 1
    dates = sorted(str(draw["draw_date_local"]) for draw in draws)
    return {
        "total_issues": len(draws),
        "issues_by_year": dict(sorted(by_year.items())),
        "date_range": {"first": dates[0] if dates else None, "last": dates[-1] if dates else None},
        "run": run,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_url(url: str, max_time: float) -> tuple[bytes, int, str]:
    """Fetch through curl so DNS, redirects, and the total operation share one deadline."""
    marker = b"\n__PHASE4E30_CURL_METADATA__"
    completed = subprocess.run(
        [
            "curl", "-L", "--silent", "--show-error", "--max-time", str(max_time),
            "--write-out", "\n__PHASE4E30_CURL_METADATA__%{http_code}\t%{url_effective}", url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(f"curl exit {completed.returncode}: {error}")
    try:
        raw, metadata = completed.stdout.rsplit(marker, 1)
        status_text, final_url = metadata.decode("utf-8").split("\t", 1)
        return raw, int(status_text), final_url
    except (ValueError, UnicodeDecodeError) as error:
        raise OSError("curl returned malformed response metadata") from error


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    arguments.add_argument("--max-time", type=float, default=DEFAULT_MAX_TIME_SECONDS)
    arguments.add_argument("--start-year", type=int, default=17)
    arguments.add_argument("--end-year", type=int, default=26)
    arguments.add_argument("--max-sequence", type=int, default=160)
    args = arguments.parse_args()
    if args.interval_seconds < 2:
        arguments.error("--interval-seconds must be at least 2")
    if args.max_time <= 0 or args.max_time > 20:
        arguments.error("--max-time must be in (0, 20]")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    draws = _read_draws(DRAW_PATH)
    existing = {str(draw["issue_id"]) for draw in draws}
    missing: list[dict[str, object]] = []
    parse_failures: list[dict[str, object]] = []
    successful = 0
    requests = 0
    request_starts: list[float] = []
    last_start: float | None = None
    for year in range(args.start_year, args.end_year + 1):
        for sequence in range(1, args.max_sequence + 1):
            issue = f"{year:02d}{sequence:03d}"
            if issue in existing:
                continue
            if last_start is not None:
                time.sleep(max(0.0, args.interval_seconds - (time.monotonic() - last_start)))
            started = time.monotonic()
            last_start = started
            request_starts.append(started)
            requests += 1
            url = URL_TEMPLATE.format(issue=issue)
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            try:
                raw, status, final_url = fetch_url(url, args.max_time)
                if status != 200 or final_url != url:
                    missing.append({"issue_id": issue, "status": status, "final_url": final_url})
                    continue
                draw = parse_draw_html(raw, url=url, fetched_at_utc=fetched_at)
                if draw["issue_id"] != issue:
                    raise ValueError(f"page issue {draw['issue_id']} does not match requested issue")
                draws.append(draw)
                existing.add(issue)
                successful += 1
                _write_draws(DRAW_PATH, draws)
                print(f"success {issue}", flush=True)
            except (TimeoutError, OSError) as error:
                missing.append({"issue_id": issue, "status": "network_error", "error": str(error)})
                print(f"missing {issue} network error: {error}", flush=True)
            except ValueError as error:
                parse_failures.append({"issue_id": issue, "url": url, "error": str(error)})
                print(f"parse_failed {issue}: {error}", flush=True)

    intervals = [b - a for a, b in zip(request_starts, request_starts[1:])]
    run = {
        "started_with_existing": len(draws) - successful,
        "successful": len(draws),
        "newly_successful": successful,
        "missing_count": len(missing),
        "missing": missing,
        "parse_failed_count": len(parse_failures),
        "parse_failures": parse_failures,
        "total_requests": requests,
        "average_request_start_interval_seconds": (sum(intervals) / len(intervals)) if intervals else None,
        "minimum_configured_interval_seconds": args.interval_seconds,
        "request_timeout_seconds": args.max_time,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    _write_draws(DRAW_PATH, draws)
    _write_json(SUMMARY_PATH, build_summary(draws, run=run))
    manifest = {
        "schema_version": "phase4e30-manifest-v1",
        "source": {"url_template": URL_TEMPLATE, "requested_years": [args.start_year, args.end_year], "max_sequence": args.max_sequence},
        "inputs": {
            "official_page_raw_hashes": {
                "issue_count": len(draws),
                "sha256": hashlib.sha256("\n".join(
                    f"{draw['issue_id']}\t{draw['provenance']['raw_sha256']}"
                    for draw in sorted(draws, key=lambda row: str(row["issue_id"]))
                ).encode("ascii")).hexdigest(),
                "derivation": "sha256 of sorted issue_id<TAB>raw_sha256 lines",
            }
        },
        "outputs": {
            str(DRAW_PATH.relative_to(ROOT)): {"sha256": _sha256(DRAW_PATH), "issue_count": len(draws)},
            str(SUMMARY_PATH.relative_to(ROOT)): {"sha256": _sha256(SUMMARY_PATH)},
        },
    }
    _write_json(MANIFEST_PATH, manifest)
    print(json.dumps(build_summary(draws, run=run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
