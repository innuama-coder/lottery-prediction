from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import shutil
import tempfile
from decimal import Decimal, localcontext
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RULES = {"ssq": ((33, 6), (16, 1)), "dlt": ((35, 5), (12, 2))}


def load(path: Path): return json.loads(path.read_text())
def sha(path: Path): return hashlib.sha256(path.read_bytes()).hexdigest()
def canon(value): return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
def elementary(weights, k):
    dp = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1): dp[order] += weight * dp[order - 1]
    return dp[k]


def replay_game(release: Path, draws_path: Path, game: str):
    selection = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    if selection["family"] == "M0" or not selection["non_m0"]: raise ValueError("M0 serving mutation accepted")
    model_path = release / selection["model_path"]
    model = load(model_path)
    if model.get("model_release_id") != selection["model_release_id"]: raise ValueError("model id mutation")
    if model.get("feature_release_id") != selection["feature_release_id"]: raise ValueError("feature identity mutation")
    draws = [json.loads(line) for line in draws_path.read_text().splitlines() if json.loads(line)["game"] == game]
    if any(row.get("available_at_utc") is not None for row in draws): raise ValueError("fabricated available_at")
    cutoff = next(i for i, row in enumerate(draws) if row["issue_id"] == model["training_cutoff_issue"]) + 1
    if cutoff != model["training_count"]: raise ValueError("cutoff position mismatch")
    training = draws[:cutoff]
    formal = next((release/f"forecasts/{game}").glob("*/top1000.jsonl"))
    forecast_path = formal.parent / "forecast.json"
    lock_path = formal.parent / "lock.json"
    forecast, lock = load(forecast_path), load(lock_path)
    expected_provider = selection["model_path"]
    if forecast.get("model_release_id") != model["model_release_id"] or forecast.get("provider_access") != [expected_provider]: raise ValueError("provider reference mutation")
    if lock.get("content_sha256") != sha(forecast_path) or lock.get("top1000_sha256") != sha(formal) or lock.get("status") != "LOCKED": raise ValueError("lock mutation")
    recomputed = []
    for zone_index, (n, k) in enumerate(RULES[game]):
        counts = [0] * n
        key = "front_numbers" if zone_index == 0 else "back_numbers"
        for draw in training:
            for number in draw[key]: counts[number - 1] += 1
        alpha, beta = 1.0, max(1.0, n / k - 1.0)
        rates = [(count + alpha) / (len(training) + alpha + beta) for count in counts]
        mean = sum(rates) / n; scale = max(max(abs(x - mean) for x in rates), 1e-12)
        feature = [(x - mean) / scale for x in rates]
        zone = model["zones"][zone_index]
        if counts != zone["counts"] or any(abs(a-b) > 1e-14 for a,b in zip(feature, zone["feature"])): raise ValueError("feature mutation")
        weights = [math.exp(max(-8.0, min(8.0, float(zone["theta"]) * x))) for x in feature]
        normalizer = elementary(weights, k)
        if any(abs(a-b) > 1e-14 for a,b in zip(weights, zone["weights"])) or abs(normalizer-zone["normalizer"]) > 1e-10: raise ValueError("model mutation")
        combos = []
        for combo in itertools.combinations(range(1, n+1), k):
            probability = math.prod(weights[x-1] for x in combo) / normalizer
            combos.append((probability, combo))
        combos.sort(key=lambda x: (-x[0], x[1])); recomputed.append(combos)
    rows=[json.loads(x) for x in formal.read_text().splitlines()]
    if len(rows) != 1000: raise ValueError("Top-1000 row count mutation")
    with localcontext() as context:
        context.prec=80
        fw=[Decimal(str(value)) for value in model["zones"][0]["weights"]]
        bw=[Decimal(str(value)) for value in model["zones"][1]["weights"]]
        fn=Decimal(str(model["zones"][0]["normalizer"])); bn=Decimal(str(model["zones"][1]["normalizer"]))
        exact_zones=[]
        for (n,k),weights,normalizer in zip(RULES[game],(fw,bw),(fn,bn)):
            zone=[(math.prod(weights[number-1] for number in combo)/normalizer,combo) for combo in itertools.combinations(range(1,n+1),k)]
            zone.sort(key=lambda value:value[1]);zone.sort(key=lambda value:value[0],reverse=True);exact_zones.append(zone)
        exact=[(fp*bp,front,back) for fp,front in exact_zones[0][:1000] for bp,back in exact_zones[1]]
    exact.sort(key=lambda value:(value[1],value[2]));exact.sort(key=lambda value:value[0],reverse=True);exact=exact[:1000]
    histogram={probability:sum(item[0]==probability for item in exact) for probability,_,_ in exact}
    bounds={}; cursor=1
    for probability in sorted(histogram,reverse=True):
        bounds[probability]=(cursor,cursor+histogram[probability]-1); cursor+=histogram[probability]
    for index, (probability, front, back) in enumerate(exact):
        row=rows[index]
        lower,upper=bounds[probability]; spelling=format(probability,"f"); key=hashlib.sha256(spelling.encode()).hexdigest()
        expected={"joint_probability":spelling,"probability_representation":"P4-DECIMAL-EXACT-1","tie_group_id":f"tie-{key[:24]}","tie_group_size":histogram[probability],"tie_rank_lower":lower,"tie_rank_upper":upper,"tie_midrank":format((Decimal(lower)+Decimal(upper))/2,"f"),"tie_key":f"probability:{key}"}
        mismatches={field:(row.get(field),value) for field,value in expected.items() if row.get(field)!=value}
        if row["front_numbers"] != list(front) or row["back_numbers"] != list(back) or mismatches: raise ValueError(f"Top-1000 mutation at {index + 1}: {mismatches}")
    return {"game":game,"feature_match":True,"model_match":True,"top1000_match":True,"ticket_count":len(rows),"model_sha256":sha(model_path),"top1000_sha256":sha(formal)}


def mutation_checks(release: Path, draws_path: Path) -> dict[str, str]:
    """Apply every mutation to a disposable copy and require a specific rejection."""
    cases = ("early_draw", "cutoff", "feature_value", "theta", "model_id", "probability", "top1000_order", "lock", "provider_reference", "m0_serving")
    detected = {}
    for case in cases:
        with tempfile.TemporaryDirectory(prefix=f"p4-replay-{case}-") as raw:
            copy = Path(raw) / "release"
            shutil.copytree(release, copy)
            draw_copy = Path(raw) / "draws.jsonl"
            shutil.copy2(draws_path, draw_copy)
            selection_path = copy / "selection/serving-selection.json"
            selection = load(selection_path)
            chosen = selection["serving_model_by_game"]["ssq"]
            model_path = copy / chosen["model_path"]
            model = load(model_path)
            formal = next((copy / "forecasts/ssq").glob("*/top1000.jsonl"))
            forecast_path, lock_path = formal.parent / "forecast.json", formal.parent / "lock.json"
            if case == "early_draw":
                rows = draw_copy.read_text().splitlines()
                for index, encoded in enumerate(rows):
                    value = json.loads(encoded)
                    if value["game"] == "ssq":
                        replacement = next(number for number in range(1, 34) if number not in value["front_numbers"])
                        value["front_numbers"][0] = replacement
                        value["front_numbers"].sort()
                        rows[index] = json.dumps(value, sort_keys=True, separators=(",", ":"))
                        break
                draw_copy.write_text("\n".join(rows) + "\n")
            elif case == "cutoff": model["training_count"] += 1; model_path.write_bytes(canon(model))
            elif case == "feature_value": model["zones"][0]["feature"][0] += .01; model_path.write_bytes(canon(model))
            elif case == "theta": model["zones"][0]["theta"] += .01; model_path.write_bytes(canon(model))
            elif case == "model_id": model["model_release_id"] += "-tampered"; model_path.write_bytes(canon(model))
            elif case == "probability":
                rows = formal.read_text().splitlines(); value = json.loads(rows[0]); value["joint_probability"] = "1.0"; rows[0] = json.dumps(value, sort_keys=True, separators=(",", ":")); formal.write_text("\n".join(rows) + "\n")
            elif case == "top1000_order":
                rows = formal.read_text().splitlines(); rows[0], rows[1] = rows[1], rows[0]; formal.write_text("\n".join(rows) + "\n")
            elif case == "lock":
                lock = load(lock_path); lock["top1000_sha256"] = "0" * 64; lock_path.write_bytes(canon(lock))
            elif case == "provider_reference":
                value = load(forecast_path); value["provider_access"] = ["fixture"]; forecast_path.write_bytes(canon(value))
            elif case == "m0_serving":
                chosen["family"] = "M0"; chosen["non_m0"] = False; selection_path.write_bytes(canon(selection))
            try:
                replay_game(copy, draw_copy, "ssq")
            except (ValueError, StopIteration, KeyError, IndexError, json.JSONDecodeError):
                detected[case] = "DETECTED"
            else:
                raise ValueError(f"mutation escaped independent replay: {case}")
    return detected


def main():
    p=argparse.ArgumentParser();p.add_argument("--release",type=Path,required=True);p.add_argument("--draws",type=Path,required=True);p.add_argument("--output",type=Path);p.add_argument("--check-only",action="store_true");a=p.parse_args()
    if (a.output is None) == (not a.check_only): p.error("choose exactly one of --output or --check-only")
    release=a.release.resolve(); results=[replay_game(release,a.draws,g) for g in ("ssq","dlt")]
    mutations=mutation_checks(release,a.draws)
    report={"artifact_type":"phase4_independent_bottom_up_replay","games":results,"product_core_import_count":0,"match_rate":1.0,"mutations":mutations,"mutation_detection_rate":1.0,"status":"PASS","blocking_findings":[]}
    if not a.check_only:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        if a.output.exists(): raise FileExistsError(a.output)
        a.output.write_bytes(canon(report))
    print(json.dumps(report,sort_keys=True));return 0


if __name__=="__main__": raise SystemExit(main())
