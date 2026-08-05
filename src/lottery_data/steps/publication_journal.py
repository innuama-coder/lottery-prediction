"""Durable publication transaction journal."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lottery_data.artifacts import atomic_write_json, load_json, write_once_json


STATES = (
    "PREPARED", "RELEASE_RENAMED", "PROJECTION_RENAMED", "POINTER_COMMITTED",
    "RUN_TERMINAL", "RESULT_WRITTEN", "COMPLETED",
)


class JournalError(RuntimeError):
    pass


def _safe_relative(value: str) -> str:
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or "\\" in value or ".." in pure.parts:
        raise JournalError(f"unsafe journal path: {value!r}")
    return pure.as_posix()


def _encoded(value: bytes | None) -> str | None:
    return None if value is None else base64.b64encode(value).decode("ascii")


def decode_pointer(value: str | None) -> bytes | None:
    try:
        return None if value is None else base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise JournalError("invalid pointer bytes in journal") from exc


def tree_sha256(path: Path) -> str:
    """Hash a directory tree including relative paths, types, and file bytes."""
    if not path.is_dir():
        raise JournalError(f"publication tree is missing: {path}")
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(b"D\0" if item.is_dir() else b"F\0")
        digest.update(relative)
        digest.update(b"\0")
        if item.is_file():
            digest.update(item.read_bytes())
        elif not item.is_dir():
            raise JournalError(f"unsupported publication tree entry: {item}")
    return digest.hexdigest()


@dataclass(frozen=True)
class PublicationJournal:
    path: Path

    @classmethod
    def create(
        cls,
        *,
        artifacts_root: Path,
        run_id: str,
        release_id: str,
        original_pointer_bytes: bytes | None,
        committed_pointer_bytes: bytes,
        release_path: str,
        projection_path: str,
        release_tree_sha256: str,
        projection_tree_sha256: str,
        temporary_paths: list[str],
        updated_at_utc: str,
    ) -> "PublicationJournal":
        path = artifacts_root / ".publication-journals" / f"{run_id}.json"
        value = {
            "journal_schema_version": "1.0.0",
            "run_id": run_id,
            "release_id": release_id,
            "state": "PREPARED",
            "original_pointer_b64": _encoded(original_pointer_bytes),
            "committed_pointer_b64": _encoded(committed_pointer_bytes),
            "release_path": _safe_relative(release_path),
            "projection_path": _safe_relative(projection_path),
            "release_tree_sha256": release_tree_sha256,
            "projection_tree_sha256": projection_tree_sha256,
            "temporary_paths": [_safe_relative(item) for item in temporary_paths],
            "updated_at_utc": updated_at_utc,
            "recovery": None,
        }
        write_once_json(path, value)
        return cls(path)

    def read(self) -> dict[str, Any]:
        value = load_json(self.path)
        required = {
            "journal_schema_version", "run_id", "release_id", "state", "original_pointer_b64",
            "committed_pointer_b64", "release_path", "projection_path", "temporary_paths",
            "release_tree_sha256", "projection_tree_sha256", "updated_at_utc", "recovery",
        }
        if set(value) != required or value["journal_schema_version"] != "1.0.0" or value["state"] not in STATES:
            raise JournalError(f"invalid publication journal: {self.path}")
        _safe_relative(value["release_path"])
        _safe_relative(value["projection_path"])
        if not isinstance(value["temporary_paths"], list):
            raise JournalError("journal temporary_paths must be a list")
        for item in value["temporary_paths"]:
            _safe_relative(item)
        decode_pointer(value["original_pointer_b64"])
        decode_pointer(value["committed_pointer_b64"])
        for field in ("release_tree_sha256", "projection_tree_sha256"):
            supplied = value[field]
            if not isinstance(supplied, str) or len(supplied) != 64 or any(c not in "0123456789abcdef" for c in supplied):
                raise JournalError(f"invalid {field}")
        return value

    def advance(self, state: str, *, updated_at_utc: str) -> dict[str, Any]:
        value = self.read()
        current = STATES.index(value["state"])
        if state not in STATES or STATES.index(state) != current + 1:
            raise JournalError(f"non-contiguous journal transition: {value['state']} -> {state}")
        value["state"] = state
        value["updated_at_utc"] = updated_at_utc
        atomic_write_json(self.path, value)
        return value

    def complete_recovery(
        self, *, updated_at_utc: str, quarantined: list[str], status: str = "interrupted",
    ) -> dict[str, Any]:
        value = self.read()
        if value["state"] == "COMPLETED":
            return value
        value["state"] = "COMPLETED"
        value["updated_at_utc"] = updated_at_utc
        if status not in {"interrupted", "rolled_forward"}:
            raise JournalError(f"unsupported recovery status: {status}")
        value["recovery"] = {"status": status, "quarantined": sorted(quarantined)}
        atomic_write_json(self.path, value)
        return value
