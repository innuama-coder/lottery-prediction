# Phase4E7 retrospective historical backtest

Phase4E7 uses a fixed temporal split of the existing canonical history: the final 120 draws of each game are the evaluation window and all earlier draws are training input. Labels from the evaluation window are not consumed by model fitting, ranking, or feature construction. The split, source hashes, model identifiers, Top-1000/Top-10 outputs, proper scores, moving-block bootstrap, and hit-rate summaries are recorded under `artifacts/phase4e7`.

This is explicitly `RETROSPECTIVE_BACKTEST_ONLY`. The historical window was available to earlier phases and therefore is not an untouched P4E6 report window. The results are valid for immediate model comparison and feature-engineering diagnosis, but cannot by themselves authorize production promotion. P4E6 remains `PROSPECTIVE_ONLY`, serving release `P4-P4E2-20260815-r12` is unchanged, and no probability spread adjustment is applied.
