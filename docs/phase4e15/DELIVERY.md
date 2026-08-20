# Phase4E15 per-number marginal orientation diagnostic

## Outcome

Phase4E15 completed the bounded workflow and is **not accepted**. Three of four game/zones passed the required last-60 outer individual-number association gate; DLT front failed with Spearman rho `-0.00950217` and descriptive slope `-2.74392150`.

The selected orientations differ by game/zone: raw descending marginal mass was selected for both SSQ zones and DLT front, while the registered reverse ascending-mass control was selected for DLT back. This mixed direction is recorded rather than pooled or hidden. Status remains `RETROSPECTIVE_BACKTEST_ONLY`, `promotion_eligible=false`, and P4E6 continues serving `P4-P4E2-20260815-r12` unchanged.

Marginal inclusion scores in this report are orientation/model diagnostics, not true lottery probabilities or claims of predictable lottery outcomes.

## Selection fence

- E14's inner 120-row identity is used exclusively for selection. Full per-number masses and binary outcomes are recomputed with the unchanged E13 model, and each row's ranking and fixed confidence sets are verified against E14.
- Inner rows 1–60 are fit/measure only. Rows 61–120 select orientation by positive Spearman rho, then positive descriptive slope, then monotonic five-bucket behavior; remaining ties use rho, slope, and registered candidate order.
- Candidates are raw descending marginal mass and reverse ascending marginal mass. Equal scores use canonical ascending number.
- No outer label enters orientation selection. Every target `t` uses data only through `t-1`.
- E14/E13's identical frozen outer 120 is retained: first 60 outer calibration, last 60 outer evaluation.

Selected pre-outer diagnostics:

| Game | Zone | Selected orientation | Selection rho | Selection slope | Monotonic buckets |
|---|---|---|---:|---:|---|
| SSQ | front | raw descending | 0.034848 | 0.539813 | no |
| SSQ | back | raw descending | 0.024410 | 1.436346 | no |
| DLT | front | raw descending | 0.006833 | -0.030275 | no |
| DLT | back | reverse ascending control | 0.037385 | 2.775078 | no |

DLT front had no orientation with both positive rho and positive slope on the selection half: raw won the first registered criterion (positive rho), while reverse had positive slope but negative rho.

## Frozen outer evaluation

Acceptance uses only individual candidate-number observations: orientation score versus the binary event that the candidate number appears in the actual zone draw. Fixed-set or pooled association is reported but is not an acceptance gate.

| Game | Zone | Orientation | Individual observations | Spearman rho | Descriptive slope | Pass |
|---|---|---|---:|---:|---:|---|
| SSQ | front | raw descending | 1,980 | 0.016280 | 0.265785 | yes |
| SSQ | back | raw descending | 960 | 0.016103 | 1.850398 | yes |
| DLT | front | raw descending | 2,100 | -0.009502 | -2.743921 | **no** |
| DLT | back | reverse ascending control | 720 | 0.017411 | 4.577729 | yes |

Overall acceptance requires every row above to pass, so `accepted_all_games_zones=false`.

## Fixed-size overlap reports

The first interval below is the Wilson 95% interval for predicted-number hit rate (`overlap / selected numbers`). Reports also include actual-number coverage (`overlap / zone draw count`), its Wilson interval, any-hit/exact-zone rates, selected-number and fixed-set association, bucket diagnostics, and canonical per-number summaries.

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

Wilson intervals are descriptive because candidate-number outcomes within a draw are dependent.

## Evidence and invariants

Machine-readable evidence is in `artifacts/phase4e15/summary.json` and each game's `report.json`, `inner-rolling-report.jsonl`, and `outer-rolling-report.jsonl`.

Each game report includes SHA-256 hashes for canonical source data; the registered P4E2 oracle; E13 script, summary, report, and outer rows; E14 script, summary, report, inner rows, and outer rows; generated E15 rows; the inherited E13/E14/E15 outer identity; and separate first-60 and last-60 selection split identities. Strict-lag fields assert `maximum_training_position=target_position-1` for every inner and outer row and `outer_labels_used_for_orientation_selection=false`.

The E13/E14 exact-ticket comparison is copied unchanged. No E9–E14 artifact, number model, serving selection, or promotion gate is modified.
