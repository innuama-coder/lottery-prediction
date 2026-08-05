# TASK-007 — 合成基准与 Autoresearch 元评估协议

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：冻结未来系统必须通过的合成世界、隐藏答案、指标和资格门；本任务设计可执行协议，不声称尚未实现的系统已经运行或通过。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-003-randomness-audit-power-envelope.md
- docs/research/TASK-004-sequential-evaluation-governance.md
- docs/research/TASK-005-model-feature-selection-study.md
- docs/research/TASK-006-autoresearch-architecture-safety.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-007-synthetic-benchmark-protocol.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- 均匀世界、静态偏差、动态漂移、机器/球组偏差、对子交互、规则变更和泄漏陷阱场景库
- 误报率、检出功效、检测时延、校准、后悔值、恢复时间和重放一致性指标
- 基准版本、种子、场景难度与隐藏答案隔离规则
- 系统进入真实前瞻影子期的二元资格门槛

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-007-AC-01`、`TASK-007-AC-02`、`TASK-007-AC-03`、`TASK-007-AC-04`，具体文字以 canonical roadmap 为准。

- 协议规定均匀世界在线 FDR/类型一错误不得超过 0.05，并明确使用 Monte Carlo 区间判断。
- 协议规定对 TASK-003 定义的 δ* 及以上效应，目标检出功效不低于 0.80；低于 δ* 标为功效不足。
- 所有泄漏陷阱、隐藏答案隔离、独立重放容差和任一漏检即失败规则在未来执行前冻结。
- 协议定义复杂模型只能在匹配其注入机制的场景胜出，均匀世界不得稳定击败 M0。
- 报告明确标注“协议尚未执行”，不得填写虚构点估计、区间或通过结论。

## Verification / 验收方法

- 审查场景—机制—指标—门槛—失败条件矩阵是否覆盖所有计划模型。
- 审查隐藏真相、种子、制品、独立重放和未来执行顺序是否无歧义。
- 直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-007-synthetic-benchmark-protocol.md`。

## 停止条件

- 场景真相无法与未来研究角色隔离。
- TASK-003 的 δ* 或 TASK-004 的错误率合同缺失。
- 协议覆盖不了 TASK-005 中拟开放的模型机制。
- 报告混入尚未发生的运行结果。

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

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/research/TASK-003-randomness-audit-power-envelope.md；docs/research/TASK-004-sequential-evaluation-governance.md；docs/research/TASK-005-model-feature-selection-study.md；docs/research/TASK-006-autoresearch-architecture-safety.md
- Outputs: docs/research/TASK-007-synthetic-benchmark-protocol.md；artifacts/research/reviews/TASK-007-review.json
- Game scope: 场景、阈值和资格门逐 active_game 冻结；excluded_games 不执行基准。
- Independent review: artifacts/research/reviews/TASK-007-review.json
- Effort class: benchmark_protocol_review
- Resource budget: 冻结场景矩阵、重复次数上限、隐藏答案管理方式、复核轮次和未来执行预算边界。
- Timeout: 协议无法在执行前冻结隐藏答案、容差或二元资格门时 HOLD。
- TASK-007-AC-01：协议规定均匀世界错误率不超过 0.05，并说明使用 Monte Carlo 区间判定。
- TASK-007-AC-02：协议规定 delta-star 及以上效应目标功效不低于 0.80，低于 delta-star 标为功效不足。
- TASK-007-AC-03：所有泄漏陷阱、隐藏答案、复核容差和进入真实影子期的二元门槛均在执行前冻结。
- TASK-007-AC-04：报告明确标注这是待未来实现执行的协议，不包含虚构运行结果。
