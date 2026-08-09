# Phase 3 historical research runtime

This runbook implements only the historical-research baseline defined by
`tasks/phase3/README.md`. It does not authorize a production prediction,
public non-uniform forecast, Champion promotion, Top-1000 product, purchase,
bet, return claim, or winner guarantee. M0 remains the permanent Champion.

## Current gate

The frozen Phase 1 records are `retrospective_current_view` and have
`available_at_utc=null`. Historical `prior_draw_result` features therefore use
`retrospective_sequence_safe`, not reconstructed publication timestamps. The
checked-in temporal ledger covers 300 outer targets and 37,350 strictly earlier
source-target relations. Training reads only the earlier prefix; a forecast hash
must be locked before the scorer unlocks the target label. External time-varying
features remain prohibited unless genuine `available_at_utc < prediction_locked_at`
evidence is frozen. Preparation outputs use `artifacts/phase-3-prep/<prep-id>/`;
formal output uses `artifacts/phase-3/<release-id>/` only after W07 freezes the
release, actor identities, wheelhouse manifest, workload and whitelist.

## W04 build and lint

The repository-native commands intentionally require no network or new tool:

```bash
python3 -m compileall -q -f src/lottery_research/phase3 scripts/phase3
git diff --check
```

The Phase 3 lock is `requirements/phase3.lock`. A prepared offline environment
is reconstructed with:

```bash
python3 -m pip install --no-index --find-links artifacts/phase-3-prep/<prep-id>/wheelhouse -r requirements/phase3.lock
python3 -m pip install --no-index --no-deps --no-build-isolation -e .
```

Readiness records installed versions and any mismatch; it never downloads a
dependency. Resource fields are facts, not generic VPS pass thresholds.

## Required regression commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
PYTHONPATH=src python3 -c 'from pathlib import Path; from lottery_research.phase3.prerun_contract import validate_prerun_contract; import json; print(json.dumps(validate_prerun_contract(Path.cwd()), sort_keys=True))'
```

The last command must exit 0 and emit `terminal=READY_FOR_RESULTS_BLIND_FREEZE`,
`outer_target_count=300`, `expanded_sequence_relation_count=37350`, and
`formal_run_authorized=false`. This means W01-W03 are coherent; it does not
authorize W08 before W04-W07 are accepted.

## W01-W03 receipt sequence

The release controller first creates and schema-validates
`$PREP_ROOT/control/actor-assignments-preparation.json`. It must bind the four
preparation roles to real task/session records and their SHA-256 values. The
following sequence is then exact; every retry uses a new `PREP_ID` and preserves
the old directory.

```bash
PREP_ID=p3-prep-controller-issued-i01
PREP_ROOT=artifacts/phase-3-prep/$PREP_ID
PREP_ACTORS=$PREP_ROOT/control/actor-assignments-preparation.json

PYTHONPATH=src python3 scripts/phase3/validate_prerun_contract.py \
  --check W01 --identity "$PREP_ID-W01" --actor-assignments "$PREP_ACTORS" \
  --output "$PREP_ROOT/work-items/W01/receipt.json"

PYTHONPATH=src python3 scripts/phase3/validate_prerun_contract.py \
  --check W02 --identity "$PREP_ID-W02" --actor-assignments "$PREP_ACTORS" \
  --upstream-receipt "$PREP_ROOT/work-items/W01/receipt.json" \
  --output "$PREP_ROOT/work-items/W02/receipt.json"

PYTHONPATH=src python3 scripts/phase3/validate_prerun_contract.py \
  --check W03 --identity "$PREP_ID-W03" --actor-assignments "$PREP_ACTORS" \
  --upstream-receipt "$PREP_ROOT/work-items/W02/receipt.json" \
  --output "$PREP_ROOT/work-items/W03/receipt.json"
```

The W04-W13 commands and receipt-emission suffixes are defined without omitted
arguments in `docs/plans/phase-3-detailed-plan.md`. `qualify` executes all 1,000
uniform and 1,000 injected registered worlds plus the frozen probability,
leakage, isolation, ledger, recovery, and authorization negative controls. It
returns PASS only when both numerical thresholds and every guard pass. `run`
consumes a W07 authorization and performs the 600 registered M0/M1 logical
experiments; there is no synthetic-only or fixed-HOLD formal command path.
Until W04-W07 produce their complete receipts, formal `run` and final acceptance
remain unauthorized. Any such refusal is operational and is not a PIT-data gap.

W04 dependency and component evidence is generated before implementation
validation with the explicit preparation identity:

```bash
PYTHONPATH=src python3 scripts/phase3/prepare_w04.py --prep-root "$PREP_ROOT"
```

The script builds the wheelhouse from `requirements/phase3.lock`, records every
wheel SHA-256, rebuilds a temporary isolated environment with `--no-index
--find-links`, and runs 20 observed repetitions of each of the seven frozen
component benchmarks. Formal W08-W13 commands do not fetch inputs or dependencies.

W06 deliberately crosses a real process boundary. The first `qualify` invocation
uses `--stop-after-uniform`, closes the append-only ledger after all 1,000 uniform
worlds, writes a hash-bound checkpoint, and exits 20 with
`CONTROLLED_INTERRUPT_AFTER_UNIFORM`. A second invocation uses the exact same
identity and output with `--resume`; it validates the preserved hashes, appends
the 1,000 injected worlds and real negative-control mutations, and is the only
invocation that emits the W06 PASS receipt. An output without the first-stage
checkpoint cannot be resumed.

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

Formal W08–W13 execution requires W01–W07 PASS, conflict-free actor IDs, a W04
wheelhouse manifest and offline reconstruction receipt, and a frozen workload,
canonical-attempt ledger and whitelist. A release has at most two acceptance
iterations; exhaustion seals a HOLD and requires explicit authorization for a
new release. This runbook does not itself create that authorization.

For W08, `run` derives its 300 label-free targets from the frozen availability
ledger, supplies only sanitized strictly-earlier prefixes to a spawn-isolated
trainer that is quarantined before payload parsing, persists each forecast and
`forecast_locked`, and then invokes the scoring-only guarded label store through
a PID-bound opaque capability never sent to the trainer. The store fixes the
ledger and forecast/receipt locations to canonical paths and fails before
reading numbers unless the continuous ledger identity/sequence, current file
hash, and every release/run/experiment/attempt/target/model/path binding match
the global latest ledger lock. Successful runs retain 600 files below
`runs/label-unlocks/`.

After W08 and again during W10/W12/final postcheck, recompute those bindings with
an explicit release path:

```sh
PYTHONPATH=src python3 scripts/phase3/recompute_guarded_unlocks.py \
  --release-root "$RELEASE_ROOT" \
  --output "$RELEASE_ROOT/review/guarded-unlock-recomputation.json"
```

PASS requires exactly 600 current forecast hashes, 600 matching lock events,
600 unique receipt hashes, 600 matching unlock events, zero pre-lock reads, and
zero identity/hash mismatches. W08–W13 remain offline.
