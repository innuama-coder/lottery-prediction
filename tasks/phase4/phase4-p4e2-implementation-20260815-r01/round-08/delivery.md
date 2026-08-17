# Round 08 delivery

Status: `COMPLETE`

- successful create-once release: `P4-P4E2-20260815-r11`
- implementation/source commit: `74ee8a7bead8785f463ba7a92528e88e962fb17e`
- stable exact order key: `P4S10HE1`, `1e-10`, `ROUND_HALF_EVEN`
- versioned representation/ranking contract:
  `P4-LOGSUMEXP-STABLE-SCORE-KEY-1` and
  `joint_stable_score_key_desc_tie_canonical_ticket_asc_v1`
- product and standalone Oracle derive every score/tie identity and Top-1000
  ordering from the stable key
- exhaustive six-scope preserved-r10 proof: `6,000/6,000` rows one-ULP stable,
  all adjacent distinct scores preserved, membership/order/rank unchanged
- formal A01-A10 plus A07b: PASS
- independent replay/mutations: `100% / 100%` (`28/28`)
- final closure and manifest coverage: PASS / `1.0`
- Linux CPython 3.12.3 exact one-command acceptance: PASS; release unchanged
- preserved r10 and its failed macOS controller evidence: unchanged
- all earlier releases and Phase 0-3 protected artifacts: unchanged
- macOS CPython 3.12.11 controller execution: pending; no macOS PASS claimed

Controller command:

```bash
PHASE4_PYTHON=$(command -v python3.12) \
  scripts/phase4/local-accept-release \
  --release artifacts/phase-4/P4-P4E2-20260815-r11
```

The branch push and PR #10 round-08 update follow the immutable evidence commit.
