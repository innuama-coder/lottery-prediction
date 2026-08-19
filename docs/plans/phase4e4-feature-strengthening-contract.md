# Phase 4E4 feature-strengthening frozen authority

P4E4 starts from `b7471921de45e5d890f10608e3c76c1e978d4194` on the isolated branch `codex/phase4e4-feature-strengthening-20260819-r01`. The accepted serving release remains `P4-P4E2-20260815-r12`; P4E3 and every prior release are immutable evidence.

The machine authorities are [the phase contract](../../config/phase4e4/authority-contract.json) and [the experiment registry](../../config/phase4e4/experiment-registry.json). They are committed and pushed before any new historical outcome is acquired or inspected. The original 200 rows per game are preservation-only. In particular, P4E3 positions 176–199 cannot be used by P4E4 selection, tuning, or promotion.

After the freeze, provenance-tracked official history is acquired. For each game, the newest 60 valid official rows strictly before the original baseline start form a genuinely new report-only window. Selection receives a physically separate prefix artifact without those labels. A pushed selection checkpoint is a mandatory capability boundary before the report command can read them.

Probability spread, entropy, support, ranks, Top-K results, and portfolio concentration are discrimination diagnostics. They cannot authorize promotion. Promotion requires simultaneous favorable joint log-loss and full-space multiclass Brier evidence against uniform M0, time-safe retrained r12 logic, and time-safe retrained P4E3 Transition, with frozen direction, calibration, block-bootstrap, Holm, normalization, replay, mutation, inventory, and test gates. Any failed statistical gate leaves r12 serving with `FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION`.
