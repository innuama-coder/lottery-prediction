# Round 05 acceptance

Status: `PASS`

Release: `P4-P4E2-20260815-r08`

## Portable local contract

- supported runtime: CPython 3.12 on a supported local platform, including macOS CPython 3.12.11
- entry point: `scripts/phase4/local-accept-release`
- numeric contract: `P4-LOCAL-SEMANTIC-BINARY64-1`
- bounds are conjunctive and finite-only: absolute `1e-12`, relative `1e-12`, and at most 8 binary64 ULPs
- approximation is restricted to the enumerated recomputed numeric leaf paths
- IDs, hashes, issues, cutoffs, lineage, ticket membership/order, score/tie identities, and create-once files remain exact
- historical Phase 2/2.1 suites are verified locally through immutable receipts and manifest closure; their VPS environment is not replayed locally

Focused verifier tests: 7 PASS. These cover the observed macOS four-ULP values, the exact 8-ULP pass and 9-ULP rejection boundary, non-finite rejection, unlisted-path rejection, canonical contract-copy validation, missing/tampered final closure, exact Top-1000 order/tie/model-lineage rejection, and absence of VPS-only paths from the local entry point/checklist.

## Formal matrix

All immutable receipts below have exit code 0:

- `A01-compileall`: PASS
- `A02-phase4`: PASS (147 tests; 20 audited superseded T00-T24-only skips)
- `A03-phase4-oracle`: PASS
- `A04-phase3`: PASS
- `A05-phase2-1`: PASS under its frozen formal interpreter
- `A06-phase2`: PASS under its frozen formal interpreter
- `A07-authority`: PASS
- `A07b-authority-independent`: PASS
- `A08-contract`: PASS (10 schemas; 31 negative cases)
- `A09-bottom-up`: PASS
- `A10-replay-validation`: PASS

## Product and closure gates

- finalizer: PASS, `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`
- formal bottom-up validation: PASS with `recomputed_from_bottom_up: true`
- portable local acceptance: PASS; replay `100%`; mutations `100%`; release unchanged
- SSQ: model `p4e2r-ssq-3de6ddb6b45811b9`, feature `f01-f14-ssq-df19ed4902d5d51b`, cutoff `2026085`, target `after-2026085`, 1,000 rows
- DLT: model `p4e2r-dlt-b4904bd351714af7`, feature `f01-f14-dlt-fccb1329026cc439`, cutoff `2026083`, target `after-2026083`, 1,000 rows
- scientific status: SSQ/DLT `no_confirmed_lift`
- mutation detection: 26/26; product-core imports in the independent oracle: 0
- scheduler recovery: 10 injected faults with identical output identities
- protected Phase 0-3 roots and historical `P4-RMVP-20260815-r08`: unchanged
- manifest: 174 entries; coverage `1.0`; SHA-256 `2266c96137ecaf90bcbde8524931ca7d5f11e0391f8e5a364ddc5be963775b9d`
- final closure SHA-256: `4427be34f85a9d0946d6485fd42ba1027a81270f3973049c4d6db9fc126c07e9`
- replay report SHA-256: `731294cbdcd1dde89c6a3e86815d96875a4253f256c1fbabc0534aed006c8f2f`
- local checklist SHA-256: `f4bdb329cc41e290830f35937725ba17525a0f10f26b86e7e7871796b44f9fcc`
- local contract SHA-256: `40bf53486dfb4b3e78fba9423599b7131a7822b4196bc28468059aac8526eb84`

The engineering release readiness is not a claim of lottery predictability, winnings, profit, or scientific lift.
