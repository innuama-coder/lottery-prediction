# Phase 4E3 feature-strengthening frozen contract

This successor starts from accepted commit `61afa5be20b49e5545d106d490cdc0c33cba9dc4` and does not alter the immutable `P4-P4E2-20260815-r12` release or earlier artifacts.

The machine authority is `config/phase4e3/phase-contract.json`; the candidate authority is `config/phase4e3/experiment-registry.json`. Both are frozen before any P4E3 report-only label is evaluated. Positions 176–199 (24 draws per game) are report-only. Candidate construction, tuning, redundancy filtering, and selection are confined to strict prefixes and positions below 176 with a two-draw purge.

The primary question is whether sequence-safe information improves proper scores, not whether it makes a forecast look more decisive. Promotion requires independently recomputed report-only improvement over uniform M0 and the accepted r12 method, multiplicity correction, a favorable block-bootstrap interval, stable direction, no material calibration regression, and clean leakage/mutation/replay evidence. Otherwise r12 remains serving and the honest terminal state is `FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION`.

No immutable release identifier is allocated by this development phase. Adverse evidence is a required output.
