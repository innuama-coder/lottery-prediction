"""Parser for saved Guangdong Sports Lottery DLT announcements."""

from __future__ import annotations

import re
from typing import Any


def parse(body: bytes, game: str) -> list[dict[str, Any]]:
    if game != "dlt":
        raise ValueError("gdlottery parser supports only dlt")
    text = body.decode("utf-8")
    issue_match = re.search(r"第(\d{5})期开奖公告", text)
    date_match = re.search(r"开奖日期：\s*(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    numbers_match = re.search(
        r"本期开奖号码：</li>\s*<li>([0-9 ]+)</li>\s*<li>([0-9 ]+)</li>", text, flags=re.I,
    )
    if not issue_match or not date_match or not numbers_match:
        raise ValueError("gdlottery parser could not find issue, date, and draw numbers")
    raw_issue_id = issue_match.group(1)
    return [{
        "raw_issue_id": raw_issue_id,
        "issue_id": "20" + raw_issue_id,
        "draw_date_local": (
            f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        ),
        "front_numbers": [int(item) for item in numbers_match.group(1).split()],
        "back_numbers": [int(item) for item in numbers_match.group(2).split()],
    }]
