# TASK-008 — 前瞻影子验证与研究转产品决策协议

## Execution Objective / 角色与任务

你是本阶段的研究负责人。唯一任务是：在真实开奖前锁定预测，给出 GO/HOLD/STOP 的可信决策，而非追逐事后回测。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/research/TASK-001-official-data-source-feasibility.md
- docs/research/TASK-004-sequential-evaluation-governance.md
- docs/research/TASK-005-model-feature-selection-study.md
- docs/research/TASK-006-autoresearch-architecture-safety.md
- docs/research/TASK-007-synthetic-benchmark-protocol.md

## Scope / 唯一交付物

只创建或更新 `docs/research/TASK-008-prospective-shadow-validation-protocol.md`。所有 Research Report 文档必须位于 `docs/research/`。

期望报告覆盖：

- 开奖前 forecast lock、时间戳、签名/校验和、公开/审计留痕与开奖后评分协议
- 两种彩票分开治理的 Champion/Challenger 影子运行方案
- 数据异常、规则变更、模型漂移、回滚与冻结规则
- 进入 PRD/HLD/实现的 GO/HOLD/STOP 决策模板

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-008-AC-01`、`TASK-008-AC-02`、`TASK-008-AC-03`，具体文字以 canonical roadmap 为准。

- 每期预测在官方停售/开奖前锁定，之后不可改写；开奖号码发布后只允许追加评分和官方修订记录。
- 达到 TASK-003 的最小前瞻期数之前不得 GO；持续查看只使用 TASK-004 的 anytime-valid 证据。
- GO 必须同时满足 TASK-004 全部门槛、TASK-007 资格、独立重放一致及无未解决数据/规则异常。
- HOLD 用于统计证据不足但系统完整的情况；STOP 用于证据不利、泄漏、重放失败或治理违约。
- 若没有非均匀模型通过，M0 仍可作为诚实概率基线进入只读研究产品；不得包装成“高概率号码”。

## Verification / 验收方法

- 以模拟开奖时钟演练锁定、发布、更正、评分、回滚及规则变更。
- 审计任一期 forecast hash、输入 snapshot、模型版本和评分结果是否可端到端重放。
- 用胜出、无结论、失败三类证据包执行 GO/HOLD/STOP 决策桌面演练。
- 直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-008-prospective-shadow-validation-protocol.md`。

## 停止条件

- 预测不能在开奖前形成不可变证据。
- 前瞻期数或证据门槛被事后缩短/修改。
- 数据、模型或规则异常无法触发自动冻结。

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

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/research/TASK-001-official-data-source-feasibility.md；docs/research/TASK-004-sequential-evaluation-governance.md；docs/research/TASK-005-model-feature-selection-study.md；docs/research/TASK-006-autoresearch-architecture-safety.md；docs/research/TASK-007-synthetic-benchmark-protocol.md
- Outputs: docs/research/TASK-008-prospective-shadow-validation-protocol.md；artifacts/research/reviews/TASK-008-review.json
- Game scope: 预测锁定、评分和决策逐 active_game 独立运行；excluded_games 不生成预测批次。
- Independent review: artifacts/research/reviews/TASK-008-review.json
- Effort class: prospective_protocol_review
- Resource budget: 冻结影子期最小/最大期数、每期计算预算、存储预算、异常复核时限和停止规则。
- Timeout: 无法保证截止前不可变锁定、独立评分或官方修订可追踪时 HOLD，不得启动影子期。
- TASK-008-AC-01：预测在官方停售或开奖前不可变锁定，开奖后只允许追加评分和官方修订事件。
- TASK-008-AC-02：GO 同时依赖最小功效期数、序贯证据、合成资格、独立重放和无未解决数据异常。
- TASK-008-AC-03：报告明确这是未来影子运行协议，不虚构真实前瞻结果。
