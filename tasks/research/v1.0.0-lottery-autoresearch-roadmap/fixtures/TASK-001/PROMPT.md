# TASK-001 — 官方数据源、规则版本与可得性实证

## Execution Objective / 角色与任务

你是阶段 0 的执行负责人。唯一任务是：按 P0-01 至 P0-07 实际验证大乐透和双色球的官方事实源、历史覆盖、规则时间线、修订与可用字段，并生成可独立重放的证据。

先读取以下来源：

- docs/research/lottery-autoresearch-technical-strategy.md
- docs/roadmap/phase-0-data-feasibility-plan.md
- docs/roadmap/phase-0-acceptance-contract.json
- 体彩与福彩官方规则页、开奖公告和可访问历史接口

## Scope / 交付物

允许且必须按需要创建：

- `docs/research/TASK-001-official-data-source-feasibility.md`
- `artifacts/phase-0/`
- `scripts/phase0/`
- `tests/phase0/`

不得修改未来生产服务、模型训练或预测输出代码。

期望报告覆盖：

- 事前冻结的每彩种目标/最低历史区间、核对样本、观察计划、预算、时钟容差与独立复核人
- 两种彩票的权威主发布源、官方核对渠道、共享上游依赖与访问/条款矩阵
- number_space_version、draw_process_version、prize_rule_version 三个单值轴、active_promotion_ids 活动集合与七字段时间语义
- 预期/实际期号集合、计划/实际请求集合、缺口分类、修订链、原始载荷与 evidence manifest 方案
- 可得/不可得机制元数据清单及阻塞分类
- JSON Schema、RFC 8785 规范哈希、低频抓取、版权/条款、编码/OCR、冲突、独立重放、阶段 1 夹具与唯一阶段决策契约

## Acceptance / 验收标准

报告必须逐项标注并回答 `TASK-001-AC-01`、`TASK-001-AC-02`、`TASK-001-AC-03`、`TASK-001-AC-04`，具体文字以 canonical roadmap 为准。

- P0-01 在观察来源结果前冻结每彩种目标/最低区间、UTC 截止时间、历史核对样本、在线计划、访问/重试预算、时钟容差和独立复核人；必填值无 null，观察后不得缩小或放宽。
- 大乐透与双色球均有权威主发布源；报告评估官方核对渠道的可用性和共享上游，并为记录分配 `corroborated_official`、`shared_upstream` 或 `primary_only`。缺少第二官方渠道降低佐证置信度，但不单独否定数据可行性，也不得用第三方替代最终真值。
- 每个核心字段均映射实际存储的原始载荷、允许保留的响应元数据、初始/最终 URL、retrieved_at、载荷 SHA-256、parser_artifact_sha256、环境锁和 RFC 8785 规范记录哈希；七个时间字段不混用，秘密响应头不持久化。
- 三个单值规则轴和 active_promotion_ids 覆盖冻结的目标与最低区间；通过预期期号集合与实际集合对账证明覆盖，规则空档和各类缺口均显式分类，不以抽样声称全量，也不在观察后缩短区间。
- 官方核对渠道可用时，与主发布源逐字段比较在线每期开奖、全部规则边界/异常/冲突和事前冻结的历史分层样本；不可用部分按冻结口径标为 `primary_only`。存在佐证证据的记录未解决核心号码冲突为 0，全部记录人工核心号码改写次数为 0。
- 按冻结请求集合观察每种彩票至少两个完整开奖周期，计划与实际请求逐项对账，并完成失败注入、恢复和修订重放；该结果只证明可行性，不得宣称长期稳定或生产 SLO。
- 访问不绕过登录、验证码、令牌、限流或其他控制；条款、robots、版权和检查日期有证据，实质性权限不确定即 HOLD。
- 记录状态和官方/解析器修订只追加不覆盖；当前与历史视图可确定性重建，独立复核人能用冻结的唯一命令复算 Schema、哈希、覆盖、核对、修订和 14 个硬门，并验证阶段 1 夹具。
- 机器号、球组、摇奖设备等机制字段按“官方可得/官方不可得/非官方仅作辅助”分类，不得把不可得字段伪装为特征。

## Verification / 验收方法

- 先核验 scope-freeze、observation-plan 和 reviewer-assignment 的 Schema、内容哈希、无 null 及冻结时间早于任何来源观察；再核验 reviewer-attestation 只在 P0-07 重放后签署，且包含输入哈希、命令、退出码和结论。
- 逐行审查来源权威性、共享上游、字段—时间可得性、访问策略和条款矩阵。
- 按规则批准、公告、上市日期和边界期号交叉核对三个单值规则轴与 active_promotion_ids。
- 复核目标/最低区间的预期与实际期号集合、冻结核对样本、计划/实际请求、缺口分类、原始证据哈希和官方渠道差异。
- 由未参与解析器编写的人从空派生目录执行唯一验证命令，重放正常、官方更正、编码错误、格式变化和来源失败场景，并核验阶段 1 夹具。
- 阶段验收直接运行：`python scripts/phase0/verify_phase0.py --contract docs/roadmap/phase-0-acceptance-contract.json --artifacts artifacts/phase-0`。
- 报告质量直接运行：`python tasks/research/v1.0.0-lottery-autoresearch-roadmap/validate_research_report_quality.py --planning tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json docs/research/TASK-001-official-data-source-feasibility.md`。

## 停止条件

- 任一彩种缺少无需绕过访问控制即可使用的权威核心开奖号码来源时，该彩种为 STOP；仅当另一彩种 PASS_FULL 或 PASS_LIMITED 时，项目才可能为 LIMITED_GO。
- 官方条款不允许所需采集/留存，或存在实质性权限不确定且无合规替代方案。
- 无法确定 number_space 或 draw_process 变更边界，导致不同生成过程不可区分。
- 存在无法回到原始官方证据的核心号码、未解决号码冲突、人工改数或未解释缺期。

## Constraints / 边界

- 这是数据可行性实证任务，不是 documentation-only 任务。
- 只允许实现阶段 0 所需的采集、解析、Schema 校验、故障注入、重放、验收工具及其测试；不得修改未来生产系统。
- 不得执行购彩、自动投注或收益承诺，不得声称预测可逼近 100%。
- 来源冲突时保留冲突和不确定性；不得发明产品事实、统计结果或官方字段。
- P0-01 未完成前不得开始任何来源结果观察；不得在观察后降低标准。
- 交接时提供：全部阶段 0 产物、验证命令及输出、每彩种结论、唯一项目决策、残余风险和 TASK-002 是否解锁。

## Decision Principles

Prefer official and point-in-time evidence; preserve uncertainty; never relax a threshold after seeing results; stop when a dependency or source is missing.

## Canonical Contract / 唯一执行契约

本节逐字同步 research-planning.json，优先级高于上文的解释性文字。

- Source refs: docs/research/lottery-autoresearch-technical-strategy.md；docs/roadmap/phase-0-data-feasibility-plan.md；docs/roadmap/phase-0-acceptance-contract.json；体彩与福彩官方规则页、开奖公告和可合规访问的历史入口
- Outputs: docs/research/TASK-001-official-data-source-feasibility.md；artifacts/phase-0/；scripts/phase0/；tests/phase0/
- Game scope: 生成 active_games、excluded_games、per_game_outcome、coverage_tier、corroboration_tier 与 evidence_ref，写入 artifacts/phase-0/stage1-handoff-fixture.json；其中 corroboration_tier 按彩种报告已接受记录的最低层级、各层级计数及计算证据，后续只能使用通过硬门的彩种。
- Independent review assignment: artifacts/phase-0/reviewer-assignment.json（P0-01 事前冻结）
- Independent review attestation: artifacts/phase-0/reviewer-attestation.json（P0-07 重放后签署）
- Effort class: multi_cycle_observation
- Resource budget: P0-01 冻结请求、重试、存储、计算和人工复核预算。
- Timeout: 到 acceptance_cutoff_utc 仍未完成最少观察周期或关键来源持续不可用时必须 HOLD，不得延长截止时间掩盖失败。
- TASK-001-AC-01：验收合同定义的 14 个硬门对每个彩种都有可追溯的 PASS 或 FAIL 证据。
- TASK-001-AC-02：全部 required_artifact_keys 存在、通过对应 Schema，并可回到实际存储的官方原始载荷。
- TASK-001-AC-03：独立复核人从空派生目录得到相同规范记录哈希、覆盖结果、修订视图、硬门结论和唯一项目决策。
- TASK-001-AC-04：研究总结报告如实区分 PASS_FULL、PASS_LIMITED、HOLD、STOP 和尚未验证事实。
