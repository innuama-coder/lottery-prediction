# Phase4E9 nested coverage spaces

## Objective

Phase4E9 converts ticket ranking into nested, measurable coverage spaces. For every one of the final 120 historical draws, the model is refitted with only draws available through `t-1`, and the winning ticket receives a deterministic rank over the complete legal ticket space.

The fixed compression ladder is `Top-100000 -> 50000 -> 10000 -> 5000 -> 2000 -> 1000`. A smaller space is accepted only when its later evaluation coverage satisfies the registered reliability gate. The experiment does not sharpen, rescale, or relabel model scores as winning probabilities.

## Ranking and split

- Model configurations are inherited from Phase4E8 inner-fold selection: SSQ uses `history_structure + L2=8`; DLT uses `all14 + L2=24`.
- Ranking uses the registered `joint_stable_score_key_desc_tie_canonical_ticket_asc_v1` contract, including the `P4S10HE1` score tick and canonical ticket tie-break.
- Draws 1-60 form the calibration segment. Draws 61-120 form the evaluation segment and never select `K`.
- The first reliable space uses the split-conformal 90% calibration rank quantile `ceil((n+1)*0.90)`.

## Acceptance standard

The first space is reliable only when the independent 60-draw evaluation segment has:

- coverage rate at least `80%`;
- Wilson 95% lower confidence bound at least `75%`;
- valid full-space ranks for every draw;
- strict `t-1` lag for every draw;
- monotonic coverage across every nested fixed `K`.

Each fixed compression level is reported separately. `Top-100000` or a smaller space is not accepted merely because a larger calibrated space passes.

## Evidence

The machine-readable evidence is stored in `artifacts/phase4e9/summary.json`, each game's `report.json`, and each game's 120-line `rolling-report.jsonl`. `tests/phase4/test_phase4e9_nested_spaces.py` independently checks the delivery invariants and serving fence.

All evidence is `RETROSPECTIVE_BACKTEST_ONLY`. P4E6 remains `PROSPECTIVE_ONLY`; serving release `P4-P4E2-20260815-r12` is unchanged, and promotion remains forbidden.
