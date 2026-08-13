from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .identity import content_id, validate_stable_id, verify_content_id
from .serialization import canonical_json_bytes, canonical_sha256, load_json, sha256_bytes, sha256_file
from .storage import (
    AdvisoryFileLock,
    IdentityReuseError,
    atomic_replace_json,
    ensure_directory,
    remove_durable,
    resolve_inside,
    write_once_json,
)


class LedgerMismatch(ValueError):
    exit_code = 5


class StaleLedgerHead(LedgerMismatch):
    pass


@dataclass(frozen=True)
class LedgerHead:
    ledger_id: str
    ordinal: int
    event_path: str
    event_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "ordinal": self.ordinal,
            "event_path": self.event_path,
            "event_sha256": self.event_sha256,
        }


class AppendOnlyLedger:
    def __init__(self, runtime_root: Path, ledger_id: str) -> None:
        self.runtime_root = runtime_root.resolve()
        self.ledger_id = validate_stable_id(ledger_id, "ledger identity")
        self.root = resolve_inside(self.runtime_root, f"ledgers/{self.ledger_id}")
        self.events_root = self.root / "events"
        self.payloads_root = self.root / "payloads"
        self.head_path = self.root / "head.json"
        self.current_view_path = self.root / "current-view.json"
        self.pending_path = self.root / ".pending-append.json"
        self.lock = AdvisoryFileLock(self.root / ".ledger.lock")

    def _event_files(self) -> list[Path]:
        if not self.events_root.exists():
            return []
        return sorted(path for path in self.events_root.iterdir() if path.is_file() and path.suffix == ".json")

    def _read_head_unlocked(self) -> LedgerHead | None:
        if not self.head_path.exists():
            return None
        value = load_json(self.head_path, reject_floats=True)
        if set(value) != {"ledger_id", "ordinal", "event_path", "event_sha256"}:
            raise LedgerMismatch("ledger head fields are invalid")
        if value["ledger_id"] != self.ledger_id:
            raise LedgerMismatch("ledger head identity mismatch")
        path = resolve_inside(self.runtime_root, value["event_path"])
        if not path.is_file() or sha256_file(path) != value["event_sha256"]:
            raise LedgerMismatch("ledger head event hash mismatch")
        return LedgerHead(**value)

    def _validate_events_unlocked(self, *, require_head: bool = True) -> tuple[list[dict[str, Any]], LedgerHead | None]:
        rows: list[dict[str, Any]] = []
        previous: str | None = None
        for ordinal, path in enumerate(self._event_files(), start=1):
            row = load_json(path, reject_floats=True)
            required = {
                "schema_version", "artifact_type", "event_id", "ledger_id", "ordinal",
                "previous_event_sha256", "object_id", "event_type", "event_at_utc",
                "payload_sha256", "producer_provenance",
            }
            if set(row) != required or row["schema_version"] != "1.0.0" or row["artifact_type"] != "phase4_ledger_event":
                raise LedgerMismatch("ledger event shape mismatch")
            if row["ledger_id"] != self.ledger_id or row["ordinal"] != ordinal:
                raise LedgerMismatch("ledger identity or ordinal mismatch")
            if row["previous_event_sha256"] != previous:
                raise LedgerMismatch("ledger previous-event hash mismatch")
            verify_content_id(row["event_id"], "event", row, excluded_fields=("event_id",))
            expected_name = f"{ordinal:012d}-{row['event_id'].split(':', 1)[1]}.json"
            if path.name != expected_name:
                raise LedgerMismatch("ledger event filename does not match its ordinal and identity")
            payload_path = self.payloads_root / f"{row['payload_sha256']}.json"
            if not payload_path.is_file() or sha256_file(payload_path) != row["payload_sha256"]:
                raise LedgerMismatch("ledger payload object mismatch")
            previous = sha256_file(path)
            rows.append(row)
        head = self._read_head_unlocked()
        if rows:
            last_path = self._event_files()[-1]
            expected = LedgerHead(
                ledger_id=self.ledger_id,
                ordinal=len(rows),
                event_path=last_path.relative_to(self.runtime_root).as_posix(),
                event_sha256=sha256_file(last_path),
            )
            if require_head and head != expected:
                raise LedgerMismatch("ledger head does not identify the final event")
        elif head is not None:
            raise LedgerMismatch("empty ledger has a head")
        return rows, head

    def _project_unlocked(self, rows: list[dict[str, Any]], head: LedgerHead) -> dict[str, Any]:
        objects: dict[str, dict[str, Any]] = {}
        for row in rows:
            objects[row["object_id"]] = {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "ordinal": row["ordinal"],
                "payload_sha256": row["payload_sha256"],
            }
        return {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_ledger_current_view",
            "ledger_id": self.ledger_id,
            "head_sha256": head.event_sha256,
            "head_ordinal": head.ordinal,
            "objects": objects,
        }

    def _recover_unlocked(self) -> None:
        if not self.pending_path.exists():
            return
        pending = load_json(self.pending_path, reject_floats=True)
        if set(pending) != {"ledger_id", "previous_head_sha256", "event_path", "event_sha256", "event"}:
            raise LedgerMismatch("pending append journal shape mismatch")
        if pending["ledger_id"] != self.ledger_id:
            raise LedgerMismatch("pending append ledger mismatch")
        event_path = resolve_inside(self.runtime_root, pending["event_path"])
        if not event_path.exists():
            remove_durable(self.pending_path)
            return
        if sha256_file(event_path) != pending["event_sha256"] or load_json(event_path, reject_floats=True) != pending["event"]:
            raise LedgerMismatch("pending append event mismatch")
        current = self._read_head_unlocked()
        current_hash = None if current is None else current.event_sha256
        if current_hash not in {pending["previous_head_sha256"], pending["event_sha256"]}:
            raise LedgerMismatch("pending append cannot reconcile the current head")
        row = pending["event"]
        new_head = LedgerHead(self.ledger_id, row["ordinal"], pending["event_path"], pending["event_sha256"])
        atomic_replace_json(self.head_path, new_head.as_dict())
        rows, _ = self._validate_events_unlocked(require_head=True)
        atomic_replace_json(self.current_view_path, self._project_unlocked(rows, new_head))
        remove_durable(self.pending_path)

    def read_head(self) -> LedgerHead | None:
        with self.lock:
            self._recover_unlocked()
            _, head = self._validate_events_unlocked()
            return head

    def validate(self) -> dict[str, Any]:
        with self.lock:
            self._recover_unlocked()
            rows, head = self._validate_events_unlocked()
            if head is not None:
                expected_view = self._project_unlocked(rows, head)
                if not self.current_view_path.is_file() or load_json(self.current_view_path, reject_floats=True) != expected_view:
                    raise LedgerMismatch("ledger current view mismatch")
            return {
                "status": "PASS",
                "ledger_id": self.ledger_id,
                "event_count": len(rows),
                "head_sha256": None if head is None else head.event_sha256,
            }

    def append_event(
        self,
        *,
        object_id: str,
        event_type: str,
        event_at_utc: str,
        payload: Mapping[str, Any],
        producer_provenance: Mapping[str, Any],
        expected_head_sha256: str | None,
        fault: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        validate_stable_id(object_id, "object identity")
        validate_stable_id(event_type, "event type")
        callback = fault or (lambda _stage: None)
        with self.lock:
            self._recover_unlocked()
            rows, head = self._validate_events_unlocked()
            actual_head = None if head is None else head.event_sha256
            if actual_head != expected_head_sha256:
                raise StaleLedgerHead(f"expected head {expected_head_sha256}, found {actual_head}")
            payload_bytes = canonical_json_bytes(dict(payload))
            payload_sha = sha256_bytes(payload_bytes)
            payload_path = self.payloads_root / f"{payload_sha}.json"
            if payload_path.exists():
                if sha256_file(payload_path) != payload_sha:
                    raise LedgerMismatch("existing content-addressed payload is corrupt")
            else:
                write_once_json(payload_path, dict(payload))
            callback("after_payload")
            body: dict[str, Any] = {
                "schema_version": "1.0.0",
                "artifact_type": "phase4_ledger_event",
                "ledger_id": self.ledger_id,
                "ordinal": len(rows) + 1,
                "previous_event_sha256": actual_head,
                "object_id": object_id,
                "event_type": event_type,
                "event_at_utc": event_at_utc,
                "payload_sha256": payload_sha,
                "producer_provenance": dict(producer_provenance),
            }
            body["event_id"] = content_id("event", body)
            event_name = f"{body['ordinal']:012d}-{body['event_id'].split(':', 1)[1]}.json"
            event_path = self.events_root / event_name
            event_relative = event_path.relative_to(self.runtime_root).as_posix()
            event_sha = canonical_sha256(body)
            pending = {
                "ledger_id": self.ledger_id,
                "previous_head_sha256": actual_head,
                "event_path": event_relative,
                "event_sha256": event_sha,
                "event": body,
            }
            write_once_json(self.pending_path, pending)
            callback("after_journal")
            write_once_json(event_path, body)
            callback("after_event")
            new_head = LedgerHead(self.ledger_id, body["ordinal"], event_relative, event_sha)
            atomic_replace_json(self.head_path, new_head.as_dict())
            callback("after_head")
            projected_rows = [*rows, body]
            atomic_replace_json(self.current_view_path, self._project_unlocked(projected_rows, new_head))
            callback("after_view")
            remove_durable(self.pending_path)
            callback("after_commit")
            return {"event": body, "event_sha256": event_sha, "head": new_head.as_dict()}


def append_event(runtime_root: Path, ledger_id: str, **arguments: Any) -> dict[str, Any]:
    return AppendOnlyLedger(runtime_root, ledger_id).append_event(**arguments)
