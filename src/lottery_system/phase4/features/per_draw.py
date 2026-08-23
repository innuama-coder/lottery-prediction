"""Observable draw-context features with explicit availability semantics."""
from __future__ import annotations
from datetime import date
import math

RULE_REGIMES = {
    "ssq": [(date(2003, 2, 23), "ssq-2003-v1", "https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/")],
    "dlt": [(date(2007, 5, 28), "dlt-2007-v1", "https://m.lottery.gov.cn/ksjz/m/yxgz_dlt/")],
}

def weekday(draw_date):
    d = date.fromisoformat(str(draw_date)); angle = 2*math.pi*d.weekday()/7
    return {"weekday": d.weekday(), "sin": math.sin(angle), "cos": math.cos(angle)}

def days_since_draw(draw_date, previous_date=None):
    return None if previous_date is None else (date.fromisoformat(str(draw_date))-date.fromisoformat(str(previous_date))).days

def rule_regime(game, draw_date):
    d = date.fromisoformat(str(draw_date)); candidates = [x for x in RULE_REGIMES[game.lower()] if x[0] <= d]
    return candidates[-1][1] if candidates else "unknown"

def ball_set_id(enriched=None):
    return (enriched or {}).get("ball_set_id")

def draw_position(enriched=None):
    return (enriched or {}).get("draw_position", {"front": {}, "back": {}})

def sales_amount(amount=None, sealed_before_cutoff=False):
    return math.log1p(amount) if amount is not None and sealed_before_cutoff else None

def jackpot_balance(previous_published_balance=None):
    return math.log1p(previous_published_balance) if previous_published_balance is not None else None

TIME_WEEKDAY=weekday
TIME_DAYS_SINCE_DRAW=days_since_draw
TIME_RULE_REGIME=rule_regime
ENV_BALL_SET_ID=ball_set_id
ENV_DRAW_POSITION=draw_position
CTX_SALES_AMOUNT=sales_amount
CTX_JACKPOT_BALANCE=jackpot_balance
