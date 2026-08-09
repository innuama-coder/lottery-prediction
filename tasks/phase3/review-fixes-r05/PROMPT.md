# Phase 3 review fixes R05: recovered candidate delivery

This branch already contains the complete R04 implementation recovered from
task `t-d8b2b1d780014a808fee`. The candidate changes only
`tests/phase3/_release_fixture.py` so synthetic Phase 3 releases derive their
40-character implementation identity from the canonical implementation
inventory without calling Git.

## Required work

- Inspect the recovered diff and confirm it is limited to the test fixture.
- Run only these fast provider checks:

```sh
python3 -m compileall -q -f tests/phase3/_release_fixture.py
python3 - <<'PY'
from pathlib import Path
source = Path("tests/phase3/_release_fixture.py").read_text(encoding="utf-8")
assert "subprocess" not in source
assert "rev-parse" not in source
assert "inventory_sha = canonical_sha256(inventory_rows)" in source
assert source.count("implementation_freeze_commit\": freeze_commit") == 3
PY
git status --porcelain
```

- Do not install dependencies and do not rerun the full suites. The independent
  verifier has the frozen system dependencies and will run Phase 3, Phase 2.1,
  and Phase 2 in a source-only checkout without `.git`.
- Do not modify any repository file. If the worktree is clean, do not create an
  empty commit. Report the existing HEAD and finish immediately.

## Boundaries

- Production code, plans, schemas, contracts, configuration, models, features,
  PIT semantics, thresholds, and Champion policy are immutable.
- All `artifacts/` paths are immutable.
- The recovered fixture change is the only implementation delta under review.
