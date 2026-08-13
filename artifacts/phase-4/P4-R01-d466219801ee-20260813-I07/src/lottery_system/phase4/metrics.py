from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Mapping, Sequence

from .identity import content_id, validate_stable_id
from .probability import DECIMAL_PRECISION, SCALE, ZoneDistribution
from .ranking import _joint_top, rank_bands, top_zone_combinations
from .serialization import decimal_string


class MetricViolation(ValueError):
    exit_code = 6
    terminal = "FAIL_METRIC_ORACLE_MISMATCH"


METRIC_CONTRACT_ID = "phase4-metric-v1"
TOP_K = (10, 100, 200, 1000)


def _decimal(value: Decimal, label: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise MetricViolation(f"{label} must be a finite Decimal")
    return value


def inclusion_probabilities(zone: ZoneDistribution) -> tuple[Decimal, ...]:
    """Return exact fixed-cardinality marginal inclusion probabilities."""
    if not isinstance(zone, ZoneDistribution) or zone.k <= 0 or zone.k > len(zone.weights):
        raise MetricViolation("inclusion probability zone is invalid")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        result: list[Decimal] = []
        for excluded, weight in enumerate(zone.weights):
            states = [Decimal(1)] + [Decimal(0)] * (zone.k - 1)
            seen = 0
            for index, other in enumerate(zone.weights):
                if index == excluded:
                    continue
                seen += 1
                for chosen in range(min(seen, zone.k - 1), 0, -1):
                    states[chosen] += other * states[chosen - 1]
            result.append(+(weight * states[zone.k - 1] / zone.partition))
        if any(not value.is_finite() or value < 0 or value > 1 for value in result):
            raise MetricViolation("inclusion probability is outside [0,1]")
        residual = Decimal(zone.k) - sum(result, Decimal(0))
        if residual:
            if abs(residual) > Decimal("1e-70"):
                raise MetricViolation("fixed-cardinality inclusion probabilities do not sum to k")
            # Decimal80 division can leave a final-unit residual (for example
            # 33 equal marginals summing to 6). Close it deterministically at
            # the lowest-numbered maximum marginal so the persisted vector
            # has the exact fixed-cardinality invariant.
            closure_index = max(range(len(result)), key=lambda index: (result[index], -index))
            result[closure_index] += residual
        if sum(result, Decimal(0)) != Decimal(zone.k) or any(value < 0 or value > 1 for value in result):
            raise MetricViolation("fixed-cardinality inclusion probability closure failed")
        return tuple(result)


def derive_score_id(forecast_id: str, result_revision_id: str, metric_contract_id: str) -> str:
    key = {
        "forecast_id": validate_stable_id(forecast_id, "forecast identity"),
        "result_revision_id": validate_stable_id(result_revision_id, "result revision identity"),
        "metric_contract_id": validate_stable_id(metric_contract_id, "metric contract identity"),
    }
    return content_id("score", key)


def _canonical_result(numbers: Sequence[object], *, n: int, k: int, label: str) -> tuple[int, ...]:
    if type(numbers) not in {list, tuple} or len(numbers) != k or any(type(item) is not int for item in numbers):
        raise MetricViolation(f"{label} result has the wrong shape")
    canonical = tuple(numbers)
    if canonical != tuple(sorted(canonical)) or len(set(canonical)) != k or any(item < 1 or item > n for item in canonical):
        raise MetricViolation(f"{label} result is not a legal canonical ticket")
    return canonical


def score_zones(
    *,
    model_front: ZoneDistribution,
    model_back: ZoneDistribution,
    champion_front: ZoneDistribution,
    champion_back: ZoneDistribution,
    result_front: Sequence[object],
    result_back: Sequence[object],
    histogram: Mapping[int, int],
    ordered_tickets: Sequence[Mapping[str, Any]],
    forecast_id: str,
    result_revision_id: str,
    metric_contract_id: str,
    comparator_forecast_id: str,
    context: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compute a score and the immutable vectors required by window aggregation."""
    if metric_contract_id != METRIC_CONTRACT_ID:
        raise MetricViolation("unregistered metric contract")
    for zone, champion, label in (
        (model_front, champion_front, "front"), (model_back, champion_back, "back")
    ):
        if not isinstance(zone, ZoneDistribution) or not isinstance(champion, ZoneDistribution):
            raise MetricViolation(f"{label} score distribution is invalid")
        if len(zone.ticks) != len(champion.ticks) or zone.k != champion.k:
            raise MetricViolation("Champion comparator rule differs from the scored forecast")
        for candidate in (zone, champion):
            if not candidate.partition.is_finite() or candidate.partition <= 0 or any(not weight.is_finite() or weight <= 0 for weight in candidate.weights):
                raise MetricViolation("zero or non-finite score probability is forbidden")
        if any(tick != 0 for tick in champion.ticks):
            raise MetricViolation("Champion comparator is not the permanent M0 distribution")
    front = _canonical_result(result_front, n=len(model_front.ticks), k=model_front.k, label="front")
    back = _canonical_result(result_back, n=len(model_back.ticks), k=model_back.k, label="back")
    if not histogram or any(type(key) is not int or type(count) is not int or count <= 0 for key, count in histogram.items()):
        raise MetricViolation("rank histogram is invalid")
    total_space = sum(histogram.values())
    expected_space = __import__("math").comb(len(model_front.ticks), model_front.k) * __import__("math").comb(len(model_back.ticks), model_back.k)
    if total_space != expected_space:
        raise MetricViolation("rank histogram does not cover the legal space")
    model_score = model_front.score(front) + model_back.score(back)
    champion_score = champion_front.score(front) + champion_back.score(back)
    if model_score not in histogram:
        raise MetricViolation("observed ticket score is absent from the full histogram")
    with localcontext() as decimal_context:
        decimal_context.prec = DECIMAL_PRECISION
        model_probability = +((Decimal(model_score) / SCALE).exp() / (model_front.partition * model_back.partition))
        champion_probability = +((Decimal(champion_score) / SCALE).exp() / (champion_front.partition * champion_back.partition))
        if model_probability <= 0 or champion_probability <= 0:
            raise MetricViolation("zero score probability is forbidden")
        joint_log_score = +(-model_probability.ln())
        skill = +(model_probability.ln() - champion_probability.ln())
        front_inclusion = inclusion_probabilities(model_front)
        back_inclusion = inclusion_probabilities(model_back)
        terms = [
            (probability - Decimal(number in front)) ** 2
            for number, probability in enumerate(front_inclusion, start=1)
        ] + [
            (probability - Decimal(number in back)) ** 2
            for number, probability in enumerate(back_inclusion, start=1)
        ]
        brier = +(sum(terms, Decimal(0)) / Decimal(len(terms)))
        lower, upper, midrank_text = rank_bands(histogram)[model_score]
        midrank = Decimal(midrank_text)
        percentile = +((midrank - Decimal("0.5")) / Decimal(total_space))
    if not ordered_tickets or len(ordered_tickets) > total_space:
        raise MetricViolation("locked ticket prefix length is invalid")
    expected_prefix = _joint_top(
        top_zone_combinations(model_front, len(ordered_tickets)),
        top_zone_combinations(model_back, len(ordered_tickets)),
        len(ordered_tickets),
    )
    found_positions: list[int] = []
    previous = 0
    seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    for row in ordered_tickets:
        if not isinstance(row, Mapping) or type(row.get("front")) is not list or type(row.get("back")) is not list or type(row.get("display_position")) is not int:
            raise MetricViolation("locked ticket prefix has an invalid row")
        position = row["display_position"]
        if position != previous + 1:
            raise MetricViolation("locked ticket prefix positions are not contiguous")
        previous = position
        ticket = (
            _canonical_result(row["front"], n=len(model_front.ticks), k=model_front.k, label="locked front"),
            _canonical_result(row["back"], n=len(model_back.ticks), k=model_back.k, label="locked back"),
        )
        if ticket in seen:
            raise MetricViolation("locked ticket prefix contains a duplicate")
        seen.add(ticket)
        expected_score, expected_front, expected_back = expected_prefix[position - 1]
        if ticket != (expected_front, expected_back) or model_front.score(ticket[0]) + model_back.score(ticket[1]) != expected_score:
            raise MetricViolation("locked ticket prefix is not the deterministic probability prefix")
        if ticket == (front, back):
            found_positions.append(position)
    if len(found_positions) > 1:
        raise MetricViolation("observed ticket appears more than once")
    display_position = found_positions[0] if found_positions else None
    score_id = derive_score_id(forecast_id, result_revision_id, metric_contract_id)
    score = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_score",
        "score_id": score_id,
        "forecast_id": validate_stable_id(forecast_id, "forecast identity"),
        "result_revision_id": validate_stable_id(result_revision_id, "result revision identity"),
        "metric_contract_id": metric_contract_id,
        "comparator_forecast_id": validate_stable_id(comparator_forecast_id, "comparator forecast identity"),
        "hit_at_k": {str(k): int(display_position is not None and display_position <= k) for k in TOP_K},
        "joint_log_score": decimal_string(joint_log_score),
        "skill_vs_champion": decimal_string(skill),
        "inclusion_brier": decimal_string(brier),
        "tie_rank_lower": lower,
        "tie_rank_upper": upper,
        "tie_midrank": decimal_string(midrank),
        "midrank_percentile": decimal_string(percentile),
    }
    required_context = {"game", "issue_id", "model_id", "model_release_id", "config_id", "comparator_champion_id"}
    supplied_context = dict(context or {})
    if set(supplied_context) != required_context or any(type(value) is not str or not value for value in supplied_context.values()):
        raise MetricViolation("score window context is incomplete")
    detail = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_score_window_detail",
        "score_id": score_id,
        **supplied_context,
        "front_inclusion": [decimal_string(value) for value in front_inclusion],
        "back_inclusion": [decimal_string(value) for value in back_inclusion],
        "observed_front": list(front),
        "observed_back": list(back),
        "recomputation": {
            "model_front": {"ticks": list(model_front.ticks), "k": model_front.k},
            "model_back": {"ticks": list(model_back.ticks), "k": model_back.k},
            "champion_front": {"ticks": list(champion_front.ticks), "k": champion_front.k},
            "champion_back": {"ticks": list(champion_back.ticks), "k": champion_back.k},
            "result": {"front": list(front), "back": list(back)},
            "ordered_tickets": [
                {"front": list(row["front"]), "back": list(row["back"]), "display_position": row["display_position"]}
                for row in ordered_tickets
            ],
            "forecast_id": forecast_id,
            "result_revision_id": result_revision_id,
            "metric_contract_id": metric_contract_id,
            "comparator_forecast_id": comparator_forecast_id,
        },
    }
    return {"score": score, "detail": detail}
