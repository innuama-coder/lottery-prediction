# Phase 3 post-merge review fixes R03 continuation

This branch contains the recovered, uncommitted implementation from failed
executor task `t-6364924d8e004e4a9a04`. That task ended only because its fixed
one-hour deadline expired while the full Phase 3 test suite was running. Treat
the recovered implementation as the starting point; inspect it, finish it, and
commit it. Do not discard or reimplement working changes without evidence.

The authoritative requirements and boundaries remain
`tasks/phase3/review-fixes-r01/PROMPT.md`. In particular, close W11 structured
guard attribution, mandatory W10 reconstruction, resumable W08 formal runs,
recursive W13 manifest verification, successful-terminal exit codes, and the
W11/W13 ordering clarification. Preserve every existing historical artifact
and all frozen scientific definitions.

## Known recovered state

- Focused independent-reconstruction and manifest-closure regressions reached
  17/17 PASS after one test-fixture correction.
- A real controlled W08 interruption followed by a distinct-process resume
  reached PASS with 300 targets and canonical coverage 1.0.
- Phase 2 regression reached 31/31 PASS.
- Model and feature definitions were checked unchanged. The modified Phase 3
  config files update only the hash bindings required by the changed authority
  README and implementation source.
- The final full Phase 3 test command was killed with exit code 137 when the
  outer executor deadline expired. Its result is unknown and must be rerun.

## Required continuation

1. Inspect all recovered changes and `git status` before editing.
2. Run focused tests first, including `test_phase3_review_fixes.py` and
   `test_w08_run_resume.py`. Fix every failure at its root cause.
3. Run the complete acceptance commands below. Do not weaken, skip, mark slow,
   or mock the attack and resume tests to obtain a pass.
4. Confirm no existing file under `artifacts/phase-1`, `artifacts/phase-2`,
   `artifacts/phase-2.1`, `artifacts/phase-3-prep`, or `artifacts/phase-3` was
   changed, deleted, or regenerated.
5. Commit all intended source, schema, test, contract, and task changes. Leave
   a clean worktree. The final response must begin with `COMPLETED:` and report
   the commit SHA and exact test counts.

## Acceptance commands

```bash
python3 -m compileall -q -f src/lottery_research/phase3 scripts/phase3 tests/phase3
git diff --check 466ce883f84da0f52dcf913038c2be821f4f9da3...HEAD
PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p "test_*.py" -v
TMPDIR=/tmp PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
git diff --exit-code 466ce883f84da0f52dcf913038c2be821f4f9da3...HEAD -- artifacts/phase-1 artifacts/phase-2 artifacts/phase-2.1 artifacts/phase-3-prep artifacts/phase-3
```

Do not create a formal Phase 3 release in this task. Delivery is implementation
and regression code only; formal scientific execution remains a separate,
controller-authorized operation after code acceptance.
