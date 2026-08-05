"""Parser for saved ydniu history pages."""

from __future__ import annotations

import re
from typing import Any


def parse(body: bytes, game: str) -> list[dict[str, Any]]:
    if game not in {"ssq", "dlt"}:
        raise ValueError(f"unsupported ydniu game: {game}")
    text = body.decode("utf-8")
    records: list[dict[str, Any]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        issue_match = re.search(r"<td>\s*(20\d{5})\s*</td>", row, flags=re.I)
        date_match = re.search(r"<td>\s*(20\d{2}-\d{2}-\d{2})[^<]*</td>", row, flags=re.I)
        if not issue_match or not date_match or "open_number" not in row:
            continue
        raw_issue_id = issue_match.group(1)
        if game == "ssq":
            front = [int(item) for item in re.findall(r'<i class="hq">(\d{2})</i>', row, flags=re.I)]
            back = [int(item) for item in re.findall(r'<i class="lq">(\d{2})</i>', row, flags=re.I)]
        else:
            front = [int(item) for item in re.findall(r'<i class="lq">(\d{2})</i>', row, flags=re.I)]
            back = [int(item) for item in re.findall(r'<i class="yq">(\d{2})</i>', row, flags=re.I)]
        records.append({
            "raw_issue_id": raw_issue_id,
            "issue_id": raw_issue_id,
            "draw_date_local": date_match.group(1),
            "front_numbers": front,
            "back_numbers": back,
        })
    if not records:
        raise ValueError(f"ydniu parser found no {game} records")
    return records
