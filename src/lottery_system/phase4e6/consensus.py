from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from statistics import fmean, pstdev
from typing import Iterable, Mapping, Sequence


IDENTITY_FIELDS = ("issue", "draw_date", "front", "back")
OPERATIONAL_FIELDS = (
    "sales",
    "jackpot",
    "first_prize_count",
    "first_prize_amount",
    "second_prize_count",
    "second_prize_amount",
)
TOLERANCES = {
    "sales": 1.0,
    "jackpot": 1.0,
    "first_prize_count": 0.0,
    "first_prize_amount": 1.0,
    "second_prize_count": 0.0,
    "second_prize_amount": 1.0,
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _integer(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer field")
    parsed = int(str(value).replace(",", ""))
    return parsed


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(str(value).replace(",", ""))
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("operational number must be finite and nonnegative")
    return parsed


def _balls(value: object, expected: int, maximum: int) -> list[int]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("balls must be a list")
    parsed = [_integer(item) for item in value]
    if any(item is None for item in parsed):
        raise ValueError("ball cannot be missing")
    result = [int(item) for item in parsed]
    if len(result) != expected or result != sorted(result) or len(set(result)) != expected:
        raise ValueError("balls must be sorted and unique with the game-specific length")
    if result[0] < 1 or result[-1] > maximum:
        raise ValueError("ball outside game range")
    return result


def normalize_observation(observation: Mapping[str, object]) -> dict[str, object]:
    game = str(observation["game"]).lower()
    if game not in ("ssq", "dlt"):
        raise ValueError(f"unsupported game: {game}")
    issue = str(observation["issue"])
    if len(issue) != 7 or not issue.isdigit():
        raise ValueError(f"invalid issue: {issue}")
    draw_date = date.fromisoformat(str(observation["draw_date"])).isoformat()
    front = _balls(observation["front"], 6 if game == "ssq" else 5, 33 if game == "ssq" else 35)
    back = _balls(observation["back"], 1 if game == "ssq" else 2, 16 if game == "ssq" else 12)
    result: dict[str, object] = {
        "game": game,
        "issue": issue,
        "draw_date": draw_date,
        "front": front,
        "back": back,
        "source_id": str(observation["source_id"]),
        "capture_group": str(observation["capture_group"]),
        "accessible": bool(observation.get("accessible", True)),
        "lineage": str(observation.get("lineage", "unknown")),
        "suspected_common_upstream": observation.get("suspected_common_upstream"),
        "raw_receipt": str(observation["raw_receipt"]),
        "raw_body_sha256": str(observation["raw_body_sha256"]),
    }
    for field in OPERATIONAL_FIELDS:
        result[field] = _integer(observation.get(field)) if field.endswith("_count") else _number(observation.get(field))
    result["regional_distribution"] = observation.get("regional_distribution")
    return result


def _agree(field: str, values: Sequence[object]) -> bool:
    if field in IDENTITY_FIELDS or field.endswith("_count"):
        return all(value == values[0] for value in values[1:])
    numeric = [float(value) for value in values]
    return max(numeric) - min(numeric) <= TOLERANCES[field]


def consensus_issue(observations: Iterable[Mapping[str, object]]) -> dict[str, object]:
    normalized = [normalize_observation(item) for item in observations if bool(item.get("accessible", True))]
    if not normalized:
        raise ValueError("at least one accessible observation is required")
    games = {str(item["game"]) for item in normalized}
    issues = {str(item["issue"]) for item in normalized}
    if len(games) != 1 or len(issues) != 1:
        raise ValueError("consensus batch must contain one game and issue")
    source_count = len({str(item["source_id"]) for item in normalized})
    independent_count = len({str(item["capture_group"]) for item in normalized})
    fields: dict[str, object] = {}
    conflict_fields: list[str] = []
    missing_fields: list[str] = []
    for field in (*IDENTITY_FIELDS, *OPERATIONAL_FIELDS):
        by_group: dict[str, object] = {}
        for item in normalized:
            value = item.get(field)
            if value is not None:
                by_group.setdefault(str(item["capture_group"]), value)
        values = list(by_group.values())
        if len(values) < 2:
            fields[field] = None
            missing_fields.append(field)
        elif not _agree(field, values):
            fields[field] = None
            conflict_fields.append(field)
        elif field in OPERATIONAL_FIELDS and not field.endswith("_count"):
            fields[field] = fmean(float(value) for value in values)
        else:
            fields[field] = values[0]
    identity_ok = all(fields[field] is not None for field in IDENTITY_FIELDS)
    operational_ok = all(fields[field] is not None for field in OPERATIONAL_FIELDS)
    quarantined = bool(conflict_fields or not identity_ok or not operational_ok or independent_count < 2)
    result = {
        "schema_version": "phase4e6.consensus-row.v1",
        "game": next(iter(games)),
        "issue": next(iter(issues)),
        **fields,
        "source_count": source_count,
        "independent_source_count": independent_count,
        "conflict": bool(conflict_fields),
        "conflict_fields": sorted(conflict_fields),
        "missing_fields": sorted(missing_fields),
        "identity_consensus": identity_ok,
        "operational_consensus": operational_ok,
        "quarantined": quarantined,
        "status": "QUARANTINED" if quarantined else "ACCEPTED",
        "provenance": [
            {
                key: item[key]
                for key in ("source_id", "capture_group", "lineage", "suspected_common_upstream", "raw_receipt", "raw_body_sha256")
            }
            for item in sorted(normalized, key=lambda row: (str(row["capture_group"]), str(row["source_id"])))
        ],
    }
    result["row_sha256"] = sha256(canonical(result))
    return result


def require_strict_prior(current_index: int, metadata_index: int) -> None:
    if metadata_index >= current_index:
        raise ValueError(f"current/future metadata rejected: metadata_index={metadata_index} current_index={current_index}")


def _lag(values: list[float | None], position: int, lag: int) -> float | None:
    index = position - lag
    return values[index] if index >= 0 else None


def _window(values: list[float | None], position: int, width: int) -> list[float]:
    return [float(value) for value in values[max(0, position - width) : position] if value is not None]


def build_lagged_feature_rows(
    draws: Sequence[Mapping[str, object]],
    consensus: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    issue_to_index = {str(draw["issue"]): index for index, draw in enumerate(draws)}
    series: dict[str, list[float | None]] = {field: [None] * len(draws) for field in OPERATIONAL_FIELDS}
    eligible: list[bool] = [False] * len(draws)
    for issue, row in consensus.items():
        if issue not in issue_to_index:
            continue
        index = issue_to_index[issue]
        if not bool(row.get("quarantined", True)):
            eligible[index] = True
            for field in OPERATIONAL_FIELDS:
                value = row.get(field)
                series[field][index] = None if value is None else float(value)
    result: list[dict[str, object]] = []
    last_eligible: int | None = None
    for position, draw in enumerate(draws):
        if position > 0 and eligible[position - 1]:
            last_eligible = position - 1
        prior_consensus = consensus.get(str(draws[position - 1]["issue"])) if position else None
        values: dict[str, float | int | None] = {}
        for field in OPERATIONAL_FIELDS:
            for lag in (1, 2, 4):
                values[f"{field}_lag_{lag}"] = _lag(series[field], position, lag)
            lag1, lag2 = _lag(series[field], position, 1), _lag(series[field], position, 2)
            values[f"{field}_change_lag_1"] = None if lag1 is None or lag2 is None else lag1 - lag2
            lag3 = _lag(series[field], position, 3)
            values[f"{field}_change_lag_2"] = None if lag2 is None or lag3 is None else lag2 - lag3
            for width in (4, 8):
                window = _window(series[field], position, width)
                values[f"{field}_volatility_{width}"] = pstdev(window) if len(window) >= 2 else None
        jackpot1, jackpot2 = values["jackpot_lag_1"], values["jackpot_lag_2"]
        values["rollover"] = None if jackpot1 is None or jackpot2 is None else int(float(jackpot1) > float(jackpot2))
        maximum_metadata_issue = str(draws[last_eligible]["issue"]) if last_eligible is not None else None
        if last_eligible is not None:
            require_strict_prior(position, last_eligible)
        row = {
            "game": str(draw["game"]),
            "prediction_issue": str(draw["issue"]),
            "maximum_metadata_issue": maximum_metadata_issue,
            "staleness_draws": None if last_eligible is None else position - last_eligible,
            "source_count_lag_1": None if prior_consensus is None else int(prior_consensus.get("source_count", 0)),
            "independent_source_count_lag_1": None if prior_consensus is None else int(prior_consensus.get("independent_source_count", 0)),
            "conflict_lag_1": None if prior_consensus is None else int(bool(prior_consensus.get("conflict"))),
            "quarantined_lag_1": None if prior_consensus is None else int(bool(prior_consensus.get("quarantined", True))),
            "missing_fraction_lag_1": None if prior_consensus is None else len(prior_consensus.get("missing_fields", [])) / len((*IDENTITY_FIELDS, *OPERATIONAL_FIELDS)),
            "values": values,
        }
        result.append(row)
    return result


def normalize_probabilities(values: Sequence[float]) -> list[float]:
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("probabilities must be finite, nonnegative, and nonempty")
    total = math.fsum(values)
    if total <= 0:
        raise ValueError("probability mass must be positive")
    return [value / total for value in values]
