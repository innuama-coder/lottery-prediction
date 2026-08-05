# TASK-002 — 规范数据集、时间语义与数据血缘契约

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：定义可重放、无泄漏、支持修订的数据集和预测快照契约。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/roadmap/phase-0-data-feasibility-plan.md
- docs/roadmap/phase-0-acceptance-contract.json
- docs/research/TASK-001-official-data-source-feasibility.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-002-canonical-dataset-provenance-contract.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- raw snapshot、三个单值规则轴、active_promotion_ids、draw、draw_number、revision、status_event、mechanism_metadata、feature_snapshot、forecast、experiment 的数据字典
- draw_at、page_published_at、http_date、first_seen_at、retrieved_at、corrected_at、available_at 的时间语义
- 不可变快照、追加式状态/修订链、RFC 8785 规范哈希、parser_artifact_sha256、环境锁、幂等键和质量不变量
- 按期号滚动切分与 point-in-time join 契约

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-002-AC-01`、`TASK-002-AC-02`、`TASK-002-AC-03`，具体文字以 canonical roadmap 为准。

- 每张逻辑表具备主键、字段类型、允许空值、来源、available_at 和版本策略；原始响应字节与解码/解析派生物分离。
- 所有预测输入可证明在预测锁定时刻已经可得；任何事后开奖字段不得进入特征快照。
- 大乐透前区/后区与双色球红球/蓝球的固定基数约束、范围约束、排序与唯一性约束均明确。
- 三个单值规则轴和零到多个 active_promotion_ids 的基数、有效期、重叠及期号映射明确，奖级或活动变化不得冒充号码生成变化。
- 状态事件和修订只追加不覆盖；迟到数据、官方更正、解析器重处理、重复期号、规则切换、编码/OCR 和渠道分歧均有确定性 supersession、当前视图和历史视图规则。
- 从实际存储的 raw payload 经 evidence manifest、payload SHA-256、parser_artifact_sha256、环境锁和 RFC 8785 规范记录哈希到 forecast/experiment 全链路可追溯；响应头遵守白名单且不存在未声明派生字段或人工核心号码改写。

## Verification / 验收方法

- 用至少一个正常期、一个合成官方更正与解析器重处理场景、一个规则切换与重叠活动场景做纸面重放，并分别重建当前和历史视图。
- 逐字段执行 point-in-time 泄漏审查和不变量清单审查。
- 直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-002-canonical-dataset-provenance-contract.md`。

## 停止条件

- TASK-001 的核心字段来源或规则边界未通过。
- 无法给预测输入建立可靠 available_at。
- 数据更正不能保留原始快照和审计链。

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

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/roadmap/phase-0-data-feasibility-plan.md；docs/roadmap/phase-0-acceptance-contract.json；docs/research/TASK-001-official-data-source-feasibility.md
- Outputs: docs/research/TASK-002-canonical-dataset-provenance-contract.md；artifacts/research/reviews/TASK-002-review.json
- Game scope: 按 active_games 分节；excluded_games 只记录阻塞原因，不得生成通过结论。
- Independent review: artifacts/research/reviews/TASK-002-review.json
- Effort class: contract_review
- Resource budget: 任务开始前冻结审查轮次、样例数量和复核工时。
- Timeout: 关键字段无法建立 available_at 或两轮审查后仍有高风险泄漏争议时 HOLD。
- TASK-002-AC-01：每个训练字段都能证明在预测锁定前可用，开奖后字段不能进入特征快照。
- TASK-002-AC-02：状态、修订、supersession、当前视图和历史视图规则均确定且可重放。
- TASK-002-AC-03：所有字段和规则只引用 TASK-001 已验收证据，不存在人工核心号码改写路径。
