#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
from lottery_system.phase4.model_selection import candidate_space, evaluate_candidate, load_draws, precompute_number_features

ROOT=Path(__file__).resolve().parents[2]

def main():
    draws=load_draws(ROOT/"artifacts/phase4e30_data_expansion/dlt-draws-full.jsonl")
    feature_cache=precompute_number_features(draws)
    output=ROOT/"artifacts/phase4e32_model_search"; output.mkdir(parents=True,exist_ok=True)
    results=[]
    for i,config in enumerate(candidate_space(),1):
        result=evaluate_candidate(draws,config,feature_cache=feature_cache); results.append(result)
        print(f"[{i:02d}/48] {config.family} {config.feature_subset} lambda={config.regularization_lambda} W={config.window}: hits={result['mean_top_k_hits']:.6f}",flush=True)
    with (output/"candidates.jsonl").open("w",encoding="utf-8") as f:
        for row in results: f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n")
    ranked=sorted(results,key=lambda r:(-r["mean_top_k_hits"],r["mean_binary_log_loss"],json.dumps(r["config"],sort_keys=True)))
    best=ranked[0]; confirmed=best["top_k_hits_delta_vs_uniform"]>0 and best["log_loss_delta_vs_uniform"]<0
    summary={"phase":"phase4e32","draw_count":len(draws),"evaluation_window":120,"candidate_count":len(results),
             "candidate_space":{"families":["A","B","C"],"lambdas":[1e-4,1e-3,1e-2],"windows":[30,60],"legal_count":48},
             "uniform_baseline":{"front_mean_top_10_hits":5*10/35,"back_mean_top_4_hits":2*4/12,
                                 "combined_mean_hits":5*10/35+2*4/12},
             "best_config":best,"ranked_candidates":ranked,
             "selection_bias_warning":{"best_of_n_upward_bias":True,"n":len(results),
                 "warning":"best-of-N 上偏：同一评估窗选择 48 个候选中的最大值会产生选择偏差；该结果不是独立确认，也不得解释为显著提升。"},
             "scientific_conclusion":"confirmed_lift" if confirmed else "no_confirmed_lift",
             "recommendation":"建议扩大特征输入，并使用新的独立留出样本确认。" if not confirmed else "仍需新的独立留出样本确认，当前不声称 lift。",
             "claim":"No lift,收益,或中奖能力 is claimed from this best-of-N search."}
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
if __name__=="__main__": main()
