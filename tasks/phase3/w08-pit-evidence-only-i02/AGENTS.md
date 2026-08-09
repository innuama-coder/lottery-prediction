# Agent rules

> SUPERSEDED: Historical task record only. Do not execute. Phase 3 v1.1 uses `retrospective_sequence_safe`; new legacy PIT collection is prohibited.

- Use the repository scripts rather than hand-writing evidence JSON.
- Keep the original reconnaissance receipts in the i02 bundle so that the
  evidence manifest can hash-bind them.
- Treat `available_at_utc < prediction_locked_at` as a strict proof
  requirement. Unknown remains unknown.
- Commit the generated evidence and top-level preparation status before
  finishing. A clean worktree is required for delivery.
