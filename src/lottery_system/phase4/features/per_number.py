"""Point-in-time per-number features.

Every function consumes an already-cut history.  It deliberately has no target
draw argument, which makes accidentally including the target less likely.
"""
from __future__ import annotations

from collections import Counter
from itertools import permutations
import math

WINDOW = 30
EPS = 1e-12


def _sets(history):
    return [set(map(int, row)) for row in history]


def _series(history, n):
    rows = _sets(history)
    return {i: [int(i in row) for row in rows] for i in range(1, n + 1)}


def rolling_rate(history, n, window=WINDOW):
    rows = _sets(history)[-window:]
    d = len(rows)
    return {i: (sum(i in r for r in rows) / d if d else 0.0) for i in range(1, n + 1)}


def waiting_time(history, n, window=None):
    rows = _sets(history)
    if window is not None:
        rows = rows[-window:]
    values, censored = {}, {}
    for i in range(1, n + 1):
        positions = [j for j, row in enumerate(rows) if i in row]
        values[i] = len(rows) - 1 - positions[-1] if positions else len(rows)
        censored[i] = not positions
    return values, censored


def uniformity_residual(history, n, k, window=WINDOW):
    rows = _sets(history)[-window:]
    w = len(rows)
    mean = w * k / n
    sd = math.sqrt(w * (k / n) * (1 - k / n))
    return {i: ((sum(i in r for r in rows) - mean) / sd if sd else 0.0) for i in range(1, n + 1)}


def lag_autocorrelation(history, n, lag=1, window=WINDOW):
    out = {}
    for i, x in _series(_sets(history)[-window:], n).items():
        if len(x) <= lag:
            out[i] = 0.0; continue
        mean = sum(x) / len(x)
        den = sum((v - mean) ** 2 for v in x)
        out[i] = sum((x[t] - mean) * (x[t-lag] - mean) for t in range(lag, len(x))) / den if den else 0.0
    return out


def markov_transition(history, n, window=WINDOW):
    out = {}
    for i, x in _series(_sets(history)[-window:], n).items():
        c = Counter(zip(x, x[1:]))
        p10 = (c[(0, 1)] + 1) / (c[(0, 0)] + c[(0, 1)] + 2)
        p11 = (c[(1, 1)] + 1) / (c[(1, 0)] + c[(1, 1)] + 2)
        out[i] = {"p_1_given_0": p10, "p_1_given_1": p11}
    return out


def _distribution(history, n, window=WINDOW, smoothing=0.0):
    rows = _sets(history)[-window:]
    counts = [sum(i in r for r in rows) + smoothing for i in range(1, n + 1)]
    total = sum(counts)
    return [v / total for v in counts] if total else [1 / n] * n


def shannon_entropy(history, n, window=WINDOW):
    p = _distribution(history, n, window)
    value = -sum(x * math.log2(x) for x in p if x) / math.log2(n)
    return {i: value for i in range(1, n + 1)}


def renyi_entropy(history, n, q=2.0, window=WINDOW):
    p = _distribution(history, n, window)
    value = math.log2(sum(x ** q for x in p)) / (1-q) / math.log2(n)
    return {i: value for i in range(1, n + 1)}


def run_length(history, n, window=WINDOW):
    out = {}
    for i, x in _series(_sets(history)[-window:], n).items():
        terminal = 0
        if x:
            terminal = 1
            for j in range(len(x)-2, -1, -1):
                if x[j] != x[-1]: break
                terminal += 1
        runs = (1 + sum(a != b for a, b in zip(x, x[1:]))) if x else 0
        out[i] = {"terminal_length": terminal, "run_count": runs, "terminal_state": x[-1] if x else None}
    return out


def change_point_score(history, n, window=WINDOW):
    rows = _sets(history)
    recent, previous = rows[-window:], rows[-2*window:-window]
    p = _distribution(previous, n, window, 0.5)
    q = _distribution(recent, n, window, 0.5)
    m = [(a+b)/2 for a,b in zip(p,q)]
    terms = [0.5*(a*math.log2(a/c)+b*math.log2(b/c)) for a,b,c in zip(p,q,m)]
    return {i: terms[i-1] for i in range(1, n+1)}


def _scalar_series(history):
    # Per-draw sum is invariant to announcement sorting.
    return [float(sum(row)) for row in history]


def permutation_entropy(history, n, m=3, window=WINDOW):
    x = _scalar_series(_sets(history)[-window:])
    patterns = Counter(tuple(sorted(range(m), key=lambda j: (x[t+j], j))) for t in range(max(0, len(x)-m+1)))
    total = sum(patterns.values())
    h = -sum((c/total)*math.log2(c/total) for c in patterns.values()) / math.log2(math.factorial(m)) if total else 0.0
    return {i: h for i in range(1, n+1)}


def sample_entropy(history, n, m=2, r=0.2, window=WINDOW):
    x = _scalar_series(_sets(history)[-window:])
    if len(x) < m + 2:
        value = 0.0
    else:
        mean = sum(x)/len(x); sd = math.sqrt(sum((v-mean)**2 for v in x)/len(x)); tol = r*sd
        def matches(size):
            templates = [x[i:i+size] for i in range(len(x)-size+1)]
            return sum(max(abs(a-b) for a,b in zip(templates[i], templates[j])) <= tol
                       for i in range(len(templates)) for j in range(i+1, len(templates)))
        b, a = matches(m), matches(m+1)
        value = -math.log(a/b) if a and b else -math.log(1/(b+1)) if b else 0.0
    return {i: value for i in range(1, n+1)}


def hypergeometric_mahalanobis(history, n, k, window=WINDOW):
    """Generalized-inverse quadratic form for the singular inclusion covariance."""
    rows = _sets(history)[-window:]; w = len(rows)
    if not w or n <= 1:
        value = 0.0
    else:
        delta = [sum(i in r for r in rows)/w-k/n for i in range(1,n+1)]
        # Cov(mean) has eigenvalue k(n-k)/(n(n-1)w) on sum-zero space.
        eigen = k*(n-k)/(n*(n-1)*w)
        value = sum(d*d for d in delta)/eigen if eigen else 0.0
    return {i: value for i in range(1,n+1)}


FREQ_ROLLING_RATE = rolling_rate
FREQ_WAITING_TIME = waiting_time
FREQ_UNIFORMITY_RESIDUAL = uniformity_residual
STAT_LAG_AUTOCORRELATION = lag_autocorrelation
STAT_MARKOV_TRANSITION = markov_transition
STAT_SHANNON_ENTROPY = shannon_entropy
STAT_RENYI_ENTROPY = renyi_entropy
STAT_RUN_LENGTH = run_length
STAT_CHANGE_POINT_SCORE = change_point_score
STAT_PERMUTATION_ENTROPY = permutation_entropy
STAT_SAMPLE_ENTROPY = sample_entropy
STAT_HYPERGEOMETRIC_MAHALANOBIS = hypergeometric_mahalanobis
