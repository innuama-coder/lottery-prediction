# Phase4E10 orientation selection

Phase4E10 tests whether the selected Phase4E8 score direction itself is responsible for an unnecessarily large reliable coverage space. It pre-registers three parameter controls: the selected positive orientation, a full negative-orientation control, and a zero-weight uniform canonical-order control.

Candidate selection uses a 120-draw walk-forward window immediately before the frozen Phase4E9 outer window. Every target uses only data through `t-1`. Candidates are ordered by the smallest split-conformal 90% rank space, then 80%, then 50%; the final 120 outcomes are not read for this selection.

After selection, the frozen outer window is split into 60 calibration and 60 evaluation draws. The 90% calibration space is reliable only if evaluation coverage is at least 80% and the Wilson 95% lower bound is at least 75%. `Top-100000` remains a separate compression gate.

This is a bounded diagnostic parameter iteration. A positive global scale is intentionally absent because it cannot change rank. Phase4E10 is `RETROSPECTIVE_BACKTEST_ONLY`, cannot promote a model, and leaves P4E6 release `P4-P4E2-20260815-r12` in `PROSPECTIVE_ONLY` unchanged.
