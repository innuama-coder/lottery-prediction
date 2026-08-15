# Phase 4 real-model MVP release runbook

This is the canonical D00–D15 operating path. The older T00–T24 preparation
tools are retained for historical diagnosis only; they are not acceptance
commands and their unavailable prep identity must never be fabricated.

Use the interpreter path frozen into this release when the builder runs. Every
validation receipt records its resolved interpreter, Python version,
`requirements/phase4.lock` hash,
exact argv, exit code, stdout, and stderr. Formal commands use the immutable
release path and never `latest`, a glob, an inline model, or a fixture model.
The historical Phase 2/2.1 regression interpreter is frozen separately because
those suites require their own locked NumPy environment. Its invocation path is
preserved (a virtual-environment symlink must not be replaced by its base
interpreter), while its base interpreter realpath is recorded alongside it.

## Frozen commands

```bash
PY=/usr/bin/python3.12
HISTORICAL_PY=/home/royzuo/codex-tasks/phase4-p4e2-implementation-20260815-r01/acceptance-venv/bin/python
HISTORICAL_PY_REALPATH=/usr/bin/python3.12
DRAW=artifacts/phase-1/baseline-v1/draws.jsonl
RID=P4-P4E2-20260815-r04
REL=artifacts/phase-4/$RID
IMPL=7c14eb0ee7f9aa803a5bcd684aaecd5fa35d42b6

$PY scripts/phase4/freeze_authority.py --check --require-serving-model-per-game --reject-baseline-only-pass
$PY scripts/phase4/validate_real_model_contracts.py
PYTHONPATH=src $PY scripts/phase4/build_real_model_release.py --release "$RID" --phase1-draws "$DRAW" --output "$REL" --source-commit "$IMPL" --historical-interpreter "$HISTORICAL_PY"
PYTHONPATH=src $PY scripts/phase4_independent/replay_real_model_release.py --release "$REL" --draws "$DRAW" --output "$REL/replay/replay-report.json"
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli inspect --release "$REL" --game ssq
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli inspect --release "$REL" --game dlt
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli score --release "$REL" --game ssq
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli score --release "$REL" --game dlt
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli research --release "$REL" --game ssq
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli research --release "$REL" --game dlt
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli schedule --release "$REL"
```

Before finalization, use `run_acceptance_command.py` with a unique attempt ID
for compileall, Phase 4, Phase 4 oracle, Phase 3, Phase 2.1, Phase 2, authority,
contract, and replay validations. Failed attempts remain in
`validation/attempts/` and are never overwritten. After every required receipt
passes, execute:

```bash
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A01-compileall -- $PY -m compileall -q src scripts tests
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A02-phase4 -- $PY -m unittest discover -s tests/phase4 -p 'test_*.py' -v
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A03-phase4-oracle -- $PY -m unittest discover -s tests/phase4_oracle -p 'test_*.py' -v
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A04-phase3 -- $PY -m unittest discover -s tests/phase3 -p 'test_*.py' -v
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A05-phase2-1 -- $HISTORICAL_PY -m unittest discover -s tests/phase2_1 -p 'test_*.py' -v
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A06-phase2 -- $HISTORICAL_PY -m unittest discover -s tests/phase2 -p 'test_*.py' -v
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A07-authority -- $PY scripts/phase4/freeze_authority.py --check --require-serving-model-per-game --reject-baseline-only-pass
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A08-contract -- $PY scripts/phase4/validate_real_model_contracts.py
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A09-bottom-up -- $PY scripts/phase4_independent/replay_real_model_release.py --release "$REL" --draws "$DRAW" --check-only
$PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A10-replay-validation -- $PY -m lottery_system.phase4.real_cli replay --release "$REL" --independent
PYTHONPATH=src $PY scripts/phase4/finalize_real_model_release.py --release "$REL"
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli validate --release "$REL"
```

Finalization creates D14 before D13, includes the checklist candidate and its
receipt in the pre-acceptance manifest, then only appends the three D15 files.
The terminal state is `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE` only when every
recorded command passes and the independent replay detects all mutations.

The serving model for each game is P4E2-R trained from the frozen Phase 1
canonical history and consumes F01-F14 across all three required feature groups.
It uses complete-enumeration streaming log-sum-exp normalization and full-ticket
Top-K evaluation. M0 and historical P4E1-R are diagnostic/legacy-only and cannot
lock a new product forecast.
Scientific status (`no_confirmed_lift`, `worse_than_M0`, or
`insufficient_evidence`) is reported independently of engineering readiness and
is never a claim of predictability, winnings, or profit.
