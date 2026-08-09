# Phase 3 W08 PIT evidence-only delivery, iteration i02

> SUPERSEDED: Historical task record only. Do not execute. Phase 3 v1.1 uses `retrospective_sequence_safe`; new legacy PIT collection is prohibited.

Work on the current checkout only. The authoritative Phase 3 definition is
`tasks/phase3/README.md`; the implementation and recovery design are in
`docs/research/phase3-pit-evidence-preparation.md`.

Create the new immutable evidence-only release
`p3-pit-prep-20260808-i02`. This is not permission to start formal W08-W13.
The expected scientific result is a truthful `HOLD_PENDING_PIT_EVIDENCE` with
zero formal results unless every one of the 400 frozen rows is independently
proven eligible.

Required procedure:

1. Read the Phase 3 authority, the PIT design document, and the existing
   implementation before making changes.
2. Run the bounded preparation-only reconnaissance:
   `python3 scripts/phase3/pit_collect_recon.py --output /tmp/p3-pit-recon-i02`.
   Preserve every generated receipt. Do not use a live result, HTTP headers,
   retrieval time, draw date, current page or planned schedule to infer
   availability.
3. Build the new bundle exactly once, in this new directory:
   `python3 scripts/phase3/pit_recovery.py build --identity p3-pit-prep-20260808-i02 --output artifacts/phase-3-pit/p3-pit-prep-20260808-i02 --receipts-dir /tmp/p3-pit-recon-i02 --status-output artifacts/phase-3-pit-preparation/phase3-pit-preparation-status.json`.
   Exit code 20 is the expected HOLD exit code; record it and continue. Never
   delete, overwrite or mutate an existing release directory.
4. Run the Phase 3, Phase 2.1 and Phase 2 regression suites, the PIT tamper
   matrix, and the independent PIT validator. The validator also exits 20 for
   the expected HOLD; inspect its JSON rather than treating the nonzero code
   as success.
5. Confirm the status file and bundle both state: `terminal` is
   `HOLD_PENDING_PIT_EVIDENCE`, `formal_result_count` is 0,
   `formal_run_authorized` is false, `eligible_feature_coverage` is 0.0,
   `acceptance_verdict` is `BLOCKED`, and `delivery_state` is `HOLD`.
6. Commit only the new i02 evidence/status files and any necessary truthful
   verification fixes. Do not modify `config/phase3/`, `artifacts/phase-1/`,
   `artifacts/phase-2/`, `artifacts/phase-2.1/`, or create
   `artifacts/phase-3/<release-id>`.

Before reporting completion, run `git status --short`, commit all intended
changes, and report the commit SHA plus exact command outcomes. Do not claim a
successful Phase 3 delivery: this task delivers verified evidence-only HOLD.
