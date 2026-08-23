#!/usr/bin/env python3
"""Collect the bounded DLT enrichment and build deterministic PIT snapshots."""
from __future__ import annotations

import hashlib
from html import unescape
import json
import math
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from lottery_system.phase4.features import FEATURE_IDS
from lottery_system.phase4.features import per_draw, per_number, relation

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"
POOL = ROOT / "artifacts/phase4e25_b1_dlt_pool_data/dlt-draws.jsonl"
OUT = ROOT / "artifacts/phase4e29_feature_engine"
SOURCE = "https://www.gdlottery.cn/f_html/kjgg/P085_{issue}.html"
UA = "phase4e29-feature-research/1.0 (read-only; bounded)"
CONFIG = {"ssq":{"front":(33,6),"back":(16,1)}, "dlt":{"front":(35,5),"back":(12,2)}}

def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def read_jsonl(path):
    with path.open(encoding="utf-8") as f: return [json.loads(line) for line in f if line.strip()]

def canonical(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def parse_enrichment(raw, issue, url, fetched_at):
    text=unescape(raw.decode("utf-8","replace"))
    ball=re.search(r"本期使用第\s*(\d+)\s*套摇奖球",text)
    order=re.search(r"本期出球顺序：\s*</li>\s*<li>([^<]+)</li>\s*<li>([^<]+)</li>",text,re.I)
    if not ball or not order: raise ValueError(f"official fields absent for {issue}")
    front=[int(x) for x in re.findall(r"\d+",order.group(1))]
    back=[int(x) for x in re.findall(r"\d+",order.group(2))]
    if len(front)!=5 or len(back)!=2: raise ValueError(f"invalid draw order for {issue}")
    return {"game":"dlt","issue_id":"20"+issue,"ball_set_id":int(ball.group(1)),
            "draw_position":{"front":{str(n):p for p,n in enumerate(front,1)},"back":{str(n):p for p,n in enumerate(back,1)}},
            "draw_order":{"front":front,"back":back},
            "provenance":{"url":url,"fetched_at_utc":fetched_at,"raw_sha256":hashlib.sha256(raw).hexdigest()}}

def collect_enrichment(path):
    if path.exists(): return read_jsonl(path)
    issues=sorted({str(x["issue_id"]).zfill(5) for x in read_jsonl(POOL)})
    if len(issues)>20: raise RuntimeError("collection hard limit exceeded")
    records=[]
    for index,issue in enumerate(issues):
        if index: time.sleep(3.05)
        url=SOURCE.format(issue=issue)
        fetched=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")
        with urlopen(Request(url,headers={"User-Agent":UA}),timeout=20) as response: raw=response.read()
        records.append(parse_enrichment(raw,issue,url,fetched))
    path.write_text("".join(canonical(x)+"\n" for x in records),encoding="utf-8")
    return records

def _write_record(stream, record): stream.write(canonical(record)+"\n")

def build_snapshot(game, draws, enrichment, pool):
    selected=draws[-120:]; cutoff_index=len(draws)-120; prefix=draws[:cutoff_index]
    cutoff=selected[0]["issue_id"]
    out_path=OUT/f"{game}-feature-snapshot.jsonl"
    enrich_by={x["issue_id"]:x for x in enrichment}; pool_by={"20"+str(x["issue_id"]):x for x in pool}
    with out_path.open("w",encoding="utf-8") as f:
        for zone in ("front","back"):
            n,k=CONFIG[game][zone]; history=[x[f"{zone}_numbers"] for x in prefix]
            features={
              "FREQ_ROLLING_RATE":per_number.rolling_rate(history,n),
              "FREQ_WAITING_TIME":per_number.waiting_time(history,n)[0],
              "FREQ_UNIFORMITY_RESIDUAL":per_number.uniformity_residual(history,n,k),
              "STAT_LAG_AUTOCORRELATION":per_number.lag_autocorrelation(history,n),
              "STAT_MARKOV_TRANSITION":per_number.markov_transition(history,n),
              "STAT_SHANNON_ENTROPY":per_number.shannon_entropy(history,n),
              "STAT_RENYI_ENTROPY":per_number.renyi_entropy(history,n),
              "STAT_RUN_LENGTH":per_number.run_length(history,n),
              "STAT_CHANGE_POINT_SCORE":per_number.change_point_score(history,n),
              "STAT_PERMUTATION_ENTROPY":per_number.permutation_entropy(history,n),
              "STAT_SAMPLE_ENTROPY":per_number.sample_entropy(history,n),
              "STAT_HYPERGEOMETRIC_MAHALANOBIS":per_number.hypergeometric_mahalanobis(history,n,k),
            }
            wait_censored=per_number.waiting_time(history,n)[1]
            for feature_id,values in features.items():
                for number,value in values.items():
                    rec={"game":game,"issue_id":cutoff,"cutoff_issue":prefix[-1]["issue_id"],"zone":zone,"feature_id":feature_id,"number":number,"value":value}
                    if feature_id=="FREQ_WAITING_TIME": rec["censored"]=wait_censored[number]
                    _write_record(f,rec)
            for feature_id, values in (("REL_PAIR_RATE",relation.pair_rate(history)),("REL_TRIPLE_RATE",relation.triple_rate(history))):
                for numbers,value in sorted(values.items()):
                    _write_record(f,{"game":game,"issue_id":cutoff,"cutoff_issue":prefix[-1]["issue_id"],"zone":zone,"feature_id":feature_id,"number":"-".join(map(str,numbers)),"value":value})
        # The first evaluation draw still has a known immediately preceding date.
        previous_date=prefix[-1]["draw_date_local"]; previous_pool=None
        for draw in selected:
            enriched=enrich_by.get(draw["issue_id"]); current_pool=pool_by.get(draw["issue_id"])
            contexts={
              "TIME_WEEKDAY":per_draw.weekday(draw["draw_date_local"]),
              "TIME_DAYS_SINCE_DRAW":per_draw.days_since_draw(draw["draw_date_local"],previous_date),
              "TIME_RULE_REGIME":per_draw.rule_regime(game,draw["draw_date_local"]),
              "ENV_BALL_SET_ID":per_draw.ball_set_id(enriched),
              "ENV_DRAW_POSITION":per_draw.draw_position(enriched) if enriched else None,
              # Current sales are not proven sealed at the forecast cutoff.
              "CTX_SALES_AMOUNT":per_draw.sales_amount(current_pool.get("national_sales_yuan") if current_pool else None,False),
              "CTX_JACKPOT_BALANCE":per_draw.jackpot_balance(previous_pool.get("pool_rollover_yuan") if previous_pool else None),
            }
            for feature_id,value in contexts.items(): _write_record(f,{"game":game,"issue_id":draw["issue_id"],"feature_id":feature_id,"value":value})
            previous_date=draw["draw_date_local"]
            if current_pool: previous_pool=current_pool
    return out_path

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    enrichment=collect_enrichment(OUT/"dlt-enriched.jsonl")
    all_draws=read_jsonl(INPUT); pool=read_jsonl(POOL)
    outputs=[]
    for game in ("ssq","dlt"):
        draws=sorted((x for x in all_draws if x["game"]==game),key=lambda x:x["issue_id"])
        outputs.append(build_snapshot(game,draws,enrichment,pool))
    manifest={"schema_version":"phase4e29-v1","inputs":{str(INPUT.relative_to(ROOT)):sha256(INPUT),str(POOL.relative_to(ROOT)):sha256(POOL)},
              "feature_ids":list(FEATURE_IDS),"parameters":{"rolling_window":30,"change_windows":[30,30],"lag":1,"markov_alpha":1,"renyi_q":2,"permutation_m":3,"sample_entropy_m":2,"sample_entropy_r_sd":0.2,"recent_overlap_window":10,"recent_overlap_decay":0.8},
              "outputs":{str(p.relative_to(ROOT)):sha256(p) for p in outputs+[OUT/"dlt-enriched.jsonl"]}}
    (OUT/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(canonical({"status":"ok","feature_count":len(FEATURE_IDS),"output":str(OUT.relative_to(ROOT))}))

if __name__=="__main__": main()
