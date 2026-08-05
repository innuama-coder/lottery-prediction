from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_file


_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_stable_id(value: object, label: str) -> str:
    """Validate an identifier without constructing or touching a filesystem path."""
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{label} must be a stable identifier")
    return value


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_once_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_parent(path.parent)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_parent(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value))


def atomic_write_jsonl(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    sort_keys: tuple[str, ...],
) -> None:
    atomic_write_bytes(path, canonical_jsonl_bytes(rows, sort_keys=sort_keys))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def managed_hashes(root: Path, paths: Iterable[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    resolved_root = root.resolve()
    for path in paths:
        resolved = path.resolve()
        relative = resolved.relative_to(resolved_root).as_posix()
        result[relative] = sha256_file(resolved)
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class RunLayout:
    artifacts_root: Path
    run_id: str

    def __post_init__(self) -> None:
        validate_stable_id(self.run_id, "run-id")

    @property
    def run_root(self) -> Path:
        return self.artifacts_root / "runs" / self.run_id

    @property
    def manifest(self) -> Path:
        return self.run_root / "run-manifest.json"

    @property
    def events(self) -> Path:
        return self.run_root / "events.jsonl"

    @property
    def raw_root(self) -> Path:
        return self.run_root / "raw"

    @property
    def observations(self) -> Path:
        return self.run_root / "observations.jsonl"

    @property
    def reconciliation(self) -> Path:
        return self.run_root / "reconciliation.jsonl"

    @property
    def candidate_draws(self) -> Path:
        return self.run_root / "candidate-draws.jsonl"

    @property
    def quality_report(self) -> Path:
        return self.run_root / "quality-report.json"

    @property
    def hashes(self) -> Path:
        return self.run_root / "hashes.json"

    @property
    def result(self) -> Path:
        return self.run_root / "run-result.json"

    def ref(self, path: Path) -> str:
        return path.resolve().relative_to(self.artifacts_root.resolve()).as_posix()

    def create(self) -> None:
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=False)
        self.raw_root.mkdir()
