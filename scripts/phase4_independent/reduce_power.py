#!/usr/bin/env python3
"""Independently reduce T13 per-cell power counts into prospective gates."""
from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal, localcontext
from math import comb
from pathlib import Path
from typing import Any

PRECISION = 80


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def binomial_le(n: int, p: Decimal, maximum: int) -> Decimal:
    if p == 0:
        return Decimal(1)
    if p == 1:
        return Decimal(int(maximum >= n))
    term = (1 - p) ** n
    total = term
    for j in range(maximum):
        term *= Decimal(n - j) / Decimal(j + 1) * p / (1 - p)
        total += term
    return +total


def binomial_ge(n: int, p: Decimal, minimum: int) -> Decimal:
    if p == 0:
        return Decimal(0)
    if p == 1:
        return Decimal(1)
    term = Decimal(comb(n, minimum)) * p**minimum * (1 - p) ** (n - minimum)
    total = term
    for j in range(minimum, n):
        term *= Decimal(n - j) / Decimal(j + 1) * p / (1 - p)
        total += term
    return +total


def text(value: Decimal) -> str:
    value = +value
    raw = format(value, "f")
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


def reduce(summary: dict[str, Any], formal: int, uniform_max: int, positive_min: int) -> dict[str, Any]:
    if summary.get("artifact_type") != "phase4_power_confirmation_summary":
        raise ValueError("input is not a power-confirmation summary")
    cells = summary.get("cells")
    if not isinstance(cells, list) or len(cells) != 8:
        raise ValueError("exactly eight power cells are required")
    rows = []
    with localcontext() as context:
        context.prec = PRECISION
        for cell in cells:
            world = cell["world"]
            rate = Decimal(cell["sequence_rate_estimate"])
            lower, upper = map(Decimal, cell["sequence_rate_simultaneous_interval"])
            if not (Decimal(0) <= lower <= rate <= upper <= Decimal(1)):
                raise ValueError("invalid sequence interval")
            if world == "uniform":
                point = binomial_le(formal, rate, uniform_max)
                interval = [binomial_le(formal, upper, uniform_max), binomial_le(formal, lower, uniform_max)]
                sequence_pass = upper <= Decimal("0.05")
            elif world in {"static_bias", "slow_drift", "useful_feature"}:
                point = binomial_ge(formal, rate, positive_min)
                interval = [binomial_ge(formal, lower, positive_min), binomial_ge(formal, upper, positive_min)]
                sequence_pass = lower >= Decimal("0.90")
            else:
                raise ValueError("unregistered power world")
            rows.append({
                "game": cell["game"], "world": world,
                "sequence_rate_estimate": text(rate),
                "sequence_rate_simultaneous_interval": [text(lower), text(upper)],
                "formal_1000_gate_pass_probability_estimate": text(point),
                "formal_1000_gate_pass_probability_interval": [text(interval[0]), text(interval[1])],
                "sequence_gate_pass": sequence_pass,
                "aggregate_gate_pass": point >= Decimal("0.90") and interval[0] >= Decimal("0.90"),
            })
    passed = all(row["sequence_gate_pass"] and row["aggregate_gate_pass"] for row in rows)
    return {"schema_version":"1.0.0", "artifact_type":"phase4_power_aggregate_gates",
            "design_id":summary["design_id"], "formal_sequences":formal,
            "uniform_max_successes":uniform_max, "positive_min_successes":positive_min,
            "decimal_precision":PRECISION, "cells":rows, "status":"PASS" if passed else "HOLD",
            "terminal":"POWER_CONFIRMED" if passed else "HOLD_DESIGN_NOT_POWERED"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--formal-sequences", required=True, type=int)
    parser.add_argument("--uniform-max-successes", required=True, type=int)
    parser.add_argument("--positive-min-successes", required=True, type=int)
    parser.add_argument("--decimal-precision", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.decimal_precision != PRECISION:
        raise ValueError("decimal precision must be 80")
    source = args.input / "summary.json" if args.input.is_dir() else args.input
    result = reduce(json.loads(source.read_text(encoding="utf-8")), args.formal_sequences,
                    args.uniform_max_successes, args.positive_min_successes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical(result)); handle.flush(); os.fsync(handle.fileno())
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
