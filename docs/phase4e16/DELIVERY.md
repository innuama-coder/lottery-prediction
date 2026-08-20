# Phase4E16 stable per-number orientation

## Outcome

Phase4E16 completed the bounded stability workflow and is **not accepted**. SSQ front, SSQ back, and DLT back selected stable orientations and passed the frozen last-60 outer evaluation. DLT front had only two positive raw-orientation blocks and one positive reverse-orientation block, so neither candidate met the required 3-of-4 gate. It therefore retained the registered P4E15 raw orientation as an unstable fallback. DLT front remained negative on outer evaluation (`rho=-0.009502`, slope `-2.743921`), and expansion stops here.

The status remains `RETROSPECTIVE_BACKTEST_ONLY`, `promotion_eligible=false`, and P4E6 continues serving `P4-P4E2-20260815-r12` unchanged. Marginal inclusion scores are orientation/model diagnostics, not true lottery probabilities or claims of predictable lottery outcomes.

## Selection fence and identity

- The selection window is exactly the 240 targets immediately before the frozen outer: Python half-open `N-360..N-120`, or `N-360` through `N-121` inclusive.
- It is divided chronologically into four non-overlapping 60-target blocks. Each target `t` is recomputed from the unchanged E13 model using training data only through `t-1`.
- Raw descending and reverse ascending marginal orientations are evaluated on every block using one binary observation per draw and candidate number.
- A candidate is stable only with both positive Spearman rho and positive descriptive slope in at least three blocks. Eligible candidates are ordered by positive-block count, median rho, then registered candidate order.
- If neither candidate is stable, the P4E15 orientation remains registered as the fallback and is explicitly marked unstable. Outer labels are never used for selection.
- The final 120 selection targets are identical to E14/E15 inner targets. Recomputed E13 marginal rankings and confidence sets match E14, and the full recomputed zone values match E15.
- The outer remains the exact E13/E14/E15 120-target identity: first 60 calibration and last 60 evaluation. E16 outer rows embed E15 rows unchanged and add only the E16 diagnostic layer.

| Game | Selection issues | Target positions | Selection identity SHA-256 |
|---|---|---:|---|
| SSQ | 2024028–2025116 | 3122–3361 | `fdddfa866fb32e40c8856d56a4043f572183c88d6c6e510a37d9b6540a3ff87b` |
| DLT | 2024036–2025123 | 923–1162 | `dc6a1c76dca9267ac49631c7a7eb4abddc4853996e7eea338746b4554828f689` |

## Four-block stability

The block cells show `rho / slope`; a check mark means both are positive.

| Game/zone | Candidate | B1 | B2 | B3 | B4 | Positive blocks | Median rho | Result |
|---|---|---:|---:|---:|---:|---:|---:|---|
| SSQ front | raw | 0.032002 / 0.735058 ✓ | -0.019475 / -0.420675 | 0.007762 / 0.250017 ✓ | 0.034848 / 0.539813 ✓ | 3 | 0.019882 | stable selected |
| SSQ front | reverse | -0.032002 / -0.735058 | 0.019475 / 0.420675 ✓ | -0.007762 / -0.250017 | -0.034848 / -0.539813 | 1 | -0.019882 | ineligible |
| SSQ back | raw | 0.003680 / 0.148700 ✓ | -0.065747 / -9.543677 | 0.017889 / 0.457089 ✓ | 0.024410 / 1.436346 ✓ | 3 | 0.010784 | stable selected |
| SSQ back | reverse | -0.003680 / -0.148700 | 0.065747 / 9.543677 ✓ | -0.017889 / -0.457089 | -0.024410 / -1.436346 | 1 | -0.010784 | ineligible |
| DLT front | raw | 0.064246 / 3.013914 ✓ | -0.017904 / -0.699361 | 0.050743 / 2.236644 ✓ | 0.006833 / -0.030275 | 2 | 0.028788 | unstable fallback |
| DLT front | reverse | -0.064246 / -3.013914 | 0.017904 / 0.699361 ✓ | -0.050743 / -2.236644 | -0.006833 / 0.030275 | 1 | -0.028788 | ineligible |
| DLT back | raw | -0.034552 / -5.087486 | -0.012282 / -8.181889 | 0.057647 / 3.156967 ✓ | -0.037385 / -2.775078 | 1 | -0.023417 | ineligible |
| DLT back | reverse | 0.034552 / 5.087486 ✓ | 0.012282 / 8.181889 ✓ | -0.057647 / -3.156967 | 0.037385 / 2.775078 ✓ | 3 | 0.023417 | stable selected |

## Frozen outer evaluation

Acceptance is the game/zone-level association over all individual candidate-number observations in the last 60 outer targets. Fixed-size and pooled-set association does not enter acceptance.

| Game | Zone | Applied orientation | Stability | Observations | Spearman rho | Descriptive slope | Pass |
|---|---|---|---|---:|---:|---:|---|
| SSQ | front | raw descending | stable | 1,980 | 0.016280 | 0.265785 | yes |
| SSQ | back | raw descending | stable | 960 | 0.016103 | 1.850398 | yes |
| DLT | front | P4E15 raw fallback | **unstable** | 2,100 | -0.009502 | -2.743921 | **no** |
| DLT | back | reverse ascending control | stable | 720 | 0.017411 | 4.577729 | yes |

The machine-readable split reports also include rho and slope separately for every canonical number. Overall acceptance requires all four rows above to pass, so `accepted_all_games_zones=false`.

## Fixed-size overlap and Wilson intervals

These last-60 results report overlap with each fixed-size selected set. The interval is the descriptive Wilson 95% interval for predicted-number hit rate (`overlap / selected-number trials`); within-draw candidate outcomes are dependent.

| Game/zone | Size | Overlap | Predicted-number hit rate (Wilson 95%) | Actual-number coverage rate |
|---|---:|---:|---:|---:|
| SSQ front | 5 | 62 | 0.2067 (0.1647–0.2561) | 0.1722 |
| SSQ front | 8 | 100 | 0.2083 (0.1744–0.2469) | 0.2778 |
| SSQ front | 10 | 118 | 0.1967 (0.1668–0.2304) | 0.3278 |
| SSQ front | 12 | 145 | 0.2014 (0.1737–0.2322) | 0.4028 |
| SSQ front | 15 | 170 | 0.1889 (0.1647–0.2158) | 0.4722 |
| SSQ front | 20 | 222 | 0.1850 (0.1640–0.2080) | 0.6167 |
| SSQ back | 1 | 5 | 0.0833 (0.0361–0.1807) | 0.0833 |
| SSQ back | 2 | 11 | 0.0917 (0.0520–0.1567) | 0.1833 |
| SSQ back | 3 | 14 | 0.0778 (0.0469–0.1263) | 0.2333 |
| SSQ back | 4 | 19 | 0.0792 (0.0513–0.1203) | 0.3167 |
| SSQ back | 6 | 22 | 0.0611 (0.0407–0.0908) | 0.3667 |
| DLT front | 5 | 40 | 0.1333 (0.0995–0.1765) | 0.1333 |
| DLT front | 8 | 67 | 0.1396 (0.1114–0.1735) | 0.2233 |
| DLT front | 10 | 78 | 0.1300 (0.1054–0.1593) | 0.2600 |
| DLT front | 12 | 99 | 0.1375 (0.1143–0.1646) | 0.3300 |
| DLT front | 15 | 132 | 0.1467 (0.1251–0.1713) | 0.4400 |
| DLT front | 20 | 170 | 0.1417 (0.1231–0.1625) | 0.5667 |
| DLT back | 1 | 9 | 0.1500 (0.0810–0.2611) | 0.0750 |
| DLT back | 2 | 18 | 0.1500 (0.0970–0.2247) | 0.1500 |
| DLT back | 3 | 25 | 0.1389 (0.0959–0.1970) | 0.2083 |
| DLT back | 4 | 36 | 0.1500 (0.1104–0.2007) | 0.3000 |
| DLT back | 6 | 59 | 0.1639 (0.1292–0.2056) | 0.4917 |

## Exact-ticket comparison and evidence

The E13–E15 exact-ticket comparison is copied byte-for-value unchanged: no compressed space is accepted; the first-ranked-space evaluation remains `52/60` for SSQ and `59/60` for DLT; and serving gates remain unchanged.

Machine-readable evidence is in `artifacts/phase4e16/summary.json` and each game's `report.json`, `inner-rolling-report.jsonl`, and `outer-rolling-report.jsonl`. Each report records canonical source and registered oracle hashes; E13, E14, and E15 script/summary/report/row hashes; generated E16 row hashes; inherited outer identity hashes; the 240-target selection identity; four block identities; strict-lag assertions; and the no-outer-label selection fence.

No E9–E15 artifact, E13 model, serving selection, or promotion gate was modified.
