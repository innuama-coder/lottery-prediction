# Phase4E21 bonus-rule hardening delivery

## Scope and rule contract

Phase4E21 centralizes routine-only SSQ and DLT prize calculation in
`src/lottery_system/phase4/bonus.py`. The result is determined only by game,
registered rule version, and hit state. Issue metadata, special draws, 福运奖,
promotions, floating payouts, and issue-specific payout fields are accepted for
invariance testing but are not read by the rule function.

All internal prize amounts are integer yuan. Tier 1 and tier 2 are fixed at
5,000,000 and 100,000 yuan for both games. SSQ lower tiers are 3,000, 200, 10,
and 5 yuan. DLT keeps distinct registered tables and fixed amounts for the old
9-tier rule and the 7-tier rule beginning with issue 2026014.

The exhaustive DLT old-rule table assigns the 17 winning states specified by
the registered mapping. Tier 9 is retained as a registered tier but has no
remaining non-overlapping state; `(0, 0)` is the sole unmatched old-rule state.
The new-rule table is independent and leaves `(0, 0)` and `(1, 0)` unmatched.

## Validation

- Focused affected tests: 42 passed.
- Remaining `tests/phase4`: 331 passed, 20 skipped.
- Exhaustive state audits: SSQ 7x2 for both registered versions and DLT 6x3
  for both registered versions; all mutually exclusive and exact.
- Metadata mutation, first/second fixed amount, unmatched-zero, invalid-hit,
  rule isolation, SSQ/DLT isolation, and independent replay checks passed.
- DLT recalculation replay is byte-identical at
  `92ab4e19875e847b799c3d36a0dac21748b916610adb539b73267e3246f6dba0`.
- The append-only evidence manifest contains 34 files.

Commands used:

```text
PYTHONPATH=src python3 scripts/phase4e20/ssq_supervised_compression.py --output-dir artifacts/phase4e21_bonus_hardening/recalculated/phase4e20
PYTHONPATH=src python3 scripts/phase4e21_bonus_hardening/run_bonus_hardening.py --resume-existing
PYTHONPATH=.:src python3 scripts/phase4e21_bonus_hardening/run_bonus_hardening.py --refresh-dlt-evidence
PYTHONPATH=.:src python3 -m unittest -v tests.phase4.test_phase4e21_bonus_hardening tests.phase4.test_phase4e17_prize_metrics tests.phase4.test_phase4e19_ssq_prize_aware tests.phase4.test_phase4e20_supervised_compression tests.phase4.test_phase4e17_artifacts
python3 scripts/phase4e21_bonus_hardening/run_phase4_tests.py --exclude-module test_release_acceptance
```

The clean-tree release acceptance is intentionally executed after committing
the evidence; its result is reported in the final delivery handoff.

## Evidence and conclusion

- Decision: `artifacts/phase4e21_bonus_hardening/delivery/decision.json`
- Manifest: `artifacts/phase4e21_bonus_hardening/delivery/manifest.json`
- State audit: `artifacts/phase4e21_bonus_hardening/state-space-audit.json`
- Old/new hashes: `artifacts/phase4e21_bonus_hardening/old-new-report-hashes.json`
- Replay receipt: `artifacts/phase4e21_bonus_hardening/independent-replay.json`
- Frozen isolation receipt:
  `artifacts/phase4e21_bonus_hardening/dlt-serving-isolation.json`
- Recalculated reports:
  `artifacts/phase4e21_bonus_hardening/recalculated/`

The acceptance conclusion is `ACCEPT_BONUS_HARDENING_NO_PROMOTION`. Both E19
and E20 remain honestly `NO_PROMOTION` with hard gates false. Historical
artifacts were not overwritten, thresholds were not weakened, and serving
remains `P4-P4E2-20260815-r12` / `PROSPECTIVE_ONLY`.
