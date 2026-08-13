from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from math import comb
from pathlib import Path
from typing import Any

from oracle_math import (
    DECIMAL_PRECISION,
    SCALE,
    canonical_bytes,
    combinations_with_scores,
    decimal_string,
    normalize_ticks,
    partition_direct,
    sha256_file,
)


EXPECTED_SPEC = {
    "schema_version": "1.0.0",
    "artifact_type": "phase4_analytic_feasibility_spec",
    "spec_id": "p4-analytic-feasibility-v1",
    "result_blind": True,
    "small_space": {"N": 10, "k": 3, "space_size": 120},
    "scale": 1024,
    "cycles": 150,
    "effect_vector": [1, 1, 1, 0, 0, 0, 0, -1, -1, -1],
    "effect_ticks": [1536, 1792, 2048],
    "slow_drift_ramp_cycles": 100,
    "feature_context": "strict_alternation_75_75_context_fixed_before_draw",
    "family_initial_wealth": "0.006",
    "alpha_first": "0.003",
    "uniform_sequence_upper_bound": "0.018",
    "formal_sequences": 1000,
    "uniform_max_false_proposals": 50,
    "positive_min_recoveries": 900,
    "positive_bound": "1-exp(-2*(mu-h)^2/sum_range_squared)",
    "threshold_h": "ln(1/0.003)",
    "rounding": "ROUND_HALF_EVEN",
    "decimal_precision": 80,
    "selection_minima": {"uniform_aggregate": "0.99", "positive_aggregate": "0.99", "positive_sequence": "0.93"},
    "required_reference_minima": {
        "weakest_uniform_aggregate": "0.9999999999",
        "worst_positive_sequence": "0.93954",
        "worst_positive_aggregate": "0.99999950",
    },
    "t01_qualification_contract_sha256": "abb8d09dd5464d1eacc316b376ebad39eb885e52ed3c3de7d3de143cb1b76264",
    "t01_alpha_contract_sha256": "a0ba22154d374c2eb09401f8a86c377e8dd7443222825ff42b6e4cd561044006",
}
T01_QUALIFICATION = Path("config/phase4/qualification-preregistration.json")
T01_ALPHA = Path("config/phase4/alpha-contract.json")


def _require_exact(actual: Any, expected: Any, path: str = "$") -> None:
    if type(actual) is not type(expected):
        raise ValueError(f"analytic spec type mismatch at {path}: {type(actual).__name__} != {type(expected).__name__}")
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            raise ValueError(f"analytic spec keys mismatch at {path}: {sorted(actual)} != {sorted(expected)}")
        for key in expected:
            _require_exact(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"analytic spec list length mismatch at {path}")
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            _require_exact(actual_item, expected_item, f"{path}[{index}]")
    elif actual != expected:
        raise ValueError(f"analytic spec value mismatch at {path}: {actual!r} != {expected!r}")


def validate_analytic_spec(spec: Any) -> dict[str, Any]:
    _require_exact(spec, EXPECTED_SPEC)
    if sha256_file(T01_QUALIFICATION) != spec["t01_qualification_contract_sha256"]:
        raise ValueError("T01 qualification contract hash binding mismatch")
    if sha256_file(T01_ALPHA) != spec["t01_alpha_contract_sha256"]:
        raise ValueError("T01 alpha contract hash binding mismatch")
    return spec


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def binomial_cdf_le(n: int, probability: Decimal, maximum: int) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        if probability == 0:
            return Decimal(1)
        if probability == 1:
            return Decimal(1 if maximum >= n else 0)
        term = (Decimal(1) - probability) ** n
        total = term
        for j in range(maximum):
            term *= Decimal(n - j) / Decimal(j + 1) * probability / (Decimal(1) - probability)
            total += term
        return +min(Decimal(1), max(Decimal(0), total))


def binomial_tail_ge(n: int, probability: Decimal, minimum: int) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        if probability == 0:
            return Decimal(0)
        if probability == 1:
            return Decimal(1)
        term = Decimal(comb(n, minimum)) * probability**minimum * (Decimal(1) - probability) ** (n - minimum)
        total = term
        for j in range(minimum, n):
            term *= Decimal(n - j) / Decimal(j + 1) * probability / (Decimal(1) - probability)
            total += term
        return +min(Decimal(1), max(Decimal(0), total))


def _distribution(ticks: tuple[int, ...], n: int, k: int, scale: int) -> list[tuple[Decimal, tuple[int, ...]]]:
    rows = combinations_with_scores(n, k, ticks)
    z = partition_direct(rows, scale=scale)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return [((Decimal(score) / Decimal(scale)).exp() / z, ticket) for score, ticket in rows]


def _period_terms(ticks: tuple[int, ...], n: int, k: int, scale: int, space_size: int) -> tuple[Decimal, Decimal]:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        distribution = _distribution(ticks, n, k, scale)
        uniform = Decimal(1) / Decimal(space_size)
        log_ratios = [(probability / uniform).ln() for probability, _ in distribution]
        mu = sum(probability * log_ratio for (probability, _), log_ratio in zip(distribution, log_ratios))
        range_width = max(log_ratios) - min(log_ratios)
        return +mu, +(range_width * range_width)


def _round_ramp(q: int, period: int, ramp_cycles: int) -> int:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        value = Decimal(q) * Decimal(min(period, ramp_cycles)) / Decimal(ramp_cycles)
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _positive_bound(mu: Decimal, sum_range_squared: Decimal, threshold: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        if mu <= threshold:
            return Decimal(0)
        return +(Decimal(1) - (Decimal(-2) * (mu - threshold) ** 2 / sum_range_squared).exp())


def _candidate(q: int, threshold: Decimal, spec: dict[str, Any]) -> dict[str, Any]:
    n, k, space_size = spec["small_space"]["N"], spec["small_space"]["k"], spec["small_space"]["space_size"]
    scale, cycles, ramp = spec["scale"], spec["cycles"], spec["slow_drift_ramp_cycles"]
    effect_vector = spec["effect_vector"]
    ticks = lambda magnitude, sign=1: normalize_ticks([sign * magnitude * value for value in effect_vector])
    static_mu_one, static_range_one = _period_terms(ticks(q), n, k, scale, space_size)
    static_mu = static_mu_one * Decimal(cycles)
    static_range = static_range_one * Decimal(cycles)

    slow_mu = Decimal(0)
    slow_range = Decimal(0)
    for period in range(1, cycles + 1):
        period_mu, period_range = _period_terms(ticks(_round_ramp(q, period, ramp)), n, k, scale, space_size)
        slow_mu += period_mu
        slow_range += period_range

    positive_mu, positive_range = _period_terms(ticks(q), n, k, scale, space_size)
    negative_mu, negative_range = _period_terms(ticks(q, -1), n, k, scale, space_size)
    half_cycles = Decimal(cycles) / Decimal(2)
    feature_mu = half_cycles * (positive_mu + negative_mu)
    feature_range = half_cycles * (positive_range + negative_range)

    worlds = []
    for world, mu, range_squared in (
        ("static_bias", static_mu, static_range),
        ("slow_drift", slow_mu, slow_range),
        ("useful_feature", feature_mu, feature_range),
    ):
        sequence = _positive_bound(mu, range_squared, threshold)
        aggregate = binomial_tail_ge(spec["formal_sequences"], sequence, spec["positive_min_recoveries"])
        worlds.append({
            "world": world,
            "mu": decimal_string(mu),
            "sum_range_squared": decimal_string(range_squared),
            "threshold_h": decimal_string(threshold),
            "sequence_recovery_lower_bound": decimal_string(sequence),
            "formal_1000_gate_pass_probability_lower_bound": decimal_string(aggregate),
            "sequence_selection_gate_pass": sequence >= Decimal(spec["selection_minima"]["positive_sequence"]),
            "aggregate_selection_gate_pass": aggregate >= Decimal(spec["selection_minima"]["positive_aggregate"]),
        })
    return {"q": q, "worlds": worlds}


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent analytic Phase 4 qualification feasibility checker")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to reuse immutable output directory")
    spec = validate_analytic_spec(_load(args.spec))
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        threshold = (Decimal(1) / Decimal(spec["alpha_first"])).ln()
        uniform_sequence_upper = Decimal(spec["uniform_sequence_upper_bound"])
        uniform_aggregate_lower = binomial_cdf_le(spec["formal_sequences"], uniform_sequence_upper, spec["uniform_max_false_proposals"])
        candidates = [_candidate(q, threshold, spec) for q in spec["effect_ticks"]]
    all_worlds = [world for candidate in candidates for world in candidate["worlds"]]
    worst_sequence = min(Decimal(world["sequence_recovery_lower_bound"]) for world in all_worlds)
    worst_aggregate = min(Decimal(world["formal_1000_gate_pass_probability_lower_bound"]) for world in all_worlds)
    assertions = {
        "enumerated_combination_count": len(combinations_with_scores(spec["small_space"]["N"], spec["small_space"]["k"], (0,) * spec["small_space"]["N"])) == spec["small_space"]["space_size"],
        "uniform_aggregate_gt_required_reference": uniform_aggregate_lower > Decimal(spec["required_reference_minima"]["weakest_uniform_aggregate"]),
        "uniform_aggregate_meets_selection": uniform_aggregate_lower >= Decimal(spec["selection_minima"]["uniform_aggregate"]),
        "worst_positive_sequence_ge_required_reference": worst_sequence >= Decimal(spec["required_reference_minima"]["worst_positive_sequence"]),
        "worst_positive_aggregate_gt_required_reference": worst_aggregate > Decimal(spec["required_reference_minima"]["worst_positive_aggregate"]),
        "all_selection_gates": all(world["sequence_selection_gate_pass"] and world["aggregate_selection_gate_pass"] for world in all_worlds),
    }
    status = "PASS" if all(assertions.values()) else "HOLD"
    certificate = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_analytic_feasibility_certificate",
        "spec_id": spec["spec_id"],
        "spec_sha256": sha256_file(args.spec),
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "result_blind": spec["result_blind"],
        "decimal_precision": spec["decimal_precision"],
        "formula": spec["positive_bound"],
        "uniform": {
            "sequence_false_proposal_upper_bound": decimal_string(uniform_sequence_upper),
            "formal_1000_gate_pass_probability_lower_bound": decimal_string(uniform_aggregate_lower),
        },
        "candidates": candidates,
        "worst_positive_sequence_lower_bound": decimal_string(worst_sequence),
        "worst_positive_aggregate_lower_bound": decimal_string(worst_aggregate),
        "assertions": assertions,
        "status": status,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    _write_new(args.output / "certificate.json", certificate)
    _write_new(args.output / "source-input-hashes.json", {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_feasibility_source_input_hashes",
        "files": [
            {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in (Path(__file__), Path(__file__).with_name("oracle_math.py"), args.spec, T01_QUALIFICATION, T01_ALPHA)
        ],
        "status": status,
    })
    print(json.dumps({"status": status, "output": str(args.output), "assertions": assertions}, sort_keys=True))
    return 0 if status == "PASS" else 20


if __name__ == "__main__":
    raise SystemExit(main())
