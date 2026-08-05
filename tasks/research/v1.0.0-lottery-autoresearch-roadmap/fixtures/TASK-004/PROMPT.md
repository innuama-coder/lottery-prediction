# TASK-004 — 序贯评估、校准与 Champion 治理协议

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：建立允许持续观察但不膨胀假阳性的模型比较、晋级和回滚规则。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-002-canonical-dataset-provenance-contract.md
- docs/research/TASK-003-randomness-audit-power-envelope.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-004-sequential-evaluation-governance.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- 联合 log score 相对 M0 的主指标与边际 Brier/校准辅助指标
- anytime-valid confidence sequence/e-process 的精确定义
- 在线 FDR、可选停止、模型切换、晋级、冻结与回滚协议
- Top-1000 覆盖的产品观察指标及其不得作为主模型选择指标的边界

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-004-AC-01`、`TASK-004-AC-02`，具体文字以 canonical roadmap 为准。

- 主指标为每期联合 log score 差 ΔLS=M_challenger−M0，概率下界/裁剪规则在预测前冻结。
- 序贯单模型晋级使用 α=0.05 的 anytime-valid 规则（等价 e-value 门槛至少 1/α=20），模型族搜索使用在线 FDR q=0.05。
- GO 同时要求：达到 TASK-003 的功效期数、ΔLS 置信序列下界>0、e-value≥20、校准无实质退化、负控未触发。
- HOLD 表示证据不充分且继续影子运行；STOP/ROLLBACK 在数据泄漏、规则漂移、校准失效或下界持续不利时触发。
- 所有比较按彩种/规则版本预声明层级，禁止用同一开奖既调参又作最终证据。

## Verification / 验收方法

- 用均匀模拟验证长期持续查看时类型一错误仍受控。
- 用手工构造的胜/负/无差异轨迹逐条执行 GO/HOLD/STOP 决策表。
- 直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-004-sequential-evaluation-governance.md`。

## 停止条件

- TASK-003 未给出 δ*、功效目标或最小前瞻期数。
- 联合概率无法归一化或无法对真实开奖号码计算有限 log score。
- 协议允许研究者在看到结果后改变指标、窗口或模型族。

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

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/research/TASK-002-canonical-dataset-provenance-contract.md；docs/research/TASK-003-randomness-audit-power-envelope.md
- Outputs: docs/research/TASK-004-sequential-evaluation-governance.md；artifacts/research/reviews/TASK-004-review.json
- Game scope: 指标和序贯证据按 active_game 分层；excluded_games 不参与证据累积。
- Independent review: artifacts/research/reviews/TASK-004-review.json
- Effort class: protocol_review
- Resource budget: 冻结协议例题数量、审查轮次和统计复核工时。
- Timeout: 两轮审查后仍存在可选停止或多重性漏洞时 HOLD。
- TASK-004-AC-01：主指标为逐期联合 log score 差；序贯单模型使用 alpha=0.05、e-value 至少 20，模型族在线 FDR q=0.05；校准、负控和 TASK-003 功效期数共同形成无矛盾决策表。
- TASK-004-AC-02：同一开奖不能同时用于调参与最终证据，可选停止不会破坏声明错误率。
