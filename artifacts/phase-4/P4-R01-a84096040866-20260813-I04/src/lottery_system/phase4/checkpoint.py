from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .identity import validate_stable_id
from .serialization import load_json, sha256_file
from .storage import resolve_inside, write_once_json


class CheckpointMismatch(ValueError):
    exit_code = 20


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_checkpoint(
    runtime_root: Path,
    *,
    checkpoint_id: str,
    run_id: str,
    plan_key: Sequence[Any],
    ledger_head_sha256: str,
    input_hashes: Iterable[str],
    output_hashes: Iterable[str],
    stage: str,
    next_ordinal: int,
    rng_counter: int,
    created_at_utc: str | None = None,
) -> Path:
    validate_stable_id(checkpoint_id, "checkpoint identity")
    validate_stable_id(run_id, "run identity")
    if len(plan_key) != 5:
        raise ValueError("checkpoint plan key must contain five fields")
    if next_ordinal < 1 or rng_counter < 0:
        raise ValueError("checkpoint ordinals are invalid")
    payload = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_checkpoint",
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
        "plan_key": list(plan_key),
        "ledger_head_sha256": ledger_head_sha256,
        "input_hashes": sorted(set(input_hashes)),
        "output_hashes": sorted(set(output_hashes)),
        "stage": stage,
        "next_ordinal": next_ordinal,
        "rng_counter": rng_counter,
        "created_at_utc": created_at_utc or _utc_now(),
    }
    path = resolve_inside(runtime_root, f"checkpoints/{checkpoint_id}.json")
    write_once_json(path, payload)
    return path


def load_checkpoint(
    runtime_root: Path,
    checkpoint_id: str,
    *,
    expected_run_id: str | None = None,
    expected_plan_key: Sequence[Any] | None = None,
    expected_ledger_head_sha256: str | None = None,
    verify_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    validate_stable_id(checkpoint_id, "checkpoint identity")
    path = resolve_inside(runtime_root, f"checkpoints/{checkpoint_id}.json")
    value = load_json(path, reject_floats=True)
    required = {
        "schema_version", "artifact_type", "checkpoint_id", "run_id", "plan_key",
        "ledger_head_sha256", "input_hashes", "output_hashes", "stage", "next_ordinal",
        "rng_counter", "created_at_utc",
    }
    if set(value) != required or value["schema_version"] != "1.0.0" or value["artifact_type"] != "phase4_checkpoint":
        raise CheckpointMismatch("checkpoint shape mismatch")
    if value["checkpoint_id"] != checkpoint_id:
        raise CheckpointMismatch("checkpoint identity mismatch")
    if expected_run_id is not None and value["run_id"] != expected_run_id:
        raise CheckpointMismatch("checkpoint run mismatch")
    if expected_plan_key is not None and value["plan_key"] != list(expected_plan_key):
        raise CheckpointMismatch("checkpoint plan mismatch")
    if expected_ledger_head_sha256 is not None and value["ledger_head_sha256"] != expected_ledger_head_sha256:
        raise CheckpointMismatch("checkpoint ledger head mismatch")
    observed = {sha256_file(item) for item in verify_paths}
    registered = set(value["input_hashes"]) | set(value["output_hashes"])
    if not observed <= registered:
        raise CheckpointMismatch("checkpoint bound file hash mismatch")
    return value
