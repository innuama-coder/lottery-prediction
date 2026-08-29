# Round 08 launch

The controller fetched round-07 commit
`d40058367eb5164d139c6704cf124afaab3efe97` and ran the exact public local
entry point against immutable release `P4-P4E2-20260815-r10` on macOS CPython
3.12.11. The preserved result is:

```text
LOCAL ACCEPTANCE: FAIL
reason: HOLD_REPLAY_MISMATCH:top1000.0.score_identity
```

The first three SSQ and DLT canonical ticket keys, membership, order, and rank
were unchanged. Each recomputed `log_joint_score` differed by one binary64 ULP,
so the r10 identity `binary64:<float.hex()>` differed even though the ranking
fact was stable. The six controller pairs are frozen in
`tests/phase4/fixtures/stable-score-key-macos-31211.json`.

Release r10, its failed macOS evidence, and every earlier release remain
immutable. No round-08 release identity is allocated until the stable-key
migration, focused tests, full historical matrices, and independent r10
migration replay pass.
