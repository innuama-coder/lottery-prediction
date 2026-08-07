from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .serialization import canonical_json_bytes, canonical_sha256, load_json, write_new_json


TERMINAL_STATES = frozenset({"succeeded", "failed", "timeout", "crashed", "rejected", "not_opened", "indeterminate"})


class AppendOnlyLedger:
    def __init__(self, path: Path, identity: str) -> None:
        if not identity or "/" in identity or "latest" in identity.lower() or "*" in identity:
            raise ValueError("ledger identity must be explicit and immutable")
        self.path = path
        self.identity = identity
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("xb")
        self._states: dict[str, str] = {}

    def _append(self, experiment_id: str, state: str, details: dict[str, Any]) -> None:
        row = {
            "schema_version": "3.0.0",
            "artifact_type": "phase3_experiment_ledger_event",
            "ledger_identity": self.identity,
            "sequence": len(self._states) if state == "started" else len(self._states) + sum(value in TERMINAL_STATES for value in self._states.values()),
            "experiment_id": experiment_id,
            "state": state,
            "details": details,
        }
        self._handle.write(canonical_json_bytes(row))
        self._handle.flush()
        self._states[experiment_id] = state

    def start(self, experiment_id: str, details: dict[str, Any]) -> None:
        if experiment_id in self._states:
            raise ValueError("experiment identity has already been used")
        self._append(experiment_id, "started", details)

    def finish(self, experiment_id: str, state: str, details: dict[str, Any]) -> None:
        if state not in TERMINAL_STATES:
            raise ValueError("illegal experiment terminal state")
        if self._states.get(experiment_id) != "started":
            raise ValueError("terminal event requires exactly one started event")
        self._append(experiment_id, state, details)

    def close(self) -> None:
        self._handle.close()

    def __del__(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle is not None and not handle.closed:
            handle.close()


class CheckpointStore:
    def __init__(self, path: Path, run_identity: str) -> None:
        self.path = path
        self.run_identity = run_identity

    def write_new(self, payload: Any) -> None:
        write_new_json(
            self.path,
            {
                "schema_version": "3.0.0",
                "artifact_type": "phase3_checkpoint",
                "run_identity": self.run_identity,
                "payload_sha256": canonical_sha256(payload),
                "payload": payload,
            },
        )

    def load(self) -> dict[str, Any]:
        value = load_json(self.path)
        if value["run_identity"] != self.run_identity:
            raise ValueError("checkpoint run identity mismatch")
        if value["payload_sha256"] != canonical_sha256(value["payload"]):
            raise ValueError("checkpoint payload hash mismatch")
        return value


def validate_ledger(path: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            experiment = row["experiment_id"]
            state = row["state"]
            if state == "started":
                if experiment in states:
                    raise ValueError("duplicate experiment start")
            elif state in TERMINAL_STATES:
                if states.get(experiment) != "started":
                    raise ValueError("terminal state is missing its start or overwrites a terminal")
            else:
                raise ValueError("unknown ledger state")
            states[experiment] = state
    if any(state == "started" for state in states.values()):
        raise ValueError("registered experiment lacks a terminal state")
    return states

