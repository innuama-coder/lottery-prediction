# Final macOS controller command

From a clean checkout of the pushed delivery commit, using the existing macOS
CPython 3.12.11 environment:

```bash
PHASE4_PYTHON=.p4-local-venv/bin/python \
  scripts/phase4/local-accept-release \
  --release artifacts/phase-4/P4-P4E2-20260815-r12
```

This command is read-only. Final macOS r12 acceptance remains controller-owned;
this Linux delivery does not claim a macOS PASS.
