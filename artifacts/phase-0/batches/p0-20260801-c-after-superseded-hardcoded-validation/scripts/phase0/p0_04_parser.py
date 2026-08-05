"""Deterministic, fail-closed parsers for the Phase-0 official HTML samples.

The parsers intentionally cover only the two public source shapes selected by
P0-04: a Guangdong Sports Lottery per-issue DLT page and a Guangdong Welfare
Lottery SSQ history table.  They do not attempt browser emulation or fallback
to a national endpoint that rejected ordinary public GET requests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser


class ParseError(ValueError):
    """Raised when source bytes cannot be parsed without guessing."""


@dataclass(frozen=True)
class ParsedDraw:
    game: str
    issue_id: str
    front_numbers: tuple[str, ...]
    back_numbers: tuple[str, ...]
    draw_date: date


class _VisibleHtmlParser(HTMLParser):
    _SUPPRESSED = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._suppressed_depth = 0
        self._text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in self._SUPPRESSED:
            self._suppressed_depth += 1
        if self._suppressed_depth:
            return
        if lowered == "tr":
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._SUPPRESSED:
            self._suppressed_depth = max(0, self._suppressed_depth - 1)
            return
        if self._suppressed_depth:
            return
        if lowered in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(_collapse(" ".join(self._cell)))
            self._cell = None
        elif lowered == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        self._text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    @property
    def visible_text(self) -> str:
        return _collapse(" ".join(self._text))


def decode_html(raw: bytes, content_type: str | None) -> tuple[str, str]:
    """Decode without replacement characters and report the selected codec."""

    declared = None
    if content_type:
        match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", content_type, re.I)
        if match:
            declared = match.group(1).lower()
    aliases = {
        "utf8": "utf-8",
        "utf-8": "utf-8",
        "gbk": "gb18030",
        "gb2312": "gb18030",
        "gb18030": "gb18030",
    }
    if declared and declared not in aliases:
        raise ParseError(f"unsupported declared charset: {declared}")
    candidates = [aliases[declared]] if declared else ["utf-8", "gb18030"]
    for codec in candidates:
        try:
            decoded = raw.decode(codec, errors="strict")
        except UnicodeDecodeError:
            continue
        if "\ufffd" in decoded:
            raise ParseError("replacement character present after decoding")
        return decoded, codec
    raise ParseError("payload is not valid in the declared/supported encoding")


def parse_dlt_html(html: str, expected_issue_id: str) -> ParsedDraw:
    parser = _parse_document(html)
    text = parser.visible_text
    issue_match = re.search(r"第\s*(\d{5}|\d{7})\s*期\s*开奖公告", text)
    if not issue_match:
        raise ParseError("DLT issue marker missing")
    issue_id = canonical_issue_id("dlt", issue_match.group(1))
    if issue_id != canonical_issue_id("dlt", expected_issue_id):
        raise ParseError(f"DLT issue mismatch: expected {expected_issue_id}, found {issue_id}")

    draw_date = _extract_draw_date(text)
    section = _between(text, "本期开奖号码", "本期中奖情况")
    numbers = re.findall(r"(?<!\d)(\d{2})(?!\d)", section)
    if len(numbers) != 7:
        raise ParseError(f"DLT expected exactly 7 published numbers, found {len(numbers)}")
    front = _validate_numbers(numbers[:5], count=5, lower=1, upper=35, label="DLT front")
    back = _validate_numbers(numbers[5:], count=2, lower=1, upper=12, label="DLT back")
    return ParsedDraw("dlt", issue_id, tuple(sorted(front)), tuple(sorted(back)), draw_date)


def parse_ssq_history_html(html: str, expected_issue_id: str) -> ParsedDraw:
    parser = _parse_document(html)
    expected = canonical_issue_id("ssq", expected_issue_id)
    matching_rows = [row for row in parser.rows if any(_cell_has_issue(cell, expected) for cell in row)]
    if len(matching_rows) != 1:
        raise ParseError(f"SSQ expected one row for issue {expected}, found {len(matching_rows)}")
    row = matching_rows[0]
    issue_index = next(index for index, cell in enumerate(row) if _cell_has_issue(cell, expected))
    draw_date = _extract_draw_date(" ".join(row))

    red_index = None
    red_numbers: list[str] | None = None
    for index in range(issue_index + 1, len(row)):
        tokens = re.findall(r"(?<!\d)(\d{2})(?!\d)", row[index])
        if len(tokens) == 6:
            red_numbers = _validate_numbers(tokens, count=6, lower=1, upper=33, label="SSQ red")
            red_index = index
            break
    if red_numbers is None or red_index is None:
        raise ParseError("SSQ red-number cell missing")

    blue_numbers = None
    for index in range(red_index + 1, len(row)):
        tokens = re.findall(r"(?<!\d)(\d{2})(?!\d)", row[index])
        if len(tokens) == 1:
            blue_numbers = _validate_numbers(tokens, count=1, lower=1, upper=16, label="SSQ blue")
            break
    if blue_numbers is None:
        raise ParseError("SSQ blue-number cell missing")
    return ParsedDraw(
        "ssq",
        expected,
        tuple(sorted(red_numbers)),
        tuple(sorted(blue_numbers)),
        draw_date,
    )


def canonical_issue_id(game: str, issue_id: str) -> str:
    if not issue_id.isascii() or not issue_id.isdigit():
        raise ParseError("issue id must contain ASCII digits only")
    if game == "dlt":
        if len(issue_id) == 5:
            return "20" + issue_id
        if len(issue_id) == 7 and issue_id.startswith("20"):
            return issue_id
    elif game == "ssq" and len(issue_id) == 7 and issue_id.startswith("20"):
        return issue_id
    raise ParseError(f"invalid {game} issue id: {issue_id}")


def _parse_document(html: str) -> _VisibleHtmlParser:
    parser = _VisibleHtmlParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser can surface malformed entity errors.
        raise ParseError(f"malformed HTML: {exc}") from exc
    return parser


def _validate_numbers(
    numbers: list[str], *, count: int, lower: int, upper: int, label: str
) -> list[str]:
    if len(numbers) != count:
        raise ParseError(f"{label} expected {count} numbers, found {len(numbers)}")
    if any(len(number) != 2 or not number.isascii() or not number.isdigit() for number in numbers):
        raise ParseError(f"{label} numbers must be two ASCII digits")
    if len(set(numbers)) != len(numbers):
        raise ParseError(f"{label} contains duplicate numbers")
    values = [int(number) for number in numbers]
    if any(value < lower or value > upper for value in values):
        raise ParseError(f"{label} contains a number outside {lower:02d}-{upper:02d}")
    return [f"{value:02d}" for value in values]


def _extract_draw_date(text: str) -> date:
    patterns = (
        r"开奖日期\s*[：:]?\s*(\d{4})年(\d{1,2})月(\d{1,2})日",
        r"开奖日期\s*[：:]?\s*(\d{4})-(\d{1,2})-(\d{1,2})",
        r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return date(*(int(value) for value in match.groups()))
            except ValueError as exc:
                raise ParseError(f"invalid draw date: {match.group(0)}") from exc
    raise ParseError("draw date missing")


def _between(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise ParseError(f"missing marker: {start_marker}")
    start += len(start_marker)
    end = text.find(end_marker, start)
    if end < 0:
        raise ParseError(f"missing marker: {end_marker}")
    return text[start:end]


def _cell_has_issue(cell: str, issue_id: str) -> bool:
    return bool(re.search(rf"(?<!\d){re.escape(issue_id)}(?!\d)", cell))


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
