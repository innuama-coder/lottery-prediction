# Phase4E13 partial-number hit evaluation

## Scope and interpretation

Phase4E13 evaluates partial number overlap as a separate retrospective diagnostic. It does not change the exact full-ticket gates inherited from E12, does not promote a model, and does not modify P4E6 serving release `P4-P4E2-20260815-r12`. Status remains `RETROSPECTIVE_BACKTEST_ONLY` and `promotion_eligible=false`.

For each target and zone, the evaluator exhaustively scores every legal combination with the registered P4E2 F01–F14 feature formula and the P4E12-selected walk-forward configuration. A stable softmax normalizes those scores within the zone. Each number's marginal inclusion mass is the sum of normalized combination mass over every combination containing that number. The masses sum to the zone draw count, not one.

The confidence assigned to a selected number set is its cumulative marginal inclusion mass divided by the zone draw count. This is a normalized model score for retrospective association analysis. It is not a true lottery probability, a ticket win probability, or a guarantee of winnings.

## Frozen design

- Both games use exactly the E9–E12 final 120 targets, split into the first 60 calibration and last 60 evaluation draws.
- The selected configuration is the strongest E12 candidate, chosen exclusively on the 120 targets immediately before the outer window. No outer label participates in model, mask, feature, history, or candidate selection.
- Every row records `maximum_training_position=target_position-1`, its corresponding issue, and `strict_lag=true`.
- Front confidence sets use top 5, 8, 10, 12, 15, and 20 marginally ranked numbers. Back sets use top 1, 2, 3, 4, and 6. Equal marginal mass is broken by canonical ascending number.
- Source data, registered P4E2 oracle, immutable serving model, selected model configuration, experiment configuration, E12 report, and E12 outer rows are SHA-256 bound in each report.

## Reported metrics

Each game, split, zone, and set size reports average overlap; aggregate number-level hits and trials; number hit rate with Wilson 95% interval; any-hit draw count/rate; and exact all-zone-numbers-hit draw count/rate. The outer JSONL retains the selected numbers, overlap, any-hit indicator, and exact-zone indicator for every draw.

Wilson intervals on number-level hits are descriptive because number indicators within one draw are dependent. Draw-level any-hit and exact-zone intervals use draws as trials.

Confidence association is evaluated separately for each game's calibration/evaluation front/back zone. The registered observation unit is one draw × one ladder size. Observations are sorted into five equal-count confidence buckets. Acceptance requires positive Spearman rho and positive descriptive OLS slope, plus non-decreasing bucket hit rates. One adjacent inversion no larger than 0.02 is permitted only when the two Wilson intervals overlap. The pooled ladder analysis partly reflects set size and must not be interpreted as fixed-size probability calibration.

## Results

Generated machine-readable results are in `artifacts/phase4e13/summary.json`, per-game `report.json`, and per-game 120-line `outer-rolling-report.jsonl`. Numeric results and the unchanged E12 exact-ticket comparison are populated from those artifacts after execution.

## Full-ticket comparison

The E12 split-conformal first-ranked-space evaluation and all fixed-space compression gates are copied unchanged under `full_ticket_comparison`. Partial number hits neither replace nor relax those exact-ticket gates.
