# Phase4E19 SSQ prize-aware optimization delivery

## Acceptance result

Phase4E19 is delivered as a complete deterministic SSQ-only retrospective pipeline. The hard gate did **not** pass, so the decision is `NO_PROMOTION`. P4E6 continues to serve `P4-P4E2-20260815-r12` with terminal status `PROSPECTIVE_ONLY`; no serving or DLT input, parameter, source, artifact, or release was changed.

Nested inner selection used the 240 SSQ draws immediately before the frozen outer, positions 3122–3361 (`2024028`–`2025116`), as four block-frozen chronological holdouts of 60. None of the five alternative candidates achieved positive prize uplift over the raw control in three blocks, so the registered `raw_control` fallback was frozen before outer scoring. The frozen outer is positions 3362–3481 (`2025117`–`2026085`), first 60 calibration and last 60 evaluation.

For the selected raw control, the minimum registered-N average prize was:

| Split | Minimum average prize (yuan/ticket) | Strictly > 2 |
|---|---:|---:|
| Calibration 60 | 0.6589166667 | no |
| Evaluation 60 | 1.0851166667 | no |
| All 120 | 0.9671277778 | no |

The hard gate requires every one of the 12 registered N values to exceed 2 in every split. Isolated candidate/partition averages above 2 are not a pass. Retrospectively, `prize_aware_multiscale` reached an all-120 maximum of 3.0633270833 at one partition, but its all-120 minimum was 0.8920541667 and its calibration/evaluation minima also failed. The most stable observed 36-cell minimum belonged to the random baseline at 0.7181666667; outer observations were not used to select any promotion candidate.

## Implementation

`scripts/phase4e19/ssq_prize_aware.py` now provides the runnable pipeline and reusable tested primitives:

- independent red and blue empirical-Bayes heads with strict-lag 360/720/1200 frequency, recency, and transition inputs;
- red zone, parity, sum, consecutive, red/blue parity, blue transition/recency, and historical red-pair combination-similarity features;
- a finite six-candidate registry: raw control, multiscale prize-aware, long-window, transition/joint, and two diversified variants;
- all-tier expected-prize scoring with fixed first prize 5,000,000 yuan, second prize 100,000 yuan, and registered tiers 3–6;
- deterministic enumeration/scoring of all 17,721,088 legal complete SSQ tickets, exact score/tie ranking, exact nested partition prefixes, and diversified blue/rarest-red coverage interleaving;
- per-draw and aggregate prize totals, average prize, tier counts, winners, winning rates, and draw-level normal-approximation confidence intervals;
- strict DLT hash and P4E6 serving identity refusal checks before and after execution.

Each of the 130 emitted feature lineage records contains its feature ID, scope, cutoff, registered/effective window, inclusive source bounds, input SHA-256, value SHA-256, lineage description, and strict-lag flag. Outer heads and portfolios use only data ending at position 3361. Mutation of all 120 outer labels leaves both the feature-bundle and selected portfolio hashes unchanged.

## Reproduction commands

Run from the repository root with CPython 3.12 and the existing Phase4 environment (including NumPy):

```bash
PYTHONPATH=src python3 scripts/phase4e19/ssq_prize_aware.py
PYTHONPATH=src python3 -m unittest tests.phase4.test_phase4e19_ssq_prize_aware -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase4 -p 'test_*.py' -v
sha256sum \
  artifacts/phase4e17/dlt/report.json \
  artifacts/phase4e17/dlt/outer-rolling-report.jsonl \
  artifacts/phase4e17/summary.json \
  artifacts/phase4e6/delivery/decision.json
jq '{serving_release,terminal_status}' artifacts/phase4e6/delivery/decision.json
```

The frozen-data run completed in 121.37 seconds on the delivery VPS, with peak RSS 351,608 KiB. A byte-identical independent replay completed in 140.56 seconds. The focused suite passed 11/11 tests. The full Phase4 regression command is recorded above; expected skips concern superseded T00–T24 evidence only. Its first dirty-worktree run passed 313 tests and skipped 20, while the clean-tree-only from-scratch release test correctly stopped at its `FAIL_UNFROZEN_MODEL_PATH` guard; that single test is rerun after the delivery commit.

Expected isolation hashes:

```text
40d52f1d4a97b2e8e4a4736aad994bf46e4a033cd342ec39e74b43dd6386d3fc  artifacts/phase4e17/dlt/report.json
9d8186a6d8bf3197747121fb66e9e78d846404f5af268e5fb5f5c7da66299634  artifacts/phase4e17/dlt/outer-rolling-report.jsonl
ff4e61df206638ae380f2d198188fe893f435d3b417df331aeebb06a31e7146c  artifacts/phase4e17/summary.json
d117e7bb7b0fe1ccc30d58c4971a53151e2db8b457068572d6a0b19c3990967e  artifacts/phase4e6/delivery/decision.json
```

## Machine-readable evidence

- `artifacts/phase4e19/report.json`: selection, inner evidence, all candidate/baseline outer comparisons, gates, leakage, replay, and isolation.
- `candidate-registry.json`, `feature-lineage.json`, and `strict-lag-hashes.json`: preregistration and input/value lineage.
- `inner-rolling-report.jsonl` and `outer-rolling-report.jsonl`: per-target exact partition results for every candidate and the random baseline.
- `calibration-summary.json`, `evaluation-summary.json`, and `all-120-summary.json`: frozen selected-candidate summaries.
- `replay-evidence.json`: exact portfolio replay and outer-label mutation evidence.
- `delivery/decision.json` and `delivery/manifest.json`: `NO_PROMOTION` decision and artifact hashes.

All prior Phase4E18 paths remain byte-identical to commit `f7db7601`; no Phase4E18 artifact was overwritten.
