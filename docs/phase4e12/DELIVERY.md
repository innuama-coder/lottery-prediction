# Phase4E12 bounded compression iteration

## Result

Phase4E12 is a validated no-material-improvement result. The bounded search evaluated the inherited 80-label E11 baseline, a 240-label coefficient fit, and one fixed 50:50 score ensemble of the two strongest distinct E11 masks. Neither new candidate reduced inner split-conformal `K90` or `K80` by the registered 5% material threshold in either game, so candidate expansion stopped.

Material improvement is evaluated only for the two new candidates; the inherited E11 baseline is explicitly excluded from that search. The report separately records whether any new candidate was material and whether the ultimately selected candidate was material.

The lexicographic selection rule still chose the fixed ensemble for both games because its exact canonical `K90` was marginally smaller than the baseline. This selection does not override the materiality finding and did not authorize any additional candidates.

| Game | Candidate | Inner K90 | Change vs E11 | Inner K80 | Change vs E11 |
|---|---|---:|---:|---:|---:|
| SSQ | E11 baseline, 80 labels | 15,591,524 | — | 14,096,436 | — |
| SSQ | 240-label fit | 16,071,864 | -3.08% | 14,135,844 | -0.28% |
| SSQ | fixed 50:50 ensemble | 15,566,715 | +0.16% | 13,749,325 | +2.46% |
| DLT | E11 baseline, 80 labels | 18,998,295 | — | 15,655,237 | — |
| DLT | 240-label fit | 19,644,358 | -3.40% | 17,064,637 | -9.00% |
| DLT | fixed 50:50 ensemble | 18,979,288 | +0.10% | 15,621,061 | +0.22% |

Positive changes denote compression (a smaller K); negative changes denote regression.

## Frozen outer evaluation

Candidate selection used only the 120 historical targets immediately before the frozen final 120. Every target used only labels through `t-1`. The unchanged outer window was then split into its first 60 calibration draws and last 60 independent evaluation draws.

Frozen-window identity is the canonical sequence of `(issue, target_position)` pairs. Its SHA-256 is recorded for both E12 and the E11 baseline and generation fails unless the identities match exactly.

The exact canonical split-conformal first ranked spaces remained reliable:

| Game | Calibration K90 | Evaluation hits | Rate | Wilson 95% | Gate |
|---|---:|---:|---:|---:|---|
| SSQ | 15,043,204 | 52/60 | 0.8667 | [0.7583, 0.9309] | PASS |
| DLT | 20,718,466 | 59/60 | 0.9833 | [0.9114, 0.9971] | PASS |

Fixed-space compression did not pass. Acceptance requires both evaluation rate `>=0.80` and Wilson lower bound `>=0.75`.

| Game | K | Hits/60 | Rate | Wilson 95% | Gate |
|---|---:|---:|---:|---:|---|
| SSQ | 100,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| SSQ | 50,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| SSQ | 10,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| SSQ | 5,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| SSQ | 2,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| SSQ | 1,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| DLT | 100,000 | 1/60 | 0.0167 | [0.0029, 0.0886] | FAIL |
| DLT | 50,000 | 1/60 | 0.0167 | [0.0029, 0.0886] | FAIL |
| DLT | 10,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| DLT | 5,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| DLT | 2,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |
| DLT | 1,000 | 0/60 | 0.0000 | [0.0000, 0.0602] | FAIL |

These are ranking-coverage observations, not ticket probabilities and not guarantees about a random draw. Rank-invariant positive global scaling was not evaluated.

## Contracts and artifacts

- Ranking uses score tick `P4S10HE1`, exact boundary rechecks, and canonical ticket tie-breaking.
- `K90`, `K80`, and `K50` use `ceil((n+1)p)` split-conformal order statistics; raw epsilon ranks are not used.
- The 50:50 ensemble averages masked scores with fixed weights selected before opening the outer window.
- P4E12 is `RETROSPECTIVE_BACKTEST_ONLY`, with `promotion_eligible=false`.
- P4E6 release `P4-P4E2-20260815-r12` remains unchanged in `PROSPECTIVE_ONLY` status.

Machine-readable evidence is in `artifacts/phase4e12/summary.json`, each game's `report.json`, 360-line inner report, and 120-line outer report. The reports include hashes of their E11 lineage artifacts.
