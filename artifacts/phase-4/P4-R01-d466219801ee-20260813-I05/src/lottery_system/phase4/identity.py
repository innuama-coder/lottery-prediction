from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .serialization import canonical_sha256


_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_KIND = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def validate_stable_id(value: object, label: str = "identity") -> str:
    if (
        not isinstance(value, str)
        or not _STABLE_ID.fullmatch(value)
        or value in {".", ".."}
        or "latest" in value.lower()
        or any(character in value for character in "*/\\")
    ):
        raise ValueError(f"{label} must be an explicit immutable identity")
    return value


def identity_body(body: Mapping[str, Any], excluded_fields: Iterable[str] = ()) -> dict[str, Any]:
    excluded = set(excluded_fields)
    return {key: value for key, value in body.items() if key not in excluded}


def content_id(kind: str, body: Mapping[str, Any], *, excluded_fields: Iterable[str] = ()) -> str:
    if not _KIND.fullmatch(kind):
        raise ValueError("content identity kind is invalid")
    return f"{kind}-v1:{canonical_sha256(identity_body(body, excluded_fields))}"


def verify_content_id(
    supplied: str,
    kind: str,
    body: Mapping[str, Any],
    *,
    excluded_fields: Iterable[str] = (),
) -> None:
    validate_stable_id(supplied)
    expected = content_id(kind, body, excluded_fields=excluded_fields)
    if supplied != expected:
        raise ValueError(f"content identity mismatch: expected {expected}, got {supplied}")
