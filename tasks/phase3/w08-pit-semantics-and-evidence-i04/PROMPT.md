# Phase 3 W08 PIT semantics correction and full evidence recovery i04

> SUPERSEDED: Do not execute this task. Its archived-publication prerequisite was replaced by Phase 3 v1.1 sequence-safe isolation.

You are working in a remote VPS container. Complete the Phase 3 PIT
precondition recovery with immutable identity `p3-pit-prep-20260809-i04`.
This task is
results-blind: it must never run formal W08-W13 research, create a formal
`artifacts/phase-3/<release-id>` release, make a production prediction,
promote a Champion, or perform betting-related actions.

## Objective

Make the Phase 3 PIT precondition genuinely ready only when it can be proved
from preserved, independently auditable evidence. First correct a discovered
semantic defect in the current W02 implementation: `prior_draw_result` is a
feature from an earlier source issue, while the current ledger incorrectly
binds it to the target issue's label. A result for the target issue is an
outer-evaluation label and cannot be treated as a feature available before
that same target's prediction lock.

The authoritative boundary is `tasks/phase3/README.md`, particularly the
requirements that every feature must be available before the target lock and
that target numbers are labels only. Read the overall design, detailed plan,
runtime runbook, current PIT preparation document, existing i01/i02 evidence,
and all relevant code/tests before editing.

## Required implementation

1. Create the new immutable PIT preparation bundle at
   `artifacts/phase-3-pit/p3-pit-prep-20260809-i04/`; do not rewrite i01/i02
   or the cancelled i03 remote worktree. The new bundle must explicitly record
   its parent chain and the i03 cancellation as non-authoritative recovery
   context.
2. Correct the data-time model and schemas so each ledger fact has an explicit
   `target_issue`, a feature `source_issue`, an immutable source draw binding,
   and an independently defined `prediction_locked_at`. A source issue must
   precede its target in the game sequence. The validator must reject the
   former same-issue mapping, future-source mapping, missing source binding,
   or a source result that is only a current retrospective view.
3. Derive the exact set of real atomic feature facts needed by the frozen
   Phase 3 historical evaluation. Do not claim that a 400-row target-label
   inventory is the feature inventory unless that is independently proved by
   the actual evaluator. Record all exclusions and minimum-training effects.
4. Do full, not sampled, discovery over every required fact against credible
   public historical archives. Preserve each query request/response, source
   URL, archive capture timestamp, original bytes or an immutable response
   digest, parser version, number binding result, and failure/empty/conflict
   outcome. A representative sample may be diagnostic only and must never be
   used to assert a whole-range result.
5. An eligible fact requires an archived original that binds game, source
   issue, numbers and an `independent_archive_capture_timestamp`; its exact
   availability time must be strictly before the corresponding independently
   defined target lock. Never derive availability from draw date, HTTP Date,
   current-page values, retrieval time, first-seen time, CMS PublishDate, or
   a schedule. Never invent timestamps or numbers.
6. Build and validate the bundle bottom-up. Include the complete ledger,
   archival originals/receipts or complete negative outcomes, manifest,
   data-time contract, results-blind preregistration, independent recompute,
   and expanded tamper tests. The validator must derive readiness only from
   disk evidence and must report coverage as a numerator/denominator over the
   actual feature facts.
7. Set `READY_FOR_RESULTS_BLIND_FREEZE` only if every actual fact is eligible,
   all hash and source bindings pass, `blocking_findings=0`, and the resulting
   formal workload still has valid minimum training history. Otherwise deliver
   a truthful `HOLD_PENDING_PIT_EVIDENCE`, with zero formal results and an
   actionable per-source gap ledger. Do not change status to READY merely to
   satisfy this task.
8. Update the PIT preparation document and runtime runbook to describe the
   corrected source-to-target semantics, the exact coverage outcome, and the
   recovery path. The text must distinguish known evidence from unavailable
   evidence and must not overgeneralize from a sample.
9. Add focused tests for source-target semantics, the former same-issue error,
   future leakage, receipt/source tampering, full-coverage arithmetic, and
   the truthful HOLD/READY branches. Preserve Phase 1, Phase 2, Phase 2.1,
   existing `config/phase3/` candidate contracts, and previous PIT bundles.

## Acceptance and commit

Run the commands frozen in the task spec and any new PIT validator/tamper
checks. A hold is an acceptable evidence result only when it is derived from
the complete per-fact ledger; it is not formal Phase 3 completion. Commit all
changes with `git add -A` and a descriptive commit message. Do not push.
