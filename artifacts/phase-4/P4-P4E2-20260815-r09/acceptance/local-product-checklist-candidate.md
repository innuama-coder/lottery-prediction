# Phase 4 P4E2 local product acceptance candidate

Status: `CANDIDATE_NOT_RELEASED`

Release: `P4-P4E2-20260815-r09`

## Prerequisites and local setup

- A clean checkout of this repository at the release source commit.
- CPython 3.12 (any supported patch release; CPython 3.12.11 on macOS is explicitly in scope).
- No Phase 2/2.1 historical virtual environment and no builder/VPS path is required.

Copy-paste setup (run once from the repository root):

```bash
python3.12 -m venv .p4-local-venv
.p4-local-venv/bin/python -m pip install 'jsonschema==4.26.0'
```

## One read-only acceptance command

```bash
PHASE4_PYTHON=.p4-local-venv/bin/python scripts/phase4/local-accept-release --release artifacts/phase-4/P4-P4E2-20260815-r09
```

Expected first line: `LOCAL ACCEPTANCE: PASS (READY_FOR_LOCAL_PRODUCT_ACCEPTANCE)`.
The command snapshots the release before verification and fails if any byte is changed. It verifies authority and
schemas; the final manifest/closure; immutable formal Phase 2/2.1 receipts; serving lineage and create-once locks;
1,000 ordered tickets for each game; probability qualification and exact score/tie identities; lifecycle score and
AutoResearch shadow; dual-game scheduler recovery; protected roots; independent replay and negative mutations.
Only the explicitly enumerated recomputed numeric fields in `contracts/local-verifier-contract.json` use the finite,
conjunctive absolute/relative/ULP bounds. IDs, hashes, issues, cutoffs, lineage, tickets, rank/order, score/tie identities,
and create-once files remain exact.

## Frozen inspect expectations

- SSQ: model `p4e2r-ssq-3de6ddb6b45811b9`; feature `f01-f14-ssq-df19ed4902d5d51b`; cutoff `2026085`; target `after-2026085`; rows `1000`; scientific status `no_confirmed_lift`.
- DLT: model `p4e2r-dlt-b4904bd351714af7`; feature `f01-f14-dlt-fccb1329026cc439`; cutoff `2026083`; target `after-2026083`; rows `1000`; scientific status `no_confirmed_lift`.

Inspect the concise SSQ/DLT lines printed by the command. Evidence paths: `acceptance/final-closure.json`,
`manifest/delivery-manifest.json`, `replay/replay-report.json`, `contracts/local-verifier-contract.json`,
`validation/attempts/A05-phase2-1/receipt.json`, and `validation/attempts/A06-phase2/receipt.json`.

Engineering readiness and model-internal ranking do not establish predictability, lift, winnings, or profit.
