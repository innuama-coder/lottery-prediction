#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lottery_system.phase4e5.model import fit_model, top_tickets
from lottery_system.phase4e6.consensus import build_lagged_feature_rows, canonical, sha256


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e6"
DELIVERY = BASE / "delivery"
CANONICAL = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
SELECTION_PREFIX = ROOT / "artifacts/phase-4e4/data-20260819/selection-prefix"
SEALED_REPORT = ROOT / "artifacts/phase-4e4/data-20260819/sealed-report"


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def window_audit() -> dict[str, object]:
    p4e3 = {game: {row["issue"] for row in json.loads((ROOT / f"artifacts/phase-4e3/delivery-20260819/report/{game}-report-only.json").read_text())["rows"]} for game in ("ssq", "dlt")}
    result = {}
    for game in ("ssq", "dlt"):
        canonical_rows = load_jsonl(CANONICAL / f"{game}.jsonl")
        fit = {row["issue"] for row in load_jsonl(SELECTION_PREFIX / f"{game}.jsonl")}
        p4e4 = {row["issue"] for row in load_jsonl(SEALED_REPORT / f"{game}.jsonl")}
        original_200 = {row["issue"] for row in canonical_rows[-200:]}
        p4e5_roles = json.loads((ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json").read_text())["games"][game]["report"]
        p4e5_source = load_jsonl(ROOT / json.loads((ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json").read_text())["games"][game]["eligible_source"])
        p4e5 = {row["issue"] for row in p4e5_source[-int(p4e5_roles["row_count"]):]}
        touched = fit | p4e3[game] | p4e4 | p4e5 | original_200
        untouched = [row for row in canonical_rows if row["issue"] not in touched]
        result[game] = {
            "canonical_rows": len(canonical_rows), "prior_fit_or_selection_rows": len(fit),
            "p4e3_report_rows": len(p4e3[game]), "p4e4_report_rows": len(p4e4), "p4e5_report_rows": len(p4e5),
            "original_200_rows": len(original_200), "untouched_rows": len(untouched),
            "required_contiguous_report_rows": 120, "valid_window_available": len(untouched) >= 120,
            "canonical_coverage_by_exclusion_union": len(touched & {row["issue"] for row in canonical_rows}) / len(canonical_rows),
        }
    audit = {"artifact_type": "phase4e6_untouched_window_audit", "games": result, "report_labels_opened_by_p4e6": False, "report_evaluation_count": 0, "valid_both_game_window": all(row["valid_window_available"] for row in result.values()), "terminal_status": "PROSPECTIVE_ONLY"}
    audit["receipt_sha256"] = sha256(canonical(audit)); return audit


def next_issue(game: str, issue: str) -> str:
    year, sequence = int(issue[:4]), int(issue[4:])
    return f"{year}{sequence + 1:03d}"


def next_date(game: str, current: str) -> str:
    cursor = date.fromisoformat(current)
    weekdays = {"ssq": {1, 3, 6}, "dlt": {0, 2, 5}}[game]
    while True:
        cursor += timedelta(days=1)
        if cursor.weekday() in weekdays:
            return cursor.isoformat()


def append_ssq_consensus(draws: list[dict[str, object]], consensus_rows: list[dict[str, object]]) -> None:
    known = {str(row["issue"]) for row in draws}
    for row in consensus_rows:
        if row["game"] == "ssq" and row["identity_consensus"] and str(row["issue"]) not in known:
            draws.append({"game": "ssq", "issue": row["issue"], "draw_date": row["draw_date"], "front": row["front"], "back": row["back"], "source_id": "phase4e6_identity_consensus", "source_record_sha256": row["row_sha256"]})
    draws.sort(key=lambda row: (str(row["draw_date"]), str(row["issue"])))


def main() -> int:
    DELIVERY.mkdir(parents=True, exist_ok=True)
    for directory in ("top1000", "top10-shadow", "features", "model-cards", "diagnostics", "experiments", "normalization", "inventory", "ledger"):
        (DELIVERY / directory).mkdir(exist_ok=True)
    audit = window_audit(); (BASE / "untouched-window-audit.json").write_bytes(canonical(audit))
    coverage = json.loads((BASE / "consensus/coverage-report.json").read_text())
    consensus_rows = load_jsonl(BASE / "consensus/consensus-rows.jsonl")
    consensus_map = {str(row["issue"]): row for row in consensus_rows}
    summaries = []
    ledger = []
    for game in ("ssq", "dlt"):
        draws = load_jsonl(CANONICAL / f"{game}.jsonl")
        if game == "ssq": append_ssq_consensus(draws, consensus_rows)
        feature_rows = build_lagged_feature_rows(draws, consensus_map)
        target = {"game": game, "issue": next_issue(game, str(draws[-1]["issue"])), "draw_date": next_date(game, str(draws[-1]["draw_date"])), "front": [], "back": []}
        prospective_features = build_lagged_feature_rows(draws + [target], consensus_map)[-1]
        model = fit_model(game, None, draws, list(range(len(draws))), 1.0, "B0")
        tickets, proof = top_tickets(model, None, 1000)
        for row in tickets:
            row.update({"game": game, "target_issue": target["issue"], "target_draw_date": target["draw_date"], "model": "B0", "status": "Shadow"})
        (DELIVERY / f"top1000/{game}-top1000-shadow.jsonl").write_bytes(b"".join(canonical(row) for row in tickets))
        (DELIVERY / f"top10-shadow/{game}-top10-shadow.jsonl").write_bytes(b"".join(canonical(row) for row in tickets[:10]))
        (DELIVERY / f"features/{game}-prospective-feature-snapshot.json").write_bytes(canonical(prospective_features))
        proof.update({"artifact_type": "phase4e6_normalization_proof", "game": game, "ticket_count": len(tickets), "top1000_probability_sum": sum(float(row["joint_probability"]) for row in tickets)})
        (DELIVERY / f"normalization/{game}-normalization-proof.json").write_bytes(canonical(proof))
        gate = coverage["games"][game]
        card = {"artifact_type": "phase4e6_model_card", "game": game, "selected_model": "B0", "selection_basis": "fail-closed prospective fallback; no untouched report and both-game consensus gate failed", "training_rows": len(draws), "operational_coverage": gate["accepted_fraction"], "serving_status": "SHADOW_ONLY", "probability_postprocessing": "none", "limitations": ["lottery outcomes are random", "no validated incremental skill", "operational metadata is stale or unavailable for SSQ"]}
        (DELIVERY / f"model-cards/{game}-model-card.json").write_bytes(canonical(card))
        diagnostic = {"artifact_type": "phase4e6_feature_diagnostic", "game": game, "feature_row_count": len(feature_rows), "prospective_maximum_metadata_issue": prospective_features["maximum_metadata_issue"], "prospective_staleness_draws": prospective_features["staleness_draws"], "source_count_lag_1": prospective_features["source_count_lag_1"], "conflict_lag_1": prospective_features["conflict_lag_1"], "quarantined_lag_1": prospective_features["quarantined_lag_1"], "operational_coverage_gate_pass": gate["accepted_fraction"] >= 0.95}
        (DELIVERY / f"diagnostics/{game}-feature-diagnostics.json").write_bytes(canonical(diagnostic))
        ablation = {"artifact_type": "phase4e6_ablation", "game": game, "status": "NOT_STATISTICALLY_ESTIMABLE", "candidate_family": ["B0", "C1", "Q1", "O1", "O2", "O3"], "reason": "no genuinely untouched 120-draw report window and both-game operational consensus coverage gate failed", "adverse_or_missing_result_retained": True}
        (DELIVERY / f"experiments/{game}-ablation.json").write_bytes(canonical(ablation))
        entry = {"artifact_type": "phase4e6_prospective_prediction_ledger_entry", "game": game, "target_issue": target["issue"], "target_draw_date": target["draw_date"], "issued_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "outcome_known_at_issue_time": False, "model": "B0", "top1000_sha256": sha256((DELIVERY / f"top1000/{game}-top1000-shadow.jsonl").read_bytes()), "top10_sha256": sha256((DELIVERY / f"top10-shadow/{game}-top10-shadow.jsonl").read_bytes()), "serving_release": "P4-P4E2-20260815-r12", "serving_changed": False, "status": "PROSPECTIVE_SHADOW"}
        ledger.append(entry); summaries.append({"game": game, "model": "B0", "target_issue": target["issue"], "operational_coverage_gate_pass": diagnostic["operational_coverage_gate_pass"]})
    (DELIVERY / "ledger/prospective-prediction-ledger.jsonl").write_bytes(b"".join(canonical(row) for row in ledger))
    selection = {"artifact_type": "phase4e6_prospective_selection_receipt", "report_labels_read": False, "report_evaluations": 0, "candidate_family_frozen": ["B0", "C1", "Q1", "O1", "O2", "O3"], "selected_shadow_fallback": "B0", "statistical_selection_performed": False, "reason": "PROSPECTIVE_ONLY and both-game data-quality gate failure", "games": summaries}
    selection["receipt_sha256"] = sha256(canonical(selection)); (DELIVERY / "prospective-selection-receipt.json").write_bytes(canonical(selection))
    clean = subprocess.run(["git", "diff", "--quiet", "40afa230", "--", "artifacts/phase-4", "artifacts/phase-4e3", "artifacts/phase-4e4", "artifacts/phase-4e5"], cwd=ROOT).returncode == 0
    inventory = {"artifact_type": "phase4e6_prior_byte_inventory", "base_commit": "40afa230", "scopes": ["artifacts/phase-4", "artifacts/phase-4e3", "artifacts/phase-4e4", "artifacts/phase-4e5"], "all_bytes_unchanged": clean}
    (DELIVERY / "inventory/prior-byte-inventory.json").write_bytes(canonical(inventory))
    decision = {"artifact_type": "phase4e6_decision", "terminal_status": "PROSPECTIVE_ONLY", "valid_untouched_report_window": False, "both_games_consensus_coverage_gate_pass": coverage["both_games_coverage_gate_pass"], "statistical_promotion_gate_pass": False, "all_promotion_gates_pass": False, "serving_release": "P4-P4E2-20260815-r12", "serving_release_changed": False, "release_allocation": "FORBIDDEN", "probability_spread_adjustment": "none"}
    decision["receipt_sha256"] = sha256(canonical(decision)); (DELIVERY / "decision.json").write_bytes(canonical(decision))
    print(json.dumps(decision, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
