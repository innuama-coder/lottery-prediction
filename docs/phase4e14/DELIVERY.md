# Phase4E14 fixed-size confidence calibration

## Outcome

Phase4E14 completed as a bounded retrospective diagnostic and was **not accepted**. Every SSQ and DLT zone failed the registered last-60 outer rule because at least one fixed set size lacked positive Spearman association, positive descriptive slope, or monotonic bucket behavior within the registered tolerance. No model was promoted, and P4E6 continues serving `P4-P4E2-20260815-r12` unchanged.

The result supports the E14 conclusion that pooled or set-level confidence can mislead: fitted transforms selected on pre-outer data did not generalize consistently across the fixed-size outer evaluation.

## Frozen design

- Candidate transforms were raw marginal mass, the reverse-mass control, empirical rank/overlap quantile mapping, and PAVA isotonic expected overlap.
- The 120 targets immediately before the frozen outer window were split into first 60 transform-fit rows and last 60 transform-holdout selection rows.
- No outer label entered fitting or selection. Every target `t` used history only through `t-1`.
- The E9–E13 frozen 120-row outer identity was retained, with the first 60 used only for outer calibration evaluation and the last 60 used for final evaluation.
- Selection was separate for every game, zone, and fixed set size. The registered reverse-mass candidate remained control-only and could not win.
- Number rankings, selected confidence sets, and the E13 exact-ticket comparison were copied unchanged.

## Evaluation result

All four game/zone results failed. Across the 22 fixed sizes, none passed the complete last-60 rule. Some isolated sizes had positive rho and slope, but every fixed size showed non-monotonic bucket behavior outside the registered allowance; several also had zero or negative association.

The machine-readable evidence is in:

- `artifacts/phase4e14/summary.json`
- `artifacts/phase4e14/{ssq,dlt}/report.json`
- `artifacts/phase4e14/{ssq,dlt}/inner-rolling-report.jsonl`
- `artifacts/phase4e14/{ssq,dlt}/outer-rolling-report.jsonl`

Each game report binds source data, the registered P4E2 oracle, the E13 script/summary/report/outer rows, the inherited outer identity, the pre-outer selection identity, and both generated E14 row files by SHA-256.

## Interpretation fence

E14 scores are retrospective association or expected-overlap scores. They are not true lottery probabilities, ticket win probabilities, or evidence of prospective improvement. The failed result does not modify the immutable serving selection or relax any exact-ticket gate.
