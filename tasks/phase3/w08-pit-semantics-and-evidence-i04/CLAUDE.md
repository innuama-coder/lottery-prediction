# Remote context

- Use public network access only during evidence preparation. Final validation
  must consume only preserved local evidence.
- The repository's protected Phase 1/2/2.1 artifacts are read-only. Existing
  PIT bundles are immutable parents and must not be edited or deleted.
- The i03 remote attempt was cancelled after a verifier-spec defect. Its patch
  was never an accepted delivery and its six representative probes do not
  establish full PIT coverage.
- The old W02 ledger treats each target label as its own `prior_draw_result`.
  This is the semantic defect to correct, not a loophole to exploit.
- Do not use a result label, a draw date, or a live-page retrieval timestamp as
  evidence that a feature was available before the prediction target.
