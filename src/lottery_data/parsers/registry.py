"""Frozen registry for Phase 1 snapshot parsers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import eastmoney, gdlottery, gdlottery_history, gdlottery_live, swlc, ydniu

Parser = Callable[[bytes, str], list[dict[str, Any]]]

PARSERS: dict[str, Parser] = {
    "ydniu": ydniu.parse,
    "eastmoney": eastmoney.parse,
    "gdlottery": gdlottery.parse,
}

VERSIONED_PARSERS: dict[tuple[str, str], Parser] = {
    ("phase1-ydniu-parser", "1.0.0"): ydniu.parse,
    ("phase1-swlc-live-parser", "1.0.0"): swlc.parse,
    ("phase1-gdlottery-live-parser", "2.0.0"): gdlottery_live.parse,
    ("phase1-gdlottery-history-parser", "1.0.0"): gdlottery_history.parse,
}


def get_parser(source_id: str) -> Parser:
    try:
        return PARSERS[source_id]
    except KeyError as exc:
        raise ValueError(f"no approved parser for source: {source_id}") from exc


def get_versioned_parser(parser_id: str, parser_version: str) -> Parser:
    try:
        return VERSIONED_PARSERS[(parser_id, parser_version)]
    except KeyError as exc:
        raise ValueError(f"no approved parser version: {parser_id}@{parser_version}") from exc
