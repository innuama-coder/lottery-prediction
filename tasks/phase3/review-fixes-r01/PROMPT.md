# Phase 3 post-merge review fixes R01

Base commit: `d7439daa23d7451bf087fa7d1a30f6c769587efd`.

Implement and commit the Phase 3 fixes below. Work only in this task worktree.
Do not modify, replace, delete, or regenerate any existing file under
`artifacts/phase-1/`, `artifacts/phase-2/`, `artifacts/phase-2.1/`,
`artifacts/phase-3-prep/`, or `artifacts/phase-3/`.

## Required fixes

1. Fix formal W11 E2E attribution.
   - Never assign `actual_terminal` or a rejection reason from the registry's
     expected value after catching a generic exception.
   - Each negative case must observe a structured, production-validator result
     containing the actual terminal, stable guard/error code, and process exit
     code. An unrelated missing file, malformed JSON, or wrong guard must fail
     the case.
   - Mutations must reach their registered guard rather than being rejected by
     an earlier generic forecast/hash mismatch. Add regression tests proving
     this for every registered negative case and proving unrelated exceptions
     cannot pass.
   - Add and enforce a dedicated per-case E2E receipt schema. Receipts must
     contain expected and actual terminal/exit-code/guard assertions and must
     be schema-valid.
   - `PASS_NO_SHADOW_CANDIDATE` and `PASS_INDETERMINATE` are successful
     terminals and must record exit code 0.

2. Make independent W10 model reconstruction mandatory.
   - The W10 production command must execute the standalone independent
     estimator reconstruction from frozen prefixes, in a distinct process or
     equivalently isolated reference entry point, before reporting PASS.
   - Validate its schema, release identity, 600/600 fold/lambda/weight coverage,
     probability match rate, status, and zero blocking findings.
   - W10 receipt creation and `validate_bottom_up` must reject a missing,
     HOLD/FAIL, malformed, wrong-release, incomplete, or hash-inconsistent
     reconstruction artifact. It must not be an optional `if path.is_file()`
     attachment.

3. Make W08 formal-run checkpoints genuinely resumable.
   - Add `run --resume` with the same immutable identity and canonical output.
   - Revalidate authorization, ledger identity, checkpoint payload and hashes,
     completed artifact bindings, and canonical attempts before continuing.
   - Preserve every failed/incomplete attempt and all partial evidence. Never
     delete or overwrite it. Continue with a new attempt when required and
     retain the frozen canonical-success selection rule.
   - Add a controlled interruption test that resumes in a distinct process and
     reaches the same complete scientific result as an uninterrupted run.
     Wrong identity, tampered checkpoint/ledger/artifact, and duplicate resume
     must fail closed.

4. Close W13 manifest verification.
   - Before handoff PASS, parse and schema-validate the final manifest and
     recursively verify every listed file's hash and size against the current
     release tree.
   - Enforce the exact allowed post-manifest extras created by W12/W13; do not
     silently ignore unexpected extras.
   - A listed W10 reconstruction, E2E receipt, preparation evidence, or other
     manifest file changed after acceptance must make handoff fail.
   - Add a regression reproducing the controller's attack: change
     `review/independent-model-reconstruction.json` from PASS to HOLD while
     leaving the manifest unchanged and prove the real handoff path rejects it.

5. Resolve the frozen-plan ordering contradiction without changing scientific
   scope. W11 can test only pre-acceptance validator cases; post-acceptance
   acceptance/manifest tamper verification belongs to W13. Update the Phase 3
   design/plan/task README only as needed to state this executable division,
   retaining the requirement that both classes are tested before final handoff.

## Required boundaries

- Do not change Phase 3 model definitions, feature definitions, lambda grid,
  folds, metrics, bootstrap, classification thresholds, scientific result, or
  Champion policy.
- Do not rewrite existing release evidence to make tests pass.
- Do not weaken immutable identity, guarded-label isolation, append-only ledger,
  actor independence, offline execution, or evidence-hash requirements.
- Do not add environment-specific CPU, memory, disk, architecture, or OS gates.
- Do not create a new formal Phase 3 release in this task. This task delivers
  implementation and regression fixes only; a new release is a separate
  controller-authorized execution after code acceptance.

## Acceptance commands

Run all commands from the repository root:

```bash
python3 -m compileall -q -f src/lottery_research/phase3 scripts/phase3 tests/phase3
git diff --check
PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p "test_*.py" -v
TMPDIR=/tmp PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
```

Also run targeted regressions that demonstrate each attack above. Report the
exact commands and results in the final message.

## Delivery

Commit all intended changes. The final response must begin with `COMPLETED:`
and include the commit SHA, changed paths, test counts, and any remaining
limitations. Do not leave uncommitted files.
