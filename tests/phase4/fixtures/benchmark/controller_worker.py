#!/usr/bin/env python3
"""Deterministic black-box worker used only by the non-scientific T11 benchmark."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from lottery_system.phase4.research.sequential import reduce_e_process


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def main() -> int:
    value = json.load(sys.stdin)
    if set(value) != {"benchmark_fixture_id", "cycles_per_sequence", "sequences"}:
        return 5
    if type(value["cycles_per_sequence"]) is not int or value["cycles_per_sequence"] <= 0:
        return 5
    results = []
    for row in value["sequences"]:
        if set(row) != {"sequence_id", "values"} or not row["values"]:
            return 5
        looks = []
        for cycle in range(value["cycles_per_sequence"]):
            looks.append({
                "look": cycle + 1,
                "p0": ["0.5", "0.5"],
                "p1": ["0.51", "0.49"],
                "outcome_index": row["values"][cycle % len(row["values"])] % 2,
                "p1_frozen_at_utc": f"2026-01-01T00:{cycle % 60:02d}:00Z",
                "outcome_observed_at_utc": f"2026-01-02T00:{cycle % 60:02d}:00Z",
            })
        reduced = reduce_e_process(looks, alpha_ordinal=1)
        checkpoints = [
            {"next_cycle": look["look"], "e_value": look["e_value"], "log_e_value": look["log_e_value"]}
            for look in reduced["looks"]
            if look["look"] % 10 == 0 or look["look"] == value["cycles_per_sequence"]
        ]
        body = {
            "sequence_id": row["sequence_id"], "terminal": reduced["terminal"],
            "first_crossing_look": reduced["first_crossing_look"],
            "final_e_value": reduced["looks"][-1]["e_value"],
            "looks_sha256": hashlib.sha256(canonical(reduced["looks"])).hexdigest(),
            "checkpoints": checkpoints,
        }
        body["terminal_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
        results.append(body)
    output = {
        "schema_version": "1.0.0", "artifact_type": "phase4_non_scientific_benchmark_terminals",
        "benchmark_fixture_id": value["benchmark_fixture_id"], "non_scientific": True,
        "qualification_seed_domain": None, "terminals": results,
    }
    sys.stdout.buffer.write(canonical(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
