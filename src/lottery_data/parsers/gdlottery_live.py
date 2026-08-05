"""UTF-8 v2 parser for current Guangdong Sports Lottery DLT announcements."""

from __future__ import annotations

import re
from typing import Any


def parse(body: bytes, game: str) -> list[dict[str, Any]]:
    if game != "dlt":
        raise ValueError("gdlottery live parser supports only dlt")
    text = body.decode("utf-8")
    issue = re.findall(r"第\s*(\d{5})\s*期开奖公告", text)
    drawn = re.findall(r"开奖日期[：:]\s*(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    numbers = re.findall(r"本期开奖号码[：:]\s*</li>\s*<li>\s*([0-9 ]+)\s*</li>\s*<li>\s*([0-9 ]+)\s*</li>", text, flags=re.I)
    if len(issue) != 1 or len(drawn) != 1 or len(numbers) != 1:
        raise ValueError("gdlottery live announcement fields are not unique")
    front = [int(value) for value in numbers[0][0].split()]
    back = [int(value) for value in numbers[0][1].split()]
    if len(front) != 5 or len(set(front)) != 5 or front != sorted(front) or not all(1 <= value <= 35 for value in front):
        raise ValueError("invalid gdlottery DLT front numbers")
    if len(back) != 2 or len(set(back)) != 2 or back != sorted(back) or not all(1 <= value <= 12 for value in back):
        raise ValueError("invalid gdlottery DLT back numbers")
    year, month, day = drawn[0]
    return [{
        "raw_issue_id": issue[0], "issue_id": "20" + issue[0],
        "draw_date_local": f"{year}-{int(month):02d}-{int(day):02d}",
        "front_numbers": front, "back_numbers": back,
    }]
