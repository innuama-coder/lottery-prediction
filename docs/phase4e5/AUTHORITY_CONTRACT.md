# Phase 4E5 Authority Contract

Status: **FROZEN BEFORE OUTCOME OR EXTERNAL-METADATA INSPECTION**

Phase: P4E5 external / operational metadata

Base commit: `3a65b5331f8ec8cb80d288347103db8a39992654`

Branch: `codex/phase4e5-exogenous-metadata-20260820-r01`

Serving release: **r12 (unchanged)**

Prior P4E4 terminal status: **FEATURE_ENGINEERING_DELIVERED_PROSPECTIVE_ONLY (unchanged)**

## Authority boundary

P4E5 is a shadow-only research phase. It may produce engineering evidence, but it may
not change serving bytes, the r12 pointer, production ranking behavior, or any prior
release artifact. Only an independently accepted result may be described as eligible
for a later promotion decision; promotion itself is outside this phase.

No draw outcome, historical operational metadata, source response, report label, or
coverage statistic may be inspected before this contract, the registry, and their
freeze receipt are committed and pushed. Repository structure and Git state may be
inspected solely to create the isolated branch and this authority checkpoint.

## Causal and temporal rules

For a draw with prediction timestamp `T` and issue `i`:

- Calendar, scheduled weekday, holiday, draw-gap, and declared regime fields may use
  only facts fixed and knowable before `T`.
- Sales, jackpot, first-prize count/amount, second-prize count/amount, and provincial
  first-prize distribution are permitted only from draws strictly before `i`.
- A current-row or future-row operational value is forbidden even if an official page
  publishes it later. The join key must satisfy `metadata_issue < prediction_issue`.
- Provincial distribution is enabled only when official, reproducible, per-draw data
  has adequate coverage. A single announcement is evidence of field existence, not a
  license to infer missing history.
- Source revision, source-switch, field missingness, age, and staleness flags accompany
  every operational block. Missing official data remains missing; unofficial data can
  be documented for context but has no promotion authority and cannot silently fill it.
- Fit, feature selection, normalization, clipping, vocabulary construction, and
  imputation are learned from each training prefix only. Report labels never influence
  any fitted state or candidate choice.
- Payout and player-behavior metadata are correlational operational covariates, never
  causal mechanical draw information. A null or adverse result is an acceptable end.

## Source authority and provenance

The audit covers both SSQ and DLT independently. Only official first-party lottery
sources may confer promotion authority. Every acquisition attempt, including errors
and empty responses, must retain:

1. request method and URL (with secrets removed);
2. request body or an explicit empty-body marker;
3. response body bytes;
4. response headers;
5. UTC request and response timestamps;
6. HTTP status/error;
7. SHA-256 of request and response body bytes;
8. source revision or observed revision marker when exposed.

Replays consume the captured bytes, never a newly fetched response. Any parser change
creates a new derived artifact with its own digest while preserving the raw capture.

## Candidate freeze

The exact candidates, grids, folds, metrics, multiplicity family, and gates are frozen
in `config/phase4e5_registry.json`. Candidate families are limited to:

- calendar and scheduled-weekday encodings;
- official holiday proximity/indicator encodings;
- elapsed-day and scheduled draw-gap encodings;
- date-derived, predeclared schedule/source regimes;
- strictly lagged official sales and jackpot features;
- strictly lagged official first- and second-prize count and amount features;
- strictly lagged official provincial first-prize distributions, conditionally enabled;
- source revision/switch, missingness, age, and staleness quality fields.

No post-freeze feature may enter model selection. A newly discovered field requires a
future phase and a new prospective authority contract.

## Independent historical roles

All labels used by any P4E4 report/acceptance role and the preservation-only original
200 are contaminated for P4E5 reporting. After the freeze, their issue identifiers may
be read only to construct an exclusion set; their outcomes may not be used as P4E5
selection or report evidence.

For each game, sort all remaining eligible issues chronologically and identify
contiguous runs after removing the exclusion set. Choose the most recent run capable of
providing, in order, at least 240 development rows, 120 selection rows, and 120 report
rows. The last 120 rows are the sealed report window, the preceding 120 are selection,
and all earlier rows in that same run are development. The exact issue boundaries and
their digest must be written to a role receipt before any model fit or report-label
access. If either game lacks such a run, P4E5 fails closed to
`ENGINEERING_ONLY_NO_INDEPENDENT_WINDOW`; no alternative partition may be substituted.

Selection uses five deterministic expanding-origin folds wholly inside development and
selection history. Each validation block follows its training prefix; embargo is one
draw. Report is evaluated exactly once after the candidate and all fitted procedures
are sealed. Cross-game pooling is forbidden.

## Evaluation and promotion eligibility

The unit of evaluation is the issued draw. Proper scores are per-ball Bernoulli log
loss and Brier score, reported by ball pool and game, plus calibration intercept/slope
and expected calibration error. Ranking diagnostics (Top-1000 containment/recall and
Top-10 Shadow behavior) are secondary and cannot override proper-score failure.

Candidate comparisons form one frozen multiplicity family per game. Paired
draw-block bootstrap confidence intervals use the frozen seed and resamples; one-sided
p-values are Holm corrected across all non-baseline candidates. A candidate is merely
promotion-eligible only if every registry gate passes independently for both games,
including official coverage and provenance. Any failure yields engineering-only or
no-promotion status. Probability spread must arise from the fitted candidate and may
not be widened, sharpened, or temperature-adjusted for appearance.

## Required evidence

Delivery must include SSQ and DLT feasibility/coverage audits; raw provenance; leakage,
current-row rejection, future-mutation, deterministic replay, missingness, provenance,
and exact-normalization tests; Top-1000 and Top-10 Shadow outputs for both games; model
cards; feature diagnostics; ablation and permutation analyses; candidate selection and
sealed-report receipts; a prior-release byte inventory; a phase manifest; full Phase 4
tests; and independent acceptance evidence. All final evidence is committed and pushed.
