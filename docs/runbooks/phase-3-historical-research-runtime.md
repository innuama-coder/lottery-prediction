# Phase 3 historical research runtime

This runbook implements only the historical-research baseline defined by
`tasks/phase3/README.md`. It does not authorize a production prediction,
public non-uniform forecast, Champion promotion, Top-1000 product, purchase,
bet, return claim, or winner guarantee. M0 remains the permanent Champion.

## Current gate

The frozen Phase 1 records are `retrospective_current_view` and have
`available_at_utc=null`. The checked-in availability ledger therefore has no
eligible feature row. Until independently preserved evidence proves every
actual input satisfies `available_at_utc < prediction_locked_at`, the expected
terminal is `HOLD_PENDING_PIT_EVIDENCE` and formal W08/W09 execution is
prohibited. Synthetic qualification outputs must use
`artifacts/phase-3-development/`, never `artifacts/phase-3/`.

## W04 build and lint

The repository-native commands intentionally require no network or new tool:

```bash
python3 -m compileall -q -f src/lottery_research/phase3 scripts/phase3
git diff --check
```

The Phase 3 lock is `requirements/phase3.lock`. A prepared offline environment
is reconstructed with:

```bash
python3 -m pip install --no-index --find-links <prepared-wheelhouse> -r requirements/phase3.lock
python3 -m pip install --no-index --no-deps --no-build-isolation -e .
```

Readiness records installed versions and any mismatch; it never downloads a
dependency. Resource fields are facts, not generic VPS pass thresholds.

## Required regression commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
python3 scripts/phase3/validate_prerun_contract.py
```

The last command must currently exit 2 and emit
`terminal=HOLD_PENDING_PIT_EVIDENCE`. A nonzero HOLD is the correct result.

## Non-formal qualification command sequence

Every identity is immutable, every output basename equals its identity, and
reusing an existing identity fails. The example identities are illustrative;
each retry must use a new identity and preserve the old directory.

```bash
PYTHONPATH=src python3 -m lottery_research.phase3 validate \
  --scope inputs --identity p3-dev-input-i01 \
  --output artifacts/phase-3-development/p3-dev-input-i01

PYTHONPATH=src python3 -m lottery_research.phase3 qualify \
  --identity p3-dev-qualification-i01 \
  --output artifacts/phase-3-development/p3-dev-qualification-i01

PYTHONPATH=src python3 -m lottery_research.phase3 evaluate \
  --identity p3-dev-evaluate-i01 \
  --output artifacts/phase-3-development/p3-dev-evaluate-i01 \
  --qualification artifacts/phase-3-development/p3-dev-qualification-i01

PYTHONPATH=src python3 -m lottery_research.phase3 readiness \
  --identity p3-dev-readiness-i01 \
  --output artifacts/phase-3-development/p3-dev-readiness-i01

PYTHONPATH=src python3 -m lottery_research.phase3 run \
  --identity p3-formal-refusal-i01 \
  --output artifacts/phase-3-development/p3-formal-refusal-i01

PYTHONPATH=src python3 -m lottery_research.phase3 replay \
  --identity p3-dev-replay-i01 \
  --output artifacts/phase-3-development/p3-dev-replay-i01 \
  --qualification artifacts/phase-3-development/p3-dev-qualification-i01

PYTHONPATH=src python3 -m lottery_research.phase3 verify-e2e \
  --identity p3-dev-e2e-i01 \
  --output artifacts/phase-3-development/p3-dev-e2e-i01

PYTHONPATH=src python3 -m lottery_research.phase3 accept \
  --identity p3-dev-final-i01 \
  --output artifacts/phase-3-development/p3-dev-final-i01
```

`qualify`, `evaluate`, `replay`, and `verify-e2e` operate only on explicit
synthetic/small-world artifacts. `readiness`, `run`, and `accept` must retain
the PIT HOLD, report zero formal results, and create no Phase 3 formal release
or acceptance artifact.

## Identities, recovery, and evidence

- Never use `latest`, wildcards, modification time, or an implicit directory.
- A run starts only in a new directory. Ledger events append; terminal events
  cannot be replaced. `failed`, `timeout`, `crashed`, `rejected`, and
  `not_opened` evidence remains visible.
- A checkpoint is accepted only for the same run identity and payload hash.
- The development evidence manifest enumerates each path, role, byte count,
  line count, and SHA-256. Replay reads that bottom-up evidence and uses a
  separate direct-enumeration reference path.
- If monitoring cannot confirm task ID, worktree, branch, log, artifact path,
  and last command state, preserve the scene and report `NEEDS_INPUT`.
- Any future failed acceptance attempt must use a new iteration and release/run
  identity, link its parent explicitly, and never overwrite earlier evidence.

Formal W08–W13 execution requires a separate results-blind freeze after PIT
coverage reaches 100%, W01–W07 pass, independent role assignments are valid,
the offline lock is available, and the explicit formal workload/whitelist is
frozen. None of those future steps is authorized by this runbook.
