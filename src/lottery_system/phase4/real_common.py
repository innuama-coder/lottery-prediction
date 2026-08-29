from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


RULES = {"ssq": ((33, 6), (16, 1)), "dlt": ((35, 5), (12, 2))}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Draw:
    issue: str
    front: tuple[int, ...]
    back: tuple[int, ...]
    fact_hash: str
