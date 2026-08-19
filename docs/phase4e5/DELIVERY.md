# P4E5 External / Operational Metadata Delivery

P4E5 is a shadow-only engineering delivery. Serving remains
`P4-P4E2-20260815-r12`; P4E4 remains
`FEATURE_ENGINEERING_DELIVERED_PROSPECTIVE_ONLY`; no prior release byte changed.

Official-source feasibility was asymmetric. The Guangdong Sports Lottery official
per-draw notices provided 480 reproducible DLT rows with complete sales, jackpot,
first-prize and second-prize count/amount fields. Provincial first-prize distribution
coverage exceeded the frozen 95% threshold. The China Welfare Lottery SSQ history
page, API, and known announcement returned HTTP 403 from this execution environment.
SSQ operational fields therefore remain explicitly missing; no unofficial source was
substituted.

The independent role windows are SSQ `2024008` through `2024127` and DLT `2024006`
through `2024125`, 120 draws each. They precede and exclude P4E4 report labels and the
preservation-only original 200. Selection chose `B0` for SSQ and calendar-only `C1`
for DLT. On the sealed DLT report, `C1` was worse than `B0` on mean per-ball log loss;
all operational candidates were worse still. No comparison passed the frozen
multiplicity, confidence-interval, calibration, coverage, and all-games gates.

Terminal status: **FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION**.
