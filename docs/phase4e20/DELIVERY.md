# Phase4E20 SSQ supervised compression and coverage delivery

## Decision

Phase4E20 is delivered as `NO_PROMOTION`. The inner-only maximin rule selected `sup_ensemble_unique_latin`, but the frozen outer hard gate failed. P4E6 remains `P4-P4E2-20260815-r12 / PROSPECTIVE_ONLY`; no DLT source, parameter, model, artifact, release, or serving path changed.

The selected candidate's minimum registered-N average prize was:

| Split | Binding N | Minimum yuan/ticket | Strictly > 2 |
|---|---:|---:|---:|
| Calibration 60 | 1,000 | 0.631250000 | no |
| Evaluation 60 | 1,000 | 0.545250000 | no |
| All 120 | 1,000 | 0.588250000 | no |

The binding hard-gate cell was evaluation N=1,000: 32,715 yuan over 60,000 complete tickets, or 0.54525 yuan/ticket. Its tier counts were 0/0/0/10/252/5,639 for tiers 1–6. Every split requires every registered N to be strictly greater than 2, so isolated higher cells do not qualify.

The frozen raw E19 control was reproduced as a baseline with the exact published minima: calibration 0.6589166667, evaluation 1.0851166667, and all-120 0.9671277778. The deterministic random baseline minima were 0.7002500000, 0.7452166667, and 0.7299333333 respectively. Outer results were not available to candidate selection.

## Technical iteration

The runner is `scripts/phase4e20/ssq_supervised_compression.py`. It adds the missing supervised and portfolio layers without changing prizes, N values, windows, or threshold:

- Per-number red and blue ridge heads use a preregistered finite alpha grid `{1, 25}` and 1,200 historical training draws at each frozen origin. Each origin has 39,600 red rows and 19,200 blue rows.
- Registered inputs include 30/60/120/240/360/720/1200 frequency, gap and gap deviation, multi-scale frequency trends, transition and graph statistics, regularized number identity, red zone/parity/sum/consecutive context, 3/52/156 draw-position Fourier seasonality, and red-blue interactions.
- Coefficients, intercepts, normalization means/scales, training row bounds, input/label hashes, coefficient hashes, and strict-lag lineage are recorded for all five origins: 3122, 3182, 3242, 3302, and 3362.
- Each 60-draw inner block and the 120-draw outer window uses one origin-frozen fit. Horizon forecasts advance gaps and Fourier phase while decaying trend/transition/graph state; they never consume an observation inside the frozen block/window.
- Four preregistered portfolios cover ridge-a1 unique round-robin, ridge-a25 unique Latin, supervised-head union with unique Latin, and supervised-head union with a two-ticket red cap. All use one deterministic nested complete-ticket order through N=100,000.
- Unique candidates contain 100,000 distinct red combinations at N=100,000. The layered candidate contains 60,000 distinct red combinations and never exceeds two tickets per red combination. All candidates cover all 16 blue numbers by N=1,000.

Selection used only the four inner blocks. It maximized the worst registered-N average over the worst block, then median registered-N uplift versus the raw E19 control, then registered order. Inner maximin scores were 0.5825833333 (`sup_a1_unique_rr`), 0.6216666667 (`sup_a25_unique_latin`), 0.6339500000 (`sup_ensemble_unique_latin`), and 0.5917500000 (`sup_ensemble_layered2_rr`).

## Reproduction and validation

Run from the repository root with CPython 3.12 and NumPy:

```bash
PYTHONPATH=src python3 -m unittest tests.phase4.test_phase4e20_supervised_compression -v
PYTHONPATH=src python3 scripts/phase4e20/ssq_supervised_compression.py
replay_dir=$(mktemp -d /tmp/phase4e20-replay.XXXXXX)
PYTHONPATH=src python3 scripts/phase4e20/ssq_supervised_compression.py --output-dir "$replay_dir"
PYTHONPATH=src python3 -m unittest \
  tests.phase4.test_phase4e17_artifacts \
  tests.phase4.test_phase4e17_prize_metrics \
  tests.phase4.test_phase4e17_selection \
  tests.phase4.test_phase4e19_ssq_prize_aware \
  tests.phase4.test_phase4e20_supervised_compression -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase4 -p 'test_*.py' -v
sha256sum -c artifacts/phase4e20/dlt-isolation-baseline.sha256
jq '{serving_release,terminal_status}' artifacts/phase4e6/delivery/decision.json
```

Observed results on the delivery host:

- Primary frozen pipeline: 354.30 seconds internal, 5:55.82 wall, peak RSS 260,144 KiB.
- Independent full replay: 328.33 seconds internal, 5:29.59 wall, peak RSS 276,220 KiB.
- Independent replay: 12 deterministic payload files byte-identical; normalized `report.json` identical after removing only `runtime_seconds` (`5df768696193dfdb13417e61ac1a046cd335b91bf64e9bb4d4b96a3bb0fb95ba`).
- Focused E20 tests after artifact generation: 8/8 passed in 9.05 seconds.
- Focused E17/E19/E20 regression: 36/36 passed in 17.34 seconds.
- Full dirty-worktree Phase4 discovery: 323 tests in 1,042.84 seconds; 302 passed, 20 expected superseded-evidence skips, and the single from-scratch release test correctly stopped at `FAIL_UNFROZEN_MODEL_PATH` because E20 paths were not yet committed. It was rerun after the implementation commit as recorded below.
- Post-commit from-scratch dual-game release and independent replay: 1/1 passed in 2,427.57 seconds (40:27.70 wall), peak RSS 78,952 KiB. Combined with the discovery run, all 303 runnable Phase4 cases passed; 20 cases remained expected superseded-evidence skips.
- All four frozen DLT/P4E6 hashes passed, and serving remained `P4-P4E2-20260815-r12 / PROSPECTIVE_ONLY`.

## Evidence map

- `candidate-registry.json`: finite supervised-head grid, four portfolio candidates, and both baselines.
- `model-feature-lineage.json` and `coefficients.json`: feature names, strict training bounds, normalization, coefficients, and hashes for five origins.
- `portfolio-hashes.json`: all 2,160 unique origin/target/candidate ticket-order records, hashes, layer caps, and registered-N coverage.
- `inner-rolling-report.jsonl` and `outer-rolling-report.jsonl`: exact per-target tier counts, prize totals, averages, and nested-N identities.
- `calibration-summary.json`, `evaluation-summary.json`, and `all-120-summary.json`: selected-candidate tier counts, averages, and 95% draw-level normal-approximation confidence intervals.
- `replay-evidence.json` and `independent-replay-evidence.json`: in-process exact replay, future-label mutation, and separate full-run comparison.
- `report.json`, `delivery/decision.json`, and `delivery/manifest.json`: selection, all candidate/baseline comparisons, binding gate, isolation, decision, and file hashes.

All Phase4E18 and Phase4E19 paths are preserved append-only.
