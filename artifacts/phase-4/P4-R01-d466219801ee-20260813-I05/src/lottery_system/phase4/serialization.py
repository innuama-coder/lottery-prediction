from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any


class DuplicateKeyError(ValueError):
    pass


def decimal_string(value: Decimal) -> str:
    """Return the non-exponent, finite P4 decimal representation."""

    if not value.is_finite():
        raise ValueError("P4-CJSON-1 rejects non-finite Decimal values")
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _normalize(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return decimal_string(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: P4-CJSON-1 rejects NaN and Infinity")
        raise TypeError(f"{path}: P4-CJSON-1 requires decimal values to be strings")
    if isinstance(value, list):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: object keys must be strings")
            normalized[key] = _normalize(item, f"{path}.{key}")
        return normalized
    raise TypeError(f"{path}: unsupported P4-CJSON-1 value {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the exact P4-CJSON-1 profile (not newline terminated)."""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: str | Path, *, reject_floats: bool = False) -> Any:
    def parse_float(raw: str) -> Any:
        if reject_floats:
            raise ValueError("P4-CJSON-1 JSON input requires decimal values to be strings")
        return Decimal(raw)

    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_unique_object, parse_float=parse_float)
