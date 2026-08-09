from __future__ import annotations

import json
import os
import secrets
import hmac
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import validate_payload
from .serialization import canonical_sha256, load_json, sha256_file, write_new_json


LABEL_FIELDS = frozenset({"front_numbers", "back_numbers"})
TRAINER_FORBIDDEN_FIELDS = frozenset({
    "front_numbers", "back_numbers", "label", "label_store", "label_store_path",
    "target_front_numbers", "target_back_numbers",
})
LABEL_SOURCE = Path("artifacts/phase-1/baseline-v1/draws.jsonl")


# A spawned trainer is permanently quarantined before it inspects its payload.
# Scoring access is represented by an opaque, process-bound capability rather
# than a caller-supplied role string.  The capability is never serialized or
# submitted to the trainer executor.
_PROCESS_CAPABILITY_ROLE = "unassigned"
_PROCESS_CAPABILITY_NONCE = secrets.token_hex(32)
_TRAINER_FS_GUARD_INSTALLED = False


@dataclass(frozen=True)
class _ScoringCapability:
    owner_pid: int
    nonce: str


def quarantine_current_process_as_trainer() -> None:
    global _PROCESS_CAPABILITY_ROLE, _TRAINER_FS_GUARD_INSTALLED
    if _PROCESS_CAPABILITY_ROLE == "scorer":
        raise ValueError("TRAINER_PROCESS_ALREADY_HAS_SCORING_CAPABILITY")
    _PROCESS_CAPABILITY_ROLE = "trainer"
    if _TRAINER_FS_GUARD_INSTALLED:
        return
    artifacts_root = (Path.cwd() / "artifacts").resolve()

    def deny_label_bearing_filesystem(event: str, arguments: tuple[object, ...]) -> None:
        if event == "open" and arguments and isinstance(arguments[0], (str, bytes)):
            raw = os.fsdecode(arguments[0])
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            try:
                if candidate.resolve().is_relative_to(artifacts_root):
                    raise PermissionError("TRAINER_ARTIFACT_FILESYSTEM_CAPABILITY_DENIED")
            except (OSError, RuntimeError):
                raise PermissionError("TRAINER_ARTIFACT_FILESYSTEM_CAPABILITY_DENIED")
        if event in {
            "subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp",
            "os.fork", "os.forkpty", "os.exec",
        } or event.startswith("ctypes."):
            raise PermissionError("TRAINER_CHILD_PROCESS_CAPABILITY_DENIED")

    sys.addaudithook(deny_label_bearing_filesystem)
    _TRAINER_FS_GUARD_INSTALLED = True


def activate_scoring_capability() -> _ScoringCapability:
    """Mint a non-transferable capability unless this process is a trainer."""

    global _PROCESS_CAPABILITY_ROLE
    if _PROCESS_CAPABILITY_ROLE == "trainer":
        raise ValueError("LABEL_STORE_CAPABILITY_DENIED")
    _PROCESS_CAPABILITY_ROLE = "scorer"
    return _ScoringCapability(os.getpid(), _PROCESS_CAPABILITY_NONCE)


def _require_scoring_capability(capability: object) -> None:
    if (
        _PROCESS_CAPABILITY_ROLE != "scorer"
        or not isinstance(capability, _ScoringCapability)
        or capability.owner_pid != os.getpid()
        or not hmac.compare_digest(capability.nonce, _PROCESS_CAPABILITY_NONCE)
    ):
        raise ValueError("LABEL_STORE_CAPABILITY_DENIED")


@dataclass(frozen=True)
class TargetMetadata:
    """Label-free target identity published by the frozen target catalog."""

    game: str
    target_issue: str
    ordinal: int
    source_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "game": self.game,
            "target_issue": self.target_issue,
            "ordinal": self.ordinal,
            "source_count": self.source_count,
        }


@dataclass(frozen=True)
class UnlockedLabel:
    game: str
    target_issue: str
    front_numbers: tuple[int, ...]
    back_numbers: tuple[int, ...]
    receipt_path: str
    receipt_sha256: str
    label_store_identity: str


def load_target_catalog(root: Path) -> tuple[TargetMetadata, ...]:
    """Load only non-label target metadata from the frozen availability ledger."""

    ledger = load_json(root / "config/phase3/availability-ledger.json")
    targets: list[TargetMetadata] = []
    by_game: dict[str, list[dict[str, Any]]] = {"dlt": [], "ssq": []}
    for row in ledger["entries"]:
        if LABEL_FIELDS.intersection(row):
            raise ValueError("TARGET_CATALOG_CONTAINS_LABEL_FIELDS")
        if row.get("eligibility") == "eligible" and row.get("source_field") == "prior_draw_result":
            by_game[row["game"]].append(row)
    for game in ("dlt", "ssq"):
        rows = sorted(by_game[game], key=lambda value: value["target_issue"])
        if len(rows) != 150:
            raise ValueError(f"{game} target catalog coverage is not 150")
        for ordinal, row in enumerate(rows, start=50):
            if row["source_count"] != ordinal or len(row["source_issues"]) != ordinal:
                raise ValueError("target catalog ordinal/source count mismatch")
            if row["source_issues"][-1] >= row["target_issue"]:
                raise ValueError("target catalog source is not strictly earlier")
            targets.append(TargetMetadata(game, row["target_issue"], ordinal, row["source_count"]))
    if len(targets) != 300 or len({(row.game, row.target_issue) for row in targets}) != 300:
        raise ValueError("target catalog identity coverage mismatch")
    return tuple(targets)


def read_training_prefix(root: Path, target: TargetMetadata) -> tuple[dict[str, Any], ...]:
    """Return a sanitized same-game prefix without reading the target record.

    The loop terminates as soon as ``source_count`` same-game rows have been
    consumed.  Consequently the target and all later same-game records are not
    parsed or retained by this interface.
    """

    prefix: list[dict[str, Any]] = []
    with (root / LABEL_SOURCE).open("r", encoding="utf-8") as handle:
        while len(prefix) < target.source_count:
            line = handle.readline()
            if not line:
                raise ValueError("training prefix source ended before the registered cutoff")
            row = json.loads(line)
            if row["game"] != target.game:
                continue
            if row["issue_id"] >= target.target_issue:
                raise ValueError("TRAINING_PREFIX_REACHED_TARGET_OR_FUTURE_LABEL")
            prefix.append({
                "game": row["game"],
                "issue_id": row["issue_id"],
                "prior_front_numbers": tuple(row["front_numbers"]),
                "prior_back_numbers": tuple(row["back_numbers"]),
            })
    if len(prefix) != target.source_count or prefix[-1]["issue_id"] >= target.target_issue:
        raise ValueError("training prefix coverage or cutoff mismatch")
    if any(TRAINER_FORBIDDEN_FIELDS.intersection(row) for row in prefix):
        raise ValueError("TRAINER_INPUT_CONTAINS_FORBIDDEN_LABEL_CAPABILITY")
    return tuple(prefix)


def trainer_input_payload(target: TargetMetadata, prefix: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(prefix)
    payload = {
        "schema_version": "phase3_trainer_input_v1",
        "capability": "training_prefix_only_no_label_store",
        "target": target.as_dict(),
        "prefix": rows,
    }
    if TRAINER_FORBIDDEN_FIELDS.intersection(payload) or any(TRAINER_FORBIDDEN_FIELDS.intersection(row) for row in rows):
        raise ValueError("TRAINER_INPUT_CONTAINS_FORBIDDEN_LABEL_CAPABILITY")
    if len(rows) != target.source_count or any(row["game"] != target.game or row["issue_id"] >= target.target_issue for row in rows):
        raise ValueError("TRAINER_INPUT_PREFIX_BOUNDARY_REJECTED")
    return payload


def read_scoring_label_inventory(root: Path, *, capability: object) -> dict[str, list[dict[str, Any]]]:
    """Explicit scoring/replay interface protected by a process capability."""

    _require_scoring_capability(capability)
    rows: dict[str, list[dict[str, Any]]] = {"dlt": [], "ssq": []}
    with (root / LABEL_SOURCE).open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[row["game"]].append(row)
    for game in rows:
        rows[game].sort(key=lambda row: row["issue_id"])
        if len(rows[game]) != 200 or len({row["issue_id"] for row in rows[game]}) != 200:
            raise ValueError(f"{game} frozen scoring label inventory is invalid")
    return rows


def _ledger_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class GuardedLabelStore:
    """Scoring-only label capability guarded by persisted forecast state."""

    def __init__(self, root: Path, *, capability: object) -> None:
        _require_scoring_capability(capability)
        self._capability = capability
        self.root = root.resolve()
        self.source = self.root / LABEL_SOURCE
        manifest = load_json(self.root / "config/phase3/input-manifest.json")
        source_row = next(row for row in manifest["files"] if row["role"] == "phase1_draws")
        self._expected_source_sha256 = source_row["sha256"]
        self.identity = f"phase3-label-store-v1:{self._expected_source_sha256}"
        self._source_hash_verified = False
        self.number_read_count = 0
        self.rejection_count = 0

    def _reject(self, message: str) -> None:
        self.rejection_count += 1
        raise ValueError(message)

    def _read_one(self, game: str, target_issue: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
        self.number_read_count += 1
        matches: list[dict[str, Any]] = []
        with self.source.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row["game"] == game and row["issue_id"] == target_issue:
                    matches.append(row)
        if len(matches) != 1:
            raise ValueError("label store target identity is not unique")
        row = matches[0]
        return tuple(row["front_numbers"]), tuple(row["back_numbers"])

    def guarded_unlock(
        self,
        *,
        release_root: Path,
        ledger: Any,
        experiment_id: str,
        attempt_id: str,
        release_id: str,
        run_id: str,
        game: str,
        target_issue: str,
        model_id: str,
        forecast_path: Path,
        receipt_path: Path,
    ) -> UnlockedLabel:
        """Validate every lock binding before performing the first label read."""

        _require_scoring_capability(capability=self._capability)
        release_root = release_root.resolve()
        forecast_path = forecast_path.resolve()
        receipt_path = receipt_path.resolve()
        try:
            forecast_relative = forecast_path.relative_to(release_root).as_posix()
            receipt_relative = receipt_path.relative_to(release_root).as_posix()
        except ValueError:
            self._reject("LABEL_UNLOCK_PATH_OUTSIDE_RELEASE")
        expected_forecast_relative = f"runs/forecasts/{game}/{target_issue}/{model_id}.json"
        expected_receipt_relative = f"runs/label-unlocks/{game}/{target_issue}/{model_id}.json"
        if forecast_relative != expected_forecast_relative or receipt_relative != expected_receipt_relative:
            self._reject("LABEL_UNLOCK_NONCANONICAL_PATH")
        if not forecast_path.is_file() or receipt_path.exists():
            self._reject("LABEL_UNLOCK_FORECAST_NOT_PERSISTED_OR_RECEIPT_REUSED")
        if release_id != release_root.name:
            self._reject("LABEL_UNLOCK_RELEASE_ID_MISMATCH")
        expected_experiment = f"{game}-{target_issue}-{model_id}"
        if experiment_id != expected_experiment:
            self._reject("LABEL_UNLOCK_EXPERIMENT_ID_MISMATCH")
        if attempt_id != f"{experiment_id}-attempt-01":
            self._reject("LABEL_UNLOCK_ATTEMPT_ID_MISMATCH")
        expected_ledger_path = release_root / "runs/experiment-ledger.jsonl"
        ledger_path = Path(getattr(ledger, "path", "")).resolve()
        if ledger_path != expected_ledger_path or getattr(ledger, "identity", None) != run_id:
            self._reject("LABEL_UNLOCK_LEDGER_CAPABILITY_MISMATCH")
        rows = _ledger_rows(ledger_path)
        if not rows:
            self._reject("LABEL_UNLOCK_PRE_LOCK_REJECTED")
        if any(
            row.get("ledger_identity") != run_id or row.get("sequence") != sequence
            for sequence, row in enumerate(rows)
        ):
            self._reject("LABEL_UNLOCK_LEDGER_IDENTITY_OR_SEQUENCE_MISMATCH")
        latest = rows[-1]
        if (
            latest.get("state") != "forecast_locked"
            or latest.get("ledger_identity") != run_id
            or latest.get("experiment_id") != experiment_id
            or latest.get("attempt_id") != attempt_id
        ):
            self._reject("LABEL_UNLOCK_LATEST_LEDGER_STATE_MISMATCH")
        started = [row for row in rows if row.get("state") == "started" and row.get("experiment_id") == experiment_id and row.get("attempt_id") == attempt_id]
        if len(started) != 1:
            self._reject("LABEL_UNLOCK_STARTED_IDENTITY_MISMATCH")
        start_details = started[0].get("details", {})
        if (start_details.get("release_id"), start_details.get("game"), start_details.get("target_issue"), start_details.get("model_id")) != (release_id, game, target_issue, model_id):
            self._reject("LABEL_UNLOCK_STARTED_IDENTITY_MISMATCH")
        locked_sha = latest.get("details", {}).get("forecast_sha256")
        lock_identity = latest.get("details", {})
        if (
            lock_identity.get("release_id"), lock_identity.get("run_id"),
            lock_identity.get("experiment_id"), lock_identity.get("attempt_id"),
            lock_identity.get("game"), lock_identity.get("target_issue"),
            lock_identity.get("model_id"), lock_identity.get("forecast_path"),
        ) != (
            release_id, run_id, experiment_id, attempt_id, game, target_issue,
            model_id, forecast_relative,
        ):
            self._reject("LABEL_UNLOCK_FORECAST_PATH_MISMATCH")
        if not isinstance(locked_sha, str) or sha256_file(forecast_path) != locked_sha:
            self._reject("LABEL_UNLOCK_FORECAST_HASH_MISMATCH")
        forecast = load_json(forecast_path)
        if (
            forecast.get("release_id"), forecast.get("run_id"), forecast.get("game"),
            forecast.get("target_issue"), forecast.get("model_id")
        ) != (release_id, run_id, game, target_issue, model_id):
            self._reject("LABEL_UNLOCK_FORECAST_IDENTITY_MISMATCH")
        if not self._source_hash_verified:
            if sha256_file(self.source) != self._expected_source_sha256:
                self._reject("LABEL_STORE_SOURCE_HASH_MISMATCH")
            self._source_hash_verified = True

        # This is intentionally the first call that reads target numbers.
        front, back = self._read_one(game, target_issue)
        receipt = {
            "schema_version": "3.0.0",
            "artifact_type": "phase3_guarded_label_unlock_receipt",
            "release_id": release_id,
            "run_id": run_id,
            "experiment_id": experiment_id,
            "attempt_id": attempt_id,
            "game": game,
            "target_issue": target_issue,
            "model_id": model_id,
            "forecast_path": forecast_relative,
            "forecast_sha256": locked_sha,
            "label_store_identity": self.identity,
            "label_sha256": canonical_sha256({"front": front, "back": back}),
            "label_unlocked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "read_ordinal": self.number_read_count,
            "guard_validation": {
                "forecast_persisted": True,
                "forecast_current_hash_matches_lock": True,
                "latest_ledger_state_is_matching_lock": True,
                "release_experiment_attempt_target_model_match": True,
                "label_read_after_all_guards": True,
            },
        }
        validate_payload(self.root, "guarded_label_unlock", receipt)
        write_new_json(receipt_path, receipt)
        receipt_sha = sha256_file(receipt_path)
        ledger.progress(experiment_id, "label_unlocked", {
            "release_id": release_id,
            "run_id": run_id,
            "experiment_id": experiment_id,
            "attempt_id": attempt_id,
            "game": game,
            "target_issue": target_issue,
            "model_id": model_id,
            "forecast_path": forecast_relative,
            "forecast_sha256": locked_sha,
            "label_store_identity": self.identity,
            "unlock_receipt_path": receipt_relative,
            "unlock_receipt_sha256": receipt_sha,
            "label_sha256": receipt["label_sha256"],
            "label_unlocked_at": receipt["label_unlocked_at"],
        }, attempt_id=attempt_id)
        return UnlockedLabel(game, target_issue, front, back, receipt_relative, receipt_sha, self.identity)


def validate_guarded_unlock_evidence(release_root: Path) -> dict[str, Any]:
    """Recompute all guarded unlock bindings directly from ledger/files."""

    release_root = release_root.resolve()
    ledger_path = release_root / "runs/experiment-ledger.jsonl"
    events = _ledger_rows(ledger_path)
    with (release_root / "runs/forecast-index.jsonl").open("r", encoding="utf-8") as handle:
        forecasts = [json.loads(line) for line in handle if line.strip()]
    with (release_root / "runs/metric-index.jsonl").open("r", encoding="utf-8") as handle:
        metrics = [json.loads(line) for line in handle if line.strip()]
    unlock_events = [row for row in events if row["state"] == "label_unlocked"]
    lock_events = [row for row in events if row["state"] == "forecast_locked"]
    actual_receipt_paths = sorted((release_root / "runs/label-unlocks").rglob("*.json")) if (release_root / "runs/label-unlocks").is_dir() else []
    input_manifest = load_json(release_root / "contracts/input-manifest.json")
    draw_source = next(row for row in input_manifest["files"] if row["role"] == "phase1_draws")
    expected_store_identity = f"phase3-label-store-v1:{draw_source['sha256']}"
    bad_keys: set[tuple[str, str, str]] = set()
    pre_lock_keys: set[tuple[str, str, str]] = set()
    receipt_hashes: set[str] = set()
    referenced_receipt_paths: set[Path] = set()
    sequence_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    positions_by_key: dict[tuple[str, str], list[int]] = {}
    for position, row in enumerate(events):
        sequence_by_key.setdefault((row["experiment_id"], row["attempt_id"]), []).append(row)
        positions_by_key.setdefault((row["experiment_id"], row["attempt_id"]), []).append(position)
    metric_by_key = {(row["game"], row["target_issue"], row["model_id"]): row for row in metrics}
    canonical_forecast_paths: dict[tuple[str, str, str], Path] = {}
    run_ids: set[object] = set()
    for item in forecasts:
        artifact_key = (item["game"], item["target_issue"], item["model_id"])
        expected_index_path = f"forecasts/{item['game']}/{item['target_issue']}/{item['model_id']}.json"
        path = (release_root / "runs" / expected_index_path).resolve()
        canonical_forecast_paths[artifact_key] = path
        if item.get("path") != expected_index_path or not path.is_relative_to(release_root):
            bad_keys.add(artifact_key)
        elif path.is_file():
            run_ids.add(load_json(path).get("run_id"))
    run_id = next(iter(run_ids)) if len(run_ids) == 1 else None
    ledger_identity_mismatch = sum(
        row.get("sequence") != sequence or row.get("ledger_identity") != run_id
        for sequence, row in enumerate(events)
    )
    for index in forecasts:
        artifact_key = (index["game"], index["target_issue"], index["model_id"])
        experiment = f"{index['game']}-{index['target_issue']}-{index['model_id']}"
        attempt = f"{experiment}-attempt-01"
        key = (experiment, attempt)
        rows = sequence_by_key.get(key, [])
        positions = positions_by_key.get(key, [])
        if [row.get("state") for row in rows] != ["started", "forecast_locked", "label_unlocked", "scored", "succeeded"]:
            bad_keys.add(artifact_key)
            pre_lock_keys.add(artifact_key)
            continue
        started, lock, unlock = rows[:3]
        # The unlock must be globally adjacent to this exact lock, not merely
        # ordered somewhere inside the same attempt's filtered history.
        if positions[2] != positions[1] + 1 or events[positions[2] - 1] is not lock:
            bad_keys.add(artifact_key)
            pre_lock_keys.add(artifact_key)
        forecast_path = canonical_forecast_paths[artifact_key]
        details = unlock["details"]
        expected_receipt_relative = f"runs/label-unlocks/{index['game']}/{index['target_issue']}/{index['model_id']}.json"
        receipt_path = (release_root / expected_receipt_relative).resolve()
        referenced_receipt_paths.add(receipt_path)
        if not receipt_path.is_file():
            bad_keys.add(artifact_key)
            continue
        receipt_sha = sha256_file(receipt_path)
        receipt = load_json(receipt_path)
        forecast = load_json(forecast_path) if forecast_path.is_file() else {}
        receipt_hashes.add(receipt_sha)
        expected = (
            release_root.name, experiment, attempt, index["game"], index["target_issue"], index["model_id"],
            f"runs/forecasts/{index['game']}/{index['target_issue']}/{index['model_id']}.json", index["sha256"],
        )
        observed_receipt = (
            receipt.get("release_id"), receipt.get("experiment_id"), receipt.get("attempt_id"), receipt.get("game"),
            receipt.get("target_issue"), receipt.get("model_id"), receipt.get("forecast_path"), receipt.get("forecast_sha256"),
        )
        observed_event = (
            details.get("release_id"), details.get("experiment_id"), details.get("attempt_id"), details.get("game"),
            details.get("target_issue"), details.get("model_id"), details.get("forecast_path"), details.get("forecast_sha256"),
        )
        lock_details = lock.get("details", {})
        observed_lock = (
            lock_details.get("release_id"), lock_details.get("experiment_id"), lock_details.get("attempt_id"),
            lock_details.get("game"), lock_details.get("target_issue"), lock_details.get("model_id"),
            lock_details.get("forecast_path"), lock_details.get("forecast_sha256"),
        )
        guards = receipt.get("guard_validation", {})
        start_details = started.get("details", {})
        metric = metric_by_key.get(artifact_key, {})
        if (
            observed_receipt != expected or observed_event != expected or observed_lock != expected
            or unlock.get("ledger_identity") != run_id or lock.get("ledger_identity") != run_id
            or details.get("run_id") != run_id or lock_details.get("run_id") != run_id
            or receipt.get("run_id") != unlock.get("ledger_identity")
            or forecast.get("run_id") != unlock.get("ledger_identity")
            or (forecast.get("release_id"), forecast.get("game"), forecast.get("target_issue"), forecast.get("model_id")) != (release_root.name, index["game"], index["target_issue"], index["model_id"])
            or (start_details.get("release_id"), start_details.get("game"), start_details.get("target_issue"), start_details.get("model_id")) != (release_root.name, index["game"], index["target_issue"], index["model_id"])
            or details.get("unlock_receipt_sha256") != receipt_sha
            or details.get("unlock_receipt_path") != expected_receipt_relative
            or metric.get("label_unlock_receipt_path") != expected_receipt_relative
            or metric.get("label_unlock_receipt_sha256") != receipt_sha
            or details.get("label_store_identity") != receipt.get("label_store_identity")
            or receipt.get("label_store_identity") != expected_store_identity
            or not forecast_path.is_file() or sha256_file(forecast_path) != index["sha256"]
            or not guards or not all(value is True for value in guards.values())
        ):
            bad_keys.add(artifact_key)
    if ledger_identity_mismatch:
        bad_keys.update((row["game"], row["target_issue"], row["model_id"]) for row in forecasts)
    actual_receipt_set = {path.resolve() for path in actual_receipt_paths}
    receipt_inventory_mismatch = 0 if referenced_receipt_paths == actual_receipt_set else 1
    if receipt_inventory_mismatch:
        bad_keys.update((row["game"], row["target_issue"], row["model_id"]) for row in forecasts)
    guarded = len(forecasts) - len(bad_keys)
    return {
        "guarded_unlock_count": guarded,
        "expected_guarded_unlock_count": 600,
        "unique_unlock_receipt_count": len(receipt_hashes),
        "unlock_receipt_file_count": len(actual_receipt_paths),
        "pre_lock_label_read_count": len(pre_lock_keys),
        "identity_or_hash_mismatch_count": len(bad_keys),
        "ledger_identity_or_sequence_mismatch_count": ledger_identity_mismatch,
        "receipt_inventory_mismatch_count": receipt_inventory_mismatch,
        "forecast_lock_event_count": len(lock_events),
        "label_unlock_event_count": len(unlock_events),
        "coverage": guarded / 600 if forecasts else 0.0,
        "status": "PASS" if guarded == len(forecasts) == len(metrics) == len(receipt_hashes) == len(actual_receipt_paths) == len(lock_events) == len(unlock_events) == 600 and not pre_lock_keys and not bad_keys and ledger_identity_mismatch == receipt_inventory_mismatch == 0 else "FAIL",
    }
