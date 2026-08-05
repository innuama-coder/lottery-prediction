from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _assert_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite JSON number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: JSON object keys must be strings")
            _assert_json_value(item, f"{path}.{key}")
        return
    raise TypeError(f"{path}: unsupported JSON value {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one value using the frozen canonical-json-v1 profile."""
    _assert_json_value(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.encode("utf-8") + b"\n"


def canonical_jsonl_bytes(
    values: Iterable[Mapping[str, Any]],
    *,
    sort_keys: tuple[str, ...],
) -> bytes:
    """Serialize JSONL in a frozen semantic field order."""
    if not sort_keys or any(not isinstance(key, str) or not key for key in sort_keys):
        raise ValueError("sort_keys must be a non-empty tuple of field names")
    rows = list(values)
    for index, row in enumerate(rows):
        missing = [key for key in sort_keys if key not in row]
        if missing:
            raise ValueError(f"row {index} is missing sort fields: {missing}")
    rows.sort(key=lambda row: tuple(row[key] for key in sort_keys))
    return b"".join(canonical_json_bytes(dict(row)) for row in rows)


def sha256_bytes(data: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def core_fact_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    draw_date = value.get("draw_date_local", value.get("draw_date"))
    if draw_date is None:
        raise KeyError("draw_date_local or draw_date is required")
    return {
        "game": value["game"],
        "issue_id": value["issue_id"],
        "draw_date": draw_date,
        "front_numbers": list(value["front_numbers"]),
        "back_numbers": list(value["back_numbers"]),
    }


def core_fact_sha256(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(core_fact_projection(value)))


def make_observation_id(
    source_id: str,
    game: str,
    issue_id: str,
    raw_sha256: str,
    parser_version: str,
) -> str:
    identity = {
        "source_id": source_id,
        "game": game,
        "issue_id": issue_id,
        "raw_sha256": raw_sha256,
        "parser_version": parser_version,
    }
    return "obs-v1:" + sha256_bytes(canonical_json_bytes(identity))


def make_revision_id(
    game: str,
    issue_id: str,
    core_fact_sha256: str,
    supersedes_revision_id: str | None,
) -> str:
    identity = {
        "game": game,
        "issue_id": issue_id,
        "core_fact_sha256": core_fact_sha256,
        "supersedes_revision_id": supersedes_revision_id,
    }
    return "rev-v1:" + sha256_bytes(canonical_json_bytes(identity))


def make_event_id(
    run_id: str,
    sequence: int,
    event_type: str,
    request_id: str | None,
    attempt: int | None,
) -> str:
    identity = {
        "run_id": run_id,
        "sequence": sequence,
        "event_type": event_type,
        "request_id": request_id,
        "attempt": attempt,
    }
    return "evt-v1:" + sha256_bytes(canonical_json_bytes(identity))


def _valid_bundle_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and "\\" not in value and ".." not in path.parts


def bundle_sha256(
    entries: Iterable[Mapping[str, str] | str | Path],
    *,
    root: str | Path | None = None,
) -> str:
    """Hash a canonical sorted ``{path, sha256}`` bundle manifest.

    Callers may provide already-hashed entries, or repository-relative paths
    together with ``root``. The freeze manifest itself is deliberately managed
    by the caller so self-reference can be excluded.
    """
    root_path = Path(root).resolve() if root is not None else None
    manifest: list[dict[str, str]] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            if set(entry) != {"path", "sha256"}:
                raise ValueError("bundle entries must contain exactly path and sha256")
            relative = entry["path"]
            digest = entry["sha256"]
        else:
            if root_path is None:
                raise ValueError("root is required when bundle entries are paths")
            supplied = Path(entry)
            if supplied.is_absolute():
                try:
                    relative = supplied.resolve().relative_to(root_path).as_posix()
                except ValueError as exc:
                    raise ValueError("bundle path is outside root") from exc
                disk_path = supplied.resolve()
            else:
                relative = supplied.as_posix()
                disk_path = (root_path / supplied).resolve()
                try:
                    disk_path.relative_to(root_path)
                except ValueError as exc:
                    raise ValueError("bundle path is outside root") from exc
            digest = sha256_file(disk_path)
        if not isinstance(relative, str) or not _valid_bundle_path(relative):
            raise ValueError(f"invalid bundle path: {relative!r}")
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid SHA-256 for {relative}")
        manifest.append({"path": relative, "sha256": digest})
    manifest.sort(key=lambda item: item["path"])
    paths = [item["path"] for item in manifest]
    if len(paths) != len(set(paths)):
        raise ValueError("bundle paths must be unique")
    return sha256_bytes(canonical_json_bytes(manifest))
