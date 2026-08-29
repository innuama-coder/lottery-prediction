# Round 04 acceptance

Status: `PASS`

Release: `P4-P4E2-20260815-r04`

## Pre-formal gates

- focused P4E2 model tests: 7 tests PASS
- focused from-scratch release acceptance: 2 tests PASS
- final-source focused release acceptance: 2 tests PASS
- post-import-fix full Phase 4 discovery: 140 tests PASS, 20 audited superseded T00-T24-only skips
- Phase 4 oracle: 18 tests PASS
- Phase 3: 69 tests PASS
- Phase 2.1: 39 tests PASS under its frozen task-local interpreter
- Phase 2: 31 tests PASS under its frozen task-local interpreter
- both D00 authority checkers and the 31-case contract mutation validator: PASS

## Formal command receipts

All receipts below are immutable files below
`validation/attempts/<attempt-id>/receipt.json` and have exit code 0:

- `A01-compileall`: PASS
- `A02-phase4`: PASS (140 tests; only 20 audited superseded T00-T24 skips)
- `A03-phase4-oracle`: PASS (18 tests)
- `A04-phase3`: PASS (69 tests)
- `A05-phase2-1`: PASS (39 tests)
- `A06-phase2`: PASS (31 tests)
- `A07-authority`: PASS
- `A07b-authority-independent`: PASS
- `A08-contract`: PASS (9 schemas; 31 negative cases)
- `A09-bottom-up`: PASS
- `A10-replay-validation`: PASS

## Product and closure gates

- independent report SHA-256: `334621d7b10a10f344cdd989285fe1b28e327e0da08b9e2a6676460b21fab293`
- independent match rate: `1.0`
- mutation detection: `1.0` across 26 mutations
- product-core imports in independent oracle: `0`
- scheduler recovery: 10 injected stage failures; identical output IDs/hashes; zero duplicate side effects
- direct SSQ/DLT `inspect`: PASS with model, F01-F14 snapshot, cutoff, parameters, probability range, exact tie/rank basis, and lock
- direct SSQ/DLT score/research replay: PASS and idempotent; serving unchanged
- direct dual-game schedule replay: PASS and idempotent; zero duplicate side effects
- finalizer: PASS, `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`
- terminal CLI bottom-up validation: PASS with `recomputed_from_bottom_up: true`
- delivery manifest: 173 entries, coverage `1.0`, SHA-256 `3b00f3fac263450ea64e6f601cef7264fb24bc90a8da46940e5ff278fc5b828d`
- machine acceptance SHA-256: `a5154398dfdd9585d4434a1582cbad805cea737d078963d3454a9b145a4b6211`
- scientific status: SSQ `no_confirmed_lift`; DLT `no_confirmed_lift`
- protected Phase 0-3 roots and historical `P4-RMVP-20260815-r08`: unchanged

The engineering release readiness is not a claim of lottery predictability,
winnings, profit, or scientific lift.
