# Round 03 acceptance

Status: `PENDING`

The final command ledger will include exact commands, exit codes, and output evidence. Required minimum commands are:

```bash
python3 scripts/phase4/freeze_authority.py --check --require-serving-model-per-game --reject-baseline-only-pass
python3 scripts/phase4_independent/check_authority_semantics.py --check
python3 scripts/phase4/validate_real_model_contracts.py
PYTHONPATH=src python3 -m unittest discover -s tests/phase4 -p 'test_*.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p 'test_*.py' -v
```

Formal P4E2 build, CLI, independent replay/mutation, bottom-up acceptance, protected-tree comparison, and release finalization commands will be appended after the new release ID is allocated.
