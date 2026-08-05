from __future__ import annotations

import math


def _betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 3e-300:
        d = 3e-300
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 3e-300:
            d = 3e-300
        c = 1.0 + aa / c
        if abs(c) < 3e-300:
            c = 3e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 3e-300:
            d = 3e-300
        c = 1.0 + aa / c
        if abs(c) < 3e-300:
            c = 3e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_quantile(probability: float, a: float, b: float) -> float:
    if probability <= 0.0:
        return 0.0
    if probability >= 1.0:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(90):
        mid = (low + high) / 2.0
        if regularized_beta(mid, a, b) < probability:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def clopper_pearson(successes: int, trials: int, alpha: float = 0.05) -> tuple[float, float]:
    if not 0 <= successes <= trials or trials <= 0:
        raise ValueError("invalid binomial counts")
    lower = 0.0 if successes == 0 else beta_quantile(alpha / 2.0, successes, trials - successes + 1)
    upper = 1.0 if successes == trials else beta_quantile(1.0 - alpha / 2.0, successes + 1, trials - successes)
    return lower, upper


def clopper_pearson_one_sided(successes: int, trials: int, alpha: float = 0.05) -> tuple[float, float]:
    lower = 0.0 if successes == 0 else beta_quantile(alpha, successes, trials - successes + 1)
    upper = 1.0 if successes == trials else beta_quantile(1.0 - alpha, successes + 1, trials - successes)
    return lower, upper

