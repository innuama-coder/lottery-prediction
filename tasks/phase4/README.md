# Phase 4：预测与 AutoResearch 闭环 MVP 定义

版本：1.5

状态：阶段产品定义

更新时间：2026-08-13

## 1. 文档定位

本文档定义 Phase 4 MVP 的产品含义、交付形式、必备特性、边界和验收标准。它回答“Phase 4 最少要交付一个什么样的可用系统，以及如何证明该系统合格”。

本文档不代替 Phase 4 总体设计、详细计划、预注册或机器验收合同，不拆分实施任务，也不表示 Phase 4 已经开发或验收。后续文档必须在本文边界内明确组件、接口、状态机、数值算法、命令、Schema、输出路径、资源预算和执行顺序。

本文档与同一固定 `main` commit 中的 `ROADMAP.md` 按职责共同构成 Phase 4 上位合同：ROADMAP 定义项目目标、阶段顺序、跨阶段科学语义和全局边界；本文档定义 Phase 4 产品形式、功能表面、交付物及唯一详细验收矩阵 `P4-MVP-A01` 至 `P4-MVP-A21`。总体设计、详细计划、实现和机器验收不得弱化任一文件；两者若出现无法按职责消解的冲突，必须 `HOLD` 并先同步修订，不能选择较宽松版本。

Phase 4 继承的正式研究基线为 Phase 3 release `P3-R07-2c0fa97-20260810-I01`，其验收结论为 `PASS / GO / no_shadow_candidate`，M0 继续作为两个彩种的默认 Champion。Phase 4 不重新开发或改写 Phase 0–3。

## 2. MVP 权威定义

Phase 4 MVP 是：

> 一个可部署在 VPS、以 CLI 和可调度作业为操作入口、以机器可读的版本化制品和不可变账本为数据接口的无界面双彩种预测系统。它能够在开奖结果未知时，分别为 SSQ 和 DLT 的下一目标期生成、锁定并发布恰好 1,000 注完整合法组合；在每个开奖结果经过核验和受控解锁后完成评分并生成唯一 AutoResearch 决策；并能够让合格候选的参数或特征配置影响下一期 shadow 预测，但不能越权晋升 Champion。

MVP 的三个组成词具有以下含义：

- **Minimum：** 交付闭环所需的最小产品面，不建设 WebUI、移动端、公共在线 API、购彩、代购、支付、资金或收益系统。
- **Viable：** 不是单个模型、Notebook、离线回测或只能演示成功路径的脚本；SSQ 和 DLT 都必须完成可恢复、可重放、可审计的端到端闭环。
- **Product：** 具有稳定机器接口、版本身份、状态和失败语义、部署及恢复方法、验收合同和可独立取回的 release，而不只是算法源代码。

Phase 4 的价值是交付“能够安全持续预测和开展受控研究的系统能力”。它不以发现真实彩票规律、产生真实 challenger 或证明预测效果提升作为阶段成功条件。

## 3. 产品形式

### 3.1 无界面可部署应用

MVP 以可在 VPS 上安装和运行的无界面应用交付。操作面由 CLI 和可调度作业组成，至少覆盖以下能力类别：

1. 准备彩种、规则和目标期。
2. 生成 Champion 及合格 shadow 的预测。
3. 在截止时间前原子锁定并发布预测。
4. 增量采集、核验和修订官方开奖结果。
5. 受控解锁标签并计算评分和排名指标。
6. 执行 AutoResearch 决策和已注册实验。
7. 查询逐彩种 Champion、候选、实验预算和科学状态。
8. 从 checkpoint 幂等恢复失败的运行。
9. 离线 replay、验证 release 和生成最终验收结论。

具体命令名、参数和退出码由 Phase 4 总体设计和机器验收合同冻结。Phase 4 不要求常驻服务、数据库服务或公共 HTTP API；后续实现可以增加内部适配层，但不能使 CLI、离线 replay 和制品取回依赖未交付的外部服务。

### 3.2 预测发布形式

“发布 Top-1000”在 Phase 4 中指：在开奖前生成一个已经原子锁定、具有固定身份、可由 CLI 查询和独立取回的机器可读 forecast bundle。每个 bundle 至少绑定：

- `game`、目标期、规则版本和锁定截止；
- Champion 或 shadow 身份、父模型、模型 release 和配置身份；
- 训练截止、输入数据、代码、依赖和随机种子身份；
- 生成时间、锁定时间和唯一预测身份；
- 恰好 1,000 注完整合法组合及其联合概率；
- 确定性展示位置、全空间 tie group、tie-aware rank 区间和 Top-K 前缀；
- 内容哈希、锁定证据和账本引用。

每个已发布组合必须分别表达 `display_position`、`probability_order_key`、`tie_key`、`tie_group_id`、`tie_probability`、`tie_rank_lower`、`tie_rank_upper`、`tie_midrank` 和 `tie_group_size`；rank 和 group size 的范围是该彩种完整合法组合空间，不限于已发布的 1,000 注。排序必须按完整组合联合概率从高到低进行。确定性 tie-break 只用于稳定序列化，不能改变概率、拆散真实 tie group 或伪装为置信度差异。发布不能依赖可变 `latest` 指针决定正式预测身份。

### 3.3 运行账本和控制状态

MVP 使用 append-only 或等价不可变语义保存：

- 目标期、官方结果、修订链和核验状态；
- forecast、lock、unlock 和 score；
- model、feature、experiment 和 decision；
- 逐彩种 `champion_by_game`；
- 逐彩种及假设族 alpha wealth；
- 失败、超时、跳过、零实验、`no_change` 和恢复记录。

工程、模型改进和 Top-K 是三类独立状态，不得使用一个全局科学状态代替：

| 状态类型 | 唯一记录键 | 允许值 |
| --- | --- | --- |
| 工程交付状态 | `(system_release_id)` | `HOLD`、`FAIL`、`READY_FOR_HUMAN_ACCEPTANCE` |
| 模型改进状态 | `(game,model_id,comparator_champion_id,model_release_id,window_id)` | `baseline_only`、`shadow_candidate`、`prospective_improvement_confirmed` |
| Top-K 结果状态 | `(game,K,model_id,comparator_champion_id,model_release_id,window_id)` | `insufficient_observation`、`no_confirmed_lift`、`confirmed_lift` |

上述允许值是 Phase 4 机器交付状态。Phase 4 任务只能写 `HOLD|FAIL|READY_FOR_HUMAN_ACCEPTANCE`；`SYSTEM_MVP_GO` 不是 Phase 4 任务状态，而是阶段外最终验收在收到机器交付包后才可写入的后续项目状态。历史/合成证据最多把对应逐彩种模型记录变为 `shadow_candidate`，不能写 `prospective_improvement_confirmed`；没有真实冻结观察窗口时，全部真实 Top-K 状态必须保持 `insufficient_observation`，不能写 `no_confirmed_lift|confirmed_lift`。

实验的 `rejected|archived`、执行的失败终态和模型改进状态属于不同对象，不能互相代填。项目级摘要只能列出完整逐彩种、逐模型、逐 K 矩阵，不得生成丢失 comparator、release 或 window 的全局 `improved=true`。

已有事实不得原地改写。官方修订、配置替换和状态变化必须追加新记录并引用被替代身份。

### 3.4 Phase 4 自有增量数据层

“Phase 1 兼容追加发布”只表示 DrawRecord、规则、核验、修订链和发布语义兼容，不表示继续写入 Phase 1 的正式目录。Phase 4 必须在自己的 runtime 或 staging namespace 中创建新的不可变数据 release。Phase 4 数据链的冻结创世父身份是：

| 字段 | 固定值 |
| --- | --- |
| `base_phase1_release_id` | `baseline-v1` |
| `base_phase1_manifest_sha256` | `0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1` |
| `base_phase1_records_sha256` | `f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1` |
| `base_phase1_observations_sha256` | `dc974863c845da1e895ecf623bc6e878ba6aa6710c902357bce68ad5e661966e` |

首个 Phase 4 data release 必须同时声明上述四项身份、`previous_phase4_release_id=null`，并在 Phase 4 namespace 中保存或离线可解析地绑定与上述哈希一致的基线内容；不能以可变 `current-release.json` 指针代替固定身份。后续 data release 必须保持相同创世父身份，并通过 `previous_phase4_release_id` 严格引用直接前驱。断链、换基线、拼接另一条链、空基线或仅有 Schema 兼容但内容无关的 release 均必须 fail closed。每个预测必须绑定当时实际读取的 Phase 4 data release 身份。

固定代码身份中的整个 `artifacts/phase-1/` 权威树只读，包括 baseline、current pointer、releases、runs、reviews、live-validation、acceptance 及历史快照。正式运行、canary、测试和恢复都不得在该树创建、修改或删除文件。真实接口 canary 对远端来源只读，其本地输出只能写入隔离的 Phase 4 staging namespace；正式 Phase 4 数据 release 只能写入冻结的 Phase 4 自有路径。具体 runtime/staging 路径必须在总体设计中冻结。

Phase 4 readiness 和最终 validator 必须在操作前后递归核对 Phase 1 受保护清单的路径、大小和 SHA-256；任何变化都 fail closed。Schema 兼容性通过隔离 staging 中的 Phase 1 validator/独立兼容性检查证明，不通过修改 Phase 1 文件证明。

### 3.5 版本化交付包

正式 MVP 以唯一 release 交付：

```text
artifacts/phase-4/<release-id>/
```

release 必须包含冻结合同、运行证据、正负 E2E、合成资格、full-rule known-answer、独立 replay、review、acceptance 和递归 evidence manifest。源代码存在但没有可独立验证的正式 release，不构成 Phase 4 MVP 交付。

## 4. 必备产品特性

### 4.1 双彩种独立闭环

SSQ 和 DLT 都必须完成：

```text
prepare -> predict -> lock -> ingest -> unlock -> score
        -> autoresearch -> decision -> next forecast
```

两个彩种可以复用代码，但不得合并 Champion、模型参数、预测、实验预算、alpha wealth、指标或科学证据。一个彩种的状态变化不得改变另一个彩种的输出和状态。

### 4.2 完整组合 Top-1000

- 每个有效预测必须恰好包含 1,000 注合法且互不重复的完整组合。
- SSQ 的一个预测对象是完整的 `6+1` 组合；DLT 是完整的 `5+2` 组合。
- Top-10、Top-100、Top-200 必须是同一 Top-1000 的严格前缀。
- 完整合法组合空间上的模型联合概率必须严格大于 0 并归一化为 1，确保每个合法开奖结果都有有限、可序列化的联合 log score。
- 固定输入、代码、配置和种子必须生成相同预测身份和相同规范化内容。

### 4.3 正确的概率和并列语义

联合概率是模型估计，不是中奖率保证、收益结论或对彩票随机性的判断。M0 的全空间同概率必须如实表达为一个覆盖完整合法组合空间的 tie group；M0 的 Top-1000 只是在该并列组内按冻结规则选出的确定性展示子集，不能把展示位置 1–1,000 解释为概率排名 1–1,000。

当任意 tie group 跨越 Top-K 截止时，`hit@K` 仍按开奖前锁定的确定性前 K 注计算，而稠密排名指标必须使用完整空间的 tie rank 区间和 midrank。两类指标必须同时保存，不能相互替代，也不能把同概率但未被展示的组合描述为概率更低。

每个允许进入 Phase 4 registry 的模型必须冻结一种确定性、可传递、跨独立 replay 一致的规范概率顺序表示 `probability_order_key`。该键必须编码模型合同规定的完整冻结精度，不能为了制造 tie 而合并不同的规范模型概率。两个组合只有在规范顺序键完全相同时才属于同一 tie；`tie_key` 必须由规范顺序键确定性派生，`tie_group_id` 必须再绑定 forecast 身份，并在使用摘要时进行碰撞核对。禁止使用成对 `isclose`、遍历顺序或运行时浮点格式临时聚类，因为近似相等不具备传递性。

数值概率用于评分和校准，规范顺序键用于排序及 tie 等价类；两者的顺序关系必须在冻结容差内一致，否则预测非法。模型 registry 还必须声明完整空间 tie group size 和 rank bounds 的精确计算方法及 benchmark。M0 可以解析计算；其他候选若无法在批准预算内确定性计算，则保持未接入，或令工程状态为 `HOLD` 并记录 `reason_code=HOLD_UNSUPPORTED_TIE_SEMANTICS`，不得用错误 rank 降级通过。

### 4.4 结果前锁定和受控标签解锁

时间合同分为三类，三类证据不得混用：

1. **Phase 1 历史开奖号特征：** 继承 Phase 3 的 `retrospective_sequence_safe`。只允许来源期严格早于目标期，所有拟合和变换只读取目标期以前的训练前缀；不要求为这些历史记录补造 `available_at_utc` 或原始网页归档。
2. **外部时变预测特征：** 必须具有可核验的真实 `available_at_utc < prediction_locked_at`。当前页面、抓取时间倒填、开奖日期推断或没有可用时间证据的字段一律不能进入模型。
3. **官方结果标签：** 必须先存在不可变 forecast lock receipt，之后才能核验结果并授予 label capability；要求 `prediction_locked_at < result_verified_at <= label_unlocked_at`。历史或当前结果接口 canary 只证明连接、解析、规则和修订处理 readiness，不证明历史前瞻时序，也不要求为旧开奖结果建立开奖前可用证据。

合成/固定 E2E 使用受控事件时钟和能力隔离证明上述因果顺序。Phase 4 不使用历史回填生成真实前瞻证据；真实官方开奖中的连续时序和运行 SLO 由 Phase 5 验证。标签提前读取、未来特征、错误时间类别、锁后改写和结果后修改指标规则必须 fail closed。

### 4.5 开奖后评分和状态更新

开奖前 forecast 诊断必须绑定 `(forecast_id,metric_contract_id)`，在锁定时计算、保存且随 forecast 一起不可变：

- Top-K 联合概率覆盖及嵌套一致性；
- 完整空间归一化、排序和 tie 守护结果。

开奖后逐预测 score 必须绑定 `(forecast_id,result_revision_id,metric_contract_id)`；其中相对 skill 还必须绑定同一目标期的 `comparator_forecast_id`。逐预测 score 包含：

- `hit@10`、`hit@100`、`hit@200` 和 `hit@1000`；
- 联合 log score 及相对当前 Champion 的 skill；
- inclusion Brier；
- 实际完整组合的 tie-aware rank 区间和 midrank percentile。

校准、reliability、稳定性、累计 hit rate 和累计覆盖属于窗口聚合指标，必须绑定 `(game,model_id,comparator_champion_id,model_release_id,window_id,metric_contract_id)`，保存 `observation_count` 和组成该窗口的逐预测指标身份。冻结最小样本量未满足时只能写专用的 `insufficient_observation` 聚合状态，不能伪造数值或借用 Top-K 科学状态代填。逐预测公式、窗口边界、分箱边界、最小样本量、零概率拒绝、数值容差和无效输入终态必须在结果前冻结。

官方结果修订必须传播到全部受影响的派生对象，而不能只追加一个 DrawRecord：

1. 新结果版本引用 `supersedes_revision_id`；旧结果、旧 score、旧 aggregate、旧 decision、已执行实验和已锁定 forecast 永久保留，不得改写或删除。
2. 系统追加绑定新 `result_revision_id` 的 corrected score、窗口聚合和当前视图替换记录，并列出全部被替代派生身份；与结果无关且已经锁定的 forecast 诊断不重算。当前查询和后续训练只能解析到最新完成核验的修订链头。
3. 已发生的 alpha spending 不退款、不重置，也不能把修订当成新的独立检验机会。系统必须追加 `trigger=official_result_revision` 的 remediation decision，引用原 decision、新旧结果和受影响实验，按冻结修订策略归档、重放或重新资格相关候选。
4. 依赖旧标签但尚未影响已锁定 forecast 的候选、聚合或未提交动作，在修复完成前不得进入新的 shadow forecast。已经锁定的 forecast 保持原身份和原数据血缘；修复后的未锁定 forecast 必须使用包含新修订的 data release。
5. 修订传播和恢复必须以 `(game,issue_id,new_result_revision_id,correction_policy_version)` 幂等；部分传播、重复重评分、重复 spending、旧版本重新成为当前结果或无法列清影响范围时 fail closed。

真实 Top-K 观察不足时必须保持 `insufficient_observation`。联合 log score、合成覆盖改善或参数发生变化不能替代真实 `confirmed_lift` 证据。修订重算不得把同一期的多个结果版本计为多个独立观察。

### 4.6 真实可调整的 AutoResearch

每个新解锁开奖结果都必须触发一个具有唯一身份和终态的 AutoResearch decision。MVP 必须通过受控正向案例证明系统能够：

1. 修改允许的候选参数并形成可验证 diff。
2. 启用、禁用或调整允许的候选特征配置并形成可验证 diff。
3. 生成新的候选及配置身份。
4. 使新配置改变下一期 shadow 的概率或 Top-1000。

真实数据没有合格假设时允许 `experiment_count=0` 和 `no_change`，但必须记录机器可判定理由。永远返回 `no_change`、只改变配置文件却不影响下一期 shadow，或创建空实验冒充研究，均不合格。

### 4.7 受控候选生命周期

候选生命周期必须为：

```text
proposal -> registered experiment -> historical/synthetic qualification
         -> rejected|archived|shadow_candidate
         -> prospective shadow
```

Phase 4 的历史或合成证据最多产生 `shadow_candidate`，不得直接修改 Champion。研究执行者不能修改数据采集、规则解释、标签隔离、评分器、验收器、既有预测或既有评分。M0 永久保留为逐彩种可回退基线。

### 4.8 在线错误预算

每个彩种和假设族分别维护结果前冻结的 alpha wealth。alpha spending、奖励、每周期最大实验数、预算耗尽和停止规则必须可从底层实验记录重算；alpha wealth 不得为负。预算耗尽后只能形成明确 `no_change` 或等待下一预注册窗口，不能继续未注册搜索。

### 4.9 幂等、恢复和可观测性

- 每个预测、结果、评分、实验和决策必须有唯一身份和唯一终态。
- 重试和 checkpoint 恢复不得重复预测、解锁、评分、实验或 alpha spending。
- 失败、超时、跳过和不利结果必须保留，不能覆盖成成功。
- 数据源冲突、修订、网络失败、运行失败和预算耗尽必须产生结构化状态和可操作告警。
- 实际执行环境和资源消耗必须记录，但不得以任意通用 CPU、内存、磁盘或架构值代替批准工作负载是否完成。

### 4.10 目标期调度

调度是 MVP 行为，不只是 CLI 可被外部调用。目标期日历和调度 release 必须确定性产生逐 `(game,target_issue,action,planned_at_utc,schedule_release_id)` 的计划记录，保存 `Asia/Shanghai` 业务时间及对应 UTC，并为每个计划动作追加唯一终态。

- 同一计划的重复触发必须返回同一运行身份或明确幂等终态，不能重复 forecast、unlock、score、experiment 或 alpha spending。
- 漏触发、迟到、进程重启、并发触发和超过锁定截止必须保留结构化终态和告警；超过截止不得补造有效预测。
- 日历/规则映射不唯一、目标期倒退或与官方状态冲突时 fail closed，不能自行猜测新期号。
- SSQ 与 DLT 的计划、锁定和失败隔离；一个彩种失败不能取消、延迟或改变另一个彩种的身份。

Phase 4 使用受控虚拟时钟完整验证触发行为，不需要等待真实开奖。VPS readiness 还必须安装或配置总体设计选定的实际调度适配器，并独立核对动作、参数、工作目录、时区、触发定义、并发策略、补偿策略和下一次计划；该记录只是安装时点快照，不声称已经连续可靠运行。真实准时率和持续 SLO 由 Phase 5 验证。

### 4.11 独立重放和证据闭包

独立复核路径必须从冻结底层输入重新计算预测身份、Top-K、概率、排名指标、逐彩种 Champion、科学状态矩阵、alpha wealth、研究决策和证据闭包。独立路径不得只信任顶层 `PASS`、汇总报告或被复核实现输出的派生结论。

## 5. 产品边界和非目标

Phase 4 MVP 包含：

- SSQ/DLT 规则、目标期日历和官方结果增量采集 readiness；
- Champion/shadow 预测、锁定、发布、评分和 AutoResearch；
- CLI、调度入口、离线 replay、幂等恢复和告警；
- Schema、依赖锁、测试、运行手册和版本化 release。

Phase 4 MVP 不包含：

- Phase 5 的每彩种 20 个真实连续已开奖目标期运行；
- 真实 challenger 晋升及 Phase 6 的双人批准和生产治理闭环；
- WebUI、移动端、公共在线 API 或面向消费者的账户系统；
- 自动购彩、代购、投注、支付、资金管理或收益测算；
- 必须发现真实 shadow candidate、替换 M0 或证明彩票存在可利用规律；
- 承诺准确率单调上升、Top-K 真实召回已经提高、中奖或收益；
- 使用历史回填冒充开奖前冻结的真实前瞻证据；
- 改写 Phase 0–3 的冻结输入、结果、review、acceptance 或 manifest。

真实官方接口在 Phase 4 只通过远端只读 canary 验收连接、目标期/结果解析、规则映射、修订、去重、Phase 1 Schema 兼容和失败语义。canary 的本地输出只进入隔离的 Phase 4 staging，不写 Phase 1；它可以读取已经公开的期次，不要求等待一个新开奖周期，不产生前瞻表现或历史 PIT 结论。合成和固定 fixture 用于完整闭环及调整能力资格，不得写成真实彩票预测改善。真实前瞻运行属于 Phase 5。

## 6. MVP 交付物

Phase 4 必须交付以下完整集合：

1. **定义与合同：** 本产品定义、总体设计、详细计划、三类时间合同、预注册、机器验收合同、故障模型和 SLO 合同。
2. **实现：** Phase 4 自有数据追加层、日历、预测/锁定、评分、AutoResearch、结果修订传播、三类状态矩阵、调度、恢复、告警和 replay 代码。
3. **机器接口：** CLI、配置、依赖锁，以及 data-release、calendar、schedule、forecast、ranking、metric、experiment、decision、champion-by-game、model-status、top-k-status、alpha-wealth、manifest、review、machine-delivery 和 acceptance Schema；model/top-k 可以由同一个 `scientific-status` Schema 承载，但记录类型、主键和允许值必须分离。
4. **验证资产：** 单元测试、Schema 测试、概率/排序和逐预测/窗口指标的独立 known-answer oracle、正负 E2E、修订传播 E2E、故障恢复、泄漏负控、合成资格 fixture 和结果前 qualification-design/power 制品。
5. **运行材料：** 依赖 wheelhouse 及 manifest、隔离环境重建 receipt、benchmark，以及 VPS 部署、运行、故障处理、恢复、证据取回和验收手册。
6. **正式证据：** readiness、独立 replay、独立机器 review、最终机器 validator、机器交付声明、递归 evidence manifest 和 `artifacts/phase-4/<release-id>/`。

缺少其中任一类均不能得到 `READY_FOR_HUMAN_ACCEPTANCE`。

## 7. MVP 验收标准

### 7.1 验收矩阵

| ID | 验收对象 | 合格标准 | 验收方法和主要证据 |
| --- | --- | --- | --- |
| P4-MVP-A01 | 双彩种闭环 | SSQ、DLT 分别完成从 prepare 到 next forecast 的全流程；lock、核验、unlock 和下一期训练截止满足冻结因果顺序 | 每彩种正向 E2E；独立核对事件时钟、capability receipt、状态、身份和账本 |
| P4-MVP-A02 | Top-1000 | 每个有效预测恰好 1,000 注合法、唯一完整组合；Top-K 严格嵌套 | Schema、组合规则 known-answer、重复检查和固定输入 replay |
| P4-MVP-A03 | 概率与排序 | 完整空间概率严格为正且归一；规范顺序键确定性且与数值概率一致；展示位置与完整空间 tie rank 明确分离 | 概率 known-answer、零/负/NaN/无穷输入负控、规范键独立重算、非传递近似负控、输入排列扰动、跨 Top-K tie 和序列化检查 |
| P4-MVP-A04 | M0 并列 | M0 全空间同概率被表示为一个完整空间 tie group；1,000 注的确定性选择不改变概率、全局 rank 区间或 midrank | 两彩种 M0 fixed fixture；独立重算 full-space group size、rank bounds 和指标 |
| P4-MVP-A05 | 参数调整 | 正向 fixture 产生参数 diff、新候选身份及变化后的下一期 shadow 概率或 Top-1000 | 参数正向 E2E；比较父子配置和 forecast |
| P4-MVP-A06 | 特征调整 | 正向 fixture 产生允许特征的 enable/disable 或配置 diff，并被下一期 shadow 实际使用 | 特征正向 E2E；核对特征快照、配置和 forecast 血缘 |
| P4-MVP-A07 | 均匀序列负控 | 每彩种 1,000 个序列、每序列固定 150 周期；序列内任一错误 shadow proposal 的发生率不高于 5% | 小空间 `N=10,k=3` 顺序资格；从逐序列终态重算比率 |
| P4-MVP-A08 | 错误预算与越权 | alpha wealth/停止规则重算一致率 100%；alpha 不为负；历史或合成 Champion 晋升次数为 0 | 独立重算实验账本；预算耗尽和直接晋升负控 |
| P4-MVP-A09 | 合成恢复能力 | 每彩种的静态偏差、缓慢漂移和有用特征序列级恢复率分别不低于 90%，且正式资格前已有独立功效确认种子证明门槛可完成 | 同一小空间正向资格；逐序列重算正确方向或配置；核对 qualification-design/power |
| P4-MVP-A10 | 完整规则能力 | SSQ/DLT 的 Top-10/100/200/1000 八个单元中，候选对结果前冻结、独立于候选实现的已知非均匀生成分布的真实覆盖均严格优于 M0 | 当前完整规则空间 independent-oracle known-answer；报告并重算全部八个预期值 |
| P4-MVP-A11 | 时间、标签与篡改防护 | 三类时间合同分别正确；提前读标签、未来特征、无 PIT 外部字段、锁后改写和结果后改指标均 fail closed | 历史 sequence-safe 正控、外部 PIT 正负控、label capability、时间旅行、锁后 mutation 和评分器负向 E2E |
| P4-MVP-A12 | 彩种和治理隔离 | 跨彩种证据合并、跨彩种状态污染和直接改 Champion 均 fail closed | 隔离及越权负向 E2E；重算两个彩种状态 |
| P4-MVP-A13 | 幂等恢复 | 每项唯一终态；恢复不重复预测、解锁、评分、实验或 alpha spending | 各阶段故障注入、checkpoint 恢复和账本计数检查 |
| P4-MVP-A14 | 官方接口与自有追加层 readiness | 两彩种目标期、增量结果、核验、修订、去重和 Phase 1 Schema 兼容均可执行；Phase 4 genesis 与固定 Phase 1 基线内容一致且后继链连续；只写 Phase 4 staging/runtime；失败有明确终态 | 固定响应测试、远端只读 canary、genesis 四项身份及离线内容重算、断链/换基线/空基线负控、隔离追加/修订/冲突/网络失败 E2E、Phase 1 前后递归哈希 |
| P4-MVP-A15 | 独立重放 | 底层重算预测身份、Top-K、指标、Champion、三类状态矩阵、alpha wealth、决策和证据闭包一致 | 独立实现路径 replay 和递归 manifest 验证 |
| P4-MVP-A16 | 最终交付 | 交付物覆盖率 100%，blocking findings 为 0，不含越界科学声明 | 最终机器 validator、独立机器 review、机器交付声明和 acceptance Schema |
| P4-MVP-A17 | 安装与运行 readiness | 从冻结依赖在全新隔离环境完成安装、CLI smoke、固定 fixture、checkpoint 恢复和 release replay；无未锁定依赖或隐式外部服务 | wheelhouse manifest、离线重建 receipt、命令/退出码记录、环境事实、benchmark 和 evidence-return canary |
| P4-MVP-A18 | 状态矩阵 | 工程、模型改进和 Top-K 状态使用各自完整主键；Phase 4 只写本阶段允许值；不存在跨 game/K/comparator/release/window 外推或全局 improved | Schema 正负测试、本阶段允许/未来阶段禁用转换 E2E、维度删除/混用/跨彩种污染负控和逐记录独立重算 |
| P4-MVP-A19 | 调度 | 双彩种计划确定、触发幂等、截止保护、漏跑/迟到/重启/并发终态和隔离均正确；VPS 调度定义与冻结计划一致 | 虚拟时钟正负 E2E、安装/配置审计、重复及补偿触发测试、计划账本与告警重算 |
| P4-MVP-A20 | Forecast 诊断、评分与窗口指标 | 开奖前 forecast 诊断、开奖后逐预测 score 和窗口指标的作用域、主键、公式、比较对象、样本量及不足状态正确；全部数值在冻结容差内匹配独立 oracle | SSQ/DLT 独立数值 known-answer、Phase 3 指标回归、诊断错误绑定结果/首期/小样本/分箱边界/跨 tie/零概率/错误 comparator/修订后窗口负控 |
| P4-MVP-A21 | 结果修订传播 | 新结果版本完整传播到 score、aggregate、remediation decision、候选资格和后续未锁定 forecast；历史及已锁定 forecast 不改写；不退款、不重复 spending、不重复计入观察 | 合成修订正负 E2E、传播中断和 checkpoint 恢复、重复触发、旧链头/漏列影响/部分重算负控、独立 current-view 和 alpha 重算 |

### 7.2 合成资格与模拟功效固定要求

P4-MVP-A07 至 P4-MVP-A09 的序列长度、每期最大实验数、生成分布、效应大小、种子、置信算法、alpha wealth 公式和停止规则必须在正式资格结果生成前冻结。

- 均匀负控最低工作负载为每彩种 1,000 个序列、每序列 150 周期。
- 静态偏差、缓慢漂移和有用特征三类正控分别对每个彩种运行，且每类不得低于相同的 1,000 序列和 150 周期基线。
- 小空间只验证顺序控制器的错误率和恢复能力，不替代 SSQ/DLT 完整规则 known-answer。
- 准备期使用开发/调参、功效确认、正式资格三套完全不重叠的种子集合。种子派生算法、模拟方法、重复次数，以及允许选择的控制器和效应配置边界必须在功效确认前固定。
- 开发/调参种子可以用于选择边界内的最终控制器和效应配置，但不得作为功效或正式验收证据。设计选定后，使用未参与选择的功效确认种子生成 `qualification-design/power` 制品；不得根据该批结果继续修改同一设计身份。
- qualification-design 必须分别报告：均匀负控达到“错误 proposal 序列率不高于 5%”的预计通过概率，以及六个逐彩种正控单元达到“恢复率不低于 90%”的预计通过概率和不确定性。每个正式门的预计通过概率必须至少为 90%，否则工程状态为 `HOLD` 并记录 `reason_code=HOLD_DESIGN_NOT_POWERED`，不得启动正式资格。后续修改必须使用新设计身份和新的确定性功效确认种子，保留旧失败制品。
- 正式 master seed、派生算法和全部正式序列身份必须在正式资格前冻结，并与开发/调参、功效确认种子集合完全不相交。正式运行后不得更换效应、控制器、阈值、种子或删除失败序列；重试只能使用同一序列身份从 checkpoint 恢复。
- 资格运行前必须 benchmark 批准工作负载并冻结并行、checkpoint 和资源预算；不预设与工作负载无关的通用 VPS 硬件门槛。

模拟功效只证明冻结设计有合理机会通过既定阶段门，不改变正式门的 5% 和 90% 阈值，也不能替代正式 1,000 序列结果。

### 7.3 full-rule known-answer 独立性

P4-MVP-A10 的生成分布和 oracle 必须满足：

1. 两彩种生成分布、参数、规则身份、八个 K 单元、数值精度和期望覆盖值在候选正式运行前冻结并哈希绑定。
2. 生成分布来自预先说明的数学规格，不得由被验收候选的输出、正式结果或 Top-1000 反向构造。
3. oracle 由独立复核路径实现，不得导入或调用被验收的模型归一化、Top-K、排序或覆盖计算核心函数。
4. oracle 输出每个单元的 M0 覆盖、候选真实覆盖、差值和计算误差界；不能只输出布尔 `better=true`。
5. 产品路径与 oracle 超出冻结容差或任一单元不严格优于 M0 时，P4-MVP-A10 失败。不得挑选 K、改分布或换 oracle 重试。

该 known-answer 证明完整规则实现和排名改善能力，不证明生成分布代表真实彩票，也不产生真实 `confirmed_lift`。

### 7.4 安装与运行 readiness

P4-MVP-A17 必须在一次性全新目录或等价干净隔离环境执行。准备阶段允许联网构建并哈希 wheelhouse，也允许 A14 的只读官方接口 canary；隔离安装必须只消费冻结依赖锁和 wheelhouse，不得临时下载未锁定依赖。合成正式资格只消费冻结输入和本地依赖。

readiness 记录实际操作系统、架构、解释器、处理器、内存、磁盘、命令、墙钟、峰值资源和制品大小，并用 benchmark 证明批准工作负载能够完成。不存在通用硬件数值门槛；只有离线重建、CLI smoke、fixture、恢复、replay、证据回传或批准工作负载出现可复现错误时才 `HOLD`。

### 7.5 验收方法组合

最终验收必须同时包含：

1. 单元和属性测试。
2. 所有交付 Schema 的正负验证。
3. qualification-design/power 和开发、功效确认、正式种子不相交验证。
4. 小空间 known-answer 和独立 full-rule oracle。
5. Forecast 诊断、逐预测 score 和窗口指标的独立数值 known-answer、作用域/边界负控及 Phase 3 回归。
6. 双彩种正向和负向 E2E。
7. 官方结果修订全链传播、中断恢复、幂等和旧历史保留 E2E。
8. 故障注入、幂等恢复和告警验证。
9. 三类时间合同、标签隔离、未来信息、篡改和越权负控。
10. 全部冻结合成资格运行。
11. Phase 4 genesis、后继链、自有增量发布、Phase 1 保护哈希和远端只读 canary。
12. 三类状态矩阵的主键、允许值、转换和跨维度负控。
13. 虚拟时钟调度 E2E 和 VPS 调度定义安装审计。
14. 干净隔离环境安装、CLI smoke、恢复及 release replay。
15. 独立 bottom-up replay。
16. 从正式 release 底层证据重算的最终 validator。
17. 独立机器 review、机器交付声明和最终机器验收。

只证明命令被触发、代码测试通过、存在 Top-1000 文件或顶层文件自报 `PASS`，均不足以验收 MVP。

## 8. 验收结论语义

只有 P4-MVP-A01 至 P4-MVP-A21 全部通过、blocking findings 为 0，Phase 4 机器工程交付状态才能是：

```text
READY_FOR_HUMAN_ACCEPTANCE
```

允许同时存在以下科学状态：

```text
model_status[(game,model,comparator,release,window)] = baseline_only
top_k_status[(game,K,model,comparator,release,window)] = insufficient_observation
champion_by_game = {SSQ: M0, DLT: M0}
```

这表示预测与 AutoResearch 闭环产品已经达到机器可交付标准，交付物已封存并可取回，随后才交由人类进行阶段外最终验收；尚无真实证据证明模型或 Top-K 表现改善。不得把参数变化、合成恢复、full-rule known-answer 或 `READY_FOR_HUMAN_ACCEPTANCE` 表述为真实预测效果提高。

如果输入、运行、证据或复核尚可恢复但未完成，结论为 `HOLD`；出现不可恢复泄漏、锁后改写、选择性删除、证据伪造或越权 Champion 变更时，结论为 `FAIL`。模型科学状态不能覆盖工程 `HOLD|FAIL`。

## 9. 正式开发前必须冻结的设计输入

Phase 4 进入正式实现和资格运行前，总体设计、详细计划、预注册和机器验收合同至少必须冻结：

- 官方来源、目标期日历、锁定截止、核验和修订规则；
- 固定 Phase 1 genesis 四项身份、Phase 4 自有 runtime/staging 数据路径、创世/后继追加状态机和 Phase 1 受保护哈希清单；
- 历史序列、外部时变预测特征和官方结果标签三类互斥时间合同；
- CLI 命令、退出码、配置、Schema、账本和 release 目录；
- 工程、模型改进和 Top-K 三类状态的记录类型、完整主键、允许值和转换规则；
- 允许修改的模型、参数族、特征族及其边界；
- candidate identity、diff 和 shadow 接入规则；
- alpha wealth 初值、spending、奖励、假设族和停止规则；
- 每周期实验预算、失败终态和 checkpoint 恢复语义；
- 官方结果修订的影响闭包、corrected score/aggregate、remediation decision、alpha 不退款、候选重新资格和 current-view 解析规则；
- 开奖前 forecast 诊断、开奖后逐预测 score 与窗口指标清单、完整主键、公式、比较对象、窗口边界、分箱、最小样本量、不足状态、零概率拒绝、数值容差和独立 known-answer 预期；
- 所有合成生成分布、效应大小、开发/功效确认/正式种子隔离、模拟功效、置信算法和数值容差；
- full-rule known-answer 的独立参考分布、oracle 实现身份和八个单元数值预期；
- 规范 `probability_order_key`/`tie_key`、完整空间 tie 计数方法、跨 Top-K tie 和 M0 rank 区间语义；
- `Asia/Shanghai` 日历、UTC 映射、调度计划键、唯一终态、截止/补偿/并发策略和实际 VPS 调度适配器；
- 角色分离、依赖锁/wheelhouse、隔离重建、执行命令、benchmark、资源预算、输出路径和 acceptance 合同。

上述内容未冻结时，可以开展设计和实现准备，但不得生成 Phase 4 正式资格结果或 `READY_FOR_HUMAN_ACCEPTANCE` acceptance。人类最终验收不属于 Phase 4 任务、依赖或阻塞条件。
