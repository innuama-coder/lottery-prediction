# Phase4E11 Mask Selection

## Objective

Evaluate whether a bounded feature-mask search improves the reliable nested ranking space while preserving strict walk-forward isolation. Candidate masks are selected only from the 120 draws immediately before the frozen outer 120-draw window.

## Candidate contract

- `e8_selected`: the frozen Phase4E8 mask for the game.
- `all14`: all registered features.
- `history_only`: the five history features.
- `history_structure`: the first twelve history and structure features.

For each game, candidates are ranked by split-conformal inner `K90`, then `K80`, then `K50`, with canonical score-tick ranking and exact boundary rechecks inherited from Phase4E9. The selected mask is fit again for the outer window. No outer evaluation label is used in candidate selection.

## Acceptance method

The outer window is split into 60 calibration draws and 60 independent evaluation draws. The reliability gate requires evaluation coverage at least `0.80` and Wilson 95% lower bound at least `0.75`. Nested spaces are reported for `1000, 2000, 5000, 10000, 50000, 100000` tickets, and compression is accepted only when the same gate passes.

This is a retrospective backtest artifact. It cannot promote or alter the unchanged Phase4E6 serving release (`P4-P4E2-20260815-r12`, `PROSPECTIVE_ONLY`). A ranking score is not represented as a true lottery probability.

## Artifacts

- `artifacts/phase4e11/summary.json`
- `artifacts/phase4e11/ssq/report.json`
- `artifacts/phase4e11/dlt/report.json`
- `artifacts/phase4e11/*/inner-rolling-report.jsonl`
- `artifacts/phase4e11/*/outer-rolling-report.jsonl`
