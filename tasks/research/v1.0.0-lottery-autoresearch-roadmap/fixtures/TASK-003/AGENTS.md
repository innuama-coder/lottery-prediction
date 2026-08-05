# Agent Instructions — TASK-003

Karpathy Context Engineering contract for the current task.

## Role

Produce the TASK-003 report plus the reproducible simulation artifacts, research scripts and tests declared in the canonical roadmap.

## Source Order / Required reading order

1. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json`
2. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASKS.md`
3. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASK-003/PROMPT.md`
4. The source refs listed in that prompt, in order.

## Research method

- Apply falsification-first, point-in-time, power-aware and source-traceable reasoning.
- Treat prior accepted reports as frozen inputs; do not redefine their metrics or thresholds.
- Separate observed facts, sourced interpretation, assumptions and unresolved questions.
- No-new-facts rule: if evidence is absent, record a limitation or blocker.

## Constraints / Boundary

- Research code is allowed only under `scripts/research/TASK-003/` with artifacts under `artifacts/research/TASK-003/` and tests under `tests/research/TASK-003/`.
- Do not modify production code or phase-0 raw evidence.
- Freeze tests, thresholds, parameter grids and seed policy before viewing results.
- No betting automation, profit promise or claim of approaching 100% prediction.

## Verification and handoff

Run both direct verification commands in `PROMPT.md`. Hand off the report, artifacts, scripts, tests, validator output, PASS/HOLD/FAIL verdict, blockers, residual risks and whether dependents may start.
