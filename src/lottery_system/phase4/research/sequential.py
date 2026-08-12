from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any

from ..serialization import decimal_string
from .alpha import alpha_spend


class SequentialViolation(ValueError):
    exit_code = 5


def _probability(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise SequentialViolation(f"{label} must be a decimal string")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise SequentialViolation(f"{label} is invalid") from exc
    if not result.is_finite() or result <= 0 or result > 1:
        raise SequentialViolation(f"{label} must be in (0,1]")
    return result


def validate_lr_distribution(p0: Sequence[object], p1: Sequence[object]) -> Decimal:
    if isinstance(p0, (str, bytes)) or isinstance(p1, (str, bytes)) or len(p0) != len(p1) or len(p0) < 2:
        raise SequentialViolation("p0 and p1 distributions must have the same nontrivial support")
    left = [_probability(value, "p0 probability") for value in p0]
    right = [_probability(value, "p1 probability") for value in p1]
    with localcontext() as context:
        context.prec = 80
        if sum(left) != Decimal(1) or sum(right) != Decimal(1):
            raise SequentialViolation("registered distributions must each sum exactly to one")
        expectation = sum((a * (b / a) for a, b in zip(left, right, strict=True)), Decimal(0))
        if expectation != Decimal(1):
            raise SequentialViolation("likelihood-ratio increment does not have M0 conditional mean one")
        return expectation


def _instant(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise SequentialViolation(f"{label} must be RFC3339 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SequentialViolation(f"{label} is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise SequentialViolation(f"{label} must include an offset")
    return parsed.astimezone(timezone.utc)


def reduce_e_process(looks: Sequence[Mapping[str, Any]], *, alpha_ordinal: int, minimum_look: int = 30, maximum_look: int = 150) -> dict[str, Any]:
    if not isinstance(looks, list) or not looks:
        raise SequentialViolation("at least one registered look is required")
    if isinstance(minimum_look, bool) or isinstance(maximum_look, bool) or minimum_look != 30 or maximum_look != 150:
        raise SequentialViolation("look bounds differ from the frozen contract")
    with localcontext() as context:
        context.prec = 80
        alpha = alpha_spend(alpha_ordinal)
        threshold = Decimal(1) / alpha
        e_value = Decimal(1)
        log_e = Decimal(0)
        crossing: int | None = None
        rows: list[dict[str, Any]] = []
        for expected_look, raw in enumerate(looks, start=1):
            required = {"look", "p0", "p1", "outcome_index", "p1_frozen_at_utc", "outcome_observed_at_utc"}
            if set(raw) != required or raw["look"] != expected_look:
                raise SequentialViolation("look sequence is noncanonical")
            p0, p1 = raw["p0"], raw["p1"]
            if not isinstance(p0, list) or not isinstance(p1, list):
                raise SequentialViolation("look distributions must be explicit arrays")
            validate_lr_distribution(p0, p1)
            outcome = raw["outcome_index"]
            if isinstance(outcome, bool) or not isinstance(outcome, int) or outcome < 0 or outcome >= len(p0):
                raise SequentialViolation("outcome index is invalid")
            if _instant(raw["p1_frozen_at_utc"], "p1 frozen time") >= _instant(raw["outcome_observed_at_utc"], "outcome observed time"):
                raise SequentialViolation("p1 must be frozen strictly before its outcome")
            if crossing is not None:
                raise SequentialViolation("look after first stopping boundary is forbidden")
            ratio = _probability(p1[outcome], "p1 outcome probability") / _probability(p0[outcome], "p0 outcome probability")
            e_value *= ratio
            log_e += ratio.ln()
            if expected_look >= minimum_look and e_value >= threshold:
                crossing = expected_look
            rows.append({
                "look": expected_look, "p0": list(p0), "p1": list(p1), "outcome_index": outcome,
                "p1_frozen_at_utc": raw["p1_frozen_at_utc"], "outcome_observed_at_utc": raw["outcome_observed_at_utc"],
                "lr_increment": decimal_string(ratio), "e_value": decimal_string(e_value), "log_e_value": decimal_string(log_e),
                "crossed": crossing == expected_look,
            })
        terminal = "shadow_candidate" if crossing is not None else ("rejected" if len(looks) == maximum_look else "in_progress")
        return {
            "alpha_ordinal": alpha_ordinal, "alpha_spent": decimal_string(alpha), "threshold": decimal_string(threshold),
            "minimum_look": minimum_look, "maximum_look": maximum_look, "looks": rows,
            "first_crossing_look": crossing, "terminal": terminal,
        }


def resume_e_process(checkpoint: Mapping[str, Any], additional_looks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    required = {"alpha_ordinal", "alpha_spent", "threshold", "minimum_look", "maximum_look", "looks", "first_crossing_look", "terminal"}
    if set(checkpoint) != required or checkpoint.get("terminal") != "in_progress":
        raise SequentialViolation("only a complete in-progress checkpoint may be resumed")
    raw_prior = [
        {key: row[key] for key in ("look", "p0", "p1", "outcome_index", "p1_frozen_at_utc", "outcome_observed_at_utc")}
        for row in checkpoint["looks"]
    ]
    recomputed = reduce_e_process(
        raw_prior, alpha_ordinal=checkpoint["alpha_ordinal"],
        minimum_look=checkpoint["minimum_look"], maximum_look=checkpoint["maximum_look"],
    )
    if recomputed != dict(checkpoint):
        raise SequentialViolation("checkpoint is not reducible from its immutable looks")
    return reduce_e_process(
        [*raw_prior, *[dict(row) for row in additional_looks]], alpha_ordinal=checkpoint["alpha_ordinal"],
        minimum_look=checkpoint["minimum_look"], maximum_look=checkpoint["maximum_look"],
    )
