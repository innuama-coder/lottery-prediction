from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
import random
import subprocess
from pathlib import Path

from lottery_system.phase4.real_common import RULES, digest
from lottery_system.phase4.real_model import load_draws, write_once
from lottery_system.phase4e3.model import subset_probability, zone_distribution
from scripts.phase4e3.run_selection import fit_family


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DELIVERY = ROOT / "artifacts/phase-4e3/delivery-20260819"
SSQ_FILE_SHA256 = "05c1a5e74b3dee8205c202c96975dbb674a8810f3d7b307de1d1a3bfcdc71824"
SSQ_RECEIPT_SHA256 = "7194087e7aa949482afd51100bd685823898bcd20a4d812298fc9619c784a1c4"
R12_MANIFEST_SHA256 = "206c24136b65e36067ed6b25f2fa6018e9b07141518131de63dfd88f7b547920"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def verify_digest(value: dict[str, object], field: str) -> bool:
    payload = dict(value)
    claimed = payload.pop(field)
    return digest(payload) == claimed


def bootstrap(values, seed, iterations=4096, block_length=4):
    generator, means = random.Random(seed), []
    for _ in range(iterations):
        sample = []
        while len(sample) < len(values):
            start = generator.randrange(len(values))
            sample.extend(values[(start + offset) % len(values)] for offset in range(block_length))
        means.append(math.fsum(sample[: len(values)]) / len(values))
    means.sort()
    return {
        "ci95": [means[int(iterations * 0.025)], means[int(iterations * 0.975)]],
        "p": (1 + sum(value >= 0 for value in means)) / (iterations + 1),
    }


def independent_top_zone(distribution, limit=1000):
    heap = []
    for combo in itertools.combinations(range(1, int(distribution["n"]) + 1), int(distribution["k"])):
        probability = subset_probability(combo, distribution)
        entry = (probability, tuple(-number for number in combo), combo)
        if len(heap) < limit:
            heapq.heappush(heap, entry)
        elif entry[:2] > heap[0][:2]:
            heapq.heapreplace(heap, entry)
    rows = [(probability, combo) for probability, _, combo in heap]
    rows.sort(key=lambda row: row[1])
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows


def independent_top_product(front, back, limit=1000):
    rows = [(left * right, first, second) for left, first in front for right, second in back]
    rows.sort(key=lambda row: (row[1], row[2]))
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows[:limit]


def selected_shadow(selection):
    if selection["strongest_selection_candidate"]:
        return selection["strongest_selection_candidate"]
    rows = [(family, row) for family, row in selection["families"].items() if row.get("outer_folds")]
    return min(rows, key=lambda item: (item[1]["nested_mean_delta_joint_log_loss_vs_m0"], item[0]))[0]


def replay_shadow(game, delivery, draws):
    selection = load_json(delivery / f"selection/{game}-selection-receipt.json")
    family = selected_shadow(selection)
    candidate = selection["families"][family]
    fitted = [fit_family(game, draws, 176, zone, family, candidate["final_config"], candidate["feature_ids"])["model"] for zone in (0, 1)]
    distributions = [zone_distribution(game, draws, zone, fitted[zone]) for zone in (0, 1)]
    zones = [independent_top_zone(row) for row in distributions]
    expected = independent_top_product(*zones)
    observed = [json.loads(line) for line in (delivery / f"shadow/{game}-top1000.jsonl").read_text().splitlines()]
    replay_rows = [(row["joint_probability"], tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in observed]
    return {
        "row_count": len(observed), "unique_ticket_count": len({(front, back) for _, front, back in replay_rows}),
        "probabilities_monotone": all(left[0] >= right[0] > 0 for left, right in zip(replay_rows, replay_rows[1:])),
        "exact_replay_match": replay_rows == expected,
        "replay_digest": digest(replay_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery", type=Path, default=DEFAULT_DELIVERY)
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.delivery / "acceptance/independent-acceptance.json"
    checks = []

    def check(name, passed, details=None):
        checks.append({"check": name, "pass": bool(passed), **({"details": details} if details is not None else {})})

    contract = load_json(ROOT / "config/phase4e3/phase-contract.json")
    registry = load_json(ROOT / "config/phase4e3/experiment-registry.json")
    check("data_authority_sha256", file_sha(args.draws) == contract["data_authority"]["sha256"])
    check("registry_frozen_six_candidates", registry["status"] == "FROZEN" and registry["candidate_count_for_multiplicity"] == 6)
    ssq_path = args.delivery / "selection/ssq-selection-receipt.json"
    check("ssq_receipt_file_bytes_unchanged", file_sha(ssq_path) == SSQ_FILE_SHA256)
    check("r12_delivery_manifest_bytes_unchanged", file_sha(ROOT / "artifacts/phase-4/P4-P4E2-20260815-r12/manifest/delivery-manifest.json") == R12_MANIFEST_SHA256)

    replays = {}
    for game in ("ssq", "dlt"):
        selection = load_json(args.delivery / f"selection/{game}-selection-receipt.json")
        check(f"{game}_selection_embedded_digest", verify_digest(selection, "receipt_sha256"))
        check(f"{game}_selection_boundary", selection["selection_capability_last_position"] == 173
              and selection["purge_positions"] == [174, 175] and selection["report_only_first_position"] == 176
              and not selection["report_only_labels_read"])
        if game == "ssq":
            check("ssq_receipt_identity_unchanged", selection["receipt_sha256"] == SSQ_RECEIPT_SHA256)
            check("ssq_no_candidate_admitted", selection["eligible_for_report_only"] == [] and selection["strongest_selection_candidate"] is None)
        else:
            check("dlt_transition_only_admitted", selection["eligible_for_report_only"] == ["C03_TRANSITION"]
                  and selection["strongest_selection_candidate"] == "C03_TRANSITION")
            composite = selection["families"]["C06_GATED_COMPOSITE_NONLINEAR"]
            check("dlt_composite_duplicate_rejected", composite["rejection_reasons"] == ["no_preregistered_nonlinearity_eligible_composite_would_duplicate_source"]
                  and composite["rejected_before_model_selection"] and not composite["selection_direction_pass"])
        report = load_json(args.delivery / f"report/{game}-report-only.json")
        check(f"{game}_report_embedded_digest", verify_digest(report, "report_sha256"))
        check(f"{game}_report_rows_frozen", report["report_positions"] == [176, 200] and report["report_count"] == 24
              and [row["target_position"] for row in report["rows"]] == list(range(176, 200))
              and all(row["fold_role"] == "report_only" and not row["used_for_selection"] for row in report["rows"]))
        for comparator in ("r12", "m0"):
            values = [row[f"delta_joint_log_loss_vs_{comparator}"] for row in report["rows"]]
            replay = bootstrap(values, contract["evaluation"]["bootstrap"][f"seed_{game}"])
            evidence = report["comparisons"][comparator]
            check(f"{game}_{comparator}_bootstrap_replay", replay["ci95"] == evidence["ci95"] and replay["p"] == evidence["one_sided_p_mean_ge_zero"])
            check(f"{game}_{comparator}_holm_replay", evidence["holm_family_size"] == 6
                  and evidence["holm_adjusted_p"] == min(1.0, 6 * replay["p"]))
        check(f"{game}_promotion_gate_false", not report["preliminary_promotion_gate_pass"])
        for stem, field in (("candidate-audit", "audit_sha256"),):
            value = load_json(args.delivery / f"audit/{game}-{stem}.json")
            check(f"{game}_{stem}_digest", verify_digest(value, field))
        baseline_audit = load_json(args.delivery / f"audit/{game}-p4e2-audit.json")
        check(f"{game}_r12_audit_digest", verify_digest(baseline_audit, "audit_sha256"))
        snapshot = load_json(args.delivery / f"snapshots/{game}-features.json")
        check(f"{game}_snapshot_digest_and_prefix", verify_digest(snapshot, "snapshot_sha256")
              and snapshot["target_position"] == 200 and snapshot["maximum_source_position"] == 199
              and all(zone["max_source_position"] == 199 for zone in snapshot["zones"]))
        forecast = load_json(args.delivery / f"shadow/{game}-forecast.json")
        check(f"{game}_forecast_digest", verify_digest(forecast, "forecast_sha256"))
        check(f"{game}_top1000_file_hash", file_sha(args.delivery / f"shadow/{game}-top1000.jsonl") == forecast["top1000_sha256"])
        mutation = load_json(args.delivery / f"replay/{game}-mutation-evidence.json")
        check(f"{game}_mutation_digest_and_negative_cases", verify_digest(mutation, "evidence_sha256")
              and mutation["future_and_target_mutation_invariant_pass"] and mutation["strict_prefix_mutation_detected_pass"])
        card = load_json(args.delivery / f"model-cards/{game}-model-card.json")
        check(f"{game}_model_card_digest_and_shadow_only", verify_digest(card, "model_card_sha256")
              and card["intended_use"] == "research_shadow_only" and not card["serving_eligible"])
        replays[game] = replay_shadow(game, args.delivery, load_draws(args.draws, game))
        check(f"{game}_independent_top1000_exact_replay", all((replays[game][key] for key in ("exact_replay_match", "probabilities_monotone")))
              and replays[game]["row_count"] == replays[game]["unique_ticket_count"] == 1000, replays[game])

    inventory = load_json(args.delivery / "inventory/prior-release-byte-inventory.json")
    check("prior_release_inventory_digest", verify_digest(inventory, "inventory_check_sha256"))
    check("all_prior_release_bytes_unchanged", inventory["all_prior_release_bytes_unchanged"]
          and inventory["r12_git_tree_match"] and not inventory["changed_tracked_paths"] and not inventory["untracked_prior_release_paths"])
    decision = load_json(args.delivery / "decision/final-decision.json")
    check("decision_digest", verify_digest(decision, "decision_sha256"))
    check("honest_terminal_status", decision["status"] == "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION"
          and not decision["promotion_decision"] and decision["immutable_r12_remains_serving"]
          and not decision["probability_spread_used_for_promotion"])
    manifest = load_json(args.delivery / "manifest/delivery-manifest.json")
    check("manifest_embedded_digest", verify_digest(manifest, "manifest_sha256"))
    manifest_matches = all(
        (args.delivery / entry["path"]).is_file()
        and (args.delivery / entry["path"]).stat().st_size == entry["size"]
        and file_sha(args.delivery / entry["path"]) == entry["sha256"]
        for entry in manifest["entries"]
    )
    check("manifest_all_entries_match", manifest_matches and len(manifest["entries"]) == manifest["entry_count"])
    full_tests = load_json(args.delivery / "acceptance/full-test-receipt.json")
    check("full_current_phase4_suite_receipt", verify_digest(full_tests, "receipt_sha256")
          and full_tests["status"] == "PASS" and full_tests["suite_scope"] == "complete_current_tests_phase4"
          and full_tests["return_code"] == 0 and full_tests["test_count"] > 0 and full_tests["clean_worktree_before_run"])
    check("branch_name", subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() == contract["branch"])
    status = "PASS" if all(row["pass"] for row in checks) else "FAIL"
    result = {
        "artifact_type": "phase4e3_independent_acceptance", "status": status,
        "terminal_status": decision["status"], "check_count": len(checks),
        "pass_count": sum(row["pass"] for row in checks), "checks": checks,
        "independent_shadow_replays": replays,
    }
    result["acceptance_sha256"] = digest(result)
    write_once(output, result)
    print(status, result["pass_count"], result["check_count"], result["acceptance_sha256"])
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
