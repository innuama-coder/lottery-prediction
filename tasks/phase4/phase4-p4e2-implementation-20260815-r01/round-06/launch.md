# Round 06 launch

Controller execution of the exact local entry point at Round-05 commit
`4b77c6367506f6658c9eed000158a1a786c85f0e` failed on macOS CPython 3.12.11:

```text
LOCAL ACCEPTANCE: FAIL
reason: HOLD_REPLAY_MISMATCH:feature_snapshot
```

The preserved Linux release and independent macOS recomputation differed in 32
F04-derived snapshot leaves: 6 SSQ and 26 DLT. The worst cancellation-sensitive
DLT value was `0.0099312201839453045` versus `0.0099312201839450425`, or 151
binary64 ULPs while only `2.62e-16` absolute and `2.64e-14` relative.

Round 06 preserves failed r05-r07, finalized r08, every earlier task round and
release, and all protected roots. The next create-once release identity is
`P4-P4E2-20260815-r09`.
