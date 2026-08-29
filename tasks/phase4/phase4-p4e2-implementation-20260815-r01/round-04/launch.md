# Round 04 launch

Status: `PASS`

The controller findings and root-cause analysis were appended before Round 04
implementation. Failed releases r01/r02 were audited and retained. Formal r03
was also retained after its finalization import-order failure; it was never
modified, finalized, or reused after failure.

The successful formal attempt was launched create-once as:

```bash
PYTHONPATH=src /usr/bin/python3 scripts/phase4/build_real_model_release.py \
  --release P4-P4E2-20260815-r04 \
  --phase1-draws artifacts/phase-1/baseline-v1/draws.jsonl \
  --output artifacts/phase-4/P4-P4E2-20260815-r04 \
  --source-commit 7c14eb0ee7f9aa803a5bcd684aaecd5fa35d42b6 \
  --historical-interpreter /home/royzuo/codex-tasks/phase4-p4e2-implementation-20260815-r01/acceptance-venv/bin/python
```

Build result: `PASS`; elapsed `935.86 s`; peak RSS `355880 KiB`.
