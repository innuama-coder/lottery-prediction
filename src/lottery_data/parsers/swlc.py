"""Parser for the server-rendered Shanghai Welfare Lottery SSQ history table."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


class _PreviousTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.in_target = False
        self.in_row = False
        self.in_cell = False
        self.cell_text: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "table":
            classes = set((values.get("class") or "").split())
            if self.in_target:
                self.table_depth += 1
            elif "ssq-previous-table" in classes:
                self.in_target = True
                self.table_depth = 1
        elif self.in_target and tag.lower() == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag.lower() in {"td", "th"}:
            self.in_cell = True
            self.cell_text = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if self.in_cell and lower == "p":
            # The live page renders each ball in a sibling <p>. Preserve that
            # structural boundary so the strict two-digit token parser does
            # not see the six red balls as one twelve-digit value.
            self.cell_text.append(" ")
        elif self.in_cell and lower in {"td", "th"}:
            self.row.append(" ".join("".join(self.cell_text).split()))
            self.in_cell = False
        elif self.in_row and lower == "tr":
            self.rows.append(self.row)
            self.in_row = False
        elif self.in_target and lower == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_target = False


def _parse_row(cells: list[str]) -> dict[str, Any] | None:
    issue_positions = [(index, value) for index, cell in enumerate(cells) for value in re.findall(r"(?<!\d)(20\d{5})(?!\d)", cell)]
    dates = [(index, match.groups()) for index, cell in enumerate(cells) if (match := re.search(r"(20\d{2})[-/](\d{2})[-/](\d{2})", cell))]
    if not issue_positions and not dates:
        return None
    if len(issue_positions) != 1 or len(dates) != 1:
        raise ValueError("swlc row issue/date is not unique")
    issue_index, issue_id = issue_positions[0]
    date_index, date_parts = dates[0]
    candidate_cells = [cell for index, cell in enumerate(cells) if index not in {issue_index, date_index}]
    groups = [[int(value) for value in re.findall(r"(?<!\d)(\d{2})(?!\d)", cell)] for cell in candidate_cells]
    number_sets: list[tuple[list[int], int]] = []
    for index, values in enumerate(groups):
        if len(values) == 7:
            number_sets.append((values[:6], values[6]))
        if len(values) == 6 and index + 1 < len(groups) and len(groups[index + 1]) == 1:
            number_sets.append((values, groups[index + 1][0]))
    if not number_sets:
        leading: list[int] = []
        for values in groups:
            if len(values) == 1:
                leading.extend(values)
                if len(leading) == 7:
                    number_sets.append((leading[:6], leading[6]))
                    break
            elif values:
                break
    valid = [(front, back) for front, back in number_sets if len(set(front)) == 6 and front == sorted(front) and all(1 <= value <= 33 for value in front) and 1 <= back <= 16]
    if len(valid) != 1:
        raise ValueError("swlc row draw numbers are not uniquely identifiable")
    year, month, day = date_parts
    return {
        "raw_issue_id": issue_id, "issue_id": issue_id, "draw_date_local": f"{year}-{month}-{day}",
        "front_numbers": valid[0][0], "back_numbers": [valid[0][1]],
    }


def parse(body: bytes, game: str) -> list[dict[str, Any]]:
    if game != "ssq":
        raise ValueError("swlc parser supports only ssq")
    parser = _PreviousTable()
    parser.feed(body.decode("utf-8"))
    records = [record for row in parser.rows if (record := _parse_row(row)) is not None]
    if not records:
        raise ValueError("swlc parser found no SSQ records")
    issues = [record["issue_id"] for record in records]
    if len(issues) != len(set(issues)):
        raise ValueError("swlc parser found duplicate issues")
    return records
