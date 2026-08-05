from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lottery_data.models import validate_object
from lottery_data.serialization import canonical_json_bytes, make_event_id


@dataclass(frozen=True)
class EventCheckpoint:
    sequence: int
    size_bytes: int


class EventLog:
    def __init__(
        self, path: Path, run_id: str, clock: Callable[[], str],
        *, event_schema_version: str = "1.0.0",
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.clock = clock
        self.event_schema_version = event_schema_version
        self.sequence = 0
        self.path.touch(exist_ok=False)

    @classmethod
    def open_existing(cls, path: Path, run_id: str, clock: Callable[[], str]) -> "EventLog":
        if not path.is_file():
            raise FileNotFoundError(path)
        sequence = 0
        event_schema_version: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            value = json.loads(line)
            supplied_version = value.get("event_schema_version")
            if event_schema_version is None:
                event_schema_version = supplied_version
            elif supplied_version != event_schema_version:
                raise ValueError("existing event log mixes schema versions")
            sequence += 1
            if value.get("run_id") != run_id or value.get("sequence") != sequence:
                raise ValueError("existing event log is not contiguous for this run")
        instance = cls.__new__(cls)
        instance.path = path
        instance.run_id = run_id
        instance.clock = clock
        instance.event_schema_version = event_schema_version or "1.0.0"
        instance.sequence = sequence
        return instance

    def checkpoint(self) -> EventCheckpoint:
        return EventCheckpoint(self.sequence, self.path.stat().st_size)

    def restore(self, checkpoint: EventCheckpoint, recovery_path: Path) -> None:
        with self.path.open("rb") as stream:
            stream.seek(checkpoint.size_bytes)
            tail = stream.read()
        if tail:
            recovery_path.parent.mkdir(parents=True, exist_ok=True)
            with recovery_path.open("xb") as stream:
                stream.write(tail)
                stream.flush()
                os.fsync(stream.fileno())
        with self.path.open("r+b") as stream:
            stream.truncate(checkpoint.size_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        self.sequence = checkpoint.sequence

    def append(
        self,
        event_type: str,
        *,
        request_id: str | None = None,
        attempt: int | None = None,
        source_id: str | None = None,
        game: str | None = None,
        artifact_ref: str | None = None,
        error_code: str | None = None,
        error_detail_ref: str | None = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        next_sequence = self.sequence + 1
        event = {
            "event_schema_version": self.event_schema_version,
            "event_id": make_event_id(self.run_id, next_sequence, event_type, request_id, attempt),
            "run_id": self.run_id,
            "sequence": next_sequence,
            "event_type": event_type,
            "occurred_at_utc": self.clock(),
            "request_id": request_id,
            "attempt": attempt,
            "source_id": source_id,
            "game": game,
            "artifact_ref": artifact_ref,
            "error_code": error_code,
            "error_detail_ref": error_detail_ref,
        }
        event.update(metadata)
        schema = {
            "1.1.0": "RunEventV1.1",
            "1.2.0": "RunEventV1.2",
            "1.3.0": "RunEventV1.3",
        }.get(self.event_schema_version, "RunEvent")
        validate_object(schema, event)
        with self.path.open("ab") as stream:
            stream.write(canonical_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        self.sequence = next_sequence
        return event
