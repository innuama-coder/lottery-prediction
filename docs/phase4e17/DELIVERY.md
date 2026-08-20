# Phase4E17 bounded DLT-front feature/model experiment

## Outcome

Phase4E17 completed the bounded alternative per-number experiment and selected `p4e3_surprise_renewal_nonlinear` (`E01`, `E02`, `E03`, `E04`, `N01`). It passed the preregistered stability rule in three of four pre-outer blocks and was frozen before the outer was loaded. On the unchanged last-60 DLT outer evaluation its individual-number association was positive (`rho=0.037638`, descriptive slope `2.167111`). The three zones outside the experiment inherit their E16 decisions unchanged and also remain positive, so the E17 diagnostic gate passes all four game/zones.

This result is `RETROSPECTIVE_BACKTEST_ONLY` and is not promotion-eligible. P4E6 continues to serve `P4-P4E2-20260815-r12` unchanged. Candidate scores are ranking/model diagnostics, not true lottery probabilities or evidence that lottery outcomes are predictable.

## Bounded registration

The family was fixed before any E17 selection or outer evaluation:

| Order | Candidate | Features | Fixed configuration | Rationale |
|---:|---|---|---|---|
| 1 | `raw_descending_marginal_mass` | E16 control | unchanged | Raw E16 control |
| 2 | `reverse_ascending_marginal_mass_control` | E16 control | unchanged | Signed reverse E16 control |
| 3 | `p4e3_surprise_renewal_nonlinear` | E01–E04, N01 | history 120, L2 36, temperature 0.5, graph window 80, pair shrinkage 20, purge 2 | Surprise/renewal half plus its registered nonlinear interaction; conservative frozen DLT Phase4E3 C01 settings |
| 4 | `p4e3_transition_graph_nonlinear` | E05–E08, N02 | history 120, L2 4, temperature 0.5, graph window 80, pair shrinkage 20, purge 2 | Transition/graph half plus its registered nonlinear interaction; strongest frozen DLT Phase4E3 C03 settings |

The two model subsets partition all available Phase4E3 per-number features E01–E08 and N01–N02 exactly once. There was no E17 configuration grid, outer tuning, or candidate expansion.

Each Phase4E3 candidate is refit walk-forward at every target with `fit_zone`, then scored with `zone_distribution`. Only `draws[:t]` is passed into either API. Feature construction therefore ends at `t-1`; the fixed two-target purge makes the latest fitted label `t-3`. Each row records and asserts both prefix hashes, maximum source positions, fit coefficient hashes, marginal-vector hashes, and the fixed-cardinality marginal sum.

## Selection fence and frozen identity

- Selection uses exactly the 240 DLT targets immediately before the outer, positions 923–1162 and issues 2024036–2025123.
- The targets are the exact E16 identity and are split into four chronological, non-overlapping blocks of 60.
- Eligibility requires both `rho>0` and slope `>0` in at least three blocks. Eligible candidates are ordered by median rho and then registered order. Positive-block count is only the eligibility gate.
- Candidate selection completes before E17 loads outer rows. No outer label is exposed to fitting or selection.
- The outer remains the exact E13–E16 120-target identity. Its first 60 remain calibration/descriptive evaluation only; its last 60 remain frozen evaluation.

| Window | Issues | Target positions | Identity SHA-256 |
|---|---|---:|---|
| DLT selection 240 | 2024036–2025123 | 923–1162 | `dc6a1c76dca9267ac49631c7a7eb4abddc4853996e7eea338746b4554828f689` |
| DLT outer 120 | 2025124–2026093 | 1163–1282 | `1b30535f4e015816acbd29fc6650a0ff62ad00409ceab5fc362729c3353aae0e` |
| SSQ outer 120 | 2025117–2026085 | 3362–3481 | `eac6080791c889147fb41d13e55ab199c1c91cdc133bacb413a91233099acc72` |

## Four-block stability

Cells show `rho / slope`; a check means both values are positive.

| Candidate | B1 | B2 | B3 | B4 | Positive blocks | Median rho | Eligible |
|---|---:|---:|---:|---:|---:|---:|---|
| Raw E16 | 0.064246 / 3.013914 ✓ | -0.017904 / -0.699361 | 0.050743 / 2.236644 ✓ | 0.006833 / -0.030275 | 2 | 0.028788 | no |
| Reverse E16 | -0.064246 / -3.013914 | 0.017904 / 0.699361 ✓ | -0.050743 / -2.236644 | -0.006833 / 0.030275 | 1 | -0.028788 | no |
| Surprise/renewal/N01 | 0.053902 / 3.012464 ✓ | -0.029548 / -1.256066 | 0.016111 / 3.444346 ✓ | 0.005055 / 0.308694 ✓ | 3 | 0.010583 | **yes, selected** |
| Transition/graph/N02 | 0.022470 / 1.133021 ✓ | 0.000649 / 0.283227 ✓ | -0.019489 / -1.091012 | -0.026170 / -2.093416 | 2 | -0.009420 | no |

## Frozen last-60 evaluation

The association unit is one draw × one candidate number. Fixed-size set association is not an acceptance gate.

| Game/zone | Decision | Origin | Observations | Spearman rho | Descriptive slope | Diagnostic pass |
|---|---|---|---:|---:|---:|---|
| SSQ front | Raw E16 | inherited | 1,980 | 0.016280 | 0.265785 | yes |
| SSQ back | Raw E16 | inherited | 960 | 0.016103 | 1.850398 | yes |
| DLT front | Surprise/renewal/N01 | E17 selected | 2,100 | 0.037638 | 2.167111 | yes |
| DLT back | Reverse E16 | inherited | 720 | 0.017411 | 4.577729 | yes |

The machine-readable split reports include rho and slope separately for every canonical number. DLT-front's 35 last-60 per-number results are shown below to make clear that the positive pooled individual-number association does not mean every number has a positive association.

| Number | Rho | Slope | Both positive |
|---:|---:|---:|---|
| 1 | 0.192477 | 16.583610 | yes |
| 2 | 0.006416 | -1.875088 | no |
| 3 | 0.092985 | 9.904402 | yes |
| 4 | 0.025480 | 2.655363 | yes |
| 5 | -0.038581 | -2.127952 | no |
| 6 | -0.074118 | -8.919388 | no |
| 7 | 0.150048 | 6.307675 | yes |
| 8 | 0.093030 | 2.067172 | yes |
| 9 | 0.105862 | 1.659586 | yes |
| 10 | 0.121370 | 11.021492 | yes |
| 11 | -0.055461 | -7.432594 | no |
| 12 | 0.197289 | 21.571325 | yes |
| 13 | -0.079639 | -4.368007 | no |
| 14 | 0.069723 | 2.787135 | yes |
| 15 | 0.241222 | 32.453082 | yes |
| 16 | -0.019818 | -0.891354 | no |
| 17 | -0.070575 | -5.657279 | no |
| 18 | -0.087764 | -11.265730 | no |
| 19 | -0.047166 | -2.295316 | no |
| 20 | 0.007747 | 3.188531 | yes |
| 21 | -0.046013 | -2.296940 | no |
| 22 | 0.043900 | 3.108686 | yes |
| 23 | 0.165756 | 12.508810 | yes |
| 24 | -0.129117 | -14.411420 | no |
| 25 | -0.256318 | -36.998934 | no |
| 26 | -0.073993 | -5.699149 | no |
| 27 | -0.073371 | -1.899733 | no |
| 28 | 0.020214 | 4.744301 | yes |
| 29 | 0.201007 | 11.197172 | yes |
| 30 | -0.173229 | -15.307557 | no |
| 31 | -0.005223 | -1.419643 | no |
| 32 | -0.011324 | 2.116140 | no |
| 33 | -0.197730 | -23.429324 | no |
| 34 | -0.008493 | 3.731126 | no |
| 35 | -0.404201 | -49.237653 | no |

## DLT-front single-group hit-rate evaluation

The accepted hit-rate unit is one predicted number group for one target draw. For a group of size `K`, its hit rate is `overlap_count / K`. The aggregate value for a fixed size is the maximum hit rate among the evaluated groups; cross-draw sums and “any number hit” rates are not used.

| Group size | Best single-group hit rate | Best group issue | Hit count / group size |
|---:|---:|---:|---:|
| 5 | 0.6000 | 2026074 | 3 / 5 |
| 8 | 0.5000 | 2026075 | 4 / 8 |
| 10 | 0.4000 | 2026075 | 4 / 10 |
| 12 | 0.3333 | 2026091 | 4 / 12 |
| 15 | 0.3333 | 2026091 | 5 / 15 |
| 20 | 0.2500 | 2026091 | 5 / 20 |

Each JSON fixed-size record contains the complete per-group list plus `best_single_group_hit_rate`, `best_single_group_issue`, `best_single_group_hit_count`, and `best_single_group_number_count`. No arbitrary cross-period hit-rate aggregation is emitted.

## Exact-ticket gates and evidence

The E13–E16 exact-ticket comparison is copied unchanged. No compressed space is accepted; DLT's first-ranked-space evaluation remains 59/60 and SSQ's remains 52/60; `gates_changed=false`. E17 does not use the alternative number model to construct or rescore exact tickets.

Machine-readable evidence is in `artifacts/phase4e17/summary.json` and each game's `report.json`, `inner-rolling-report.jsonl`, and `outer-rolling-report.jsonl`. The reports hash the source data, registered oracle, Phase4E3 model and DLT selection receipt, E13–E16 scripts/summaries/reports/rows, the E17 script and candidate registry, generated rows, exact outer identities, selection identity, and all four block identities.

No E9–E16 code or artifact, serving selection, release, or promotion gate was modified.
