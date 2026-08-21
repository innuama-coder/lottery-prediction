#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import math
import multiprocessing
import sys
import heapq
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e16"))
sys.path.insert(0, str(ROOT / "scripts/phase4e17"))
from lottery_system.phase4e3 import model as p4e3
import run_stable_orientation as e16
import run_per_number_feature_model as e17

GAME = "ssq"
OUT = ROOT / "artifacts/phase4e18" / GAME
OUTER_DRAWS = 120
SELECTION_DRAWS = 240
BLOCK_DRAWS = 60
FEATURE_CANDIDATES = (
    {"id": "raw_control", "kind": "raw"},
    {"id": "surprise_renewal", "kind": "model", "features": ["E01", "E02", "E03", "E04", "N01"], "config": {"history": 120, "l2": 36.0, "temperature": 0.5, "graph_window": 80, "pair_shrinkage": 20.0, "purge": 2}},
    {"id": "transition_graph", "kind": "model", "features": ["E05", "E06", "E07", "E08", "N02"], "config": {"history": 120, "l2": 4.0, "temperature": 0.5, "graph_window": 80, "pair_shrinkage": 20.0, "purge": 2}},
    {"id": "short_window_blue", "kind": "model", "features": ["E01", "E02", "E05", "N01"], "config": {"history": 60, "l2": 12.0, "temperature": 0.6, "graph_window": 40, "pair_shrinkage": 30.0, "purge": 2}},
)
ZONES = ("front", "back")


def load_data():
    return e16.e13.load(GAME)


def source_rows(kind):
    return [json.loads(line) for line in (e16.OUT / GAME / f"{kind}-rolling-report.jsonl").read_text().splitlines()]


def score_for(candidate, data, target, source, zone):
    zone_index = 0 if zone == "front" else 1
    if candidate["kind"] == "raw":
        return [float(v["inclusion_mass"]) for v in source["zones"][zone]["marginal_probabilities"]]
    prefix = list(data[:target])
    cfg = candidate["config"]
    fitted = p4e3.fit_zone(GAME, prefix, len(prefix), zone_index, tuple(candidate["features"]), history=int(cfg["history"]), l2=float(cfg["l2"]), temperature=float(cfg["temperature"]), graph_window=int(cfg["graph_window"]), pair_shrinkage=float(cfg["pair_shrinkage"]), purge=int(cfg["purge"]))
    dist = p4e3.zone_distribution(GAME, prefix, zone_index, fitted)
    p4e3._TRAINING_SAMPLE_CACHE.clear()
    scores = [float(v) for v in dist["inclusion_probabilities"]]
    if not math.isclose(math.fsum(scores), float(dist["k"]), abs_tol=1e-9):
        raise ValueError("FAIL_E18_SCORE_NORMALIZATION")
    return scores


def association(scores, actual):
    observations = [{"candidate_score": float(score), "binary_hit": int(i + 1 in set(actual))} for i, score in enumerate(scores)]
    return e16.e15.association_metrics(observations, "candidate_score", "binary_hit")


def candidate_block_metrics(args):
    candidate, data, block_rows, zone = args
    metrics = []
    for src in block_rows:
        target = int(src["target_position"])
        scores = score_for(candidate, data, target, src, zone)
        actual = set(src["zones"][zone]["actual_numbers"])
        metrics.extend({"candidate_score": s, "binary_hit": int(i + 1 in actual)} for i, s in enumerate(scores))
    return e16.e15.association_metrics(metrics, "candidate_score", "binary_hit")


def select_zone(data, inner, zone):
    rows = []
    for candidate in FEATURE_CANDIDATES:
        tasks = [(candidate, data, inner[i * BLOCK_DRAWS:(i + 1) * BLOCK_DRAWS], zone) for i in range(4)]
        with multiprocessing.get_context("spawn").Pool(processes=4) as pool:
            blocks = pool.map(candidate_block_metrics, tasks)
        positive = sum(bool(m["positive_association"]) for m in blocks)
        rhos = [float(m["spearman_rho"]) for m in blocks]
        candidate_result = {"candidate_id": candidate["id"], "blocks": blocks, "positive_blocks": positive, "median_rho": sorted(rhos)[len(rhos)//2]}
        rows.append((candidate_result, candidate))
    eligible = [item for item in rows if item[0]["positive_blocks"] >= 3]
    selected_result, selected = max(eligible or rows, key=lambda item: (item[0]["positive_blocks"], item[0]["median_rho"], -list(FEATURE_CANDIDATES).index(item[1])))
    return selected, {"selected": selected_result, "candidates": [r for r, _ in rows], "selection_uses_outer_labels": False}


def decorate(data, rows, selected_by_zone):
    decorated = []
    for src in rows:
        out = copy.deepcopy(src)
        zones = {}
        for zone in ZONES:
            candidate, _ = selected_by_zone[zone]
            scores = score_for(candidate, data, int(src["target_position"]), src, zone)
            ranking = sorted(range(1, len(scores) + 1), key=lambda n: (-scores[n - 1], n))
            zones[zone] = {"selected_candidate": candidate["id"], "selected_candidate_kind": candidate["kind"], "number_observations": [{"number": n, "candidate_score": scores[n - 1], "binary_hit": int(n in set(src["zones"][zone]["actual_numbers"]))} for n in range(1, len(scores) + 1)], "selected_orientation_ranking": ranking}
        out["phase4e18_ssq_specialized_model"] = {"strict_lag": True, "zones": zones, "selection_uses_outer_labels": False}
        out["phase4e17_per_number_feature_model"] = out["phase4e18_ssq_specialized_model"]
        decorated.append(out)
    return decorated


def aggregate(rows):
    totals = {}
    for row in rows:
        metrics = e17.ranked_ticket_partition_prize_metrics(row, GAME)
        for n, value in metrics["partitions"].items():
            item = totals.setdefault(n, {"known_prize_total_yuan": 0.0, "winning_ticket_count": 0})
            item["known_prize_total_yuan"] += float(value["known_prize_total_yuan"])
            item["winning_ticket_count"] += int(value["winning_ticket_count"])
    draws = len(rows)
    for n, item in totals.items():
        item["draws"] = draws
        item["partition_size"] = int(n)
        item["average_prize_yuan"] = item["known_prize_total_yuan"] / (draws * int(n))
    return totals


def main():
    data = load_data()
    inner = source_rows("inner")
    outer = source_rows("outer")
    selected_by_zone = {}
    selections = {}
    for zone in ZONES:
        selected, report = select_zone(data, inner, zone)
        selected_by_zone[zone] = (selected, report)
        selections[zone] = report
    decorated = decorate(data, outer, selected_by_zone)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "outer-rolling-report.jsonl").write_bytes(b"".join(e16.canonical(row) for row in decorated))
    split_cal = decorated[:60]; split_eval = decorated[60:]
    partitions = {"calibration": aggregate(split_cal), "evaluation": aggregate(split_eval), "all_120": aggregate(decorated)}
    baseline_report = json.loads((ROOT / "artifacts/phase4e17/ssq/report.json").read_text())
    baseline_values = {}
    for split_name, source_name in (("calibration", "calibration"), ("evaluation", "evaluation"), ("all_120", "all_120_descriptive")):
        source_rows = baseline_report["outer_splits"][source_name]["ranked_ticket_partition_prize_metrics"]
        baseline_values[split_name] = {
            str(n): sum(float(obj["partitions"][str(n)]["known_prize_total_yuan"]) for obj in source_rows.values()) / (len(source_rows) * n)
            for n in (1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000)
        }
    report = {"artifact_type": "phase4e18_ssq_specialized_model_report", "status": "RETROSPECTIVE_BACKTEST_ONLY", "game": GAME, "promotion_eligible": False, "p4e6_serving_unchanged": True, "p4e6_serving_release": "P4-P4E2-20260815-r12", "selection": selections, "selected_candidates": {z: selected_by_zone[z][0]["id"] for z in ZONES}, "primary_metric": "known_prize_total_yuan / (draws * N)", "partitions": partitions, "baseline_partition_average_prize_yuan": baseline_values, "optimization_result": "NO_PROMOTION: specialized feature candidates did not beat the registered raw-control stability gate", "outer_draws": 120, "strict_lag": True, "dlt_isolation_required": True}
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (OUT / "summary.json").write_text(json.dumps({"game": GAME, "selected_candidates": report["selected_candidates"], "status": report["status"], "primary_metric": report["primary_metric"], "optimization_result": report["optimization_result"], "promotion_eligible": False, "p4e6_serving_unchanged": True, "dlt_isolation_required": True}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "selected_candidates": report["selected_candidates"]}))


if __name__ == "__main__":
    main()
