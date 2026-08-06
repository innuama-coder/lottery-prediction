# Phase 2.1 iteration i07: review-closure correction task

You are operating only on the remote VPS. The rejected PR is `innuama-coder/lottery-prediction#1`, target branch `main`, with current head `61a99a2c3732be0ade1f370e681d9af236902dcb` on `codex/lottery-phase-2.1-20260805`. Work in the supplied new detached worktree only. Do not modify the existing i06 evidence release, historical `artifacts/phase-2/`, Phase 1 frozen inputs/identities, or any Phase 3 files. Do not merge or reopen the PR. Commit and push a fast-forward update to the existing PR head branch only after all required verification passes.

## Objective

Address the owner review from 2026-08-06 (issue comments 5206341355 and 5206346908). A final validator must reject a bundle whose acceptance, manifest, and affected artifact identities have been recomputed after a malicious but self-consistent edit. Satisfying only schemas or internal equality is not enough.

The three blocking P1 findings below are exact acceptance criteria. Start each with a focused failing regression test that reproduces the reviewer's mutation; run it and record the expected failure before production code changes. Keep the tests as permanent regression coverage.

### P1-A: freeze and independently verify all E2E semantics

The E2E registry and case files must not be able to turn a registered failure case into `PASS` by editing `expected_terminal`, `observed_terminal`, `terminal`, evidence, manifest, and acceptance together.

1. Extend the frozen Phase 2.1 contract with a canonical, ordered definition for every one of the ten E2E cases. It must bind the immutable case ID, expected terminal, exact scenario/production-operation identity, expected exit-code class, and receipt requirements. Do not derive expectations from mutable registry data.
2. The E2E schema and final validator must require the registry to match this contract definition exactly, in its registered order. Every case requires a production verification receipt with schema validation and a consistent terminal/status/exit-code relation; expected failing scenarios require a nonzero exit and `FAIL` receipt, while normal cases require success.
3. Final validation must independently exercise or independently recompute the ten canonical production scenarios in a fresh, disposable staging copy and compare their terminal/exit/operation results to the frozen contract and recorded receipt. It must not trust mutable registry case fields to decide what to run. The staging operation must not mutate the candidate bundle and must avoid recursive final-validator invocation.
4. Add a regression that reproduces the reviewer's exact attack on `E2E-P2.1-03-release-mismatch`: change expected/observed/terminal to PASS, clear or forge evidence as needed, rebuild mutable manifest/acceptance, and prove the final validator rejects it. Also cover incorrect zero exit code for each negative E2E receipt.

### P1-B: bind external verification logs to the frozen command contract

`logs/external-*.json` must be evidence of the registered verification suite, not arbitrary successful shell commands.

1. Register a canonical ordered list for all formal external verification commands in the frozen contract. Each record must include a stable ID, literal command string, expected working-directory scope, offline/network policy, and success expectation. The list must cover the actual build, lint, Phase 2.1 test, Phase 2 regression test, and readiness commands used by the formal release procedure. Preserve the policy that the formal run uses frozen inputs and local dependencies after preparation.
2. Generate external command receipts from that contract, not a caller-supplied arbitrary list. Receipt schema/validator must bind command ID, canonical command, order, scope, result code, status/terminal relation, output hashes, input identity, and offline policy. The run summary must be the exact canonical receipt list.
3. Final validation must reject any replacement such as command `true`, altered order, missing command, altered scope, nonexecuted record, or copied summary even if all affected hashes, manifest, and acceptance are recomputed.
4. Add a regression that reproduces the reviewer's `external-01.json` -> `true` forgery and proves `validate_final_bundle()` rejects it after mutable artifacts are rebuilt.

### P1-C: final validation must perform the readiness read-only re-scan

`validate_final_bundle()` must invoke the read-only readiness verification over the original recorded task-input result roots, not only `validate_readiness()` over saved receipts. Its recomputed result must participate in G0/G6 and reject any formal historical result written after readiness.

Add a regression that creates a current-release `late-power.json` under the task-input results root after readiness, updates only mutable bundle artifacts as the reviewer describes, and proves final validation fails. Verify the read-only operation has no mutation side effects on the accepted bundle.

## Release and evidence rules

- Establish a fresh i07 contract/preregistration/release identity rooted in this rejected-head baseline; do not overwrite i06. Use a unique release ID and unique bundle directory for every formal or failed run. Keep failed evidence in place.
- Preserve facts-only VPS resource policy: never introduce generic CPU, memory, disk, or architecture thresholds as a gate.
- Preserve separation: `indeterminate` is not proof of randomness and remains distinct from delivery status.
- Retain the existing fixed acceptance behavior: core artifact specialized schemas, cross-artifact identity checks, formal-output inventory restriction, independent replay engine/path, actual command exit-code receipts, and E2E rerun semantics must not regress.
- Do not use the public network during formal execution. Preparation may use it only as already documented; formal runs use frozen inputs and local wheelhouse.

## Required VPS validation

Run all checks on the VPS. Do not use local execution as a substitute.

1. New negative regression tests for P1-A, P1-B, and P1-C must be observed failing before their implementing change, then pass after it. Include them in `tests/phase2_1`.
2. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v`
3. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v`
4. Repository build and lint checks, including `python3 -m compileall -q src scripts tests` and `git diff --check` against the final branch diff. Remove trailing whitespace; no stale command evidence may claim a check that did not run.
5. `python3 scripts/phase2_1/validate_phase2_1_readiness.py` must be read-only, return `READY`, and be rerun to demonstrate no mutation.
6. Rerun the public E2E command against an already completed bundle with its documented staging behavior; it must succeed without changing the original.
7. Run the final validator and the negative suite against the fresh i07 final bundle. Explicitly demonstrate rejection for the three new self-consistent forgery scenarios.
8. Complete qualification, audit, power, replay, independent method review, independent replay review, 10/10 E2E, G0-G6, and final acceptance on the same i07 final bundle. Recompute all final metrics from evidence. Results must be `status=PASS`, `delivery_status=GO`, `blocking_findings=0`, all five coverage/closure ratios `1.0`, and scientific classification handled honestly.

## Handoff

Commit in focused logical commits. Push only the fully validated final commit to `origin/codex/lottery-phase-2.1-20260805`. Report: commit SHA, release ID, bundle path, every command/result, red-green evidence for the three regressions, negative-forgery outcomes, G0-G6, E2E, unchanged protected-history proof, and the exact remote artifact path for retrieval. Do not claim review approval; the parent agent will independently validate and manage the PR.
