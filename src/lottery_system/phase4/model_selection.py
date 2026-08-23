"""Leakage-safe Phase4E32 DLT model search.

The evaluator uses deterministic, expanding-prefix online logistic regression.
At draw ``t`` prediction is made before either the labels or post-draw update for
``t`` is observed.  Running moments used for z-scoring follow the same rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

from lottery_system.phase4.features import per_number, ticket

LAMBDAS = (1e-4, 1e-3, 1e-2)
WINDOWS = (30, 60)
SUBSETS = ("all-per-number(12)", "per-number+per-draw(19)", "freq-only(3)", "statistical-only(9)")
PN_NAMES = ("rolling_rate", "waiting_time", "uniformity_residual", "lag_autocorrelation",
            "markov_p_1_given_1", "markov_p_1_given_0", "shannon_entropy", "renyi_entropy",
            "permutation_entropy", "sample_entropy", "run_length", "change_point_score",
            "hypergeometric_mahalanobis")


@dataclass(frozen=True)
class CandidateConfig:
    family: str
    feature_subset: str
    regularization_lambda: float
    window: int


def candidate_space() -> list[CandidateConfig]:
    """Return all 48 legal, pre-registered configurations."""
    legal = {"A": (SUBSETS[0], SUBSETS[2], SUBSETS[3]), "B": SUBSETS,
             "C": ("two-stage(12+11)",)}
    return [CandidateConfig(f, s, l, w) for f in ("A", "B", "C")
            for s in legal[f] for l in LAMBDAS for w in WINDOWS]


def top_k_hits(probabilities: Sequence[float], winners: Iterable[int], k: int) -> int:
    ranked = sorted(range(1, len(probabilities) + 1), key=lambda n: (-probabilities[n-1], n))[:k]
    return len(set(ranked) & set(map(int, winners)))


def _finite(x: object) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _number_rows(history, n: int, k: int, window: int) -> list[list[float]]:
    rate = per_number.rolling_rate(history, n, window)
    waiting, _ = per_number.waiting_time(history, n, window)
    residual = per_number.uniformity_residual(history, n, k, window)
    ac = per_number.lag_autocorrelation(history, n, window=window)
    markov = per_number.markov_transition(history, n, window)
    shannon = per_number.shannon_entropy(history, n, window)
    renyi = per_number.renyi_entropy(history, n, window=window)
    permutation = per_number.permutation_entropy(history, n, window=window)
    sample = per_number.sample_entropy(history, n, window=window)
    runs = per_number.run_length(history, n, window)
    change = per_number.change_point_score(history, n, window)
    maha = per_number.hypergeometric_mahalanobis(history, n, k, window)
    return [[_finite(v) for v in (rate[i], waiting[i], residual[i], ac[i],
            markov[i]["p_1_given_1"], markov[i]["p_1_given_0"], shannon[i], renyi[i],
            permutation[i], sample[i], runs[i]["terminal_length"], change[i], maha[i])]
            for i in range(1, n + 1)]


def _context(draws: Sequence[dict], t: int) -> list[float]:
    row = draws[t]; d = date.fromisoformat(row["draw_date_local"])
    gap = 0 if t == 0 else (d-date.fromisoformat(draws[t-1]["draw_date_local"])).days
    regime = 1.0 if d >= date(2019, 2, 18) else 0.0
    ball = row.get("ball_set_id")
    # The source does not prove target-draw sales was sealed before cutoff, so both
    # financial fields deliberately lag one draw.
    sales = math.log1p(_finite(draws[t-1].get("national_sales_yuan"))) if t else 0.0
    jackpot = math.log1p(_finite(draws[t-1].get("pool_rollover_yuan"))) if t else 0.0
    order = 1.0 if row.get("front_draw_order") or row.get("back_draw_order") else 0.0
    return [float(d.weekday()), float(gap), regime,
            float(int(hashlib.sha256(str(ball).encode()).hexdigest()[:8], 16) / 0xffffffff) if ball else 0.0,
            sales, jackpot, order]


def _select(rows: list[list[float]], subset: str, context: list[float], family: str) -> list[list[float]]:
    if subset == "freq-only(3)": base=[r[:3] for r in rows]
    elif subset == "statistical-only(9)": base=[r[3:] for r in rows]
    else: base=rows
    # Family B is defined by draw context; the subset names select its number-level base.
    return [r+context for r in base] if family == "B" else base


class _OnlineLogistic:
    def __init__(self, dimensions: int, regularization_lambda: float, prevalence: float = 0.5):
        prevalence=min(max(prevalence,1e-6),1-1e-6)
        self.w = [math.log(prevalence/(1-prevalence))] + [0.0] * dimensions; self.mean = [0.0] * dimensions
        self.m2 = [0.0] * dimensions; self.count = 0; self.lam = regularization_lambda

    def _z(self, row):
        if self.count < 2: return [0.0] * len(row)
        return [max(-8.0,min(8.0,(v-m)/max(math.sqrt(q/self.count), 1e-9))) for v,m,q in zip(row,self.mean,self.m2)]

    def predict(self, rows):
        out=[]
        for row in rows:
            z=self._z(row); score=max(-30.0,min(30.0,self.w[0]+sum(a*b for a,b in zip(self.w[1:],z))))
            out.append(1/(1+math.exp(-score)))
        return out

    def update(self, rows, labels):
        # One deterministic full-draw mini-batch; state before this call is strictly prefix-only.
        zs=[self._z(r) for r in rows]; probs=[]
        for z in zs:
            s=max(-30.0,min(30.0,self.w[0]+sum(a*b for a,b in zip(self.w[1:],z))))
            probs.append(1/(1+math.exp(-s)))
        eta=0.02/math.sqrt(1+self.count/max(1,len(rows)))
        errors=[p-y for p,y in zip(probs,labels)]
        self.w[0]-=eta*sum(errors)/len(rows)
        for j in range(len(self.w)-1):
            g=sum(e*z[j] for e,z in zip(errors,zs))/len(rows)+self.lam*self.w[j+1]
            self.w[j+1]-=eta*g
        for row in rows:
            self.count += 1
            for j,v in enumerate(row):
                delta=v-self.mean[j]; self.mean[j]+=delta/self.count; self.m2[j]+=delta*(v-self.mean[j])


def _ticket_features(candidate, history, maximum):
    gaps=ticket.gap_vector(candidate); bands=ticket.band_counts(candidate, maximum)
    ar=ticket.arithmetic_pattern(candidate)
    return [ticket.odd_count(candidate), ticket.number_sum(candidate), ticket.number_range(candidate),
            ticket.adjacent_pairs(candidate), (sum(gaps)/len(gaps) if gaps else 0),
            max(bands)-min(bands), ticket.birthday_count(candidate), ar["max_length"],
            ticket.recent_win_overlap(candidate, history),
            ticket.previous_overlap(candidate,history[-1] if history else ()), 0.0]


def _rerank(base, history, n, k, model):
    pool=sorted(range(1,n+1), key=lambda x:(-base[x-1],x))[:min(n,k+5)]
    candidates=list(combinations(pool,k)); feats=[_ticket_features(c,history,n) for c in candidates]
    scores=model.predict(feats)
    marginal=[0.0]*n
    for candidate,score in zip(candidates,scores):
        for number in candidate: marginal[number-1]+=score
    total=sum(marginal) or 1.0
    return [v*k/total for v in marginal], feats, candidates


def _loss(labels, probabilities):
    return sum(-(y*math.log(max(p,1e-12))+(1-y)*math.log(max(1-p,1e-12)))
               for y,p in zip(labels,probabilities))/len(labels)


def evaluate_candidate(draws: Sequence[dict], config: CandidateConfig, evaluation_window: int = 120,
                       audit_hook=None, feature_cache=None) -> dict:
    zone_specs=(("front",35,5,10),("back",12,2,4)); zone_results={}
    start=max(1,len(draws)-evaluation_window)
    for zone,n,k,topk in zone_specs:
        history=[]; model=None; stage2=_OnlineLogistic(11,config.regularization_lambda)
        hits=[]; losses=[]; uniform_losses=[]
        for t,row in enumerate(draws):
            key=(zone,config.window,t)
            raw=feature_cache[key] if feature_cache is not None else _number_rows(history,n,k,config.window)
            selected=_select(raw,config.feature_subset,_context(draws,t),config.family)
            if model is None: model=_OnlineLogistic(len(selected[0]),config.regularization_lambda,k/n)
            probabilities=model.predict(selected)
            if config.family == "C": probabilities, feats, tickets=_rerank(probabilities,history,n,k,stage2)
            winners=row[f"{zone}_numbers"]; labels=[float(i in winners) for i in range(1,n+1)]
            if t >= start:
                if audit_hook: audit_hook(t, tuple(tuple(x) for x in history))
                hits.append(top_k_hits(probabilities,winners,topk)); losses.append(_loss(labels,probabilities))
                uniform_losses.append(_loss(labels,[k/n]*n))
            model.update(selected,labels)
            if config.family == "C":
                # One positive and one deterministic hard negative candidate, learned only after prediction.
                negative=tickets[0] if tuple(sorted(winners)) != tickets[0] else tickets[-1]
                stage2.update([_ticket_features(winners,history,n),_ticket_features(negative,history,n)],[1.0,0.0])
            history.append(tuple(winners))
        zone_results[zone]={"mean_top_k_hits":sum(hits)/len(hits),"mean_binary_log_loss":sum(losses)/len(losses),
                            "uniform_mean_top_k_hits":k*topk/n,"uniform_binary_log_loss":sum(uniform_losses)/len(uniform_losses),
                            "top_k":topk}
    total_hits=sum(zone_results[z]["mean_top_k_hits"] for z in zone_results)
    uniform_hits=sum(zone_results[z]["uniform_mean_top_k_hits"] for z in zone_results)
    mean_loss=sum(zone_results[z]["mean_binary_log_loss"] for z in zone_results)/2
    uniform_loss=sum(zone_results[z]["uniform_binary_log_loss"] for z in zone_results)/2
    return {"config":asdict(config),"evaluation_draws":len(draws)-start,"zones":zone_results,
            "mean_top_k_hits":total_hits,"uniform_mean_top_k_hits":uniform_hits,
            "top_k_hits_delta_vs_uniform":total_hits-uniform_hits,"mean_binary_log_loss":mean_loss,
            "uniform_binary_log_loss":uniform_loss,"log_loss_delta_vs_uniform":mean_loss-uniform_loss,
            "training_scope":"expanding [0,t) prefix only","standardization_scope":"prefix only"}


def load_draws(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def precompute_number_features(draws: Sequence[dict]) -> dict:
    """Compute the expensive sequence-safe feature matrices once per W/zone."""
    cache={}
    for zone,n,k in (("front",35,5),("back",12,2)):
        history=[]
        for t,row in enumerate(draws):
            for window in WINDOWS: cache[(zone,window,t)]=_number_rows(history,n,k,window)
            history.append(tuple(row[f"{zone}_numbers"]))
    return cache
