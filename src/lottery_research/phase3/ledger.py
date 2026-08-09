from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .serialization import canonical_json_bytes, canonical_sha256, load_json, write_new_json


TERMINAL_STATES = frozenset({"succeeded", "failed", "timeout", "crashed", "rejected", "not_opened", "indeterminate"})
PROGRESS_STATES = ("started", "forecast_locked", "label_unlocked", "scored")


class AppendOnlyLedger:
    def __init__(self, path: Path, identity: str, *, resume: bool = False) -> None:
        if not identity or "/" in identity or "latest" in identity.lower() or "*" in identity:
            raise ValueError("ledger identity must be explicit and immutable")
        self.path = path
        self.identity = identity
        path.parent.mkdir(parents=True, exist_ok=True)
        self._states: dict[tuple[str, str], str] = {}
        self._sequence = 0
        if resume:
            if not path.is_file():
                raise ValueError("resume ledger is missing")
            with path.open("r", encoding="utf-8") as handle:
                for row in map(json.loads, handle):
                    if row["ledger_identity"] != identity or row["sequence"] != self._sequence:
                        raise ValueError("resume ledger identity or sequence mismatch")
                    self._states[(row["experiment_id"], row["attempt_id"])] = row["state"]
                    self._sequence += 1
            if any(state in TERMINAL_STATES for state in self._states.values()) and any(state not in TERMINAL_STATES for state in self._states.values()):
                raise ValueError("resume ledger contains an unterminated attempt")
            self._handle = path.open("ab")
        else:
            self._handle = path.open("xb")

    def _append(self, experiment_id: str, attempt_id: str, parent_attempt_id: str | None, state: str, details: dict[str, Any]) -> None:
        row = {
            "schema_version": "3.0.0",
            "artifact_type": "phase3_experiment_ledger_event",
            "ledger_identity": self.identity,
            "sequence": self._sequence,
            "experiment_id": experiment_id,
            "attempt_id": attempt_id,
            "parent_attempt_id": parent_attempt_id,
            "state": state,
            "details": details,
        }
        self._handle.write(canonical_json_bytes(row))
        self._handle.flush()
        self._states[(experiment_id, attempt_id)] = state
        self._sequence += 1

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
        if self._states.get(key) not in {"started", "scored"}:
            raise ValueError("terminal event requires a started or scored attempt")
        parent_attempt_id = details.pop("parent_attempt_id", None)
        self._append(experiment_id, attempt_id, parent_attempt_id, state, details)

    def progress(self, experiment_id: str, state: str, details: dict[str, Any], *, attempt_id: str | None = None) -> None:
        attempt_id = attempt_id or f"{experiment_id}-attempt-01"
        key = (experiment_id, attempt_id)
        if state not in PROGRESS_STATES[1:]:
            raise ValueError("illegal progress state")
        current = self._states.get(key)
        expected = {"forecast_locked": "started", "label_unlocked": "forecast_locked", "scored": "label_unlocked"}[state]
        if current != expected:
            raise ValueError(f"{state} requires {expected}")
        self._append(experiment_id, attempt_id, None, state, details)

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
            elif state in PROGRESS_STATES[1:]:
                expected = {"forecast_locked": "started", "label_unlocked": "forecast_locked", "scored": "label_unlocked"}[state]
                if states.get(key) != expected:
                    raise ValueError(f"ledger {state} is out of order")
            elif state in TERMINAL_STATES:
                if states.get(key) not in {"started", "scored"}:
                    raise ValueError("terminal state is missing its start or overwrites a terminal")
            else:
                raise ValueError("unknown ledger state")
            states[key] = state
    if any(state not in TERMINAL_STATES for state in states.values()):
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
