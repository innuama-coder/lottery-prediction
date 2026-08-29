# Round 06 acceptance

Status: `PASS`

Release: `P4-P4E2-20260815-r09`

Release source commit: `581810e7645c07aa9f2d680f21ca3e92cc941a80`

## Local semantic contract

- authority contract ID: `P4-LOCAL-SEMANTIC-BINARY64-1`
- contract schema: `1.1.0`
- tight recomputed profile retained: finite, conjunctive absolute/relative
  `1e-12`, ULP `8`
- derived snapshot profile: finite, conjunctive absolute `3e-16`, relative
  `3e-14`, ULP `151`
- numeric paths are leaf-only and explicitly enumerate F01-F14 feature values
  and F01-F14 normalization mean/scale; there is no recursive wildcard
- the observed worst-case `151` ULP pair passes; `152` ULP fails; the first test
  values above the selected absolute and relative maxima also fail
- non-numeric, boolean, NaN, and positive/negative infinity fail closed
- exact structure tests reject game/non-numeric identity mutation, feature-ID
  mutation, cutoff and prefix-fact-hash mutation, row reorder, and missing/extra row

Focused verifier tests: `11 PASS`.

## Formal matrix

All receipts have exit code zero:

- `A01-compileall`: PASS
- `A02-phase4`: PASS (`151` tests; `20` audited superseded-only skips)
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
- final state: `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`
- manifest entries/coverage: `174 / 1.0`, D14 covered
- release unchanged by the local verifier: yes
- protected roots, r05-r08, and all earlier releases: unchanged

Exact Linux command and result:

```bash
PHASE4_PYTHON=$(command -v python3.12) \
  scripts/phase4/local-accept-release \
  --release artifacts/phase-4/P4-P4E2-20260815-r09
```

Result: `LOCAL ACCEPTANCE: PASS (READY_FOR_LOCAL_PRODUCT_ACCEPTANCE)` on Linux
CPython 3.12.3. This is not a claim of macOS PASS; controller re-execution is
required.

## Exact hashes

- delivery manifest: `ea4b1b9cf63e6dc6181465c33ae1372cfb8fadbd9e5fcec145d2d16a56609fa3`
- final closure: `c1ba1b02c8b95b568cdaf32ce06c5cbbb7717feca56ecbe2bfb181f414bb1bac`
- replay report: `8afec31599b6055bfddecf3469a9c48cfbbb67eae9b44a4e3ec33995bb62aa57`
- local checklist: `ef942fe7b996e919197f9d5d9e3c23f78abbaffad091a7afe14790ae09cf115e`
- local contract: `d0563308500323b70c6de07424a208f3c1753333203e62e6ea26f16ff52bf10c`

Engineering readiness is not a claim of lottery predictability, winnings,
profit, or scientific lift.
