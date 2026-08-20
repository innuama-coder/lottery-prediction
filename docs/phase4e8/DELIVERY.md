# Phase4E8 feature and parameter iteration

Phase4E8 keeps the Phase4E7 outer holdout fixed at the final 120 historical draws for each game. It uses two rolling inner folds inside the training prefix to select among three frozen feature masks (`all14`, `history_only`, `history_structure`) and three registered L2 values (8, 24, 72). The outer holdout is evaluated once after selection; no outer labels are used for tuning.

Results are retrospective diagnostics only. DLT selects `all14 + L2=24` and has mean joint-log-loss delta `-0.00606` versus the uniform baseline, with bootstrap CI `[-0.02548, 0.01157]`. SSQ selects `history_structure + L2=8` but has delta `+0.00680`, with CI `[-0.05963, 0.08078]`. Both games have `0/120` Top-10 and `0/120` Top-1000 hits. Neither candidate is statistically promoted.

The artifacts are marked `RETROSPECTIVE_BACKTEST_ONLY`; P4E6 remains `PROSPECTIVE_ONLY`, serving release `P4-P4E2-20260815-r12` is unchanged, and no probability spread adjustment is applied.
