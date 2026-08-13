from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry, producer_provenance, project_root
from ..probability import ProbabilityViolation, distribution, normalization_proof, zone_distribution
from ..ranking import RankingViolation, rank_histogram, top1000, top_k_coverage, zone_histogram, zone_top_rows
from ..rules import RuleViolation, game_rule, normalize_ticks
from ..serialization import canonical_json_bytes, load_json, sha256_bytes, sha256_file
from ..storage import resolve_inside, write_once_json


T10_RECEIPT_SHA256 = "3e4bb4c0d019ab175bb3ab86865a715f4cb536cdbab1d70cd41c791c3e41aff4"
T10_VERDICT_SHA256 = "faa4ff88db457ce7fc25c6f10fdc59c2c80b1a9bc4a2405fad8182b82c4c8699"
T10_MANIFEST_SHA256 = "8073aa54c1d1fa8d06ff1fc56e9c2fa1c625744cd3d81e17b9575906f8157803"
SPEC_SHA256 = "c3c4a9d477bc5cbf38c1aef0495e83cba63d9b8b0ccd53f2854ffde1c90be68d"


class OracleMismatch(ValueError):
    exit_code = 20
    terminal = "HOLD_UNSUPPORTED_TIE_SEMANTICS"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise OracleMismatch(message)


def _full_rule_ticks(spec: dict[str, Any], n: int) -> tuple[int, ...]:
    raw = [0] * n
    for position, tick in zip(range(1, 5), spec["raw_tick_rule"]["positions_1_to_4"]):
        raw[position - 1] = tick
    for position, tick in zip(range(n - 3, n + 1), spec["raw_tick_rule"]["positions_n_minus_3_to_n"]):
        raw[position - 1] = tick
    return normalize_ticks(raw)


def _verify_manifest(root: Path, oracle: Path) -> dict[str, Any]:
    manifest_path = resolve_inside(oracle, "known-answer-manifest.json")
    _assert(sha256_file(manifest_path) == T10_MANIFEST_SHA256, "T10-I07 known-answer manifest identity mismatch")
    manifest = load_json(manifest_path, reject_floats=True)
    _assert(manifest.get("status") == "PASS", "T10-I07 known-answer manifest is not PASS")
    expected_names = {
        "full-rule-eight-cells.json", "full-rule-oracle.json", "guard-vectors.json", "input-contracts.json",
        "numeric-validation.json", "real-rule-m0.json", "small-space-metrics.json", "small-space-probability-rank.json",
    }
    _assert({Path(row["path"]).name for row in manifest.get("files", [])} == expected_names, "T10-I07 known-answer file set mismatch")
    for row in manifest["files"]:
        path = resolve_inside(root, row["path"])
        _assert(path.parent == oracle and path.stat().st_size == row["bytes"] and sha256_file(path) == row["sha256"], f"T10-I07 known-answer file mismatch: {path.name}")
    attempt = oracle.parent
    receipt = attempt / "receipt.json"
    verdict = attempt / "independent-validation.json"
    _assert(sha256_file(receipt) == T10_RECEIPT_SHA256, "T10-I07 producer receipt mismatch")
    _assert(sha256_file(verdict) == T10_VERDICT_SHA256, "T10-I07 independent verdict mismatch")
    _assert(load_json(receipt, reject_floats=True).get("status") == "PASS", "T10-I07 producer receipt is not PASS")
    _assert(load_json(verdict, reject_floats=True).get("status") == "PASS", "T10-I07 independent verdict is not PASS")
    return manifest


def _verify_small_space(oracle: Path) -> list[dict[str, Any]]:
    known = load_json(resolve_inside(oracle, "small-space-probability-rank.json"), reject_floats=True)
    _assert(known.get("decimal_precision") == 80 and known.get("tick_bound") == 4096, "small-space numeric contract mismatch")
    summaries: list[dict[str, Any]] = []
    for fixture in known["fixtures"]:
        zone = zone_distribution(fixture["ticks"], fixture["k"])
        histogram = [[score, count] for score, count in zone_histogram(zone.ticks, zone.k).items()]
        rows = zone_top_rows(zone, limit=100)
        histogram_hash = sha256_bytes(canonical_json_bytes(histogram))
        rows_hash = sha256_bytes(canonical_json_bytes(rows))
        _assert(histogram_hash == fixture["histogram_sha256"] and histogram == fixture["histogram"], f"small-space histogram mismatch: {fixture['fixture_id']}")
        _assert(rows_hash == fixture["top_rows_sha256"] and rows == fixture["top_rows"], f"small-space Top-K mismatch: {fixture['fixture_id']}")
        _assert(abs(zone.partition - Decimal(fixture["partition_dp"])) <= Decimal("1e-75"), f"small-space partition mismatch: {fixture['fixture_id']}")
        summaries.append({
            "fixture_id": fixture["fixture_id"], "histogram_sha256": histogram_hash,
            "top_rows_sha256": rows_hash, "histogram_total": sum(row[1] for row in histogram),
        })
    return summaries


def _verify_m0(oracle: Path) -> list[dict[str, Any]]:
    known = load_json(resolve_inside(oracle, "real-rule-m0.json"), reject_floats=True)
    _assert(known.get("status") == "PASS", "real-rule M0 oracle is not PASS")
    summaries: list[dict[str, Any]] = []
    for expected in known["games"]:
        game = expected["game"]
        rule = game_rule(game, rule_id=expected["rule_id"])
        model = distribution(game, [0] * rule.front_n, [0] * rule.back_n, model_contract_id="M0")
        histogram = rank_histogram(model)
        rows = top1000(model, forecast_id=f"oracle-m0-{game}-fixture")
        rows_hash = sha256_bytes(canonical_json_bytes(rows))
        _assert(histogram == {0: rule.space_size}, f"M0 full-space tie histogram mismatch: {game}")
        _assert(rows == expected["top1000"] and rows_hash == expected["top1000_sha256"], f"M0 Top-1000 mismatch: {game}")
        proof = normalization_proof(model, histogram)
        _assert(Decimal(proof["absolute_residual"]) <= Decimal("1e-45"), f"M0 normalization mismatch: {game}")
        summaries.append({
            "game": game, "rule_id": rule.rule_id, "space_size": rule.space_size,
            "histogram": [[0, rule.space_size]], "top1000_sha256": rows_hash,
            "normalization_proof": proof,
        })
    return summaries


def _verify_full_rule(root: Path, oracle: Path) -> list[dict[str, Any]]:
    spec_path = resolve_inside(root, "qualification-design/full-rule-spec-candidate.json")
    _assert(sha256_file(spec_path) == SPEC_SHA256, "full-rule specification identity mismatch")
    spec = load_json(spec_path, reject_floats=True)
    known = load_json(resolve_inside(oracle, "full-rule-oracle.json"), reject_floats=True)
    _assert(known.get("spec_sha256") == SPEC_SHA256 and known.get("all_eight_strictly_better") is True, "full-rule oracle/spec binding mismatch")
    summaries: list[dict[str, Any]] = []
    for expected in known["results"]:
        game = expected["game"]
        rule = game_rule(game, rule_id=expected["rule_id"])
        front = _full_rule_ticks(spec, rule.front_n)
        back = _full_rule_ticks(spec, rule.back_n)
        _assert(list(front) == expected["front_ticks"] and list(back) == expected["back_ticks"], f"full-rule tick mismatch: {game}")
        model = distribution(game, front, back, model_contract_id=spec["spec_id"])
        histogram = rank_histogram(model)
        histogram_rows = [[score, count] for score, count in histogram.items()]
        rows = top1000(model)
        histogram_hash = sha256_bytes(canonical_json_bytes(histogram_rows))
        rows_hash = sha256_bytes(canonical_json_bytes(rows))
        _assert(histogram_hash == expected["histogram_sha256"] and sum(histogram.values()) == rule.space_size, f"full-rule histogram mismatch: {game}")
        _assert(rows_hash == expected["top1000_sha256"] and rows == expected["top1000"], f"full-rule Top-1000 mismatch: {game}")
        coverage = top_k_coverage(model, rows, [10, 100, 200, 1000])
        for cell in expected["cells"]:
            difference = abs(Decimal(coverage[str(cell["K"])]) - Decimal(cell["candidate_coverage"]))
            _assert(difference <= Decimal(cell["absolute_error_bound"]), f"full-rule coverage mismatch: {game}/{cell['K']}")
        summaries.append({
            "game": game, "rule_id": rule.rule_id, "space_size": rule.space_size,
            "front_reachable_scores": len(zone_histogram(front, rule.front_k)),
            "back_reachable_scores": len(zone_histogram(back, rule.back_k)),
            "joint_reachable_scores": len(histogram), "histogram_sha256": histogram_hash,
            "top1000_sha256": rows_hash, "coverage": coverage,
            "normalization_proof": normalization_proof(model, histogram),
        })
    return summaries


def validate_probability_ranking(root: Path, oracle: Path) -> dict[str, Any]:
    oracle = oracle.resolve()
    oracle.relative_to(root.resolve())
    _verify_manifest(root, oracle)
    small = _verify_small_space(oracle)
    m0 = _verify_m0(oracle)
    full = _verify_full_rule(root, oracle)
    guards = load_json(resolve_inside(oracle, "guard-vectors.json"), reject_floats=True)
    _assert(guards.get("status") == "PASS" and guards.get("input_permutation", {}).get("stable") is True, "T10 guard vectors are not PASS")
    return {
        "schema_version": "1.0.0", "artifact_type": "phase4_product_probability_ranking_known_answer",
        "scope": "probability-ranking", "decimal_precision": 80, "tick_bounds": [-4096, 4096],
        "t10_receipt_sha256": T10_RECEIPT_SHA256, "t10_independent_verdict_sha256": T10_VERDICT_SHA256,
        "t10_known_answer_manifest_sha256": T10_MANIFEST_SHA256, "full_rule_spec_sha256": SPEC_SHA256,
        "small_space": small, "real_rule_m0": m0, "full_rule_candidate": full,
        "guard_vectors_sha256": sha256_file(resolve_inside(oracle, "guard-vectors.json")), "status": "PASS",
    }


def validate_unit(args: Any) -> dict[str, Any]:
    if args.scope == "forecast-diagnostic-time-label":
        from .forecast import validate_forecast_diagnostic_scope
        return validate_forecast_diagnostic_scope(args)
    if args.scope != "probability-ranking":
        return {"status": "HOLD", "terminal": "HOLD_UNSUPPORTED_TIE_SEMANTICS", "error": "T04 provider only owns probability-ranking", "exit_code": 20}
    root = project_root().resolve()
    output = args.output.resolve()
    try:
        relative_output = output.relative_to(root)
        parts = relative_output.parts
        _assert("work-items" in parts and any(part.startswith("T04") for part in parts), "probability validation output is outside a T04 work-item root")
        result = validate_probability_ranking(root, args.oracle.resolve())
        result["producer_provenance"] = producer_provenance(root, relative_output.as_posix())
        product_path = resolve_inside(output, "product-known-answer.json")
        if product_path.exists():
            _assert(load_json(product_path, reject_floats=True) == result, "product known-answer identity reuse mismatch")
        else:
            write_once_json(product_path, result)
    except ContractEvidenceMismatch:
        raise
    except (OSError, ValueError, ProbabilityViolation, RankingViolation, RuleViolation) as exc:
        return {"status": "HOLD", "terminal": getattr(exc, "terminal", "HOLD_UNSUPPORTED_TIE_SEMANTICS"), "error": str(exc), "exit_code": getattr(exc, "exit_code", 20)}
    return {
        "status": "PASS", "terminal": "PASS", "scope": args.scope,
        "product_known_answer_sha256": sha256_file(resolve_inside(output, "product-known-answer.json")),
        "exit_code": 0,
    }


def register(registry: ProviderRegistry) -> None:
    registry.register("validate", "unit", validate_unit)
