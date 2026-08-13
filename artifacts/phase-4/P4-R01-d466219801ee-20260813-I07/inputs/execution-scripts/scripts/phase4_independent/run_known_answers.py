from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oracle_math import (
    DECIMAL_PRECISION,
    canonical_bytes,
    compact_fixture_vector,
    effect_ticks,
    full_rule_oracle,
    guard_vectors,
    m0_real_rule_oracle,
    sha256_file,
)
from oracle_metrics import build_metric_vectors
from oracle_validation import (
    validate_full_rule_result,
    validate_full_rule_spec,
    validate_m0_results,
    validate_metric_contract,
    validate_metric_vectors_independent,
    validate_probability_contract,
)


REQUIRED_CONFIG = (
    "probability-ranking-contract.json",
    "metric-contract.json",
    "qualification-preregistration.json",
    "alpha-contract.json",
    "model-registry.json",
)


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(Path.cwd().resolve()))


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _check_contracts(config: Path, tick_bound: int) -> dict[str, Any]:
    paths = {name: config / name for name in REQUIRED_CONFIG}
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"missing frozen contract: {path}")
    probability = _load(paths["probability-ranking-contract.json"])
    metric = _load(paths["metric-contract.json"])
    prereg = _load(paths["qualification-preregistration.json"])
    alpha = _load(paths["alpha-contract.json"])
    model = _load(paths["model-registry.json"])
    validate_probability_contract(probability)
    validate_metric_contract(metric)
    assertions = {
        "tick_bound": tick_bound == 4096,
        "scale": prereg["probability_family"]["scale"] == 1024,
        "decimal_precision": prereg["probability_family"]["decimal_precision"] == DECIMAL_PRECISION,
        "bounds": prereg["probability_family"]["normalized_tick_bounds"] == [-tick_bound, tick_bound],
        "games": set(probability["games"]) == {"ssq", "dlt"},
        "top_k": probability["top_k"] == [10, 100, 200, 1000],
        "metric_minimum_observations": metric["minimum_observations"] == 30,
        "effect_ticks": prereg["effect_ticks"] == [1536, 1792, 2048],
        "cycles": prereg["cycles_per_sequence"] == 150,
        "wealth": alpha["initial_wealth_per_game_family"] == "0.006",
        "first_alpha": alpha["first_spend"] == "0.003",
        "minimum_look": prereg["sequential_test"]["minimum_look"] == 30,
        "maximum_look": prereg["sequential_test"]["maximum_look"] == 150,
        "formal_sequence_count": prereg["formal_sequences_per_cell"] == 1000,
        "formal_false_max": prereg["uniform_max_false_proposals"] == 50,
        "formal_recovery_min": prereg["positive_min_recoveries"] == 900,
        "model_fixture": any(row["model_id"] == "P4E1-full-rule-known-answer-v1" for row in model["models"]),
    }
    if not all(assertions.values()):
        raise ValueError(f"frozen contract mismatch: {assertions}")
    return {
        "assertions": assertions,
        "files": [
            {"path": _relative(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in paths.values()
        ],
        "probability": probability,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent result-blind Phase 4 known-answer generator")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--tick-bound", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--full-rule-spec", type=Path, default=Path("qualification-design/full-rule-spec-candidate.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to reuse immutable output directory")
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    contract = _check_contracts(args.spec, args.tick_bound)

    fixtures = [
        compact_fixture_vector("m0-small", [0] * 10),
        *[
            compact_fixture_vector(f"menu-q{q}-positive", effect_ticks(q))
            for q in (1536, 1792, 2048)
        ],
        *[
            compact_fixture_vector(f"menu-q{q}-negative", effect_ticks(q, sign=-1))
            for q in (1536, 1792, 2048)
        ],
        compact_fixture_vector("boundary-both-signs", [0, 4096, -4096, 4096, -4096, 2048, -2048, 1, -1, 0]),
        compact_fixture_vector("adversarial-exact-ties", [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]),
        compact_fixture_vector("adversarial-unique-sums", [0, 1, 2, 4, 8, 16, 32, 64, 128, 256]),
    ]
    small = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_probability_rank_known_answers",
        "decimal_precision": DECIMAL_PRECISION,
        "tick_bound": args.tick_bound,
        "fixtures": fixtures,
        "nontransitive_approximation_negative": {
            "values": ["1", "1.0000000000000000000000000000000000000001", "1.0000000000000000000000000000000000000002"],
            "relation": "first~second,second~third,first!~third_under_pairwise_tolerance",
            "required_equivalence": "exact_integer_score_order_key_only",
        },
    }

    full_spec_path = args.full_rule_spec
    full_spec = _load(full_spec_path)
    validate_full_rule_spec(full_spec)
    if full_spec["games"] != contract["probability"]["games"] or full_spec["top_k"] != contract["probability"]["top_k"]:
        raise ValueError("full-rule spec and probability contract mismatch")
    games = contract["probability"]["games"]
    full_results = [full_rule_oracle(game, games[game], contract["probability"]["top_k"]) for game in ("ssq", "dlt")]
    m0_results = [m0_real_rule_oracle(game, games[game], contract["probability"]["top_k"]) for game in ("ssq", "dlt")]
    cells = [cell for result in full_results for cell in result["cells"]]
    if len(cells) != 8 or not all(cell["strictly_better"] for cell in cells):
        raise AssertionError("full-rule eight-cell strict improvement failed")
    full = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_full_rule_oracle",
        "spec_id": full_spec["spec_id"],
        "spec_sha256": sha256_file(full_spec_path),
        "decimal_precision": DECIMAL_PRECISION,
        "results": full_results,
        "eight_cells": cells,
        "all_eight_strictly_better": True,
    }
    metrics = build_metric_vectors()
    m0_bundle = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_real_rule_m0_known_answers",
        "games": m0_results,
        "status": "PASS",
    }
    numeric_validation = {
        "full_rule_residuals": validate_full_rule_result(full, full_spec),
        "m0_normalization_residuals": validate_m0_results(m0_bundle),
        "metric_residuals": validate_metric_vectors_independent(metrics),
    }

    args.output.mkdir(parents=True, exist_ok=False)
    _write_new(args.output / "input-contracts.json", {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_oracle_input_inventory",
        "result_blind": True,
        "started_at_utc": started,
        "contracts": contract["files"],
        "full_rule_spec": {"path": str(full_spec_path), "sha256": sha256_file(full_spec_path)},
    })
    _write_new(args.output / "small-space-probability-rank.json", small)
    _write_new(args.output / "small-space-metrics.json", metrics)
    _write_new(args.output / "full-rule-oracle.json", full)
    _write_new(args.output / "full-rule-eight-cells.json", {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_full_rule_eight_cells",
        "spec_sha256": sha256_file(full_spec_path),
        "cells": cells,
        "status": "PASS",
    })
    _write_new(args.output / "real-rule-m0.json", m0_bundle)
    _write_new(args.output / "numeric-validation.json", {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_oracle_numeric_validation",
        "decimal_precision": DECIMAL_PRECISION,
        "probability_normalization_absolute_tolerance": "1e-45",
        "metric_absolute_tolerance": "1e-40",
        **numeric_validation,
        "status": "PASS",
    })
    _write_new(args.output / "guard-vectors.json", {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_probability_ranking_guard_vectors",
        **guard_vectors(),
        "status": "PASS",
    })
    output_files = sorted(path for path in args.output.iterdir() if path.is_file())
    _write_new(args.output / "known-answer-manifest.json", {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_known_answer_manifest",
        "source_files": [
            {"path": _relative(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in (
                Path(__file__),
                Path(__file__).with_name("oracle_math.py"),
                Path(__file__).with_name("oracle_metrics.py"),
                Path(__file__).with_name("oracle_validation.py"),
            )
        ],
        "files": [
            {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in output_files
        ],
        "status": "PASS",
    })
    print(json.dumps({"status": "PASS", "output": str(args.output), "eight_cells": len(cells)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
