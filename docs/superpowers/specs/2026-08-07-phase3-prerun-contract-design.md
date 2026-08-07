# Phase 3 Pre-Run Contract Package Design

**Status:** Design approved for W01-W03 implementation

**Scope:** Phase 3 W01-W03 only. This package freezes inputs, point-in-time eligibility rules, and the result-blind experiment contract. It does not implement models, create a formal release, run a VPS experiment, or produce Phase 3 results.

## Goal

Create a deterministic, machine-readable contract package that makes a Phase 3 historical run reproducible and prevents scientific rules from being changed after results are observed.

The package must allow a validator to answer, before any Phase 3 model result exists:

1. Which frozen upstream files are allowed and whether their identities match.
2. Which fields can be used before each target issue, with unknown availability failing closed.
3. Which models, folds, metrics, budgets, and classification rules are fixed for the run.

The current Phase 1 records have `knowledge_class=retrospective_current_view` and `available_at_utc=null`. The contract therefore records them as historical labels or unproven feature candidates, never as proven point-in-time features. If the ledger cannot establish a legal M1 training history, the contract terminates in `HOLD` or scientific `indeterminate`; it does not infer availability from draw dates or current web views.

## Authority and Frozen Inputs

The only Phase 3 authority is `tasks/phase3/README.md` at commit `0f62062d30af0cc676edde15849a33f5bc33a8aa`, SHA-256 `0b1bcc329c8063a8336e188e7e88b99542c038cc28a51387b81867d5953e1cdf`.

W01 binds these existing files without modifying them:

| Role | Path | SHA-256 |
| --- | --- | --- |
| Phase 1 acceptance | `artifacts/phase-1/acceptance/phase1-acceptance.json` | `959b1dddacf453dbff347786d572de4cd8c52d1b7eb2e7a3805cffa2a166bb18` |
| Phase 1 manifest | `artifacts/phase-1/baseline-v1/manifest.json` | `0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1` |
| Phase 1 draws | `artifacts/phase-1/baseline-v1/draws.jsonl` | `f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1` |
| Registered rules | `artifacts/phase-2/contracts/input-manifest.json` | `36ad90a204a2d0ebab5ddbfff3a4246f267e02cdd2cfe961200e515c27ef90ad` |
| Phase 2.1 acceptance | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json` | `d5dde1d4488290e41998c1e7f6d04b1b3ae094408716571ceb5451324cb8e8b4` |
| Phase 2.1 recursive manifest | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/manifest.json` | `c2fb2e4a60ed214ce4648a93a1d8b11aed2ebd41b920dd549158e5adc821e3c6` |
| Phase 2.1 historical audit | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/historical-audit.json` | `a3d0f1f2dc371e3ff53256c6f09d5b47471f84567e33feaa8efa9c8349b8a8d1` |
| Phase 2.1 power | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/power.json` | `99bca12e9452435fbc32c67686d4dc905ea4771b8bb7b7d62c02983e24b98a10` |

W01 must record the current Git commit and file identities in a new manifest. It must report exactly 400 `DrawRecord` rows, 200 per game, and treat the 800 source observations as lineage only.

## Package Components

### 1. Input manifest

`config/phase3/input-manifest.json` is the immutable W01 inventory. Each entry contains `role`, repository-relative `path`, `sha256`, byte size, record count where applicable, upstream status, and allowed use. The manifest also records the authority commit and the explicit game/rule coverage. It rejects missing files, changed hashes, duplicate roles, unsafe `latest` references, and wildcard paths.

### 2. Availability ledger and data contract

`config/phase3/availability-ledger.json` is append-only and result-blind. A ledger row identifies:

- `game`, `target_issue`, and `source_field`;
- `prediction_locked_at` and the target label unlock boundary;
- source path/content hash and evidence method;
- event time and `available_at_utc`, when proven;
- eligibility: `eligible`, `ineligible`, or `unknown`;
- a required reason code and provenance reference.

The ledger generator creates rows from the declared input fields, not from observed model performance. `available_at_utc < prediction_locked_at` is the only eligible feature rule. `null`, missing, inferred, current-view, post-draw, or revised-only timestamps are not eligible. Target draw numbers are labels and are never eligible feature inputs.

`config/phase3/data-time-contract.json` records the field allowlist, forbidden fields, rule segments, revision policy, minimum training length, and the fail-closed decision for unknown availability.

### 3. Result-blind preregistration

`config/phase3/preregistration.json` freezes:

- authority and input/ledger identities;
- separate SSQ and DLT outer targets, expanding-window cutoffs, minimum training lengths, and inner folds;
- M0 permanent Champion and mandatory M1 challenger definitions;
- M2-M4 result-before opening gates and default `not_opened` state;
- primary relative joint log-score skill, secondary Brier/calibration/stability metrics, guards, negative controls, and Top-1000 diagnostic-only status;
- model/feature search ranges, seed derivation, numerical tolerances, one-factor change rule, error budget, workload budget formula, retry/timeout rules, stopping rules, and allowed classifications;
- role separation for implementation, statistics, independent replay/review, and final acceptance.

The preregistration is valid only when its `status` is `results_blind`, its referenced W01/W02 identities match, and no Phase 3 result or score is present. The first formal release will receive a unique `release_id` only after W04-W07; this package does not create one.

## Validation and Failure Behavior

The package will use JSON Schemas under `schemas/phase3/` and one offline validator at `scripts/phase3/validate_prerun_contract.py`. The validator performs:

1. authority and upstream hash closure;
2. record count and game/rule coverage checks;
3. availability timestamp inequalities and fail-closed eligibility checks;
4. outer/inner fold uniqueness and temporal separation;
5. model/feature/preregistration cross-reference checks;
6. result-blindness, role separation, budget, classification, and forbidden-action checks.

Any mismatch returns a nonzero exit code and a machine-readable `HOLD` or `FAIL / STOP` receipt. The validator must never silently discard a bad row or choose a `latest` file. No Phase 3 result directory is created by validation.

## Planned Files

Create:

- `schemas/phase3/input-manifest.schema.json`
- `schemas/phase3/availability-ledger.schema.json`
- `schemas/phase3/data-time-contract.schema.json`
- `schemas/phase3/preregistration.schema.json`
- `config/phase3/input-manifest.json`
- `config/phase3/availability-ledger.json`
- `config/phase3/data-time-contract.json`
- `config/phase3/preregistration.json`
- `scripts/phase3/validate_prerun_contract.py`
- `tests/phase3/test_prerun_contract.py`

The existing Phase 1/Phase 2/Phase 2.1 artifacts, tests, and runtime source remain unchanged.

## Acceptance Criteria

W01-W03 is complete only when:

- all eight frozen input identities match and all declared paths exist;
- the manifest reports 400 draws, 200 per game, and 800 lineage observations without sample inflation;
- every declared source field has a ledger status and every unknown availability is fail-closed;
- SSQ/DLT folds are separate, each outer target is unique, and no inner fold crosses its outer target;
- M0/M1 and M2-M4 opening states are explicit, with M0 permanent and M1 mandatory;
- the preregistration contains no result, score, selected model, or post-result threshold;
- all forbidden production, public non-uniform prediction, betting, automatic purchase, and yield claims are rejected;
- the validator passes in offline mode and emits a stable receipt; negative fixtures prove tampering, future-field leakage, missing availability, duplicate targets, and result-after-preregistration rejection.

Passing this package authorizes W04 implementation against synthetic data. It does not authorize W07 formal release or W08 historical execution.
