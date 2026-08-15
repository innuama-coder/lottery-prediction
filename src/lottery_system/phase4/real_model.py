from __future__ import annotations

import hashlib
import itertools
import json
import math
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Iterable, Sequence

from .real_common import Draw, RULES, canonical, digest

THETA_GRID = (-2.0, -1.0, -0.5, 0.5, 1.0, 2.0)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != canonical(value):
            raise FileExistsError(f"immutable identity collision: {path}")
        return
    path.write_bytes(canonical(value))


def write_jsonl_once(path: Path, rows: Iterable[object]) -> None:
    encoded = b"".join(canonical(row) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable identity collision: {path}")
        return
    path.write_bytes(encoded)


def load_draws(path: Path, game: str) -> list[Draw]:
    if game not in RULES:
        raise ValueError("unknown game")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if raw["game"] != game:
            continue
        if any(raw.get(key) is not None for key in raw if key.startswith("available_at")):
            raise ValueError("Phase 1 retrospective history must not claim fabricated available_at")
        rows.append(Draw(raw["issue_id"], tuple(raw["front_numbers"]), tuple(raw["back_numbers"]), raw["core_fact_sha256"]))
    if len(rows) < 120 or len({row.issue for row in rows}) != len(rows):
        raise ValueError("insufficient or duplicate canonical history")
    # Phase 1 serialization is the frozen canonical comparator order. Date/issue values are never re-sorted.
    return rows


def feature_values(draws: Sequence[Draw], zone: int, n: int, k: int) -> tuple[list[float], list[int]]:
    counts = [0] * n
    for draw in draws:
        for number in draw.front if zone == 0 else draw.back:
            counts[number - 1] += 1
    alpha, beta = 1.0, max(1.0, n / k - 1.0)
    rates = [(count + alpha) / (len(draws) + alpha + beta) for count in counts]
    mean = sum(rates) / n
    scale = max(max(abs(value - mean) for value in rates), 1e-12)
    return [(value - mean) / scale for value in rates], counts


def elementary(weights: Sequence[float], k: int) -> float:
    dp = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1):
            dp[order] += weight * dp[order - 1]
    return dp[k]


def zone_model(draws: Sequence[Draw], zone: int, theta: float) -> dict[str, object]:
    n, k = RULES["ssq"][zone]  # overwritten by caller for DLT through zone_model_for_game
    return {"n": n, "k": k, "theta": theta}


def zone_parameters(game: str, draws: Sequence[Draw], zone: int, theta: float) -> dict[str, object]:
    n, k = RULES[game][zone]
    values, counts = feature_values(draws, zone, n, k)
    weights = [math.exp(max(-8.0, min(8.0, theta * value))) for value in values]
    return {"n": n, "k": k, "theta": theta, "feature": values, "counts": counts, "weights": weights, "normalizer": elementary(weights, k)}


def subset_probability(numbers: Sequence[int], zone: dict[str, object]) -> float:
    weights = zone["weights"]
    product = math.prod(weights[number - 1] for number in numbers)  # type: ignore[index]
    return product / float(zone["normalizer"])


def capped_subset_rank(numbers: Sequence[int], zone: dict[str, object], cap: int = 1000) -> int:
    """Return the exact rank through *cap*, or cap+1 once Top-K is impossible."""
    observed = tuple(numbers)
    observed_probability = subset_probability(observed, zone)
    outranking = 0
    for combo in itertools.combinations(range(1, int(zone["n"]) + 1), int(zone["k"])):
        probability = subset_probability(combo, zone)
        if probability > observed_probability or (probability == observed_probability and combo < observed):
            outranking += 1
            if outranking >= cap:
                return cap + 1
    return outranking + 1


def select_theta(game: str, draws: Sequence[Draw], zone_index: int, indices: Sequence[int]) -> tuple[float, list[dict[str, object]]]:
    metrics = []
    n, k = RULES[game][zone_index]
    for theta in THETA_GRID:
        losses = []
        for index in indices:
            prefix = draws[:index]
            values, _ = feature_values(prefix, zone_index, n, k)
            weights = [math.exp(max(-8.0, min(8.0, theta * value))) for value in values]
            normalizer = elementary(weights, k)
            observed = draws[index].front if zone_index == 0 else draws[index].back
            probability = math.prod(weights[number - 1] for number in observed) / normalizer
            losses.append(-math.log(probability))
        metrics.append({"theta": theta, "mean_log_loss": sum(losses) / len(losses), "fold_count": len(losses)})
    selected = min(metrics, key=lambda row: (row["mean_log_loss"], abs(row["theta"]), row["theta"]))
    return float(selected["theta"]), metrics


def train(game: str, draws: Sequence[Draw], cutoff_index: int | None = None) -> dict[str, object]:
    cutoff_index = len(draws) if cutoff_index is None else cutoff_index
    if cutoff_index < 120 or cutoff_index > len(draws):
        raise ValueError("illegal training cutoff")
    training = draws[:cutoff_index]
    selection_indices = list(range(cutoff_index - 80, cutoff_index - 40))
    report_indices = list(range(cutoff_index - 40, cutoff_index))
    if set(selection_indices) & set(report_indices):
        raise AssertionError("selection/report-only overlap")
    zones = []
    selection_rows = []
    report_rows = []
    for zone_index in (0, 1):
        theta, metrics = select_theta(game, training, zone_index, selection_indices)
        zones.append(zone_parameters(game, training, zone_index, theta))
        selection_rows.extend({"zone": zone_index, **row} for row in metrics)
        n, k = RULES[game][zone_index]
        model_losses, m0_losses = [], []
        for index in report_indices:
            prefix = training[:index]
            params = zone_parameters(game, prefix, zone_index, theta)
            observed = training[index].front if zone_index == 0 else training[index].back
            probability = subset_probability(observed, params)
            model_losses.append(-math.log(probability))
            m0_losses.append(math.log(math.comb(n, k)))
            uniform_probability = 1.0 / math.comb(n, k)
            # One-vs-rest Brier is explicit and comparable for the observed fixed-
            # cardinality outcome.  Calibration rows are intentionally report-only.
            observed_rank = capped_subset_rank(observed, params)
            report_rows.append({
                "fold_id": f"report-{index:04d}-z{zone_index}", "draw_index": index,
                "issue": training[index].issue, "zone": zone_index, "theta": theta,
                "model_log_loss": model_losses[-1], "m0_log_loss": m0_losses[-1],
                "delta_log_loss_vs_m0": model_losses[-1] - m0_losses[-1],
                "model_brier": (1.0 - probability) ** 2,
                "m0_brier": (1.0 - uniform_probability) ** 2,
                "calibration_predicted_probability": probability,
                "calibration_observed": 1, "outcome_rank": observed_rank,
                "top_k": {str(value): observed_rank <= value for value in (10, 100, 200, 1000)},
                "fold_role": "report_only", "used_for_selection": False,
            })
    if all(abs(float(zone["theta"])) < 1e-15 for zone in zones):
        raise ValueError("HOLD_DEGENERATE_MODEL")
    scientific = "no_confirmed_lift"
    deltas = [float(row["delta_log_loss_vs_m0"]) for row in report_rows]
    delta = sum(deltas) / len(deltas)
    if delta > 0:
        scientific = "worse_than_M0"
    model_basis = {"family": "P4E1-R", "game": game, "cutoff_issue": training[-1].issue, "theta": [zone["theta"] for zone in zones], "input_fact_hashes": [draw.fact_hash for draw in training]}
    standard_error = (sum((value - delta) ** 2 for value in deltas) / max(1, len(deltas) - 1)) ** 0.5 / len(deltas) ** 0.5
    return {"family": "P4E1-R", "game": game, "training_cutoff_issue": training[-1].issue, "training_count": len(training), "zones": zones, "selection_indices": selection_indices, "report_only_indices": report_indices, "selection_metrics": selection_rows, "report_only_metrics": report_rows, "report_only_summary": {"fold_count": len(report_indices), "zone_fold_count": len(report_rows), "mean_delta_log_loss_vs_m0": delta, "delta_log_loss_95pct_ci": [delta - 1.96 * standard_error, delta + 1.96 * standard_error], "uncertainty_method": "normal_interval_over_held_out_zone_folds", "top_k_values": [10, 100, 200, 1000]}, "scientific_status": scientific, "model_release_id": f"p4e1r-{game}-{digest(model_basis)[:16]}"}


def sorted_zone_subsets(zone: dict[str, object]) -> list[tuple[float, tuple[int, ...]]]:
    n, k = int(zone["n"]), int(zone["k"])
    rows = [(subset_probability(combo, zone), combo) for combo in itertools.combinations(range(1, n + 1), k)]
    rows.sort(key=lambda row: (-row[0], row[1]))
    return rows


def top_tickets(model: dict[str, object], top_k: int = 1000) -> list[dict[str, object]]:
    if top_k != 1000:
        raise ValueError("formal product contract requires top_k=1000")
    # Serving ranking is computed entirely from the exact decimal spellings of the
    # frozen parameters. Binary floats are used for training only.
    with localcontext() as context:
        context.prec = 80
        front_weights = [Decimal(str(value)) for value in model["zones"][0]["weights"]]  # type: ignore[index]
        back_weights = [Decimal(str(value)) for value in model["zones"][1]["weights"]]  # type: ignore[index]
        front_normalizer = Decimal(str(model["zones"][0]["normalizer"]))  # type: ignore[index]
        back_normalizer = Decimal(str(model["zones"][1]["normalizer"]))  # type: ignore[index]
        zones = []
        for zone_index, (weights, normalizer) in enumerate(((front_weights, front_normalizer), (back_weights, back_normalizer))):
            n, k = int(model["zones"][zone_index]["n"]), int(model["zones"][zone_index]["k"])  # type: ignore[index]
            zone_rows = [(math.prod(weights[number - 1] for number in combo) / normalizer, combo) for combo in itertools.combinations(range(1, n + 1), k)]
            zone_rows.sort(key=lambda row: row[1])
            zone_rows.sort(key=lambda row: row[0], reverse=True)
            zones.append(zone_rows)
        exact = [(front_probability * back_probability, front_numbers, back_numbers)
                 for front_probability, front_numbers in zones[0][:top_k]
                 for back_probability, back_numbers in zones[1]]
    # Two stable sorts preserve canonical ticket order within an exact probability
    # tie without applying a context-rounded unary minus to Decimal values.
    exact.sort(key=lambda row: (row[1], row[2]))
    exact.sort(key=lambda row: row[0], reverse=True)
    exact = exact[:top_k]
    if len({row[0] for row in exact}) < 2:
        raise ValueError("HOLD_DEGENERATE_MODEL: Top-1000 all equal")
    histogram: dict[Decimal, int] = {}
    for probability, _, _ in exact:
        histogram[probability] = histogram.get(probability, 0) + 1
    bounds: dict[Decimal, tuple[int, int]] = {}
    cursor = 1
    for probability in sorted(histogram, reverse=True):
        size = histogram[probability]
        bounds[probability] = (cursor, cursor + size - 1)
        cursor += size
    result = []
    layer = 0
    previous = None
    for rank, (probability, front_numbers, back_numbers) in enumerate(exact, 1):
        canonical_probability = format(probability, "f")
        if probability != previous:
            layer += 1
            previous = probability
        lower, upper = bounds[probability]
        probability_key = hashlib.sha256(canonical_probability.encode()).hexdigest()
        tie_key_value = f"probability:{probability_key}"
        result.append({
            "rank": rank, "front_numbers": list(front_numbers), "back_numbers": list(back_numbers),
            "joint_probability": canonical_probability, "probability_representation": "P4-DECIMAL-EXACT-1",
            "probability_layer": layer, "tie_group_id": f"tie-{probability_key[:24]}",
            "tie_group_size": histogram[probability], "tie_rank_lower": lower,
            "tie_rank_upper": upper, "tie_midrank": format((Decimal(lower) + Decimal(upper)) / 2, "f"),
            "tie_key": tie_key_value,
            "canonical_ticket_key": [list(front_numbers), list(back_numbers)],
            "explanation": {"method": "P4E1-R weighted fixed-cardinality subset", "probability_primary": True},
        })
    return result


def score_ticket(model: dict[str, object], draw: Draw, top: Sequence[dict[str, object]]) -> dict[str, object]:
    probability = subset_probability(draw.front, model["zones"][0]) * subset_probability(draw.back, model["zones"][1])  # type: ignore[index]
    lookup = {(tuple(row["front_numbers"]), tuple(row["back_numbers"])): row["rank"] for row in top}
    rank = lookup.get((draw.front, draw.back))
    return {"issue": draw.issue, "joint_log_loss": -math.log(probability), "actual_joint_probability": f"{probability:.18e}", "hit_at": {str(k): bool(rank and rank <= k) for k in (10, 100, 200, 1000)}, "top1000_rank": rank}


# The immutable P4E1 implementation above remains available to historical
# releases.  Formal builds resolve these names to the separately testable
# P4E2-R implementation.
from .p4e2_model import (  # noqa: E402
    FEATURE_GROUPS,
    FEATURE_IDS,
    feature_context,
    feature_snapshot_rows,
    fit_coefficients,
    probability_qualification,
    score_identity,
    select_candidate,
    score_ticket,
    subset_probability,
    top_tickets,
    train,
)
