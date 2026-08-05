# TASK-006 — Autoresearch 架构、安全边界与独立复核

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：定义可自主提出和淘汰假设、但不能篡改评估器或伪造结果的研究系统。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-004-sequential-evaluation-governance.md
- docs/research/TASK-005-model-feature-selection-study.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-006-autoresearch-architecture-safety.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- Hypothesis Explorer、Experiment Executor、Independent Verifier、Champion Governor 的职责边界
- 假设树、实验 manifest、失败记忆、预算、并发、重试与停止协议
- 不可变评估器、沙箱、依赖/网络、随机种子、制品校验和与可复现实验契约
- 人工批准、异常处置、回滚和禁止自动购彩边界

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-006-AC-01`、`TASK-006-AC-02`、`TASK-006-AC-03`，具体文字以 canonical roadmap 为准。

- 提出假设、执行实验、复核证据、批准晋级四种权限分离；同一代理不得自证晋级。
- 每次实验有唯一 ID、父假设、代码/数据/配置/环境摘要、种子、预算、预注册指标和完整结果，成功与失败均不可变留存。
- 评估器、最终影子集和晋级阈值对研究代理只读；网络、依赖变更和秘密访问采用最小权限。
- Independent Verifier 必须从原始快照独立重放候选结果；重放不一致即阻断晋级。
- 系统只输出研究预测与不确定性，不执行购彩、不承诺收益或“逼近 100%”。

## Verification / 验收方法

- 对评估器篡改、结果伪造、依赖污染、数据泄漏、重复实验和代理失联做威胁建模演练。
- 用一条成功链和一条失败链走通职责、审计、重放、回滚和人工门禁。
- 直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-006-autoresearch-architecture-safety.md`。

## 停止条件

- 研究代理可以修改评估器、历史结果或最终影子预测。
- 实验失败不会被持久化，导致重复搜索或选择性报告。
- 无法实现独立复核或重放一致性检查。

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

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/research/TASK-004-sequential-evaluation-governance.md；docs/research/TASK-005-model-feature-selection-study.md
- Outputs: docs/research/TASK-006-autoresearch-architecture-safety.md；artifacts/research/reviews/TASK-006-review.json
- Game scope: 治理、权限、审计与回滚覆盖每个 active_game；excluded_games 不进入实验队列。
- Independent review: artifacts/research/reviews/TASK-006-review.json
- Effort class: architecture_threat_review
- Resource budget: 冻结威胁场景数、权限矩阵审查轮次、回滚演练例数和复核工时。
- Timeout: 关键职责无法分离、审计事件不能完整追踪或回滚责任不明确时 HOLD。
- TASK-006-AC-01：四类权限分离，同一主体不能自证晋级。
- TASK-006-AC-02：每个实验的输入、代码/配置/环境摘要、种子、预算、指标和成功/失败结果不可变留存。
- TASK-006-AC-03：独立复核、最小权限、回滚和禁止自动购彩均有明确强制点。
