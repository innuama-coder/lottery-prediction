# Agent Instructions — TASK-001

Karpathy Context Engineering contract for the current task.

## Role

Execute phase 0 for `TASK-001` and produce the report, machine evidence, phase-0 tooling and tests declared in the canonical roadmap.

## Source Order / Required reading order

1. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json`
2. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASKS.md`
3. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASK-001/PROMPT.md`
4. The source refs listed in that prompt, in order.

## Research method

- Apply falsification-first, point-in-time, power-aware and source-traceable reasoning.
- Treat prior accepted reports as frozen inputs; do not redefine their metrics or thresholds.
- Separate observed facts, sourced interpretation, assumptions and unresolved questions.
- No-new-facts rule: if evidence is absent, record a limitation or blocker.

## Constraints / Boundary

- This is a data-feasibility execution task, not documentation-only.
- Changes are limited to `docs/research/TASK-001-official-data-source-feasibility.md`, `artifacts/phase-0/`, `scripts/phase0/` and `tests/phase0/`.
- Do not modify future production services, model training or prediction code.
- Freeze P0-01 before observing source results; never relax an acceptance boundary after results are visible.
- No betting automation, profit promise or claim of approaching 100% prediction.

## Verification and handoff

Run both direct verification commands in `PROMPT.md`. Hand off all phase-0 evidence, commands and outputs, per-game results, the unique project decision, blockers, residual risks and whether TASK-002 may start.
