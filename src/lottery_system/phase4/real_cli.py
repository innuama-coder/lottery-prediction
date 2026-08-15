from __future__ import annotations

import argparse
import json
from pathlib import Path

from .real_model import load_draws, train
from .real_ops import forecast_and_lock, research_release, schedule_release, score_release


def load(path: Path): return json.loads(path.read_text())


def main(argv=None):
    p=argparse.ArgumentParser(prog="lottery phase4"); sub=p.add_subparsers(dest="verb",required=True)
    inspect=sub.add_parser("inspect");inspect.add_argument("--release",type=Path,required=True);inspect.add_argument("--game",choices=("ssq","dlt"),required=True)
    validate=sub.add_parser("validate");validate.add_argument("--release",type=Path,required=True)
    replay=sub.add_parser("replay");replay.add_argument("--release",type=Path,required=True);replay.add_argument("--independent",action="store_true",required=True)
    train_p=sub.add_parser("train");train_p.add_argument("--game",choices=("ssq","dlt"),required=True);train_p.add_argument("--phase1-draws",type=Path,required=True);train_p.add_argument("--cutoff");train_p.add_argument("--output",type=Path,required=True)
    forecast=sub.add_parser("forecast");forecast.add_argument("--model-release",type=Path,required=True);forecast.add_argument("--target-issue",required=True);forecast.add_argument("--top-k",type=int,default=1000);forecast.add_argument("--lock",action="store_true",required=True)
    for name in ("score","research","schedule"):
        q=sub.add_parser(name);q.add_argument("--release",type=Path,required=True);q.add_argument("--game",choices=("ssq","dlt"))
        if name == "schedule":
            q.add_argument("--fail-after",choices=("prepare","forecast_lock","official_result_ingest","unlock_score","research_shadow"));q.add_argument("--cycle-id",default="formal-cycle-v1")
    a=p.parse_args()
    if a.verb=="inspect":
        serving=load(a.release/"selection/serving-selection.json")["serving_model_by_game"][a.game]
        forecast_path=next((a.release/f"forecasts/{a.game}").glob("*/forecast.json")); result={"status":"PASS","serving":serving,"forecast":load(forecast_path)}
    elif a.verb=="validate":
        serving=load(a.release/"selection/serving-selection.json")["serving_model_by_game"]
        result={"status":"PASS" if all(v["family"]!="M0" and v["non_m0"] for v in serving.values()) else "HOLD","serving_model_by_game":serving}
    elif a.verb=="replay": result=load(a.release/"replay/replay-report.json")
    elif a.verb=="train":
        draws=load_draws(a.phase1_draws,a.game); cutoff=len(draws) if not a.cutoff else next(i for i,d in enumerate(draws) if d.issue==a.cutoff)+1
        result=train(a.game,draws,cutoff);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,sort_keys=True,separators=(",",":"))+"\n")
    elif a.verb=="forecast": result=forecast_and_lock(a.model_release,a.target_issue,a.top_k)
    elif a.verb=="score":
        if not a.game: raise ValueError("score requires --game")
        result=score_release(a.release,a.game)
    elif a.verb=="research":
        if not a.game: raise ValueError("research requires --game")
        result=research_release(a.release,a.game)
    else: result=schedule_release(a.release,a.game,a.fail_after,a.cycle_id)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0 if result["status"] in ("PASS","LOCKED") else 20


if __name__=="__main__": raise SystemExit(main())
