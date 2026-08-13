from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .identity import content_id, validate_stable_id, verify_content_id


class CheckpointViolation(ValueError):
    exit_code = 5


STAGES = ("leased", "effects_committed", "correction_score_bound", "correction_research_bound", "completed")


def build_checkpoint(
    *, run_id: str, plan_key: Sequence[str], ledger_head_sha256: str,
    input_hashes: Sequence[str], output_hashes: Sequence[str], stage: str,
    next_ordinal: int, rng_counter: int, created_at_utc: str,
) -> dict[str, Any]:
    validate_stable_id(run_id, "run identity")
    if not isinstance(plan_key, (list, tuple)) or len(plan_key) != 5 or any(not isinstance(item, str) or not item for item in plan_key):
        raise CheckpointViolation("checkpoint plan key is invalid")
    if stage not in STAGES:
        raise CheckpointViolation("checkpoint stage is invalid")
    if isinstance(next_ordinal, bool) or not isinstance(next_ordinal, int) or next_ordinal < 1:
        raise CheckpointViolation("checkpoint next ordinal is invalid")
    if isinstance(rng_counter, bool) or not isinstance(rng_counter, int) or rng_counter < 0:
        raise CheckpointViolation("checkpoint RNG counter is invalid")
    hashes = [ledger_head_sha256, *input_hashes, *output_hashes]
    if any(not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value) for value in hashes):
        raise CheckpointViolation("checkpoint contains an invalid SHA-256")
    if len(set(input_hashes)) != len(input_hashes) or len(set(output_hashes)) != len(output_hashes):
        raise CheckpointViolation("checkpoint hashes must be unique")
    body: dict[str, Any] = {
        "schema_version":"1.0.0", "artifact_type":"phase4_checkpoint", "run_id":run_id,
        "plan_key":list(plan_key), "ledger_head_sha256":ledger_head_sha256,
        "input_hashes":list(input_hashes), "output_hashes":list(output_hashes), "stage":stage,
        "next_ordinal":next_ordinal, "rng_counter":rng_counter, "created_at_utc":created_at_utc,
    }
    body["checkpoint_id"] = content_id("checkpoint", body)
    return body


def validate_checkpoint(checkpoint: Mapping[str, Any], *, run_id: str, plan_key: Sequence[str], expected_stage: str | None = None) -> None:
    required = {"schema_version","artifact_type","checkpoint_id","run_id","plan_key","ledger_head_sha256","input_hashes","output_hashes","stage","next_ordinal","rng_counter","created_at_utc"}
    if set(checkpoint) != required or checkpoint.get("schema_version") != "1.0.0" or checkpoint.get("artifact_type") != "phase4_checkpoint":
        raise CheckpointViolation("checkpoint shape is invalid")
    if checkpoint["run_id"] != run_id or checkpoint["plan_key"] != list(plan_key):
        raise CheckpointViolation("checkpoint run or plan identity mismatch")
    if expected_stage is not None and checkpoint["stage"] != expected_stage:
        raise CheckpointViolation("checkpoint stage mismatch")
    verify_content_id(checkpoint["checkpoint_id"], "checkpoint", checkpoint, excluded_fields=("checkpoint_id",))
    build_checkpoint(
        run_id=run_id, plan_key=plan_key, ledger_head_sha256=checkpoint["ledger_head_sha256"],
        input_hashes=checkpoint["input_hashes"], output_hashes=checkpoint["output_hashes"], stage=checkpoint["stage"],
        next_ordinal=checkpoint["next_ordinal"], rng_counter=checkpoint["rng_counter"], created_at_utc=checkpoint["created_at_utc"],
    )
