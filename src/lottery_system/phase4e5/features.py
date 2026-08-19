from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .metadata import OPERATIONAL_FIELDS, calendar_fields, distribution_features


BLOCKS = {
    "calendar": [
        "month_sin", "month_cos", "day_of_year_sin", "day_of_year_cos",
        *[f"scheduled_weekday_{value}" for value in range(7)],
    ],
    "holiday": [
        "official_holiday", "days_since_official_holiday_capped_30",
        "days_until_official_holiday_capped_30", "holiday_calendar_available",
    ],
    "draw_gap": ["days_since_prior_draw", "scheduled_gap_days", "gap_anomaly"],
    "regime": ["schedule_regime_v1", "official_source_revision_regime"],
    "lagged_operational": [
        "sales_lag_1", "sales_lag_2", "sales_lag_4_mean",
        "jackpot_lag_1", "jackpot_lag_2", "jackpot_lag_4_mean",
        "first_prize_count_lag_1", "first_prize_count_lag_4_mean",
        "first_prize_amount_lag_1", "first_prize_amount_lag_4_mean",
        "second_prize_count_lag_1", "second_prize_count_lag_4_mean",
        "second_prize_amount_lag_1", "second_prize_amount_lag_4_mean",
    ],
    "provincial_distribution_conditional": [
        "province_first_prize_share_lag_1", "province_first_prize_entropy_lag_1",
        "province_first_prize_coverage_lag_1",
    ],
    "quality": [
        "source_revision_present", "source_revision_changed", "source_switch",
        "metadata_age_draws", "metadata_stale", "block_missing_fraction",
        *[f"{field}_missing" for field in OPERATIONAL_FIELDS],
    ],
}


CANDIDATE_BLOCKS = {
    "B0": [],
    "C1": ["calendar"],
    "C2": ["calendar", "holiday", "draw_gap", "regime"],
    "O1": ["lagged_operational", "quality"],
    "O2": ["calendar", "holiday", "draw_gap", "regime", "lagged_operational", "quality"],
    "O3": ["calendar", "holiday", "draw_gap", "regime", "lagged_operational", "provincial_distribution_conditional", "quality"],
}


def load_draws(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def load_metadata(path: Path) -> dict[str, dict[str, object]]:
    return {row["issue"]: row for row in load_draws(path)}


def _mean(values: Sequence[object]) -> float | None:
    finite = [float(value) for value in values if value is not None]
    return sum(finite) / len(finite) if finite else None


def require_strict_prior(target_issue: str, source_record: dict[str, object]) -> None:
    if int(str(source_record["issue"])) >= int(target_issue):
        raise ValueError(f"current/future metadata rejected: target={target_issue} source={source_record['issue']}")


def build_feature_rows(
    game: str,
    draws: Sequence[dict[str, object]],
    metadata: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    latest_metadata_index: int | None = None
    for index, draw in enumerate(draws):
        issue = str(draw["issue"])
        values = calendar_fields(str(draw["draw_date"]), str(draws[index - 1]["draw_date"]) if index else None, game)
        weekday = int(values.pop("scheduled_weekday"))
        values.update({f"scheduled_weekday_{value}": float(value == weekday) for value in range(7)})
        values["holiday_calendar_available"] = float(bool(values["holiday_calendar_available"]))
        values["schedule_regime_v1"] = 1.0
        prior_records = [metadata.get(str(draws[position]["issue"])) for position in range(max(0, index - 4), index)]
        for record in prior_records:
            if record is not None:
                require_strict_prior(issue, record)
        lag1 = prior_records[-1] if prior_records else None
        lag2 = prior_records[-2] if len(prior_records) >= 2 else None
        for field in ("sales", "jackpot"):
            values[f"{field}_lag_1"] = lag1.get(field) if lag1 else None
            values[f"{field}_lag_2"] = lag2.get(field) if lag2 else None
            values[f"{field}_lag_4_mean"] = _mean([record.get(field) for record in prior_records if record])
        for field in ("first_prize_count", "first_prize_amount", "second_prize_count", "second_prize_amount"):
            values[f"{field}_lag_1"] = lag1.get(field) if lag1 else None
            values[f"{field}_lag_4_mean"] = _mean([record.get(field) for record in prior_records if record])
        share, entropy, province_count = distribution_features(lag1.get("province_first_prize_distribution") if lag1 else None)
        values["province_first_prize_share_lag_1"] = share
        values["province_first_prize_entropy_lag_1"] = entropy
        values["province_first_prize_coverage_lag_1"] = province_count
        lag1_revision = lag1.get("source_revision") if lag1 else None
        lag2_revision = lag2.get("source_revision") if lag2 else None
        lag1_source = lag1.get("source_id") if lag1 else None
        lag2_source = lag2.get("source_id") if lag2 else None
        values["source_revision_present"] = float(lag1_revision is not None)
        values["source_revision_changed"] = float(lag1_revision is not None and lag2_revision is not None and lag1_revision != lag2_revision)
        values["source_switch"] = float(lag1_source is not None and lag2_source is not None and lag1_source != lag2_source)
        values["official_source_revision_regime"] = float(lag1_revision is not None)
        if lag1 is not None:
            latest_metadata_index = index - 1
        age = index - latest_metadata_index if latest_metadata_index is not None else None
        values["metadata_age_draws"] = age
        values["metadata_stale"] = float(age is None or age > 1)
        missing = {field: lag1 is None or lag1.get(field) is None for field in OPERATIONAL_FIELDS}
        values.update({f"{field}_missing": float(flag) for field, flag in missing.items()})
        values["block_missing_fraction"] = sum(missing.values()) / len(missing)
        rows.append({
            "game": game,
            "issue": issue,
            "draw_date": draw["draw_date"],
            "values": values,
            "maximum_metadata_issue": lag1["issue"] if lag1 else None,
            "source_revision": lag1_revision,
            "source_switch": bool(values["source_switch"]),
            "field_missing": missing,
        })
    return rows


def candidate_names(candidate_id: str, provincial_enabled: bool = True) -> list[str]:
    blocks = [block for block in CANDIDATE_BLOCKS[candidate_id] if provincial_enabled or block != "provincial_distribution_conditional"]
    return [name for block in blocks for name in BLOCKS[block]]


def raw_matrix(feature_rows: Sequence[dict[str, object]], names: Sequence[str]) -> np.ndarray:
    return np.asarray([
        [np.nan if row["values"].get(name) is None else float(row["values"][name]) for name in names]
        for row in feature_rows
    ], dtype=np.float64)


@dataclass(frozen=True)
class Preprocessor:
    names: tuple[str, ...]
    transform: str
    winsor_quantiles: tuple[float, float]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    median: tuple[float, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]

    def payload(self) -> dict[str, object]:
        return {
            "names": list(self.names), "transform": self.transform,
            "winsor_quantiles": list(self.winsor_quantiles), "lower": list(self.lower),
            "upper": list(self.upper), "median": list(self.median),
            "center": list(self.center), "scale": list(self.scale),
            "imputation": "prefix_median_with_indicator",
        }


def _nonlinear(matrix: np.ndarray, transform: str) -> np.ndarray:
    if transform == "robust_z":
        return matrix.copy()
    if transform == "log1p_robust_z":
        return np.sign(matrix) * np.log1p(np.abs(matrix))
    raise ValueError(f"unknown transform: {transform}")


def fit_preprocessor(
    matrix: np.ndarray,
    train_indices: Sequence[int],
    names: Sequence[str],
    transform: str,
    winsor_quantiles: tuple[float, float],
) -> Preprocessor:
    if not train_indices:
        raise ValueError("empty preprocessing prefix")
    transformed = _nonlinear(matrix[np.asarray(train_indices)], transform)
    lower, upper, median, center, scale = [], [], [], [], []
    for column in range(transformed.shape[1]):
        finite = transformed[:, column][np.isfinite(transformed[:, column])]
        if not len(finite):
            lo = hi = med = cen = 0.0
            scl = 1.0
        else:
            lo, hi = (float(value) for value in np.quantile(finite, winsor_quantiles))
            clipped = np.clip(finite, lo, hi)
            med = float(np.median(clipped))
            cen = med
            q25, q75 = (float(value) for value in np.quantile(clipped, (0.25, 0.75)))
            scl = max(q75 - q25, 1e-12)
        lower.append(lo); upper.append(hi); median.append(med); center.append(cen); scale.append(scl)
    return Preprocessor(tuple(names), transform, winsor_quantiles, tuple(lower), tuple(upper), tuple(median), tuple(center), tuple(scale))


def apply_preprocessor(matrix: np.ndarray, spec: Preprocessor) -> np.ndarray:
    transformed = _nonlinear(matrix, spec.transform)
    missing = ~np.isfinite(transformed)
    filled = np.where(missing, np.asarray(spec.median), transformed)
    clipped = np.clip(filled, np.asarray(spec.lower), np.asarray(spec.upper))
    normalized = (clipped - np.asarray(spec.center)) / np.asarray(spec.scale)
    return np.column_stack((normalized, missing.astype(np.float64)))


def preprocessor_from_payload(payload: dict[str, object]) -> Preprocessor:
    return Preprocessor(
        tuple(payload["names"]), str(payload["transform"]), tuple(payload["winsor_quantiles"]),
        tuple(payload["lower"]), tuple(payload["upper"]), tuple(payload["median"]),
        tuple(payload["center"]), tuple(payload["scale"]),
    )


def transformed_names(names: Sequence[str]) -> list[str]:
    return list(names) + [f"{name}__imputed" for name in names]
