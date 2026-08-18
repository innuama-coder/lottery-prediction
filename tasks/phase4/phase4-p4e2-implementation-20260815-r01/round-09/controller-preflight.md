# Controller preflight

Run from a clean checkout of the pushed round-09 commit, using the same
CPython 3.12.11 environment with jsonschema 4.26.0:

```bash
.p4-local-venv/bin/python \
  scripts/phase4_independent/audit_preserved_r11_numeric_portability.py \
  --release artifacts/phase-4/P4-P4E2-20260815-r11 \
  --draws artifacts/phase-1/baseline-v1/draws.jsonl \
  --require-zero-new-bound-failures
```

Required terminal conditions are `status=PASS`, `new_bound_failures=0`, all 86
patterns observed, SSQ/DLT semantic counts `54807/54865`, product-core imports
0, exact checks retained, and release unchanged. The JSON output includes every
pattern's comparison/difference/failure count and absolute/relative/ULP maxima.

This command is read-only and does not allocate or finalize a release.
