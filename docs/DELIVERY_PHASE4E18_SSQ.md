# Phase4E18 SSQ specialized optimization

## Scope and isolation

Phase4E18 is a standalone SSQ experiment. It does not modify DLT model selection, DLT artifacts, DLT serving, or the P4E6 serving release. DLT isolation is verified by SHA-256 identity checks for the DLT report, outer rows, summary, and P4E6 delivery decision.

## Model boundary

SSQ front and back are selected independently from four registered candidates: the raw-control baseline, a long-window surprise/renewal model, a transition/graph model, and a short-window blue-ball candidate. Candidate selection uses 240 strictly pre-outer targets split into four chronological 60-draw blocks. Outer evaluation uses the frozen 120-draw SSQ truth set, split into 60 calibration and 60 evaluation draws.

## Primary metric

For each target and each `N` in `1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000`, complete SSQ tickets are ranked and the prefix value is:

`known_prize_total_yuan / N`

Across the truth set, the reported value is total prefix prize divided by `draws * N`. Winning-ticket counts and best single-ticket hit rate are diagnostics only.

## Result

The registered raw-control candidate remains selected for both SSQ front and back. The specialized feature candidates do not pass the three-of-four positive stability-block gate, so no specialized model is promoted. This is an intentional no-promotion result; P4E6 remains unchanged and SSQ remains retrospective-only.

## Prize threshold gate

The requested acceptance threshold is `average_prize_yuan > 2.0` for the 120-draw aggregate. The current result does not pass: the best all-120 value is approximately `0.7614 yuan/ticket`. Under the configured fixed prize table, a uniform random SSQ ticket has an analytical expected value of approximately `1.1007 yuan/ticket`; therefore the requested threshold requires a substantial out-of-sample lift and cannot be asserted without evidence. The gate is recorded as `passed=false` in `summary.json`.
