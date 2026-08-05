# TASK-999 — 最终研究验收与实施准入评审

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：汇总并二元判定完整研究包是否足以进入 PRD/HLD 与开发规划。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-001-official-data-source-feasibility.md
- docs/research/TASK-002-canonical-dataset-provenance-contract.md
- docs/research/TASK-003-randomness-audit-power-envelope.md
- docs/research/TASK-004-sequential-evaluation-governance.md
- docs/research/TASK-005-model-feature-selection-study.md
- docs/research/TASK-006-autoresearch-architecture-safety.md
- docs/research/TASK-007-synthetic-benchmark-protocol.md
- docs/research/TASK-008-prospective-shadow-validation-protocol.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-999-lottery-autoresearch-research-acceptance.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- Acceptance report path: docs/research/TASK-999-lottery-autoresearch-research-acceptance.md
- 逐任务证据与依赖完成矩阵
- 跨报告术语、阈值、数据、模型和治理一致性审查
- GO/HOLD/STOP 最终结论与未解决风险
- 获准进入 PRD/HLD 时的冻结研究基线

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-999-AC-01`、`TASK-999-AC-02`、`TASK-999-AC-03`，具体文字以 canonical roadmap 为准。

- TASK-001 至 TASK-008 均通过各自质量验证和独立验收，且所有依赖有证据。
- 数据源、时间语义、δ*、α、功效、在线 FDR、模型门槛、合成基准和前瞻决策口径跨报告一致。
- 不存在未关闭的高风险泄漏、不可重放、来源冲突、评估器可变或自动购彩问题。
- 只有证据齐全时结论可为 GO；证据不足为 HOLD；任何硬失败为 STOP，不允许条件性美化为通过。
- 评审只准入后续 PRD/HLD/开发规划，不声称已经实现或证明可预测彩票。

## Verification / 验收方法

- 读取 research-planning.json 并核对全部计划路径、依赖、覆盖矩阵和来源追踪。
- 运行每个任务在 canonical roadmap 中直接列出的验证命令，并保存全部退出码和输出。
- 运行路线图校验：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_roadmap.py --roadmap tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json --contract docs/roadmap/phase-0-acceptance-contract.json`。
- 运行最终报告校验：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json --acceptance docs/research/TASK-999-lottery-autoresearch-research-acceptance.md`。
- 逐项复核 GO/HOLD/STOP 二元规则和遗留风险所有者。

## 停止条件

- 任一研究报告缺失、校验失败或依赖未完成。
- 任一关键统计阈值可被事后修改或跨报告不一致。
- 存在未解决的来源、泄漏、复现、安全或责任使用阻塞。

## Constraints / 边界

- Do not implement code.
- 这是 documentation-only 任务；不得修改生产代码、测试、构建清单、schema、migration、数据库或运行时脚本。
- 不得执行购彩、自动投注或收益承诺，不得声称预测可逼近 100%。
- 来源冲突时保留冲突和不确定性；不得发明产品事实、统计结果或官方字段。
- 前置报告若缺失或未通过，停止并报告 blocker，不得自行补写其结论。
- 交接时提供：报告路径、来源清单、验证命令及输出、PASS/HOLD/FAIL、残余风险和下一任务可否解锁。

## Decision Principles

Prefer official and point-in-time evidence; preserve uncertainty; never relax a threshold after seeing results; stop when a dependency or source is missing.

## Canonical Contract / 唯一执行契约

本节逐字同步 research-planning.json，优先级高于上文的解释性文字。

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；artifacts/research/task-states/；docs/research/TASK-001-official-data-source-feasibility.md；docs/research/TASK-002-canonical-dataset-provenance-contract.md；docs/research/TASK-003-randomness-audit-power-envelope.md；docs/research/TASK-004-sequential-evaluation-governance.md；docs/research/TASK-005-model-feature-selection-study.md；docs/research/TASK-006-autoresearch-architecture-safety.md；docs/research/TASK-007-synthetic-benchmark-protocol.md；docs/research/TASK-008-prospective-shadow-validation-protocol.md
- Outputs: docs/research/TASK-999-lottery-autoresearch-research-acceptance.md；artifacts/research/finalization/TASK-999-decision.json；artifacts/research/reviews/TASK-999-review.json
- Entry: 正常路径要求 TASK-001 至 TASK-008 全部 passed；短路路径允许 held/stopped，未执行后续任务必须在 artifacts/research/task-states/{run_id}/{task_id}.json 写入 skipped_terminal 记录。TASK-999 收口后补证必须开启新 run_id，不得覆盖旧 run。
- Game scope: 分别报告每个 active_game 的证据状态与每个 excluded_game 的阻塞原因。
- Independent review: artifacts/research/reviews/TASK-999-review.json
- Effort class: cross_report_acceptance
- Resource budget: 冻结报告清单、交叉核对项、复核轮次和异议处理时限。
- Timeout: 正常路径证据不完整但不存在硬失败时 HOLD；硬失败或 stopped 记录触发 STOP；不得无限等待以延迟结论。
- Decision order: STOP → GO → HOLD；硬失败优先，禁止默认 GO。
- TASK-999-AC-01：TASK-001 至 TASK-008 均有通过、暂停、停止或终止跳过记录；已有报告的术语和阈值一致。
- TASK-999-AC-02：报告明确区分已执行证据、研究设计和未来系统协议。
- TASK-999-AC-03：只有证据完整时才 GO；证据不足为 HOLD；硬失败为 STOP；GO 仅准入 PRD/HLD。
