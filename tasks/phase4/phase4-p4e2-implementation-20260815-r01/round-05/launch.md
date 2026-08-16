# Round 05 launch

Controller acceptance of `9739671a359658e867e39751e8bd43c5912b4ef9` and immutable release
`P4-P4E2-20260815-r04` failed on macOS CPython 3.12.11 with
`HOLD_REPLAY_MISMATCH:model:objective_trace`. The observed F04 values differ from
the Linux CPython 3.12.3 builder by four binary64 ULPs. D14 also delegates to a
formal builder runbook containing VPS-only interpreter paths.

Round 05 preserves r01-r04 and all Phase 0-3 protected roots. It separates exact
formal builder provenance from a supported, read-only local CPython 3.12 verifier,
freezes a narrow semantic numeric contract, and will allocate a new create-once
release only after implementation and focused tests pass.
