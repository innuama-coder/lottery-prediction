# TASK-005 — 模型族、特征注册表与模型烘焙方案

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：选择与样本量、固定基数组合空间和低信号现实相匹配的候选模型与特征。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-002-canonical-dataset-provenance-contract.md
- docs/research/TASK-003-randomness-audit-power-envelope.md
- docs/research/TASK-004-sequential-evaluation-governance.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-005-model-feature-selection-study.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- M0 均匀基线、M1 强收缩固定基数贝叶斯模型及后续 M2/M3/M4 候选的数学规格
- 特征注册表：机制/规则/边际/依赖/集合形态/负控/事后禁用
- 先验、收缩、消融、滚动验证、校准与 Top-1000 生成方法
- 打开复杂模型的证据门槛与淘汰条件

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-005-AC-01`、`TASK-005-AC-02`、`TASK-005-AC-03`，具体文字以 canonical roadmap 为准。

- M0 永久保留为默认 Champion；M1 是首个 Challenger，并给出归一化概率、先验、后验和抽样/排名算法。
- 每个特征记录定义、数据源、available_at、预期因果/机制理由、泄漏风险、消融组和状态。
- 事后开奖字段一律禁用；负控特征必须进入搜索以估计过拟合/泄漏风险。
- M2 动态状态空间仅在稳定非平稳证据出现后开放；M3 对子最大熵仅在交互偏差通过门槛后开放；GBDT 仅作诊断；LSTM/Transformer/RL 当前关闭。
- 模型比较完全遵循 TASK-004，不能用 Top-1000 是否命中代替概率评分。

## Verification / 验收方法

- 对每个模型完成参数量—有效样本量—可识别性审查。
- 对每个特征完成 point-in-time 与负控审查，并核对消融覆盖。
- 用 M0 数据验证复杂模型不会因搜索流程产生系统性虚假提升。
- 直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-005-model-feature-selection-study.md`。

## 停止条件

- 候选模型不能输出归一化联合概率。
- 模型参数量/自由度超出 TASK-003 证明可检测的范围。
- 特征依赖无法前瞻获得的元数据或只有非可审计来源。

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

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/research/TASK-002-canonical-dataset-provenance-contract.md；docs/research/TASK-003-randomness-audit-power-envelope.md；docs/research/TASK-004-sequential-evaluation-governance.md
- Outputs: docs/research/TASK-005-model-feature-selection-study.md；artifacts/research/reviews/TASK-005-review.json
- Game scope: 模型、特征、校准和 Top-1000 规则逐 active_game 定义；excluded_games 不产生候选模型或预测结论。
- Independent review: artifacts/research/reviews/TASK-005-review.json
- Effort class: model_registry_review
- Resource budget: 任务开始前冻结候选模型数、特征组数、审查轮次和复核工时。
- Timeout: 两轮审查后仍存在无机制依据特征、泄漏风险或复杂模型开放条件争议时 HOLD。
- TASK-005-AC-01：M0 永久保留，M1 数学规格、先验、后验和合法组合概率完整。
- TASK-005-AC-02：每个特征包含定义、来源、available_at、机制理由、泄漏风险、消融组和状态。
- TASK-005-AC-03：复杂模型开放条件引用 TASK-003/004，不能凭开发集微小提升开放。
