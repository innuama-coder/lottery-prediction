# Phase 4 MVP runtime contract

This runbook describes the result-blind operating surface frozen by T01. It is
not evidence that the product has been implemented, deployed, or accepted.

## Runtime and identities

Use Python 3.12 and the headless entry point `python -m lottery_system.phase4`.
Every mutating command requires an explicit runtime or release root, object and
contract identities, and an explicit clock. Formal operation rejects `latest`,
globs, modification-time selection, implicit network access, and environment
variables that alter scientific parameters.

Preparation, staging, runtime, and formal roots are disjoint. Never write under
the protected Phase 0–3 artifact roots. A Phase 4 data chain starts from the
four fixed `baseline-v1` identities in `config/phase4/genesis.json`; successors
retain that genesis and name their direct Phase 4 predecessor.

## Closed-loop order

For each game, execute `prepare -> predict -> lock -> ingest -> verify ->
unlock -> score -> autoresearch -> decision -> next forecast`. SSQ and DLT
share pure code only: Champion, configurations, forecasts, metrics, research
budgets, alpha wealth, and scientific state remain separate.

Forecasts must lock before 18:00 Asia/Shanghai on the explicit calendar entry.
No late run may manufacture a valid lock. Official labels become available to
the scorer only after two-source verification and a matching immutable lock.
The durable unlock receipt never contains numbers or a reusable capability;
each scorer process reacquires a PID-bound, non-serializable capability.

## Exit and recovery handling

- `0`: PASS or READY.
- `20`: HOLD; preserve the receipt and resume only from a verified checkpoint.
- `30`: retryable terminal was recorded; do not erase it.
- `4`: immutable identity reuse.
- `5`: contract or evidence mismatch.
- `6`: security or causality failure.
- Any other nonzero exit is FAIL unless a frozen command contract says more.

Checkpoints bind the run, plan key, ledger head, inputs, outputs, stage, and
counter state. Before resume, revalidate every binding. Retries append a new
attempt and never duplicate forecast, unlock, score, experiment, decision, or
alpha-spending facts.

## Source and correction handling

Only an unexpired Phase 4 source policy may authorize public read-only GETs.
SSQ requires `swlc` plus independent `ydniu` corroboration; DLT requires
`gdlottery` plus `ydniu`. Single-source results remain pending, conflicts fail
closed, and raw public responses are stored by hash in Phase 4 staging only.

An official correction appends a new revision. First recompute affected scores,
windows, and the score-side impact. Then create the research remediation,
archive or requalify affected candidates, and prove alpha history unchanged.
Only after both receipts exist may the orchestrator close the correction.
Historical results, scores, decisions, spending, and locked forecasts remain.

## Scheduler readiness

The deployable adapter is a user-level systemd oneshot service and five-minute
timer in Asia/Shanghai, with `Persistent=true`, `AccuracySec=1s`, and
`RandomizedDelaySec=0`. Audit the absolute interpreter, arguments, working
directory, timer, concurrency policy, next plan, and application lease. This is
an installation snapshot, not a Phase 5 continuous-SLO claim.

## Scientific wording

Phase 4 can prove a safe operational and research capability. Synthetic
recovery and the full-rule known answer do not prove a real lottery advantage.
Champion remains M0 for both games, real Top-K cells remain
`insufficient_observation`, and a qualified historical or synthetic candidate
is at most `shadow_candidate`.

## Formal release and evidence return

T15 freezes the implementation commit, offline wheelhouse, release venv,
independent script snapshot, actor assignment, resources, seeds, and an empty
release. T16–T24 use only that interpreter and script snapshot. Evidence closes
in order: evidence manifest, independent replay, validator, independent review,
machine delivery statement, and acceptance postcheck. Retrieve only explicitly listed
manifest paths and compare path sets, byte counts, and SHA-256 at both ends.
