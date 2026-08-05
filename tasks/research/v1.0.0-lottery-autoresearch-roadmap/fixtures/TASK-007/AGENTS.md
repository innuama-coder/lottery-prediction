# Agent Instructions — TASK-007

Karpathy Context Engineering contract for the current task.

## Role

Produce only the named Research Report for `TASK-007`: `docs/research/TASK-007-synthetic-benchmark-protocol.md`.

## Source Order / Required reading order

1. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json`
2. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASKS.md`
3. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASK-007/PROMPT.md`
4. The source refs listed in that prompt, in order.

## Research method

- Apply falsification-first, point-in-time, power-aware and source-traceable reasoning.
- Treat prior accepted reports as frozen inputs; do not redefine their metrics or thresholds.
- Separate observed facts, sourced interpretation, assumptions and unresolved questions.
- No-new-facts rule: if evidence is absent, record a limitation or blocker.

## Constraints / Boundary

- Do not implement code.
- Documentation only. Do not modify production code, tests, manifests, schemas, migrations or runtime scripts.
- The only allowed Research Report path is `docs/research/TASK-007-synthetic-benchmark-protocol.md` under `docs/research/`.
- No betting automation, profit promise or claim of approaching 100% prediction.

## Verification and handoff

Run the direct verification command listed in `PROMPT.md`. Hand off the report path, sources, validator output, PASS/HOLD/FAIL verdict, blockers, residual risks and whether dependents may start.
