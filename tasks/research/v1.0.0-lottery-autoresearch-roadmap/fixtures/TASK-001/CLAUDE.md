# Claude Task Context — TASK-001

Karpathy Context Engineering contract for the current task.

Role: phase-0 data-feasibility implementer and evidence reviewer for `官方数据源、规则版本与可得性实证`.

Fixed planning input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json`
Fixed task input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASK-001/PROMPT.md`
Fixed relationship input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASKS.md`
Fixed output areas: `docs/research/TASK-001-official-data-source-feasibility.md`, `artifacts/phase-0/`, `scripts/phase0/`, `tests/phase0/`

## Required Reading

Read the fixed inputs first, then all prompt source refs. Execute only the phase-0 evidence work allowed by the canonical roadmap.

Checklist:

- state the scope boundary and research question flow;
- cite and classify sources;
- connect each conclusion to evidence;
- include methods, alternatives, limitations, risks and verification plan;
- satisfy every task acceptance criterion;
- preserve uncertainty and stop on missing dependencies;
- run both direct phase-gate and report-quality verification commands listed in `PROMPT.md`.

Hard constraints:

- Only implement phase-0 probe, parsing, schema validation, failure injection, replay, acceptance tooling and tests.
- Do not change future production services, model training or prediction code.
- Freeze scope before source observation and preserve all conflicting evidence.
- Do not invent official data, experimental results or product facts.
- Do not make betting, profit or near-100%-accuracy claims.

## Final Response

Handoff must contain output path, evidence used, verification result, PASS/HOLD/FAIL, blockers and downstream readiness.
