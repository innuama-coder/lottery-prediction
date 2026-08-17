# Round 07 acceptance

Status: `PASS` (Linux engineering acceptance)

Release: `P4-P4E2-20260815-r10`

Release source commit: `5223e733c4154ab53782f442017ea09dc1108aa5`

Controller macOS acceptance: `PENDING`

## Controller failure preserved

Round-06 release `P4-P4E2-20260815-r09` and all of its evidence remain
unchanged. The independent macOS CPython 3.12.11 failure is preserved exactly:

```text
HOLD_REPLAY_NUMERIC_BOUND:top1000.622.joint_probability:profile=tight_recomputed_v1:abs=2.2499312661442353e-22:rel=3.5383660753807325e-15:ulp=17
```

## Local semantic contract

- authority contract ID remains `P4-LOCAL-SEMANTIC-BINARY64-1`
- contract schema: `1.2.0`
- `tight_recomputed_v1` is unchanged: finite, conjunctive absolute/relative
  `1e-12`, ULP `8`
- `derived_feature_snapshot_v1` is unchanged: finite, conjunctive absolute
  `3e-16`, relative `3e-14`, ULP `151`
- new `top1000_derived_probability_display_v1`: finite, conjunctive absolute
  `2.2499312661442353e-22`, relative `3.5383660753807325e-15`, ULP `17`
- the new class contains exactly three leaf paths:
  `top1000.*.joint_probability`,
  `historical_top1000.*.joint_probability`, and
  `shadow_top1000.*.joint_probability`
- no Top-1000 wildcard or other numeric field was added; the validator freezes
  all `43` tight, `42` feature-snapshot, and `3` probability-display paths
- the macOS fixture's exact `17`-ULP pair passes; `18` ULP, the first values
  outside the absolute/relative limits, and non-finite values fail
- complete structure and exact leaves are compared before any profiled numeric
  leaf. Focused mutations reject ticket, display order, rank, tie key, score
  identity, and lineage before the numeric comparator can run
- the complete comparison audit and coverage map is in
  `round-07/local-verifier-comparison-audit.md`

Focused local-verifier tests: `14 PASS`.

## Validation before release allocation

No round-07 release identity existed until this clean matrix passed:

- Phase 4: `154 PASS`; `20` audited superseded-only skips
- Phase 4 independent oracle: `18 PASS`
- Phase 3: `69 PASS`
- Phase 2.1: `39 PASS` under the frozen historical interpreter
- Phase 2: `31 PASS` under the frozen historical interpreter
- authority, independent authority, and local-contract validators: PASS
- direct independent replay against preserved r09: `100%`
- independent mutations against preserved r09: `26/26 DETECTED`
- protected inventories: unchanged

Only after these checks passed was `P4-P4E2-20260815-r10` allocated.

## Formal matrix

All immutable receipts have exit code zero:

- `A01-compileall`: PASS
- `A02-phase4`: PASS (`154` tests; `20` audited superseded-only skips)
- `A03-phase4-oracle`: PASS (`18` tests)
- `A04-phase3`: PASS (`69` tests)
- `A05-phase2-1`: PASS (`39` tests under the frozen historical interpreter)
- `A06-phase2`: PASS (`31` tests under the frozen historical interpreter)
- `A07-authority`: PASS
- `A07b-authority-independent`: PASS
- `A08-contract`: PASS
- `A09-bottom-up`: PASS
- `A10-replay-validation`: PASS

## Replay, closure, and local Linux execution

- independent SSQ/DLT replay: `100%`
- independent mutations: `26/26 DETECTED`
- product-core imports in independent oracle: `0`
- semantic numeric comparisons: SSQ `54,807`; DLT `54,865`
- exact Top-1000 rows: SSQ `1,000`; DLT `1,000`
- protected roots: unchanged
- final state: `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`
- manifest entries/coverage: `174 / 1.0`, D14 covered
- finalized release inventory: `178` files
- pre/post local-verifier aggregate inventory SHA-256:
  `c41c61e28420549b08e213e0dc413679187db58d62b0babb23a85a8f5ca41314`
- preserved r09 versus round-06 commit `393252e...`: no diff

Exact Linux command:

```bash
PHASE4_PYTHON=$(command -v python3.12) \
  scripts/phase4/local-accept-release \
  --release artifacts/phase-4/P4-P4E2-20260815-r10
```

Result: `LOCAL ACCEPTANCE: PASS (READY_FOR_LOCAL_PRODUCT_ACCEPTANCE)` on Linux
CPython 3.12.3, with `replay=100%`, `mutations=100%`, and
`release unchanged: yes`.

This is not a claim of macOS PASS. Independent controller execution on macOS
CPython 3.12.11 is pending.

## Exact hashes

- delivery manifest: `a26bab8b91e6ed357762871af7c31ecf60d626167ac78ef1ae5adc093713274b`
- final closure: `c51fba1b56db6e38614eb83ba859edb4760f48d9bbd7c540fbd0cf4c0e9d493a`
- replay report: `17253cf55191531de96182c6989a89180ca3e25d08c0919c5ff348438660630a`
- local checklist: `3486809a672012399ebbd2d81c6a676b727ca91db78138094fc8d1d8ec6b95cc`
- local contract: `44109116d3921fba3b033b20e3eab165f46f1250a933f799d6d3b23a1c864b76`

Engineering readiness is not a claim of lottery predictability, winnings,
profit, or scientific lift.
