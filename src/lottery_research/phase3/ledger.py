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
        self._states: dict[tuple[str, str], str] = {}

    def _append(self, experiment_id: str, attempt_id: str, parent_attempt_id: str | None, state: str, details: dict[str, Any]) -> None:
        row = {
            "schema_version": "3.0.0",
            "artifact_type": "phase3_experiment_ledger_event",
            "ledger_identity": self.identity,
            "sequence": len(self._states) if state == "started" else len(self._states) + sum(value in TERMINAL_STATES for value in self._states.values()),
            "experiment_id": experiment_id,
            "attempt_id": attempt_id,
            "parent_attempt_id": parent_attempt_id,
            "state": state,
            "details": details,
        }
        self._handle.write(canonical_json_bytes(row))
        self._handle.flush()
        self._states[(experiment_id, attempt_id)] = state

    def start(self, experiment_id: str, details: dict[str, Any], *, attempt_id: str | None = None, parent_attempt_id: str | None = None) -> None:
        attempt_id = attempt_id or f"{experiment_id}-attempt-01"
        key = (experiment_id, attempt_id)
        if key in self._states:
            raise ValueError("attempt identity has already been used")
        self._append(experiment_id, attempt_id, parent_attempt_id, "started", details)

    def finish(self, experiment_id: str, state: str, details: dict[str, Any], *, attempt_id: str | None = None) -> None:
        attempt_id = attempt_id or f"{experiment_id}-attempt-01"
        key = (experiment_id, attempt_id)
        if state not in TERMINAL_STATES:
            raise ValueError("illegal experiment terminal state")
        if self._states.get(key) != "started":
            raise ValueError("terminal event requires exactly one started event")
        parent_attempt_id = details.pop("parent_attempt_id", None)
        self._append(experiment_id, attempt_id, parent_attempt_id, state, details)

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


def validate_ledger(path: Path) -> dict[tuple[str, str], str]:
    states: dict[tuple[str, str], str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            experiment = row["experiment_id"]
            attempt = row["attempt_id"]
            key = (experiment, attempt)
            state = row["state"]
            if state == "started":
                if key in states:
                    raise ValueError("duplicate attempt start")
            elif state in TERMINAL_STATES:
                if states.get(key) != "started":
                    raise ValueError("terminal state is missing its start or overwrites a terminal")
            else:
                raise ValueError("unknown ledger state")
            states[key] = state
    if any(state == "started" for state in states.values()):
        raise ValueError("registered experiment lacks a terminal state")
    return states


def canonical_attempts(path: Path, *, max_attempts_per_experiment: int = 2) -> dict[str, str]:
    """Select the earliest complete PASS attempt without erasing failed attempts."""
    states = validate_ledger(path)
    order: dict[tuple[str, str], int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for row in map(json.loads, handle):
            key = (row["experiment_id"], row["attempt_id"])
            if row["state"] == "started":
                order[key] = row["sequence"]
    by_experiment: dict[str, list[tuple[str, str]]] = {}
    for (experiment_id, attempt_id), state in states.items():
        by_experiment.setdefault(experiment_id, []).append((attempt_id, state))
    selected: dict[str, str] = {}
    for experiment_id, attempts in by_experiment.items():
        if len(attempts) > max_attempts_per_experiment:
            raise ValueError("experiment exceeds its registered attempt budget")
        passed = [attempt_id for attempt_id, state in attempts if state == "succeeded"]
        if passed:
            selected[experiment_id] = min(passed, key=lambda attempt_id: order[(experiment_id, attempt_id)])
    return selected
