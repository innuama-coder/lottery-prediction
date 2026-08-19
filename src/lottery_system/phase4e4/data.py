from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


RULES = {"ssq": ((33, 6), (16, 1)), "dlt": ((35, 5), (12, 2))}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


@dataclass(frozen=True)
class Draw:
    game: str
    issue: str
    draw_date: str
    front: tuple[int, ...]
    back: tuple[int, ...]
    source_id: str
    source_record_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "game": self.game,
            "issue": self.issue,
            "draw_date": self.draw_date,
            "front": list(self.front),
            "back": list(self.back),
            "source_id": self.source_id,
            "source_record_sha256": self.source_record_sha256,
        }


def make_draw(game: str, issue: str, draw_date: str, front: Iterable[int], back: Iterable[int], source_id: str) -> Draw:
    front_tuple, back_tuple = tuple(front), tuple(back)
    date.fromisoformat(draw_date)
    n_front, k_front = RULES[game][0]
    n_back, k_back = RULES[game][1]
    if len(issue) != 7 or not issue.isdigit():
        raise ValueError(f"invalid {game} issue: {issue}")
    if len(front_tuple) != k_front or tuple(sorted(front_tuple)) != front_tuple or len(set(front_tuple)) != k_front or not all(1 <= x <= n_front for x in front_tuple):
        raise ValueError(f"invalid {game} front: {issue}")
    if len(back_tuple) != k_back or tuple(sorted(back_tuple)) != back_tuple or len(set(back_tuple)) != k_back or not all(1 <= x <= n_back for x in back_tuple):
        raise ValueError(f"invalid {game} back: {issue}")
    core = {"game": game, "issue": issue, "draw_date": draw_date, "front": front_tuple, "back": back_tuple, "source_id": source_id}
    return Draw(game, issue, draw_date, front_tuple, back_tuple, source_id, sha256_bytes(canonical(core)))


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cells: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self.in_row, self.cells = True, []
        elif self.in_row and tag.lower() == "td":
            self.in_cell = True

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            if not self.cells:
                self.cells.append("")
            self.cells[-1] += data

    def handle_endtag(self, tag: str) -> None:
        if self.in_row and tag.lower() == "td":
            self.in_cell = False
            self.cells.append("")
        elif self.in_row and tag.lower() == "tr":
            self.in_row = False
            cells = [cell.strip().replace("\xa0", "") for cell in self.cells]
            if cells and cells[-1] == "":
                cells.pop()
            self.rows.append(cells)


def parse_500_ssq(body: bytes) -> list[Draw]:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = body.decode("gb18030", errors="strict")
    parser = _TableParser()
    parser.feed(text)
    rows: list[Draw] = []
    for cells in parser.rows:
        if len(cells) < 16 or re.fullmatch(r"\d{5}", cells[0]) is None:
            continue
        issue = "20" + cells[0]
        front = [int(value) for value in cells[1:7]]
        back = [int(cells[7])]
        draw_date = cells[-1]
        rows.append(make_draw("ssq", issue, draw_date, front, back, "500_history_direct"))
    return validate_draws(rows, "ssq")


_DLT = re.compile(r"^(\d{2})\+(\d{2})\+(\d{2})\+(\d{2})\+(\d{2}) (\d{2})\+(\d{2})$")


def parse_gdlottery_dlt(body: bytes) -> list[Draw]:
    root = json.loads(body.decode("utf-8"))
    if not isinstance(root, dict) or not root:
        raise ValueError("invalid gdlottery root")
    rows: list[Draw] = []
    for key, record in root.items():
        if not key.startswith("085_"):
            continue
        if not isinstance(record, dict) or record.get("gameId") != "085" or key != f"085_{record.get('drawId')}":
            raise ValueError(f"invalid gdlottery identity: {key}")
        match = _DLT.fullmatch(str(record.get("kjhm", "")))
        if match is None:
            raise ValueError(f"invalid gdlottery numbers: {key}")
        values = [int(value) for value in match.groups()]
        rows.append(make_draw("dlt", "20" + str(record["drawId"]), str(record["createTime"]), values[:5], values[5:], "gdlottery_official_history_json"))
    return validate_draws(rows, "dlt")


def validate_draws(rows: Iterable[Draw], game: str) -> list[Draw]:
    ordered = sorted(rows, key=lambda row: (row.draw_date, int(row.issue)))
    if not ordered:
        raise ValueError(f"no {game} rows")
    by_issue: dict[str, Draw] = {}
    for row in ordered:
        if row.game != game:
            raise ValueError("mixed games")
        old = by_issue.get(row.issue)
        if old is not None and old != row:
            raise ValueError(f"conflicting duplicate issue: {game}/{row.issue}")
        by_issue[row.issue] = row
    result = sorted(by_issue.values(), key=lambda row: (row.draw_date, int(row.issue)))
    if any(left.draw_date > right.draw_date for left, right in zip(result, result[1:])):
        raise ValueError("nonmonotonic dates")
    return result


def load_jsonl(path: Path, game: str | None = None) -> list[Draw]:
    rows: list[Draw] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if game is not None and raw["game"] != game:
            continue
        row = make_draw(raw["game"], raw["issue"], raw["draw_date"], raw["front"], raw["back"], raw["source_id"])
        if row.source_record_sha256 != raw["source_record_sha256"]:
            raise ValueError(f"source record digest mismatch: {row.game}/{row.issue}")
        rows.append(row)
    if game is not None:
        return validate_draws(rows, game)
    return rows


def write_jsonl(path: Path, rows: Iterable[Draw]) -> None:
    payload = b"".join(canonical(row.payload()) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise FileExistsError(f"immutable output collision: {path}")
    path.write_bytes(payload)
