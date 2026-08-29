# Round 07 launch

The controller fetched round-06 commit
`393252eae7fef2de79571891b8cfaee5aa52fb83` and ran the exact public local
entry point against immutable release `P4-P4E2-20260815-r09` on macOS CPython
3.12.11. The preserved result is:

```text
LOCAL ACCEPTANCE: FAIL
reason: HOLD_REPLAY_NUMERIC_BOUND:top1000.622.joint_probability:profile=tight_recomputed_v1:abs=2.2499312661442353e-22:rel=3.5383660753807325e-15:ulp=17
```

The released rank-623 SSQ ticket is `[[5,6,13,14,24,28],[2]]`. Its stored
display probability is `6.358672953029994052e-08`; the independent macOS
recomputation is `6.35867295302997155e-08`. Ticket membership, canonical key,
order, rank, binary64 score identity, tie identity and bounds, model/feature
lineage, locks, hashes, cutoff, and source facts did not differ.

Release r09 and every earlier release remain immutable. No round-07 release
identity is allocated until the corrected source contract, focused boundary
tests, exact-first identity tests, and the complete comparison audit validate.

