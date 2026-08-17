# Round 08 acceptance

Status: `PASS` (Linux engineering acceptance)

Release: `P4-P4E2-20260815-r11`

Release source commit: `74ee8a7bead8785f463ba7a92528e88e962fb17e`

Controller macOS CPython 3.12.11 acceptance: `PENDING`

## Preserved controller failure and releases

Release `P4-P4E2-20260815-r10`, its finalized evidence, and the independent
controller result remain byte-for-byte unchanged:

```text
LOCAL ACCEPTANCE: FAIL
reason: HOLD_REPLAY_MISMATCH:top1000.0.score_identity
```

Every earlier release and all Phase 0-3 protected artifacts are also unchanged.
r11 was allocated only after the complete pre-allocation matrix and independent
six-scope r10 migration replay passed.

## Stable exact identity contract

- local contract: `P4-LOCAL-STABLE-SCORE-KEY-2`, schema `1.3.0`
- score order key: `P4S10HE1:<signed integer tick>`
- canonical conversion: exact finite-binary64 rational value, decimal quantum
  `1e-10`, `ROUND_HALF_EVEN`
- ranking: stable tick descending, canonical ticket ascending within a stable-key tie
- score identity, tie key/group, probability layer, tie bounds, and tie ordering
  derive only from the stable key and remain exact integrity fields
- non-finite scores fail closed; crossing the first stable-key boundary changes
  every derived identity
- no ticket-only score identity and no raw-binary64 identity/ranking namespace remains active
- `top1000_derived_probability_display_v1` remains exactly 3 paths and 17 ULP
- `derived_feature_snapshot_v1` remains exactly 42 paths and 151 ULP
- no score/tie identity field is routed through a numeric profile

The complete construction/validation surface and exact-versus-semantic
classification are recorded in `score-identity-migration-audit.md`.

## Preserved-r10 migration proof

The standalone Oracle audited every row in formal, historical, and shadow
Top-1000 for both games:

- scopes/rows: `6 / 6,000`
- semantic score/probability comparisons: `12,000`
- one-ULP identity invariant: PASS in both directions for every row
- product/independent keys and identities: exact match
- adjacent distinct scores preserved: PASS
- minimum adjacent distinct gap: `4.326295779955025012e-10`
- canonical ticket membership/order/rank: unchanged in all scopes
- controller one-ULP fixtures: exact product/Oracle match
- product-core imports: `0`
- preserved r10 files/inventory: `178 / e3b65e2ef7c7ab12ee7fe21c68d9847858661446f1a5242528db0dc46ba19d5c`

## Pre-allocation and formal matrices

Before r11 allocation:

- focused stable-key/local-verifier tests: `20 PASS`
- Phase 4: `161 PASS`; `20` audited superseded-only skips
- Phase 4 independent Oracle: `18 PASS`
- Phase 3: `69 PASS`
- Phase 2.1: `39 PASS` under frozen CPython 3.12.3
- Phase 2: `31 PASS` under frozen CPython 3.12.3
- compileall, primary authority, independent authority, and 12-schema/31-negative
  contract validation: PASS
- preserved-r10 independent migration replay: PASS

All formal create-once receipts have exit code zero:

- `A01-compileall`: PASS
- `A02-phase4`: PASS (`161` tests, `20` audited skips, `3613.656 s`)
- `A03-phase4-oracle`: PASS (`18` tests, `0.145 s`)
- `A04-phase3`: PASS (`69` tests, `192.594 s`)
- `A05-phase2-1`: PASS (`39` tests, `131.450 s`)
- `A06-phase2`: PASS (`31` tests, `4.396 s`)
- `A07-authority`: PASS
- `A07b-authority-independent`: PASS
- `A08-contract`: PASS
- `A09-bottom-up`: PASS
- `A10-replay-validation`: PASS

## Replay, closure, and Linux local command

- independent replay match rate: `100%`
- independent mutation detection: `100%` (`28/28 DETECTED`)
- product-core imports in independent Oracle: `0`
- semantic numeric comparisons: SSQ `54,807`; DLT `54,865`
- exact formal Top-1000 rows: SSQ `1,000`; DLT `1,000`
- historical parent and research child: independently rebuilt and matched
- protected roots: unchanged
- manifest entries/coverage: `174 / 1.0`, including D14
- finalized release inventory: `178` files
- aggregate release inventory SHA-256:
  `b01b69df6f5a39fab7b2b2215f6a89306606d6f96f354711aceaf894464357d9`
- final state: `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`

Exact Linux command:

```bash
PHASE4_PYTHON=$(command -v python3.12) \
  scripts/phase4/local-accept-release \
  --release artifacts/phase-4/P4-P4E2-20260815-r11
```

Result on Linux CPython 3.12.3:

```text
LOCAL ACCEPTANCE: PASS (READY_FOR_LOCAL_PRODUCT_ACCEPTANCE)
numeric contract: P4-LOCAL-STABLE-SCORE-KEY-2; replay=100%; mutations=100%
release unchanged: yes
```

This is not a claim of macOS PASS. Independent controller execution on macOS
CPython 3.12.11 remains pending.

## Exact release hashes

- delivery manifest: `dac2de9bec8602e2580308791356f62e8e34fed9fd26f8c3ee9251b640ed3568`
- final closure: `f43b8234312a0ba478f066ab28b94758946913010d45dc79fbc765154a5793b9`
- machine acceptance: `8bcc003f6c7200aefffbd44900f7643706cafc9c448290b7edd5795d9c552064`
- replay report: `b4720b440fba596c98c621987305e0a0641f2a8ffbdfaa4ba1c83f670366bb7e`
- local checklist: `cd1120b7d5862621a240deb110ac6be7a591fd8532bc067ead603434a288610f`
- local contract: `e584a691f52c782b869ea5f0b0c4833d5dcbab281581b2ea636b702ca65e6d04`

Engineering readiness is not a claim of lottery predictability, winnings,
profit, or scientific lift.
