from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Sequence

from oracle_math import (
    DECIMAL_PRECISION,
    SCALE,
    combinations_with_scores,
    decimal_string,
    joint_histogram,
    partition_direct,
    rank_bounds,
    zone_histogram_direct,
    zone_inclusion_probabilities,
)


WILSON_Z_95 = Decimal("1.95996398454005423552")


def _ticket_probability(
    front: Sequence[int],
    back: Sequence[int],
    front_ticks: Sequence[int],
    back_ticks: Sequence[int],
    front_z: Decimal,
    back_z: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        score = sum(front_ticks[index - 1] for index in front) + sum(back_ticks[index - 1] for index in back)
        return +((Decimal(score) / Decimal(SCALE)).exp() / (front_z * back_z))


def _wilson(successes: int, count: int) -> tuple[Decimal, Decimal]:
    if count <= 0:
        raise ValueError("Wilson interval requires observations")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        n = Decimal(count)
        p = Decimal(successes) / n
        z2 = WILSON_Z_95 * WILSON_Z_95
        denominator = Decimal(1) + z2 / n
        center = (p + z2 / (Decimal(2) * n)) / denominator
        radius = WILSON_Z_95 * ((p * (Decimal(1) - p) / n + z2 / (Decimal(4) * n * n)).sqrt()) / denominator
        return +(center - radius), +(center + radius)


def _build_metric_vectors_decimal80() -> dict[str, Any]:
    front_n, front_k = 5, 2
    back_n, back_k = 4, 1
    front_ticks = (0, 1024, 0, -1024, 512)
    back_ticks = (0, -512, 512, 0)
    front_rows = combinations_with_scores(front_n, front_k, front_ticks)
    back_rows = combinations_with_scores(back_n, back_k, back_ticks)
    front_z = partition_direct(front_rows)
    back_z = partition_direct(back_rows)
    front_hist = zone_histogram_direct(front_n, front_k, front_ticks)
    back_hist = zone_histogram_direct(back_n, back_k, back_ticks)
    histogram = joint_histogram(front_hist, back_hist)
    ordered = []
    for front_score, front in front_rows:
        for back_score, back in back_rows:
            ordered.append((front_score + back_score, front, back))
    ordered.sort(key=lambda row: (-row[0], row[1] + row[2]))

    front_inclusion = zone_inclusion_probabilities(front_n, front_k, front_ticks)
    back_inclusion = zone_inclusion_probabilities(back_n, back_k, back_ticks)
    labels = [(front, back) for _, front, back in ordered[:30]]
    per_forecast = []
    for ordinal, (front, back) in enumerate(labels, start=1):
        score = sum(front_ticks[index - 1] for index in front) + sum(back_ticks[index - 1] for index in back)
        probability = _ticket_probability(front, back, front_ticks, back_ticks, front_z, back_z)
        m0 = Decimal(1) / Decimal(len(ordered))
        lower, upper, midrank = rank_bounds(histogram, score)
        brier_terms = []
        for number, predicted in enumerate(front_inclusion, start=1):
            observed = Decimal(1 if number in front else 0)
            brier_terms.append((predicted - observed) ** 2)
        for number, predicted in enumerate(back_inclusion, start=1):
            observed = Decimal(1 if number in back else 0)
            brier_terms.append((predicted - observed) ** 2)
        position = next(index for index, row in enumerate(ordered, start=1) if row[1] == front and row[2] == back)
        per_forecast.append(
            {
                "ordinal": ordinal,
                "front": list(front),
                "back": list(back),
                "display_position": position,
                "hit_at_10": int(position <= 10),
                "hit_at_100": 1,
                "hit_at_200": 1,
                "hit_at_1000": 1,
                "joint_probability": decimal_string(probability),
                "joint_log_score": decimal_string(-probability.ln()),
                "skill_vs_m0": decimal_string(probability.ln() - m0.ln()),
                "inclusion_brier": decimal_string(sum(brier_terms) / Decimal(front_n + back_n)),
                "tie_rank_lower": lower,
                "tie_rank_upper": upper,
                "tie_midrank": decimal_string(midrank),
                "midrank_percentile": decimal_string((midrank - Decimal("0.5")) / Decimal(len(ordered))),
            }
        )

    count = len(per_forecast)
    mean_fields = ("joint_log_score", "skill_vs_m0", "inclusion_brier", "midrank_percentile")
    means = {
        field: decimal_string(sum(Decimal(row[field]) for row in per_forecast) / Decimal(count))
        for field in mean_fields
    }
    hit_windows: dict[str, Any] = {}
    for k in (10, 100, 200, 1000):
        successes = sum(row[f"hit_at_{k}"] for row in per_forecast)
        low, high = _wilson(successes, count)
        hit_windows[str(k)] = {
            "successes": successes,
            "observations": count,
            "rate": decimal_string(Decimal(successes) / Decimal(count)),
            "wilson_95": [decimal_string(low), decimal_string(high)],
        }

    atoms: list[tuple[Decimal, int]] = []
    for front, back in labels:
        atoms.extend((value, int(number in front)) for number, value in enumerate(front_inclusion, start=1))
        atoms.extend((value, int(number in back)) for number, value in enumerate(back_inclusion, start=1))
    bins = []
    ece = Decimal(0)
    for bin_index in range(10):
        selected = []
        for predicted, observed in atoms:
            index = min(9, int(predicted * Decimal(10)))
            if index == bin_index:
                selected.append((predicted, observed))
        if selected:
            mean_probability = sum(value for value, _ in selected) / Decimal(len(selected))
            observed_rate = Decimal(sum(observed for _, observed in selected)) / Decimal(len(selected))
            ece += Decimal(len(selected)) / Decimal(len(atoms)) * abs(mean_probability - observed_rate)
            bins.append(
                {
                    "bin": bin_index,
                    "count": len(selected),
                    "mean_probability": decimal_string(mean_probability),
                    "observed_rate": decimal_string(observed_rate),
                }
            )
        else:
            bins.append({"bin": bin_index, "count": 0, "mean_probability": None, "observed_rate": None})

    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_metric_known_answers",
        "decimal_precision": DECIMAL_PRECISION,
        "fixture": {
            "front": {"N": front_n, "k": front_k, "ticks": list(front_ticks)},
            "back": {"N": back_n, "k": back_k, "ticks": list(back_ticks)},
            "space_size": len(ordered),
            "result_sequence_definition": "first_30_probability_ordered_full_tickets",
        },
        "inclusion_probabilities": {
            "front": [decimal_string(value) for value in front_inclusion],
            "back": [decimal_string(value) for value in back_inclusion],
        },
        "per_forecast": per_forecast,
        "window_30": {
            "observation_count": count,
            "means": means,
            "cumulative_hit_rate": hit_windows,
            "reliability_bins": bins,
            "ece": decimal_string(ece),
            "stability": "0",
        },
        "insufficient_observation_vector": {
            "observation_count": 29,
            "status": "insufficient_observation",
            "numeric_metrics_present": False,
        },
    }


def build_metric_vectors() -> dict[str, Any]:
    """Build every probability, metric, logarithm and serialization under Decimal80."""
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return _build_metric_vectors_decimal80()
