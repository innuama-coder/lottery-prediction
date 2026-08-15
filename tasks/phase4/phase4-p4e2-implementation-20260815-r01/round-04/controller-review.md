# Round 04 controller review and root cause

The controller intentionally terminated Round 03 with exit 143 after an
independent product-contract review.  This was a recoverable review stop, not
an infrastructure failure.  All source changes and the immutable failed
release directories `P4-P4E2-20260815-r01` and
`P4-P4E2-20260815-r02` remain preserved.

The r02 build completed, but independent replay failed closed with
`HOLD_REPLAY_MISMATCH:research_child`.  Its frozen runbook is also invalid in
this worktree because it names `.venv-phase4/bin/python` rather than the
interpreter that actually built the release.  Neither r02 artifact is to be
edited or reused.

## Root cause

The recovered P4E2 implementation has a simplified runtime and acceptance
layer.  It can serialize plausible summaries, stage names, booleans, and PASS
statuses without performing or independently reconstructing the corresponding
product lifecycle and model calculations.  Consequently, top-level evidence
can look coherent while the locked forecast, verified result, scored model,
research child, selection boundary, and scientific calculations are not a
single causally bound lifecycle.

The correction must therefore replace the shallow evidence path with real,
idempotent operations and bottom-up verification:

- schedule checkpoints must bind immutable output identities and hashes for
  prepare, forecast plus create-once lock, verified result ingest, guarded
  unlock and exact-forecast score, and score-driven AutoResearch child/shadow;
- the historical virtual-clock E2E must train only through the draw before the
  target, lock that target before its result becomes available, and score that
  exact lock with the exact frozen model and matching verified revision;
- selection must be frozen in an immutable receipt before report-only labels
  are accessible, with report-label mutation unable to change it;
- ablation must zero a feature group and renormalize the complete legal space,
  while permutation must shuffle held-out feature values and recompute the
  fitted model score;
- forecast/lock/CLI evidence must carry complete authority lineage, exact tie
  semantics, probability-layer counts, and stable canonical ranking; and
- D12/D15 must reconstruct these facts from bottom-up files and detect the
  controller-specified lifecycle, scientific, lineage, tie, CLI, and
  protected-root mutations rather than trusting asserted summaries.

Time-ordered folds will be used throughout so no fold trains on a future
observation.  Permutation evidence will follow the public definition of
shuffling a held-out feature and recomputing the fitted model metric; rotating
already-computed contributions is not acceptable.

