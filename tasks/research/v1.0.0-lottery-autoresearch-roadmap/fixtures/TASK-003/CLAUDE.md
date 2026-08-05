# Claude Task Context — TASK-003

Karpathy Context Engineering contract for the current task.

Role: research-report author and evidence reviewer for `随机性审计与可检测性/功效边界`.

Fixed planning input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json`
Fixed task input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASK-003/PROMPT.md`
Fixed relationship input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASKS.md`
Fixed output areas: `docs/research/TASK-003-randomness-audit-power-envelope.md`, `artifacts/research/TASK-003/`, `scripts/research/TASK-003/`, `tests/research/TASK-003/`

## Required Reading

Read the fixed inputs first, then all prompt source refs. Produce reproducible research code and evidence only within the TASK-003 output areas.

Checklist:

- state the scope boundary and research question flow;
- cite and classify sources;
- connect each conclusion to evidence;
- include methods, alternatives, limitations, risks and verification plan;
- satisfy every task acceptance criterion;
- preserve uncertainty and stop on missing dependencies;
- run both direct empirical-replay and report-quality verification commands listed in `PROMPT.md`.

Hard constraints:

- Do not modify production code or phase-0 raw evidence.
- Freeze tests, thresholds, grids and seed policy before results are inspected.
- Keep the Research Report under `docs/research/` and reproducible research artifacts in their declared task directories.
- Do not invent official data, experimental results or product facts.
- Do not make betting, profit or near-100%-accuracy claims.

## Final Response

Handoff must contain output path, evidence used, verification result, PASS/HOLD/FAIL, blockers and downstream readiness.
