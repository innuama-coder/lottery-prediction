"""Historical co-occurrence features."""
from collections import Counter
from itertools import combinations

def pair_rate(history, window=30, subtract_expected=False, n=None, k=None):
    rows = [sorted(set(r)) for r in history][-window:]; exposure = len(rows)
    counts = Counter(pair for row in rows for pair in combinations(row, 2))
    expected = (k*(k-1)/(n*(n-1))) if subtract_expected and n and k and n > 1 else 0.0
    return {pair: count/exposure-expected for pair,count in counts.items()} if exposure else {}

def triple_rate(history, window=30):
    rows = [sorted(set(r)) for r in history][-window:]; exposure = len(rows)
    counts = Counter(triple for row in rows for triple in combinations(row, 3))
    return {triple: {"count": count, "exposure": exposure, "rate": count/exposure} for triple,count in counts.items()} if exposure else {}

REL_PAIR_RATE = pair_rate
REL_TRIPLE_RATE = triple_rate
