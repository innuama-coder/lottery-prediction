# Claude Task Context — TASK-002

Karpathy Context Engineering contract for the current task.

Role: research-report author and evidence reviewer for `规范数据集、时间语义与数据血缘契约`.

Fixed planning input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json`
Fixed task input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASK-002/PROMPT.md`
Fixed relationship input: `tasks/research/v1.0.0-lottery-autoresearch-roadmap/fixtures/TASKS.md`
Fixed output: `docs/research/TASK-002-canonical-dataset-provenance-contract.md`

## Required Reading

Read the fixed inputs first, then all prompt source refs. Write the named Research Report document only, not production code.

Checklist:

- state the scope boundary and research question flow;
- cite and classify sources;
- connect each conclusion to evidence;
- include methods, alternatives, limitations, risks and verification plan;
- satisfy every task acceptance criterion;
- preserve uncertainty and stop on missing dependencies;
- run the direct quality verification command listed in `PROMPT.md`.

Hard constraints:

- Do not implement code.
- Do not change tests, manifests, schemas, migrations, databases or runtime scripts.
- Keep every Research Report output under `docs/research/`.
- Do not invent official data, experimental results or product facts.
- Do not make betting, profit or near-100%-accuracy claims.

## Final Response

Handoff must contain output path, evidence used, verification result, PASS/HOLD/FAIL, blockers and downstream readiness.
