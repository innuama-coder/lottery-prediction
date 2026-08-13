from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, localcontext
from typing import Any

from ..identity import content_id, validate_stable_id
from ..serialization import decimal_string
from .registry import FAMILIES


class AlphaViolation(ValueError):
    exit_code = 5


W0 = Decimal("0.006")


def alpha_spend(ordinal: object) -> Decimal:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise AlphaViolation("alpha ordinal must be a positive non-boolean integer")
    with localcontext() as context:
        context.prec = 80
        return W0 / (Decimal(ordinal) * Decimal(ordinal + 1))


def make_spend_event(*, game: str, hypothesis_family: str, experiment_id: str, ordinal: int, event_at_utc: str) -> dict[str, Any]:
    if game not in {"ssq", "dlt"} or hypothesis_family not in FAMILIES:
        raise AlphaViolation("alpha game or family is invalid")
    validate_stable_id(experiment_id, "experiment identity")
    spend = alpha_spend(ordinal)
    body: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact_type": "phase4_alpha_event", "game": game,
        "hypothesis_family": hypothesis_family, "experiment_id": experiment_id,
        "alpha_ordinal": ordinal, "alpha_spent": decimal_string(spend), "reward": "0",
        "event_type": "spend", "event_at_utc": event_at_utc,
    }
    body["alpha_event_id"] = content_id("alpha-event", body)
    return body


def reduce_alpha_events(game: str, hypothesis_family: str, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if game not in {"ssq", "dlt"} or hypothesis_family not in FAMILIES:
        raise AlphaViolation("alpha game or family is invalid")
    seen_ids: set[str] = set()
    seen_experiments: set[str] = set()
    spent = Decimal(0)
    for expected_ordinal, event in enumerate(events, start=1):
        required = {"schema_version", "artifact_type", "alpha_event_id", "game", "hypothesis_family", "experiment_id", "alpha_ordinal", "alpha_spent", "reward", "event_type", "event_at_utc"}
        if set(event) != required or event.get("artifact_type") != "phase4_alpha_event" or event.get("schema_version") != "1.0.0":
            raise AlphaViolation("alpha event shape is invalid")
        if event["game"] != game or event["hypothesis_family"] != hypothesis_family:
            raise AlphaViolation("cross-game or cross-family alpha event")
        if event["event_type"] != "spend" or event["reward"] != "0":
            raise AlphaViolation("alpha refund, reset, or reward is forbidden")
        if event["alpha_ordinal"] != expected_ordinal or event["alpha_spent"] != decimal_string(alpha_spend(expected_ordinal)):
            raise AlphaViolation("alpha spend does not match the frozen ordinal formula")
        if event["alpha_event_id"] in seen_ids or event["experiment_id"] in seen_experiments:
            raise AlphaViolation("duplicate alpha event or experiment spend")
        expected_id = content_id("alpha-event", event, excluded_fields=("alpha_event_id",))
        if event["alpha_event_id"] != expected_id:
            raise AlphaViolation("alpha event identity mismatch")
        seen_ids.add(event["alpha_event_id"])
        seen_experiments.add(event["experiment_id"])
        spent += alpha_spend(expected_ordinal)
    wealth = W0 - spent
    if wealth < 0:
        raise AlphaViolation("alpha wealth is negative")
    return {
        "schema_version": "1.0.0", "artifact_type": "phase4_alpha_wealth", "game": game,
        "hypothesis_family": hypothesis_family, "initial_wealth": "0.006",
        "spent": decimal_string(spent), "rewarded": "0", "current_wealth": decimal_string(wealth),
        "events": [event["alpha_event_id"] for event in events],
    }


def total_spend_by_game(wealth_rows: Sequence[Mapping[str, Any]], game: str) -> Decimal:
    matching = [row for row in wealth_rows if row.get("game") == game]
    if {row.get("hypothesis_family") for row in matching} != set(FAMILIES):
        raise AlphaViolation("all three game/family wealth rows are required")
    total = sum((Decimal(str(row["spent"])) for row in matching), Decimal(0))
    if total > Decimal("0.018"):
        raise AlphaViolation("cross-family total alpha spend exceeds 0.018")
    return total


def validate_alpha_wealth(wealth: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> None:
    expected = reduce_alpha_events(wealth.get("game"), wealth.get("hypothesis_family"), events)
    if dict(wealth) != expected:
        raise AlphaViolation("alpha wealth is negative, stale, or not reducible from its events")
