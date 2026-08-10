# 彩票预测与 AutoResearch 系统路线图

版本：2.2

状态：候选项目级路线图；合入 `main` 后以包含本文件的固定 commit 生效

更新时间：2026-08-10

已完成阶段基线：`2f6ec9620025b858ddd66f92548c0042709fa601`

## 1. 文档定位与权威顺序

本文档从已完成的 Phase 0–3 出发，定义实现最终彩票预测与 AutoResearch 系统所需的剩余路线。它只有在合入 `main` 并取得固定 commit SHA 后，才能成为远程设计、开发和验收任务的冻结项目级输入；工作树中的未提交版本不是权威身份。

发生冲突时按以下顺序处理：

1. `main` 固定 commit 中的本文件定义项目目标、阶段顺序、全局边界和最终验收语义。
2. 各阶段在本文件边界内另行冻结的目标说明、总体设计、详细计划和机器验收合同定义该阶段实施细节。
3. Phase 0–3 已验收制品分别证明其历史交付事实，不因本路线图更新而改变。
4. `docs/research/lottery-autoresearch-technical-strategy.md` 只保留研究方法和风险背景；其中“仍不进入产品实现”的旧阶段顺序已被本文件取代。
5. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/` 是历史研究分解，不代表当前进度、产品范围或实施顺序。

任何后续阶段都不得改写 Phase 0–3 的冻结输入、结果、review、acceptance 或递归 manifest。

## 2. 最终产品目标

项目最终交付一个可持续运行、可审计的彩票预测与 AutoResearch 系统：

1. 同时支持福彩双色球（SSQ）和体彩超级大乐透（DLT）。
2. 对每个目标期输出恰好 1,000 个合法、互不重复的完整投注组合，并按模型联合概率从高到低排列。
3. 每次官方开奖后，自动评分已锁定预测，并执行一次能够调整候选模型参数和特征配置的受控 AutoResearch 循环。
4. 具备以严格历史、合成和真实前瞻证据发现、验证、采用和回退更优模型的持续改进能力。
5. 持续计算完整组合的 Top-10、Top-100、Top-200 和 Top-1000 命中/召回，并以科学上可观测的指标驱动排名质量和预测质量改进。

### 2.1 目标语义

- **预测对象：** 一个对象是一注完整、合法的 SSQ 或 DLT 组合，不是单个号码，也不是部分命中。
- **Top-1000：** 每个彩种、每个目标期必须恰好 1,000 注且无重复。排名依据是完整组合联合概率；概率相同必须保留 tie group 和 tie-aware rank，确定性 tie-break 不能伪装为置信差异。
- **置信度：** 指完整合法组合空间上归一化的模型联合概率。它是模型估计，不是中奖、收益或随机性结论。
- **Top-K 命中/召回：** 每期只有一个实际完整开奖号码，因此 `hit@K` 和 `recall@K` 都是实际组合是否位于锁定前 K 名的 0/1 值；累计值是冻结观察窗口内的均值。部分号码命中必须使用不同指标名。
- **开奖后调整：** 每期开奖都必须生成 AutoResearch 决策。真实数据可以得到 `no_change`，但系统必须通过受控正向案例证明它确实能够改变候选参数、改变候选特征配置并影响下一期 shadow 输出。
- **逐步提升：** 表示系统持续寻找并只采用有证据的改善，不表示每期强制变化、准确率单调上升或彩票必然存在可利用规律。

## 3. 三类状态不得混用

项目同时维护三类独立状态。工程状态属于项目 release；模型和 Top-K 状态不是项目级单值，而是带完整维度的记录：

| 状态类型 | 唯一记录键 | 允许值 | 回答的问题 |
| --- | --- | --- | --- |
| 工程交付状态 | `(system_release_id)` | `HOLD`、`FAIL`、`SYSTEM_MVP_GO`、`PROSPECTIVE_GO`、`SYSTEM_GO` | 双彩种预测、评分、AutoResearch、前瞻运行、治理和恢复能力是否按合同交付 |
| 模型改进状态 | `(game, model_id, comparator_champion_id, model_release_id, window_id)` | `baseline_only`、`shadow_candidate`、`prospective_improvement_confirmed` | 该彩种的该模型是否通过历史/合成筛选，以及是否真实前瞻优于该窗口冻结的比较 Champion |
| Top-K 结果状态 | `(game, K, model_id, comparator_champion_id, model_release_id, window_id)` | `insufficient_observation`、`no_confirmed_lift`、`confirmed_lift` | 该彩种、该 K、该模型相对冻结比较 Champion 是否有足够真实观测支持召回提升结论 |

规则如下：

- `SYSTEM_GO` 可以和 `baseline_only`、`insufficient_observation` 同时成立。此时系统能力合格，但不得声称实际预测效果已经提高。
- 工程状态只允许按 `SYSTEM_MVP_GO -> PROSPECTIVE_GO -> SYSTEM_GO` 前进；任一阶段的 `HOLD|FAIL` 不能被模型或 Top-K 科学状态覆盖。
- SSQ 和 DLT 分别维护 `champion_by_game`、shadow 队列、模型状态和证据窗口。两个彩种可以使用同一模型代码，但参数、预测、指标、错误预算、晋升和回退决策必须独立。
- 历史或合成优势最多产生 `shadow_candidate`，不能产生 `prospective_improvement_confirmed`。
- `prospective_improvement_confirmed` 必须来自结果前冻结、不可回填的真实前瞻 Champion/challenger 同期预测。
- Top-K 观察不足时必须保持 `insufficient_observation`；不能用联合 log score、合成结果或参数变化替代实际 Top-K 提升结论。
- 达到预注册最小观察量但未通过提升门时，Top-K 状态为 `no_confirmed_lift`；只有通过效应和置信门才是 `confirmed_lift`。
- 项目只有在相应状态真正成立时，才能分别声明“系统已交付”“模型已改善”或“Top-K 已观察到提升”。
- 模型改进和 Top-K 状态必须显式绑定 challenger、比较 Champion、release 和冻结观察窗口；新 Champion 或规则版本启用后重新建立状态，旧 comparator 下的结论不能无条件继承。
- 项目级科学摘要只能列出上述逐彩种、逐 K 状态矩阵。SSQ 的结论不得外推到 DLT，一个 K 的结论不得外推到其他 K；不得生成丢失这些维度的全局 `improved=true`。

## 4. 指标体系与可观测性边界

### 4.1 为什么不能用短期 Top-K 命中调参

完整组合空间为：

- SSQ：`C(33,6) * C(16,1) = 17,721,088`；
- DLT：`C(35,5) * C(12,2) = 21,425,712`。

在 M0 下，每期 Top-1000 命中概率只有约 `0.00564%` 和 `0.00467%`。按每周约三期开奖计算，期望观察到一次 Top-1000 命中约需 114 年和 137 年；Top-10/100/200 更稀疏。因此 20 期或数百期真实运行可以验证系统和概率质量，不能证明 Top-K 命中率已经提高。

### 4.2 分层指标

| 层级 | 指标 | 用途 |
| --- | --- | --- |
| 科学主指标 | 相对当前 Champion 的逐期联合 log-score skill | 候选选择和真实前瞻晋升的主要证据 |
| 概率守护 | 概率归一、inclusion Brier、校准、稳定性、数据完整性 | 防止通过错误置信或泄漏获得表面优势 |
| 稠密排名指标 | 实际组合 tie-aware rank 区间、midrank percentile、相对 Champion 的排名变化 | 每期开奖都提供排名质量信息，避免被确定性 tie-break 误导 |
| 输出覆盖指标 | `sum(top-K joint probability)`、Top-K 合法性、唯一性、嵌套一致性 | 验证排名输出和模型预计覆盖质量 |
| 稀疏业务结果 | `hit@10/100/200/1000`、累计 recall、观察期数和置信区间 | 长期如实报告，不作高频调参或短期阶段门 |

AutoResearch 以联合 log-score skill 为主优化目标，以校准和稠密排名指标为守护/辅助目标。候选晋升必须主指标改善、守护指标不退化，并满足结果前冻结的多重检验和稳定性门。实际 Top-K 提升对 SSQ/DLT 和 `K in {10,100,200,1000}` 八个单元分别判定；同一窗口内控制八个单元的多重比较，连续窗口使用预注册在线错误预算。只有对应单元达到功效、效应和置信门时，才能标记该单元为 `confirmed_lift`。

在合成已知分布中，可以直接计算候选对真实生成分布的 Top-K 覆盖质量，用于证明系统具有改善排名的能力；该结果不得冒充真实彩票 Top-K 提升。

## 5. 总体工作方法

系统采用相互隔离的两个循环：

1. **开奖周期循环：** 确定下一目标期，冻结预测时点数据，生成 Champion 和合格 shadow 的概率与 Top-1000，开奖前原子锁定；开奖后核验官方结果、guarded label unlock、评分并追加前瞻账本。
2. **AutoResearch 循环：** 消费本期新解锁结果并生成一个 research decision。决策可以在冻结预算内启动零个或多个注册实验；每个实验必须对应一个可证伪假设，并且只修改一个模型、参数族、特征族或训练因素。全部实验完成或决定不启动实验后，形成 `no_change|rejected|archived|shadow_candidate_proposal`。

每个 AutoResearch 决策必须绑定触发开奖、彩种、父模型/配置、训练截止、代码和数据身份、当前 alpha wealth、实验预算、`experiment_count`、参数/特征差异、实验日志和终态。`experiment_count=0` 时必须记录 `no_eligible_hypothesis|budget_exhausted|guard_hold|scheduled_no_change` 等机器理由；不能创建空实验冒充研究。研究代理不能修改数据采集、规则解释、标签隔离、评分器、验收器、既有预测或既有评分。

每个彩种和假设族分别维护结果前冻结的在线错误预算。alpha spending、奖励、最大实验数、预算耗尽和停止规则必须机器可重算；alpha wealth 不得为负，预算耗尽后只能 `no_change` 或等待下一预注册窗口，不能继续未注册搜索。

候选生命周期固定为：

```text
proposal -> registered experiment -> historical/synthetic qualification
         -> rejected|archived|shadow_candidate
         -> prospective shadow -> promote_proposal|continue|retire
         -> independent review -> Champion promotion|rejection
```

任何一步失败都必须保留，不得覆盖或只提交最佳结果。M0 永久保留为可回退基线。

## 6. 当前真实基线

| 阶段 | 状态 | 已交付能力 | 当前结论/限制 |
| --- | --- | --- | --- |
| Phase 0 | 已完成 | 双彩种官方数据可行性、Schema、来源和重放证据 | 证明可采集与核验，不证明长期可用率 |
| Phase 1 | 已完成 | SSQ/DLT 规范化数据层、规则身份、不可变历史基线 | 为后续研究和运行提供可信输入 |
| Phase 2（含 2.1 修复 release） | 已完成，`PASS / GO` | 随机性审计、功效边界、证据重算和独立复核 | 科学分类 `indeterminate`，不等于证明随机或存在信号 |
| Phase 3 | 已完成，`PASS / GO` | M0/M1 历史滚动研究、联合概率、Top-1000 研究接口、泄漏隔离、独立 replay 和验收闭包 | `no_shadow_candidate`；M0 继续作为 Champion |

Phase 3 正式验收身份是 `P3-R07-2c0fa97-20260810-I01`，权威制品为 `artifacts/phase-3/P3-R07-2c0fa97-20260810-I01/acceptance/I01/acceptance.json`：`PASS / GO / no_shadow_candidate`、blocking findings 为 0、M0 保持 Champion。Phase 4 从该身份开始，不重新开发 Phase 0–3。

## 7. Phase 4：预测与 AutoResearch 闭环 MVP

### 7.1 目标与边界

把 Phase 3 历史研究能力扩展为可部署的双彩种闭环：系统能在不知道未来结果时生成、锁定和发布 Top-1000，在开奖后评分，并完成一次真实可调整但不能越权晋升的 AutoResearch 决策。

Phase 4 使用合成/固定 fixture 验证完整闭环和调整能力，使用真实官方接口验证 readiness；不把历史回填伪装为前瞻证据，不要求发现真实 shadow candidate，不建设 WebUI、自动购彩、代购、资金或收益系统。

### 7.2 必须完成

- 建立 SSQ/DLT 目标期日历、规则映射、增量官方结果采集、修订链、去重和 Phase 1 兼容的追加发布。
- 实现 Champion/shadow 预测、完整联合概率、精确 Top-1000、tie group、锁定截止和不可变 forecast ledger。
- 实现 guarded label unlock、Top-K、联合 log score、校准、tie-aware rank 和输出覆盖指标。
- 实现候选模型/特征 registry、参数和特征配置 diff、隔离实验、预算、checkpoint、失败终态和 shadow 接入。
- 实现逐彩种 `champion_by_game`、逐彩种/假设族 alpha wealth，以及逐 `(game,K,model,comparator,release,window)` 的 Top-K 状态存储和查询。
- 提供调度、幂等恢复、告警、离线 replay、CLI、Schema、依赖锁、测试、VPS 运行手册和版本化 release。
- 在 Phase 4 正式运行前冻结数值阈值、种子、模拟功效、资源预算、角色、命令、输出路径和 acceptance 合同。

### 7.3 交付物

- 总体设计、详细计划、预注册、机器验收合同、故障模型和 SLO 合同。
- 数据/日历、预测/锁定、评分、AutoResearch 和治理前置代码。
- forecast、ranking、metric、experiment、decision、champion-by-game、scientific-status、alpha-wealth、manifest、review 和 acceptance Schema。
- 正向/负向 E2E、独立 replay、递归 evidence manifest 和 `artifacts/phase-4/<release-id>/`。

### 7.4 验收标准与方法

Phase 4 只有在以下条件全部满足时才可得到 `SYSTEM_MVP_GO`：

1. SSQ 和 DLT 均完成 `prepare -> predict -> lock -> ingest -> unlock -> score -> autoresearch -> decision -> next forecast`。
2. 每个有效预测恰好 1,000 个合法、唯一完整组合；完整空间概率归一，排序、tie、Top-K 嵌套和固定输入重放全部正确。
3. M0 全空间并列被如实表示，确定性 tie-break 不改变概率或 tie-aware 评价。
4. 参数正向 fixture 必须产生可验证参数 diff、新候选身份和变化后的 shadow 概率/Top-1000。
5. 特征正向 fixture 必须产生允许特征的 enable/disable 或配置 diff，并使下一期 shadow 使用该配置。
6. 连续 AutoResearch 控制器必须先在继承 Phase 3 的固定基数小空间 `N=10,k=3` 上通过顺序资格：对每个彩种运行 1,000 个均匀合成序列，每个序列固定 150 个开奖/研究周期；“序列内任一错误 `shadow_candidate_proposal`”的发生率不得高于 5%，历史或合成证据触发 Champion 晋升的次数必须为 0，alpha wealth/停止规则重算一致率必须为 100%。小空间只用于可承担的大规模控制器统计，不替代第 8 项真实规则 known-answer。
7. 在同一小空间中，对每个彩种分别运行静态偏差、缓慢漂移和有用特征合成序列；正确参数方向、漂移方向或特征配置的序列级恢复率分别不得低于 90%。永远 `no_change` 的控制器不能通过这些正向资格。序列长度、每期最大实验数、效应大小、种子和置信算法必须在结果前冻结，且不得低于均匀资格的 150 周期/1,000 序列基线。
8. 使用当前 SSQ/DLT 完整规则空间和已知非均匀生成分布执行 full-rule known-answer；候选对 Top-10、100、200、1000 的真实覆盖质量必须在两个彩种的全部八个单元分别严格优于 M0，不能只选择一个 K 报告。该合成能力结果不得写成真实 `confirmed_lift`。
9. 任何标签提前读取、未来特征、无 PIT 外部字段、锁后改写、非法概率、结果后改指标、跨彩种证据合并和直接改 Champion 都 fail closed。
10. 每个预测、decision 和实验有唯一终态；失败、超时、跳过、零实验和 `no_change` 均保留，恢复不重复预测、解锁、评分或 alpha spending。
11. 独立路径从底层输入重算预测身份、Top-K、概率、排名指标、逐彩种 Champion、状态矩阵、alpha wealth、决策和证据闭包，blocking findings 为 0。

单元、Schema、known-answer、双彩种正负 E2E、故障恢复、泄漏负控、合成资格、独立 replay、最终 validator 和人工签署共同构成验收。只检查“任务已触发”或顶层 `PASS` 不合格。

Phase 4 结束时允许模型状态为 `baseline_only`，Top-K 状态为 `insufficient_observation`；这不影响 `SYSTEM_MVP_GO`，但禁止宣称真实预测效果已改善。

## 8. Phase 5：固定窗口真实前瞻运行

### 8.1 目标与固定观察窗口

从结果前冻结的 activation time 开始，对两个彩种各自接下来的 20 个实际完成官方开奖的连续目标期运行 Phase 4 系统。activation 后到窗口结束之间的官方取消或正式变更期必须保留事件和官方依据，但不计入 20 个已开奖目标；数据源故障、系统故障、不利结果和零命中期仍计入，不能跳过或延长窗口来替换。

Phase 5 验证真实前瞻运行和 SLO，不宣称 20 期足以证明模型或 Top-K 改善。

本阶段的 20 期、95% 按时锁定率、24 小时评分时限和 4 小时 RTO 是项目最低产品 SLO，用于证明系统能够稳定运行，不是模型科学门。后续阶段可以收紧这些值；放宽任何一项必须发布新版项目 ROADMAP，不能由 Phase 4/5 预注册或执行人自行决定。

### 8.2 必须完成与交付物

- 每个计划目标在锁定截止前生成 Champion 预测，合格 challenger 同期生成 shadow 预测。
- 每期开奖后完成官方结果核验和 AutoResearch 决策；存在有效锁定预测时还必须完成评分、排名更新和漂移检查，缺少预测时生成明确的 `missed_forecast` 评分终态。
- 保存全部按时、迟到、失败、跳过、修订、`no_change` 和候选实验记录。
- 交付逐期 forecast/result 对应关系、SLO、累计指标、候选比较、独立复核和 `artifacts/phase-5/<release-id>/`。

### 8.3 量化验收标准

- 每彩种 20 个连续已开奖目标均有唯一终态，终态覆盖率 100%，选择性遗漏为 0；窗口内官方取消/变更事件记录覆盖率也为 100%。
- 按时有效锁定率至少 95%，即每彩种至少 19/20；同一彩种不得连续漏掉两个计划目标。
- 所有有效锁定预测最终评分率为 100%；至少 95% 在官方结果首次完成独立核验后 24 小时内评分，全部必须在 Phase 5 验收前完成。
- 所有有效预测均恰好 1,000 注，非法/重复组合、锁后改写、重复解锁和重复评分均为 0。
- 每个已开奖目标、每个彩种都有绑定新结果的 AutoResearch 决策，不因预测失败而跳过；`experiment_count`、参数/特征变化或零实验/`no_change` 理由覆盖率为 100%。
- 锁定预测和 append-only ledger 的 RPO 为 0；控制面恢复演练 RTO 不超过 4 小时，并且不能越过下一锁定截止。
- 正式窗口的网络、数据、模型、指标和证据身份全部可重放；哈希与独立 replay 一致率为 100%，blocking findings 为 0。

以上条件全部通过时工程状态为 `PROSPECTIVE_GO`。Phase 5 可以在 `baseline_only` 和 `insufficient_observation` 下通过；若出现候选优势，只能更新为 `shadow_candidate`，真实晋升仍须 Phase 6 治理和足量前瞻证据。

## 9. Phase 6：Champion 治理、1.0 交付与持续改进入口

### 9.1 目标

把已通过真实前瞻窗口的闭环固化为可维护、可回退的 1.0 系统，建立 Champion 晋升、回退、规则变更和后续连续研究治理。

### 9.2 必须完成与交付物

- 建立逐彩种 Champion/challenger 状态机、结果前晋升合同、逐彩种/假设族在线错误预算、角色分离、双人批准、冷静期和一键回退。
- 建立数据源降级、规则变更隔离、错期开奖修订、调度补偿、监控告警、备份恢复和灾难演练。
- 建立版本化模型卡、数据卡、变更日志、逐期指标导出、候选记忆库和周期性独立复核。
- 保留 M0 为永久回退模型；任何新 Champion 都必须可复算并能在守护失败时回退。
- 交付 1.0 release、部署/运行/恢复手册、治理合同、Phase 5 SLO 证据、独立统计/安全复核和 `artifacts/phase-6/<release-id>/`。

### 9.3 验收标准与方法

- Phase 5 已取得 `PROSPECTIVE_GO`，所有量化 SLO 已通过，没有未关闭 blocking finding。
- 分别为 SSQ 和 DLT 使用受控候选完成 `shadow -> promote proposal -> independent review -> Champion -> rollback` 正向 E2E；一个彩种的状态变化不得改变另一个彩种的 Champion、预测或科学状态。
- 历史单独优势、单期命中、Top-K 偶然命中、越权批准、守护退化和证据不完整均不能晋升。
- 真实 challenger 只有在该彩种的预注册前瞻联合 log-score、稳定性、校准、排名非退化和在线错误预算门全部通过后才可晋升；不得要求或使用另一个彩种的结果补足证据。
- 故障恢复、规则变更、数据修订、篡改、泄漏、选择性报告和回退 E2E 全部达到预期终态。
- 双彩种预测、Top-1000、评分、AutoResearch 和治理可定位、可重算、可恢复，blocking findings 为 0。

满足以上条件即得到 `SYSTEM_GO`。若没有真实 challenger 合格，M0 继续作为 Champion，模型状态保持 `baseline_only`；此时只能声明“持续改进系统已交付”，不能声明“预测效果已经提升”。

## 10. 1.0 之后的持续改进

系统进入持续运营后，每个候选 release 都必须使用新的不可变身份、结果前预注册、功效分析、前瞻窗口和独立复核。后续循环不新增一个以“必须发现规律”为退出条件的有限阶段，而是持续更新两类科学状态：

- challenger 首次通过某彩种相对冻结比较 Champion 的真实前瞻门后，只把对应 `(game,model,comparator,release,window)` 状态变为 `prospective_improvement_confirmed`；
- 只有某个 `(game,K,model,comparator,release,window)` 的实际 Top-K 累计结果达到预注册样本量、效应、置信门和多重比较门后，才把该单元变为 `confirmed_lift`。

未达到门槛时继续报告 `baseline_only|shadow_candidate` 和 `insufficient_observation|no_confirmed_lift`。不得降低门槛、扩大未注册搜索或删除失败来制造“逐步提升”。

## 11. 最终目标达成矩阵

| 用户目标 | `SYSTEM_GO` 必须证明 | 额外科学结果 |
| --- | --- | --- |
| 支持 SSQ 与 DLT | 两彩种真实预测、评分、恢复和证据闭环通过；分别维护 Champion、shadow 和状态 | 无 |
| 每期排序输出 1,000 注 | 数量、合法性、唯一性、联合概率、tie 和重放全部通过 | 无 |
| 每期开奖后 AutoResearch | 每彩种每期 decision 完整；允许零实验；参数和特征两类正向调整及连续控制器资格通过 | 无 |
| 逐步提升预测质量 | 候选提出、资格、shadow、晋升和回退能力全部通过 | 实际改善只有 `prospective_improvement_confirmed` 才能声明 |
| 提升 Top-10/100/200/1000 召回 | 八个 `(game,K)` 单元的指标、稠密排名目标、全规则合成覆盖改善和长期账本全部通过 | 只有具体 `(game,K,model,comparator,release,window)` 的 `confirmed_lift` 才能声明对应召回改善 |

因此 `SYSTEM_GO` 表示用户要求的系统能力已经实现，不自动表示自然开奖已经提供足够信号。任何对外或内部总结必须同时报告工程状态、两个彩种的 Champion/模型状态，以及八个 `(game,K)` 单元的 Top-K 状态。

## 12. 全局边界与启动规则

- 不使用未来开奖、当期开奖后字段或无 `available_at_utc < prediction_locked_at` 证据的外部时变字段预测当期。
- 不用历史回填、当前网页时间或开奖后生成时间伪造真实前瞻证据。
- 不合并 SSQ 与 DLT 的样本、指标或统计证据来扩大功效。
- 不删除失败实验，不只保留最佳模型，不在看到结果后更改指标、窗口、阈值或候选范围。
- 不实现自动购彩、代购、投注、资金、收益优化、中奖保证或随机性证明。
- 不把机器品牌或固定硬件规格作为科学门；实际 workload 必须通过结果前 benchmark、资源预算和恢复验证。

下一阶段是 Phase 4。启动开发前必须基于本路线图的 `main` 固定 commit 冻结 Phase 4 总体设计、详细计划、机器验收合同、输入/代码身份和 release 目录。Phase 5 只能在 Phase 4 `SYSTEM_MVP_GO` 后启动；Phase 6 只能在 Phase 5 固定窗口和量化 SLO 全部通过后启动。
