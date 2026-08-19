#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

from lottery_system.phase4e4.data import canonical, load_jsonl, make_draw, sha256_bytes, sha256_file
from lottery_system.phase4e4.model import FAMILIES, fit_model


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase-4e4"
DELIVERY = BASE / "delivery-20260819"
ACCEPTANCE = BASE / "acceptance-20260819"


def elementary(weights: list[float], k: int) -> float:
    coefficients = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1):
            coefficients[order] += weight * coefficients[order - 1]
    return coefficients[k]


def independent_top(model: dict[str, object]) -> list[dict[str, object]]:
    zones = []
    for fitted in model["zones"]:
        context = fitted["context"]
        features = context["number_features"]
        coefficients = fitted["coefficients"]
        k = int(context["k"])
        number_scores = [math.fsum(value * coefficient for value, coefficient in zip(row, coefficients)) / math.sqrt(k) for row in features]
        weights = [math.exp(max(-8.0, min(8.0, score))) for score in number_scores]
        normalizer = elementary(weights, k)
        rows = [(math.prod(weights[value - 1] for value in combo) / normalizer, combo)
                for combo in itertools.combinations(range(1, int(context["n"]) + 1), k)]
        rows.sort(key=lambda row: row[1])
        rows.sort(key=lambda row: row[0], reverse=True)
        zones.append(rows[:1000])
    candidates = [(front_probability * back_probability, front, back)
                  for front_probability, front in zones[0] for back_probability, back in zones[1]]
    candidates.sort(key=lambda row: (row[1], row[2]))
    candidates.sort(key=lambda row: row[0], reverse=True)
    return [{"rank": rank, "front": list(front), "back": list(back), "joint_probability": probability}
            for rank, (probability, front, back) in enumerate(candidates[:1000], 1)]


def verify_manifest() -> list[str]:
    failures = []
    manifest = json.loads((DELIVERY / "core-manifest.json").read_text(encoding="utf-8"))
    if manifest["manifest_sha256"] != sha256_bytes(canonical(manifest["files"])):
        failures.append("core_manifest_digest")
    for row in manifest["files"]:
        path = DELIVERY / row["path"]
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            failures.append(f"core_manifest_file:{row['path']}")
    inventory = json.loads((DELIVERY / "audits/prior-release-byte-inventory.json").read_text(encoding="utf-8"))
    for protected in inventory.values():
        rows = []
        for row in protected["files"]:
            path = ROOT / row["path"]
            if not path.is_file() or sha256_file(path) != row["sha256"]:
                failures.append(f"protected_file:{row['path']}")
            rows.append(row)
        if sha256_bytes(canonical(rows)) != protected["inventory_sha256"]:
            failures.append(f"protected_inventory:{protected['root']}")
    return failures


def main() -> int:
    failures = verify_manifest()
    replay = {}
    mutation = {}
    hypothesis_ids = []
    for game in ("ssq", "dlt"):
        selection_path = BASE / f"selection-20260819/{game}-selection-receipt.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selection_hash = selection.pop("receipt_sha256")
        if sha256_bytes(canonical(selection)) != selection_hash or selection["candidate_count"] != 7 or selection["report_labels_read"]:
            failures.append(f"selection_receipt:{game}")
        report_path = BASE / f"report-20260819/{game}-report-receipt.json"
        report_receipt = json.loads(report_path.read_text(encoding="utf-8"))
        report_hash = report_receipt.pop("receipt_sha256")
        if sha256_bytes(canonical(report_receipt)) != report_hash or report_receipt["promoted_candidates"]:
            failures.append(f"report_receipt:{game}")
        hypothesis_ids.extend(row["hypothesis_id"] for row in report_receipt["game_hypotheses"])
        prefix = load_jsonl(BASE / f"data-20260819/selection-prefix/{game}.jsonl", game)
        report = load_jsonl(BASE / f"data-20260819/sealed-report/{game}.jsonl", game)
        draws = prefix + report
        summary = json.loads((DELIVERY / f"top1000/{game}-summary.json").read_text(encoding="utf-8"))
        family = summary["candidate_id"]
        config = selection["candidates"][family]["final_config"]
        model = fit_model(game, draws, len(prefix), family, config)
        if any("number_features" not in zone["context"] for zone in model["zones"]):
            failures.append(f"independent_top_not_additive:{game}")
        else:
            expected = [json.loads(line) for line in (DELIVERY / f"top1000/{game}-{family}.jsonl").read_text(encoding="utf-8").splitlines()]
            actual = independent_top(model)
            exact_tickets = [(row["front"], row["back"], row["rank"]) for row in actual] == [(row["front"], row["back"], row["rank"]) for row in expected]
            maximum_error = max(abs(left["joint_probability"] - right["joint_probability"]) for left, right in zip(actual, expected))
            replay[game] = {"candidate_id": family, "row_count": len(actual), "exact_ticket_and_rank_match": exact_tickets,
                            "maximum_probability_absolute_error": maximum_error, "pass": exact_tickets and maximum_error <= 1e-20}
            if not replay[game]["pass"]:
                failures.append(f"independent_top_replay:{game}")
        changed = list(draws)
        target = changed[len(prefix)]
        changed[len(prefix)] = make_draw(game, target.issue, target.draw_date,
                                         tuple(range(1, len(target.front) + 1)), tuple(range(1, len(target.back) + 1)), "independent-mutation")
        replay_model = fit_model(game, changed, len(prefix), family, config)
        mutation[game] = {"future_target_mutated": target.source_record_sha256 != changed[len(prefix)].source_record_sha256,
                          "fit_and_prediction_state_unchanged": model == replay_model,
                          "maximum_training_label_position": model["maximum_training_label_position"], "report_first_position": len(prefix)}
        if not mutation[game]["future_target_mutated"] or not mutation[game]["fit_and_prediction_state_unchanged"]:
            failures.append(f"future_mutation:{game}")
        if game == "ssq" and selection["promotion_authority"] is not False:
            failures.append("ssq_promotion_authority")
    if len(hypothesis_ids) != 84 or len(set(hypothesis_ids)) != 84:
        failures.append("holm_family")
    full_tests = json.loads((ACCEPTANCE / "full-current-phase4-tests.json").read_text(encoding="utf-8"))
    if full_tests["status"] != "PASS" or not all(row["exit_code"] == 0 for row in full_tests["results"]):
        failures.append("full_current_phase4_tests")
    payload = {"artifact_type": "phase4e4_independent_acceptance", "independent_top1000_replay": replay,
               "negative_future_and_target_mutation": mutation, "holm_hypothesis_count": len(hypothesis_ids),
               "blocking_findings": failures, "serving_release": "P4-P4E2-20260815-r12",
               "terminal_state": "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION", "status": "PASS" if not failures else "FAIL"}
    payload["receipt_sha256"] = sha256_bytes(canonical(payload))
    output = ACCEPTANCE / "independent-acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(payload))
    print(json.dumps(payload, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
