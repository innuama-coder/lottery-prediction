# TASK-003 — 随机性审计与可检测性/功效边界

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：确定历史数据能检测到多小的偏差，以及哪些预测主张在样本量上不可证。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-002-canonical-dataset-provenance-contract.md

## Scope / 交付物

允许且必须按需要创建：

- `docs/research/TASK-003-randomness-audit-power-envelope.md`
- `artifacts/research/TASK-003/`
- `scripts/research/TASK-003/`
- `tests/research/TASK-003/`

这些是可复现统计研究制品，不是生产系统代码。

期望报告覆盖：

- 均匀无放回零假设及边际、位置、间隔、对子和规则分段检验族
- 多重检验层级和负控设计
- 基于仿真的最小可检测效应 δ*、目标功效和所需前瞻期数
- 检测失败、证据不足与支持零假设的区分规则

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-003-AC-01`、`TASK-003-AC-02`、`TASK-003-AC-03`，具体文字以 canonical roadmap 为准。

- 显著性水平预注册为 α=0.05，目标检验功效不低于 0.80；所有检验族和多重性范围在看结果前冻结。
- 同时报告效应量、置信区间/置信序列和校正后证据，不允许只报告 p 值。
- 均匀世界中的误报率及注入静态偏差、动态漂移、对子交互和机制偏差的检出率均由可复现实验估计。
- 为每种可研究偏差给出 δ*、达到目标功效所需样本/期数及当前数据所处位置；无法达到者明确标为不可判定。
- 报告明确声明“不拒绝零假设不等于证明绝对随机”。

## Verification / 验收方法

- 复核每个检验的零分布、统计量、分层单位和多重校正归属。
- 复核功效曲线的仿真种子、参数网格、Monte Carlo 不确定度与可重放方法。
- 实验重放直接运行：`python scripts/research/TASK-003/verify_power_study.py --artifacts artifacts/research/TASK-003`。
- 报告质量直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-003-randomness-audit-power-envelope.md`。

## 停止条件

- TASK-002 未能排除时间泄漏或规则混合。
- δ* 与业务上最小有意义效应无法定义。
- 仿真无法覆盖零假设与计划挑战模型对应的偏差族。

## Constraints / 边界

- 允许创建本任务专用的仿真、复算和测试代码；不得修改生产代码或阶段 0 原始证据。
- 检验族、阈值、参数网格和种子策略必须在查看结果前冻结。
- 不得执行购彩、自动投注或收益承诺，不得声称预测可逼近 100%。
- 来源冲突时保留冲突和不确定性；不得发明产品事实、统计结果或官方字段。
- 前置报告若缺失或未通过，停止并报告 blocker，不得自行补写其结论。
- 交接时提供：报告路径、来源清单、验证命令及输出、PASS/HOLD/FAIL、残余风险和下一任务可否解锁。

## Decision Principles

Prefer official and point-in-time evidence; preserve uncertainty; never relax a threshold after seeing results; stop when a dependency or source is missing.

## Canonical Contract / 唯一执行契约

本节逐字同步 research-planning.json，优先级高于上文的解释性文字。

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/research/TASK-001-official-data-source-feasibility.md；docs/research/TASK-002-canonical-dataset-provenance-contract.md
- Outputs: docs/research/TASK-003-randomness-audit-power-envelope.md；artifacts/research/TASK-003/；scripts/research/TASK-003/；tests/research/TASK-003/
- Game scope: 功效与 delta-star 逐 active_game 计算；excluded_games 只记录排除原因，禁止合并彩种样本。
- Independent review: artifacts/research/TASK-003/reviewer-attestation.json
- Effort class: monte_carlo_research
- Resource budget: 预注册中冻结场景数、参数网格、每格模拟次数、随机种子层级和最大计算预算。
- Timeout: 预算内 Monte Carlo 区间仍宽到无法判定目标误差或关键结果不能重放时 HOLD。
- TASK-003-AC-01：均匀世界误报率和各偏差场景检出率均由可复现实验估计，并报告 Monte Carlo 区间。
- TASK-003-AC-02：每类偏差给出 delta-star、目标功效不低于 0.80、所需样本和当前样本位置。
- TASK-003-AC-03：相同配置可复算，独立种子结果落在预注册容差内。
