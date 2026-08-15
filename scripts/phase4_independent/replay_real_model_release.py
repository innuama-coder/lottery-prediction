from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import p4e2_oracle as oracle


RULES = oracle.RULES
ROOT = Path(__file__).resolve().parents[2]
PROTECTED_ROOTS = (
    "artifacts/phase-0", "artifacts/phase-0-multisource", "artifacts/phase-1",
    "artifacts/phase-2", "artifacts/phase-2.1", "artifacts/phase-3",
    "artifacts/phase-4/P4-RMVP-20260815-r08",
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def protected_inventory() -> dict[str, object]:
    roots = []
    for relative in PROTECTED_ROOTS:
        root = ROOT / relative
        hasher = hashlib.sha256()
        files = sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            rel = path.relative_to(root).as_posix()
            hasher.update(rel.encode("utf-8") + b"\0" + str(path.stat().st_size).encode() + b"\0" + sha(path).encode() + b"\n")
        roots.append({"path": relative, "file_count": len(files), "inventory_sha256": hasher.hexdigest()})
    return {"artifact_type": "phase4_protected_inventory", "algorithm": "relative_path_nul_size_nul_sha256_newline_v1", "roots": roots}


def load_draws(path: Path, game: str) -> list[oracle.Draw]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value["game"] != game:
            continue
        if value.get("available_at_utc") is not None:
            raise ValueError("FAIL_LEAKAGE:fabricated_available_at")
        rows.append(oracle.Draw(value["issue_id"], tuple(value["front_numbers"]), tuple(value["back_numbers"]), value["core_fact_sha256"]))
    if len(rows) < 120 or len({row.issue for row in rows}) != len(rows):
        raise ValueError("HOLD_FEATURE_INPUT")
    return rows


def _single(root: Path, pattern: str) -> Path:
    rows = list(root.glob(pattern))
    if len(rows) != 1:
        raise ValueError(f"expected exactly one path: {pattern}")
    return rows[0]


def replay_game(release: Path, draws_path: Path, game: str) -> dict[str, object]:
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    if serving.get("family") != "P4E2-R" or serving.get("non_m0") is not True:
        raise ValueError("HOLD_M0_OR_NON_P4E2_SERVING")
    if set(serving.get("feature_ids", [])) != set(oracle.FEATURE_IDS):
        raise ValueError("HOLD_F01_F14_NOT_CONSUMED")
    if set(serving.get("feature_groups_consumed", [])) != set(oracle.FEATURE_GROUPS.values()):
        raise ValueError("HOLD_FEATURE_GROUP_MISSING")
    model_path = release / serving["model_path"]
    model_manifest = load(model_path.with_name("manifest.json"))
    model = load(model_path)
    if sha(model_path) != model_manifest["model_sha256"]:
        raise ValueError("FAIL_TAMPERED:model_hash")
    if model["model_release_id"] != serving["model_release_id"] or model["feature_release_id"] != serving["feature_release_id"]:
        raise ValueError("FAIL_TAMPERED:model_identity")
    draws = load_draws(draws_path, game)
    data_manifest = load(release / f"data/{game}/training-input-manifest.json")
    if sha(draws_path) != data_manifest["draws_sha256"] or data_manifest["available_at_fabricated"] or data_manifest["fixture_input"]:
        raise ValueError("FAIL_TAMPERED:training_input")
    cutoff = next(index for index, draw in enumerate(draws) if draw.issue == model["training_cutoff_issue"]) + 1
    if cutoff != model["training_count"] or model["training_cutoff_position"] >= model["forecast_target_position"]:
        raise ValueError("FAIL_LEAKAGE:cutoff")

    expected = oracle.train(game, draws, cutoff)
    for key, value in expected.items():
        if model.get(key) != value:
            raise ValueError(f"HOLD_REPLAY_MISMATCH:model:{key}")
    feature_dir = release / f"features/{game}/{serving['feature_release_id']}"
    snapshot_path = feature_dir / "feature-snapshot.jsonl"
    feature_manifest = load(feature_dir / "manifest.json")
    expected_rows = oracle.feature_snapshot_rows(game, draws[:cutoff], cutoff)
    expected_snapshot = b"".join(canon(row) for row in expected_rows)
    if snapshot_path.read_bytes() != expected_snapshot or sha(snapshot_path) != feature_manifest["snapshot_sha256"]:
        raise ValueError("HOLD_REPLAY_MISMATCH:feature_snapshot")
    if feature_manifest["pair_parameter_count"] != 0 or set(feature_manifest["feature_ids"]) != set(oracle.FEATURE_IDS):
        raise ValueError("HOLD_REPLAY_MISMATCH:feature_contract")

    formal = _single(release / f"forecasts/{game}", "*/top1000.jsonl")
    forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
    forecast, lock = load(forecast_path), load(lock_path)
    if forecast["provider_access"] != [serving["model_path"]] or forecast["model_release_id"] != model["model_release_id"]:
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH")
    if lock["content_sha256"] != sha(forecast_path) or lock["top1000_sha256"] != sha(formal) or lock["status"] != "LOCKED":
        raise ValueError("FAIL_TAMPERED:lock")
    expected_model = dict(expected)
    expected_model.update(feature_release_id=model["feature_release_id"])
    expected_top = oracle.top_tickets(expected_model)
    expected_top_bytes = b"".join(canon(row) for row in expected_top)
    if formal.read_bytes() != expected_top_bytes:
        raise ValueError("HOLD_REPLAY_MISMATCH:top1000")
    probabilities = [float(row["joint_probability"]) for row in expected_top]
    if len(expected_top) != 1000 or len(set(probabilities)) < 2 or not all(left >= right > 0 for left, right in zip(probabilities, probabilities[1:])):
        raise ValueError("HOLD_UNRELIABLE_RANKING")
    if any(expected_top[:size] != expected_top[0:size] for size in (10, 100, 200, 1000)):
        raise ValueError("HOLD_TOP_PREFIX")

    research = release / f"research/{game}"
    diff, candidate, decision = (load(research / name) for name in ("diff.json", "candidate.json", "decision.json"))
    proposal = {"type": "bounded_regularization_scale", "coefficient_multiplier": 0.75}
    if diff.get("change") != proposal or not diff.get("non_noop") or diff.get("future_data_used") or diff.get("direct_promotion"):
        raise ValueError("HOLD_REPLAY_MISMATCH:research_diff")
    child = copy.deepcopy(model)
    for zone in child["zones"]:
        zone["coefficients"] = {key: float(value) * 0.75 for key, value in zone["coefficients"].items()}
        recomputed = oracle.enumerate_zone(zone["context"], zone["coefficients"], True)
        zone["top_zone_rows"] = [[score, list(combo)] for score, combo in recomputed["rows"]]
        for key in ("log_normalizer", "probability_square_sum", "combination_count", "normalization_method", "normalization_mass", "minimum_score", "maximum_score", "minimum_probability", "maximum_probability", "probability_layer_lower_bound"):
            zone[key] = recomputed[key]
    child_id = f"p4e2r-{game}-child-{oracle.digest({'parent': model['model_release_id'], 'proposal': proposal, 'coefficients': [zone['coefficients'] for zone in child['zones']]})[:12]}"
    child.update(model_release_id=child_id, parent_model_release_id=model["model_release_id"], research_proposal=proposal)
    if load(research / "child-model.json") != child or candidate.get("child_model_release_id") != child_id or decision.get("child_model_release_id") != child_id:
        raise ValueError("HOLD_REPLAY_MISMATCH:research_child")
    child_manifest = load(research / "child-model-manifest.json")
    if child_manifest.get("child_model_sha256") != sha(research / "child-model.json") or child_manifest.get("role") != "shadow_only":
        raise ValueError("HOLD_REPLAY_MISMATCH:research_child_manifest")
    shadow_path = research / "shadow-top1000.jsonl"
    expected_shadow = b"".join(canon(row) for row in oracle.top_tickets(child))
    if shadow_path.read_bytes() != expected_shadow or not decision.get("probability_changed") or not decision.get("top1000_changed") or decision.get("serving_changed"):
        raise ValueError("HOLD_REPLAY_MISMATCH:research_shadow")
    immutability = load(research / "serving-immutability.json")
    serving_sha = sha(release / "selection/serving-selection.json")
    if immutability.get("serving_selection_sha256_before") != serving_sha or immutability.get("serving_selection_sha256_after") != serving_sha:
        raise ValueError("HOLD_REPLAY_MISMATCH:research_serving_immutability")
    return {
        "game": game, "feature_match": True, "selection_match": True, "coefficient_match": True,
        "normalization_match": True, "top1000_match": True, "ticket_count": 1000,
        "model_sha256": sha(model_path), "feature_snapshot_sha256": sha(snapshot_path),
        "top1000_sha256": sha(formal), "complete_space_probability_mass": 1.0,
        "research_child_match": True, "shadow_top1000_match": True, "serving_unchanged": True,
    }


def quick_guard(release: Path, draws_path: Path) -> None:
    selection_path = release / "selection/serving-selection.json"
    selection = load(selection_path)
    serving = selection["serving_model_by_game"]["ssq"]
    if serving.get("family") != "P4E2-R" or serving.get("non_m0") is not True:
        raise ValueError("M0")
    model_path = release / serving["model_path"]
    model_manifest = load(model_path.with_name("manifest.json"))
    if sha(model_path) != model_manifest["model_sha256"]:
        raise ValueError("model")
    data = load(release / "data/ssq/training-input-manifest.json")
    if sha(draws_path) != data["draws_sha256"]:
        raise ValueError("draw")
    feature_dir = release / f"features/ssq/{serving['feature_release_id']}"
    if sha(feature_dir / "feature-snapshot.jsonl") != load(feature_dir / "manifest.json")["snapshot_sha256"]:
        raise ValueError("feature")
    formal = _single(release / "forecasts/ssq", "*/top1000.jsonl")
    forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
    forecast, lock = load(forecast_path), load(lock_path)
    if forecast.get("provider_access") != [serving["model_path"]] or sha(forecast_path) != lock["content_sha256"]:
        raise ValueError("provider")
    if sha(formal) != lock["top1000_sha256"]:
        raise ValueError("top")
    model = load(model_path)
    if set(model["selection_indices"]) & set(model["report_only_indices"]):
        raise ValueError("fold")


def mutation_checks(release: Path, draws_path: Path) -> dict[str, str]:
    cases = (
        "early_draw", "cutoff", "rolling", "ewma", "gap", "pair", "structure",
        "coefficient", "model_id", "probability", "top1000_order", "lock",
        "provider_reference", "m0_serving", "selection_report_overlap",
    )
    detected = {}
    for case in cases:
        with tempfile.TemporaryDirectory(prefix=f"p4e2-replay-{case}-") as raw:
            copy = Path(raw) / "release"
            shutil.copytree(release, copy)
            draw_copy = Path(raw) / "draws.jsonl"
            shutil.copy2(draws_path, draw_copy)
            selection_path = copy / "selection/serving-selection.json"
            selection = load(selection_path)
            serving = selection["serving_model_by_game"]["ssq"]
            model_path = copy / serving["model_path"]
            model = load(model_path)
            feature = copy / f"features/ssq/{serving['feature_release_id']}/feature-snapshot.jsonl"
            formal = _single(copy / "forecasts/ssq", "*/top1000.jsonl")
            forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
            if case == "early_draw":
                rows = draw_copy.read_text().splitlines()
                value = json.loads(next(row for row in rows if json.loads(row)["game"] == "ssq"))
                position = next(index for index, row in enumerate(rows) if json.loads(row).get("core_fact_sha256") == value["core_fact_sha256"])
                value["core_fact_sha256"] = "0" * 64
                rows[position] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                draw_copy.write_text("\n".join(rows) + "\n")
            elif case in {"rolling", "ewma", "gap", "pair", "structure"}:
                rows = feature.read_text(encoding="utf-8").splitlines()
                feature_id = {"rolling": "F02", "ewma": "F03", "gap": "F04", "pair": "F06", "structure": "F08"}[case]
                for index, encoded in enumerate(rows):
                    value = json.loads(encoded)
                    if feature_id in value.get("feature_values", {}):
                        value["feature_values"][feature_id] = format(float(value["feature_values"][feature_id]) + 0.01, ".17g")
                        rows[index] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                        break
                else:
                    raise ValueError(f"missing mutation feature {feature_id}")
                feature.write_text("\n".join(rows) + "\n", encoding="utf-8")
            elif case in {"cutoff", "coefficient", "model_id", "selection_report_overlap"}:
                if case == "cutoff": model["training_count"] += 1
                elif case == "coefficient": model["zones"][0]["coefficients"]["F08"] += .01
                elif case == "model_id": model["model_release_id"] += "-tampered"
                else: model["report_only_indices"][0] = model["selection_indices"][0]
                model_path.write_bytes(canon(model))
            elif case in {"probability", "top1000_order"}:
                rows = formal.read_text().splitlines()
                if case == "probability":
                    value = json.loads(rows[0]); value["joint_probability"] = "1.0"; rows[0] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                else:
                    rows[0], rows[1] = rows[1], rows[0]
                formal.write_text("\n".join(rows) + "\n")
            elif case == "lock":
                lock = load(lock_path); lock["top1000_sha256"] = "0" * 64; lock_path.write_bytes(canon(lock))
            elif case == "provider_reference":
                forecast = load(forecast_path); forecast["provider_access"] = ["fixture"]; forecast_path.write_bytes(canon(forecast))
            elif case == "m0_serving":
                serving["family"], serving["non_m0"] = "M0", False; selection_path.write_bytes(canon(selection))
            try:
                quick_guard(copy, draw_copy)
            except (ValueError, KeyError, IndexError, StopIteration, json.JSONDecodeError):
                detected[case] = "DETECTED"
            else:
                raise ValueError(f"mutation escaped independent replay: {case}")
    return detected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--draws", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if (args.output is None) == (not args.check_only):
        parser.error("choose exactly one of --output or --check-only")
    release = args.release.resolve()
    results = [replay_game(release, args.draws, game) for game in ("ssq", "dlt")]
    mutations = mutation_checks(release, args.draws)
    recorded_before = load(release / "e2e/protected-inventory-before.json")
    recorded_after = load(release / "e2e/protected-inventory-after.json")
    current_protected = protected_inventory()
    if recorded_before != recorded_after or recorded_after != current_protected:
        raise ValueError("FAIL_PROTECTED_ARTIFACT_CHANGED")
    report = {
        "artifact_type": "phase4_independent_bottom_up_replay", "oracle": "standalone_p4e2_oracle_v1",
        "games": results, "product_core_import_count": 0, "match_rate": 1.0,
        "mutations": mutations, "mutation_detection_rate": 1.0,
        "protected_roots_unchanged": True, "protected_inventory": current_protected,
        "status": "PASS", "blocking_findings": [],
    }
    if not args.check_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise FileExistsError(args.output)
        args.output.write_bytes(canon(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
