# Phase 2.1 Iteration 06: Immutable Readiness Revalidation

## Task Identity

- Repository: `innuama-coder/lottery-prediction`
- Branch: `codex/lottery-phase-2.1-20260805`
- Worktree: `/home/royzuo/worktrees/lottery-prediction-lottery-phase-2.1-20260805`
- Task directory: `/home/royzuo/codex-tasks/lottery-phase-2.1-20260805`
- Baseline commit: `5e1aa705c2e0b9f33fb3ef2698e8af55301919dd`
- New release ID: `P2.1-R00-60d02be4dbe9-i06`

## Acceptance Failure to Fix

The task controller independently reproduced this failure on the VPS, in the
prepared `.phase2_1/venv` and on the immutable i05 final bundle:

```text
.phase2_1/venv/bin/python scripts/phase2_1/validate_phase2_1_readiness.py
FileExistsError: .../artifacts/phase-2.1/P2.1-R00-60d02be4dbe9-i05/logs/02-readiness.json
```

Root cause: the public readiness validation script calls the mutable formal
`readiness` command, which always creates `logs/02-readiness.json`. That is
correct for a new formal release, but it makes the required post-freeze
readiness acceptance command non-idempotent and attempts to overwrite an
immutable evidence path.

## Required Correction

1. Before implementation, add a focused failing test that creates a complete
   immutable bundle and proves the public readiness script/production entry
   validates it twice without adding, overwriting or deleting any final-bundle
   file. The test must verify both invocations return 0 and compare the bundle
   inventory before and after.
2. Add a distinct read-only readiness verification path. It must recompute and
   validate the existing readiness evidence, result count, identities and
   allowlist without creating a formal command receipt. Do not weaken the
   write-once behavior of the formal `readiness` command used while building a
   new release.
3. Make `python3 scripts/phase2_1/validate_phase2_1_readiness.py` use that
   read-only path. Document the correct activated-venv invocation in the
   runbook. The user's required command remains exactly valid after
   `source .phase2_1/venv/bin/activate`.
4. Preserve i05 and every prior release/run byte-for-byte. Create i06 as a
   new immutable release and do not patch i05 in place.
5. Use the same frozen inputs and local wheelhouse only. No network is allowed
   during formal computation or acceptance.
6. On i06, rerun in order: preparation/readiness, G0-G6, independent method
   review, qualification, audit, power, replay, independent replay review,
   10/10 E2E, manifest, acceptance and final validator. Do not generate audit
   or power before G0/G1 pass.
7. Independently invoke the public readiness script at least twice against
   the completed i06 bundle, then run the final validator. Retain command
   receipts or a dedicated non-mutating verification record outside the frozen
   final bundle, with exact exit codes and before/after inventory hashes.
8. Run remote build, lint, Phase 2.1 tests, and Phase 2 regressions in the
   activated release-local venv. The exact mandatory commands are:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
python3 scripts/phase2_1/validate_phase2_1_readiness.py
```

## Completion Conditions

- The public readiness command is idempotent and performs a bottom-up,
  read-only verification of the completed i06 bundle.
- P2.1-R00 is `READY`; the scanned formal historical result count is 0.
- G0-G6 are PASS; all 10 E2E cases reach expected terminals.
- Hash closure, result coverage, and independent replay consistency are 100%;
  blocking findings are 0.
- Final acceptance derives its scientific classification from evidence and
  preserves the separation between `indeterminate` and delivery status.
- Phase 1 frozen inputs, `artifacts/phase-2/`, and i05/prior evidence are
  unchanged.

Commit and push the branch. Your final response must begin with `COMPLETED:`,
`NEEDS_INPUT:`, or `FAILED:` and list the i06 release, commit SHA, exact
commands/exit codes, final bundle path, and evidence that two readiness
revalidations left its inventory unchanged. Do not create or merge a PR.
