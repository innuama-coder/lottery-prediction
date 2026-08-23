"""Pure candidate-ticket scoring functions."""
from __future__ import annotations
from itertools import combinations
import math

def _numbers(candidate): return sorted(set(map(int, candidate)))
def previous_overlap(candidate, previous): return len(set(candidate) & set(previous))
def odd_count(candidate): return sum(n % 2 for n in candidate)
def number_sum(candidate): return sum(candidate)
def number_range(candidate):
    x=_numbers(candidate); return x[-1]-x[0] if x else 0
def adjacent_pairs(candidate):
    x=_numbers(candidate); return sum(b-a==1 for a,b in zip(x,x[1:]))
def gap_vector(candidate):
    x=_numbers(candidate); return [b-a for a,b in zip(x,x[1:])]
def band_counts(candidate, maximum, bands=3):
    out=[0]*bands
    for n in candidate: out[min((int(n)-1)*bands//maximum,bands-1)] += 1
    return out
def birthday_count(candidate): return sum(int(n)<=31 for n in candidate)
def arithmetic_pattern(candidate, divisors=(2,3,4,5)):
    x=_numbers(candidate); s=set(x); progressions=set(); max_len=0
    for a,b in combinations(x,2):
        d=b-a; seq=[]; v=a
        while v in s: seq.append(v); v += d
        if len(seq)>=3:
            progressions.add(tuple(seq)); max_len=max(max_len,len(seq))
    return {"subset_count":len(progressions),"max_length":max_len,"divisible_counts":{str(d):sum(n%d==0 for n in x) for d in divisors}}
def recent_win_overlap(candidate, history, window=10, decay=0.8):
    c=set(candidate); rows=list(history)[-window:]
    return sum((decay**lag)*len(c & set(row)) for lag,row in enumerate(reversed(rows),1))
def winner_count_residual(observed, sales_tickets, hit_probability):
    expected=sales_tickets*hit_probability
    return (observed-expected)/math.sqrt(expected) if expected>0 else None

REL_PREVIOUS_OVERLAP=previous_overlap
STRUCT_ODD_COUNT=odd_count
STRUCT_SUM=number_sum
STRUCT_RANGE=number_range
STRUCT_ADJACENT_PAIRS=adjacent_pairs
STRUCT_GAP_VECTOR=gap_vector
STRUCT_BAND_COUNTS=band_counts
BEHAV_BIRTHDAY_COUNT=birthday_count
BEHAV_ARITHMETIC_PATTERN=arithmetic_pattern
BEHAV_RECENT_WIN_OVERLAP=recent_win_overlap
BEHAV_WINNER_COUNT_RESIDUAL=winner_count_residual
