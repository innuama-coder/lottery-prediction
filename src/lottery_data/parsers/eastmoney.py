"""Parser for saved Eastmoney ordinary history pages."""

from __future__ import annotations

import re
from typing import Any


def _normalize_issue(raw_issue_id: str) -> str:
    if len(raw_issue_id) == 7 and raw_issue_id.startswith("20"):
        return raw_issue_id
    if len(raw_issue_id) == 5:
        return "20" + raw_issue_id
    raise ValueError(f"unsupported Eastmoney issue id: {raw_issue_id}")


def parse(body: bytes, game: str) -> list[dict[str, Any]]:
    if game not in {"ssq", "dlt"}:
        raise ValueError(f"unsupported Eastmoney game: {game}")
    text = body.decode("utf-8-sig")
    records: list[dict[str, Any]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        issue_match = re.search(rf"/Result/Category/{game}\?[^\"']*?id=(\d{{5,7}})", row, flags=re.I)
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})\(", row)
        if not issue_match or not date_match:
            continue
        raw_issue_id = issue_match.group(1)
        records.append({
            "raw_issue_id": raw_issue_id,
            "issue_id": _normalize_issue(raw_issue_id),
            "draw_date_local": date_match.group(1),
            "front_numbers": [
                int(item) for item in re.findall(
                    r'<span[^>]*class="[^"]*\bred\b[^"]*"[^>]*>(\d{2})</span>', row, flags=re.I,
                )
            ],
            "back_numbers": [
                int(item) for item in re.findall(
                    r'<span[^>]*class="[^"]*\bblue\b[^"]*"[^>]*>(\d{2})</span>', row, flags=re.I,
                )
            ],
        })
    if not records:
        raise ValueError(f"Eastmoney parser found no {game} records")
    return records
