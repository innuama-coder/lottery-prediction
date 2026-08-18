# Exact Linux delivery commands

Frozen variables:

```bash
PY=/usr/bin/python3.12
HISTORICAL_PY=/home/royzuo/codex-tasks/phase4-p4e2-implementation-20260815-r01/acceptance-venv/bin/python
DRAW=artifacts/phase-1/baseline-v1/draws.jsonl
RID=P4-P4E2-20260815-r12
REL=artifacts/phase-4/$RID
IMPL=f2f4ad1e9098bf6110478fd800bcc7f16735e722
```

Allocation and replay:

```bash
PYTHONPATH=src $PY scripts/phase4/build_real_model_release.py \
  --release "$RID" --phase1-draws "$DRAW" --output "$REL" \
  --source-commit "$IMPL" --historical-interpreter "$HISTORICAL_PY"

PYTHONPATH=src $PY scripts/phase4_independent/replay_real_model_release.py \
  --release "$REL" --draws "$DRAW" \
  --output "$REL/replay/replay-report.json"
```

Formal create-once receipts:

```bash
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A01-compileall -- $PY -m compileall -q src scripts tests
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A02-phase4 -- $PY -m unittest discover -s tests/phase4 -p 'test_*.py' -v
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A03-phase4-oracle -- $PY -m unittest discover -s tests/phase4_oracle -p 'test_*.py' -v
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A04-phase3 -- $PY -m unittest discover -s tests/phase3 -p 'test_*.py' -v
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A05-phase2-1 -- $HISTORICAL_PY -m unittest discover -s tests/phase2_1 -p 'test_*.py' -v
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A06-phase2 -- $HISTORICAL_PY -m unittest discover -s tests/phase2 -p 'test_*.py' -v
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A07-authority -- $PY scripts/phase4/freeze_authority.py --check --require-serving-model-per-game --reject-baseline-only-pass
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A07b-authority-independent -- $PY scripts/phase4_independent/check_authority_semantics.py --check
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A08-contract -- $PY scripts/phase4/validate_real_model_contracts.py
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A09-bottom-up -- $PY scripts/phase4_independent/replay_real_model_release.py --release "$REL" --draws "$DRAW" --check-only
PYTHONPATH=src $PY scripts/phase4/run_acceptance_command.py --release "$REL" --attempt-id A10-replay-validation -- $PY -m lottery_system.phase4.real_cli replay --release "$REL" --independent
```

Closure and Linux local acceptance:

```bash
PYTHONPATH=src $PY scripts/phase4/finalize_real_model_release.py --release "$REL"
PYTHONPATH=src $PY -m lottery_system.phase4.real_cli validate --release "$REL"
PHASE4_PYTHON=$(command -v python3.12) scripts/phase4/local-accept-release --release "$REL"
```

Every command above returned exit 0. Formal stdout/stderr and exact interpreter,
argv, dependency-lock hash, and exit code are stored in
`$REL/validation/attempts/*/receipt.json`; build/task commands and output hashes
are stored in `$REL/contracts/D01-receipt.json` and `$REL/receipts/`.
