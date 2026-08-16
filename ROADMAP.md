# 彩票预测与 AutoResearch 系统路线图

版本：2.4

状态：Phase 4 真实模型 MVP authority；以 D00 `P4_AUTHORITY_COMMIT` 中四份 authority 文档的内容哈希共同生效

更新时间：2026-08-11

已完成阶段基线：`2f6ec9620025b858ddd66f92548c0042709fa601`

## 1. 文档定位与权威顺序

本文档从已完成的 Phase 0–3 出发，定义实现最终彩票预测与 AutoResearch 系统所需的剩余路线。它只有在合入 `main` 并取得固定 commit SHA 后，才能成为远程设计、开发和验收任务的冻结项目级输入；工作树中的未提交版本不是权威身份。

发生冲突时按以下顺序处理：

1. `main` 固定 commit 中的本文件定义项目目标、阶段顺序、全局边界和最终验收语义。
2. 各阶段在本文件边界内另行冻结的阶段定义说明其产品范围和详细验收；总体设计、详细计划和机器验收合同只能解释和实现该定义，不能弱化它。Phase 4 的四份 authority 文档必须处于同一 `P4_AUTHORITY_COMMIT`，其中 `P4-R01` 至 `P4-R17` 与 D00–D15 是唯一详细验收矩阵。
3. Phase 0–3 已验收制品分别证明其历史交付事实，不因本路线图更新而改变。
4. `docs/research/lottery-autoresearch-technical-strategy.md` 只保留研究方法和风险背景；其中“仍不进入产品实现”的旧阶段顺序已被本文件取代。
5. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/` 是历史研究分解，不代表当前进度、产品范围或实施顺序。

任何后续阶段都不得改写 Phase 0–3 的冻结输入、结果、review、acceptance 或递归 manifest。

本文件与阶段定义按职责共同生效：本文件优先定义项目目标、阶段顺序、跨阶段科学语义和全局边界；阶段定义优先定义该阶段的产品形式、功能表面、交付内容和逐项验收。路线图中的分组摘要不能替代或删减阶段定义中的详细验收。两者若出现无法按上述职责消解的冲突，阶段状态必须为 `HOLD`，先在新的固定 commit 中同步修订，不能由总体设计、详细计划、实现或验收人自行选择较宽松版本。

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
| 工程交付状态 | `(system_release_id)` | `HOLD`、`FAIL`、`READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`、`PROSPECTIVE_GO`、`SYSTEM_GO` | 双彩种真实模型预测、评分、AutoResearch、前瞻运行、治理和恢复能力是否按合同交付 |
| 模型科学状态 | `(game, model_id, comparator_id, model_release_id, window_id)` | `lift_supported`、`no_confirmed_lift`、`worse_than_M0`、`insufficient_evidence` | 该彩种的冻结模型相对 M0 在隔离报告窗口中的证据结论 |
| Top-K 结果状态 | `(game, K, model_id, comparator_champion_id, model_release_id, window_id)` | `insufficient_observation`、`no_confirmed_lift`、`confirmed_lift` | 该彩种、该 K、该模型相对冻结比较 Champion 是否有足够真实观测支持召回提升结论 |

规则如下：

- Phase 4 的 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE` 必须同时满足 `serving_model_by_game.ssq != M0` 与 `serving_model_by_game.dlt != M0`。`baseline_only` 只可用于 comparator/diagnostic/fallback 现场，不能是产品 PASS 状态。
- 工程状态只允许按 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE -> PROSPECTIVE_GO -> SYSTEM_GO` 前进；任一阶段的 `HOLD|FAIL` 不能被模型或 Top-K 科学状态覆盖。
- SSQ 和 DLT 分别维护 `champion_by_game`、shadow 队列、模型状态和证据窗口。两个彩种可以使用同一模型代码，但参数、预测、指标、错误预算、晋升和回退决策必须独立。
- 历史或合成优势最多产生 challenger/shadow 资格，不能产生 `prospective_improvement_confirmed`，也不能越权改变 serving release。
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

### 7.1 目标

把 Phase 3 历史研究能力扩展为可部署、可调度、可恢复和可独立验收的双彩种真实模型闭环 MVP。SSQ、DLT 必须分别从 Phase 1 canonical 冻结历史构造 `retrospective_sequence_safe` 特征，训练并冻结非 M0、非均匀 serving release，并由该 release 的联合概率生成、锁定和重放恰好 1,000 注完整合法组合；每个新解锁结果产生评分和唯一 AutoResearch decision，允许的真实参数或特征 diff 必须形成新 child 并改变下一期 shadow 输出，但不得越权晋升 serving。

Phase 4 不要求证明 lift；`no_confirmed_lift|worse_than_M0|insufficient_evidence` 可与工程 PASS 并存。但任一 game 使用 M0、fixture、内联参数、工作树默认模型或全等概率输出时，必须 HOLD/FAIL，不能形成产品 PASS。完整合同由同一 `P4_AUTHORITY_COMMIT` 的其余三份 authority 文档定义。

本地产品验收不得绑定正式 builder 的 VPS 绝对路径。正式 provenance 继续精确冻结 Linux interpreter/平台/依赖/命令；本地只读 verifier 支持 CPython 3.12 patch/platform 迁移，以 manifest closure 验证历史 Phase 2/2.1 receipts 而不重跑其 VPS 环境。只有合同逐路径枚举的重算浮点叶可使用 finite 且 absolute/relative/ULP 三界同时满足的语义比较；ID、hash、issue/cutoff/lineage、Top-1000 membership/order、canonical ticket、score/tie identity 和 create-once 文件始终 exact。

### 7.2 工作方法

Phase 4 必须按以下顺序推进，后续步骤不能替代前置步骤：

1. **D00 authority：** 四份 authority 文档在同一 clean commit 冻结，两个 checker 通过后解除 `HOLD_AUTHORITY_SYNC`。
2. **D01–D10 实现门：** 依次冻结合同，生成真实特征和双彩种模型，隔离 selection/report-only，完成概率排名、账本、CLI、AutoResearch、调度恢复与 workload readiness。
3. **D11–D15 正式门：** 在唯一 release 完成双彩种 E2E、独立 bottom-up replay/mutation、不可变 checklist candidate、覆盖它的 pre-acceptance manifest，最后只追加 machine acceptance、checklist release receipt 和 final closure。
4. **独立复核：** 从底层证据重算 P4-R01–P4-R17；D15 前不请求人工确认、不接受人工豁免，也不把顶层自报 `PASS` 当作事实。

### 7.3 必须完成

- 建立 Phase 4 自有数据层，以 MVP 定义冻结的 Phase 1 `baseline-v1` 四项身份作为唯一 genesis；后继 release 保持连续链，只写 Phase 4 staging/runtime，并递归保护整个 Phase 1 权威树。
- 建立 SSQ/DLT 目标期日历、规则映射、增量官方结果采集、核验、去重和修订传播；修订必须形成 corrected score/aggregate、remediation decision 和候选重新资格，不退款或重复 alpha spending。
- 为 SSQ/DLT 分别实现真实 P4E2-R 多特征模型（或通过同等验收的低容量多特征模型）；正式 serving 必须覆盖历史变化、号码关系和组合结构三类特征，冻结 data/feature/config/code/dependency/model-card 身份。历史 P4E1-R 单特征版本只能保留为不可变回放，M0 仅作 comparator 或带 `NON_PRODUCT_BASELINE` 水印的显式 diagnostic fallback。
- 实现严格为正且归一的完整空间联合概率、概率主排序 Top-1000、局部 tie、锁定截止和不可变 forecast ledger；完整空间单一 tie 或 Top-1000 全等概率必须拒绝。
- 实现 guarded label unlock，并分离开奖前 forecast 诊断、绑定结果版本的逐预测 score 和带最小样本量状态的窗口指标。
- 实现候选模型/特征 registry、参数和特征配置 diff、隔离实验、逐彩种/假设族 alpha wealth、预算、checkpoint、失败终态和 shadow 接入；历史或合成证据不得修改 Champion。
- 实现工程、模型改进和 Top-K 三类独立状态及完整主键；不得跨 game、K、comparator、release 或 window 外推。
- 提供确定性目标期调度、截止保护、并发/迟到/漏跑/补偿终态、幂等恢复、告警、离线 replay、CLI、配置、Schema、依赖锁、测试和版本化 release。
- 在正式运行前冻结 selection folds、只报告一次的 report-only folds、数值算法、容差、工作负载、资源预算、命令、输出路径和 acceptance 合同；不得因结果不利而重选。

### 7.4 边界与约束

- Phase 0–3 的冻结输入、正式结果、review、acceptance 和 manifest 全部只读；Phase 4 的 Schema 兼容不授权写入 `artifacts/phase-1/`。
- Phase 1 历史序列只适用 `retrospective_sequence_safe`；外部时变预测特征必须有真实 `available_at_utc < prediction_locked_at`；官方结果标签必须在 forecast lock 后核验并受控解锁。三类时间证据不得互换或补造。
- SSQ 与 DLT 的 serving release、参数、forecast、实验预算、指标和科学状态相互隔离。M0 永久保留为 comparator/diagnostic fallback，但不能驱动 Phase 4 正式 lock 或产品 PASS。
- 每个正式对象使用唯一身份和 append-only 终态；修订、失败、超时、跳过、零实验、`no_change`、恢复和不利结果全部保留。
- 概率排序和 tie 必须确定、可传递、可独立重放；完整空间 rank 无法在批准预算内正确计算的候选不得接入或必须 `HOLD`，不能降级伪造。
- 公网只用于准备依赖和只读官方接口 canary；隔离安装和合成正式资格只消费冻结依赖及输入。环境只记录事实并以批准 workload benchmark 判定，不设任意通用 VPS 硬件门槛。
- Phase 4 不等待真实开奖周期，不验证连续运行 SLO，不产生真实 Champion 晋升或真实改善结论；这些分别属于 Phase 5 和 Phase 6。
- 不建设 WebUI、移动端、公共 API、自动购彩、代购、投注、支付、资金、收益或中奖保证系统。

### 7.5 交付物

Phase 4 必须交付完整集合，缺少任一类均不能得到 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`：

1. **定义与合同：** MVP 定义、总体设计、详细计划、三类时间合同、预注册、机器验收合同、故障模型和 SLO 合同。
2. **实现：** 自有数据追加层、日历、调度、预测/锁定、评分、AutoResearch、修订传播、三类状态矩阵、恢复、告警和 replay 代码。
3. **机器接口：** CLI、配置、依赖锁，以及 data-release、calendar、schedule、forecast、ranking、metric、experiment、decision、champion-by-game、model-status、top-k-status、alpha-wealth、manifest、review 和 acceptance Schema。
4. **验证资产：** 单元/属性/Schema 测试、真实双彩种训练/forecast E2E、独立概率/排序/指标 oracle、bottom-up replay/mutation、修订传播、故障恢复和泄漏负控。
5. **运行材料：** wheelhouse 及 manifest、离线重建 receipt、benchmark、VPS 部署/运行/故障/恢复/证据取回/验收手册和 readiness 证据。
6. **正式证据：** 单一 `artifacts/phase-4/<release-id>/` 下的 D00–D15 receipts、双彩种 E2E、独立 replay、不可变 checklist candidate、pre-acceptance manifest 和只追加的最终 acceptance 闭包。

### 7.6 验收标准与方法

`tasks/phase4/README.md` 的 `P4-R01` 至 `P4-R17` 和详细计划 D00–D15 是唯一逐项验收标准；下表仅作路线图摘要：

| 路线图验收组 | 详细门 | 必须证明 |
| --- | --- | --- |
| 双彩种真实模型产品 | P4-R02–P4-R11 | 真实特征/模型、隔离回测、非 M0 serving、各 1,000 注与冻结 CLI lineage |
| AutoResearch、因果与恢复 | P4-R12–P4-R13 | append-only 时间状态、修订恢复、child/shadow 真实变化且不改 serving |
| 重放与交付闭包 | P4-R14–P4-R17 | 独立 bottom-up replay、manifest、不变率、科学措辞和机器后本地交接 |

以下项目级硬门不得放宽：双彩种分别使用真实 Phase 1 历史训练的非 M0 serving；selection/report-only 隔离；各 1,000 注合法唯一、严格正、至少两个规范概率层且概率主排序；正式 CLI 实际加载冻结 release；shadow 真实变化但不晋升 serving；独立 replay/mutation、protected roots、manifest 覆盖率和 pre-acceptance 不变率均为 100%。

单元、Schema、真实双彩种正负 E2E、故障恢复、泄漏和越权负控、隔离安装、独立 replay、最终 validator 与独立 checker 必须在同一正式 release 上共同通过。只证明命令被触发、代码测试通过、存在 Top-1000 文件、M0 known-answer 或顶层文件自报 `PASS` 均不合格；开发过程不存在人工门。

只有详细计划 D00–D15 和 P4-R01–P4-R17 全部通过、blocking findings 为 0、两彩种正式 serving 均为真实非 M0 非均匀冻结模型时，工程状态才能为 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`。科学状态可以如实为 `no_confirmed_lift|worse_than_M0|insufficient_evidence`；这不影响真实模型功能验收，但禁止宣称效果已经改善。

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

下一阶段是 Phase 4。启动 D01 前必须在同一个 clean `P4_AUTHORITY_COMMIT` 中冻结四份 authority 文档并通过两个 D00 checker。Phase 5 只能在 Phase 4 D00–D15 与 P4-R01–P4-R17 全部通过并取得 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE` 后启动；Phase 6 只能在 Phase 5 固定窗口和量化 SLO 全部通过后启动。
