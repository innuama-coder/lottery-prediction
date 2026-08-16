# Phase 4 真实模型预测与 AutoResearch 闭环 MVP 总体设计

版本：3.1（多特征模型与 portable local verifier 设计）

状态：`D00_AUTHORITY_SYNCED`。本文与 `ROADMAP.md`、`tasks/phase4/README.md`、`docs/plans/phase-4-detailed-plan.md` 在同一 `P4_AUTHORITY_COMMIT` 冻结；D00 两个 checker 通过后解除 `HOLD_AUTHORITY_SYNC`，才允许启动 D01。

## 1. 结论、目标与诚实边界

Phase 4 的产品目标是：对 SSQ 和 DLT，各自从 Phase 1 冻结历史序列构造 `retrospective_sequence_safe` 多特征快照，训练并冻结一个非 M0、非均匀的 `P4E2-R`（或经过同等验收的低容量多特征模型）`serving_model_by_game` release；对明确目标期，用该 release 的联合概率降序生成、锁定并可重放各 1,000 注正式预测；在开奖后可评分，并让 AutoResearch 的参数或特征调整进入下一期 challenger/shadow。现有 `P4E1-R` 单一长期频率模型保留为不可变历史版本，不构成本设计的特征工程交付。

工程成功必须同时满足：

1. `serving_model_by_game.ssq != M0` 且 `serving_model_by_game.dlt != M0`；两者都绑定真实训练数据、feature snapshot、训练配置、代码/依赖身份和 model card。
2. 每个正式 forecast 实际加载对应冻结模型 release；完整合法空间不是单一 tie group，至少有两个可达概率层级，正式 Top-1000 内也至少有两个不同的规范概率值。
3. Top-1000 的主顺序是联合概率严格降序；确定性号码顺序仅在真实局部等概率组内 tie-break，不能主导选择。
4. SSQ、DLT 均由真实 Phase 1 历史输入完成训练、回测、正式 CLI 预测、锁定和独立 replay；fixture、预写 ticks、合成分布或 M0 演示不能替代。
5. 任一彩种缺少真实模型、退化为全等概率、无法可靠排序、训练截止不合法或 serving 命令未加载冻结 release，终态为 `HOLD_BASELINE_ONLY` 或更具体的 HOLD，不得回退 M0 后声明预测 MVP PASS。

这只证明“真实模型预测能力已交付”，不证明彩票存在可利用规律，不保证中奖或收益，也不等于“模型已显著优于随机基线”或“持续提升已证实”。在合同要求的独立 report-only 窗口已经形成时，回测无 lift、置信区间跨零或统计功效不足可以与工程 PASS 并存，但必须如实呈现为 `no_confirmed_lift|insufficient_evidence`；无法形成该独立窗口则为 `HOLD_BACKTEST_INCOMPLETE`。工程 PASS 不能与缺少真实训练模型并存。

## 2. 根因分析与合同修复

### 2.1 工程状态与科学状态分离为何被错误扩大

原设计正确地认识到有限历史和极低 Top-K 命中率不能证明科学优势，却把“无需证明 lift”错误推导成“无需交付实际模型”。工程状态只验证循环、Schema、概率归一、排序、锁定和恢复，科学状态允许 `baseline_only`，最终又允许 `champion_by_game={ssq:M0,dlt:M0}`。于是“基础设施能承载模型”替代了“产品正在用模型”。

修复是建立两个正交而非互相替代的门：

- 产品功能门：两彩种必须各有真实历史训练、冻结、非均匀的 serving release，并实际驱动正式 Top-1000；否则 HOLD。
- 科学效果门：按冻结时间切分报告其相对 M0 的 log loss、Brier/校准和 Top-K 覆盖及不确定性；未证明 lift 不阻止功能交付，也不得改写成改善结论。

### 2.2 合成正控、known-answer 和候选接口为何不够

合成正控证明控制器在人工注入的可辨识信号下能调整；known-answer 证明概率、归一化、tie/rank 和 Top-K 实现符合数学；candidate 接口证明系统能保存某种候选。三者都不证明生产路径读取了真实历史、按冻结顺序构造了 `retrospective_sequence_safe` 特征、估计了参数、冻结了 release，或 CLI 加载了该 release。预先写死的 ticks、fixture 参数和测试分布因此只能进入测试证据，不能成为 `serving_model_by_game`。

### 2.3 M0 展示子集为何不是 Top-1000 概率预测

M0 对完整合法空间赋同一概率，所有组合处于一个覆盖全空间的 tie group。按字典序取前 1,000 注只产生可重复的展示子集；其中第 1 注不比第 1001 注更可能，故它不是“最可能 1,000 注”。确定性 tie-break 是局部等概率时的稳定化工具，不能创造概率证据。

### 2.4 旧条件如何共同造成缺口

旧边界声明 Phase 4 不要求真实 shadow、替换 M0 或真实 lift；P4E1/候选只被当作可选能力；旧 T04/T10 以 M0/full-space known-answer 为概率主路径，T07/T12/T13/T16以大量合成资格为核心，T05/T09/T11/T17只要求流程可运行；旧 T24 又明确允许 Champion 仍为 M0、Top-K 为 `insufficient_observation`。A01–A03能由 M0满足，A04专门接受其完整空间并列，A05–A10只证明合成调整能力，因此 T00–T24 全通过仍不产生真实模型预测。

### 2.5 无需等待未来开奖的可交付方案

使用已冻结、已公开的 Phase 1 历史序列作 expanding/rolling-origin 训练与回测；每个回测目标期只读取此前期次。用全部合法训练前缀冻结一个最小低容量模型，立即对历史末端之后的明确目标期生成正式、未评分但已锁定的 Top-1000。这样可以当下验证训练、非均匀概率、排序、CLI、锁定和 replay；效果结论只来自历史时间切分，不等待未来开奖。未来结果只用于运营评分和后续治理，不是 Phase 4 完成条件。

### 2.6 正式构建 provenance 与可移植本地验收是两个合同

正式 release provenance 不可变地记录 Linux builder 的精确解释器 realpath、Python patch、平台、依赖锁、命令和哈希；这些字段证明发布者实际使用了什么，不是本地用户必须复现的绝对路径。本地产品验收是另一个只读合同：支持 clean CPython 3.12 环境的 patch/platform 迁移，不要求历史 Phase 2/2.1 VPS virtualenv，也不重新运行这些历史 regression suites。它从 final manifest closure 验证 A05/A06 的不可变 receipt、命令、退出码和 hash。

跨 CPython 3.12 patch 或平台重算的 binary64 字段采用 `P4-LOCAL-SEMANTIC-BINARY64-1`：只有合同逐路径枚举的浮点叶可语义比较；两值必须有限，并且同时满足 absolute `<=1e-12`、relative `<=1e-12`、ULP distance `<=8`。该 8-ULP 上限覆盖已观测 macOS 3.12.11 与 Linux 3.12.3 的 4-ULP `math.fsum` 差异，并保留一倍保护余量；三个上限取 conjunction，避免近零绝对容差或大值相对容差单独放宽。release/object IDs、SHA-256、issue/cutoff/lineage、ticket membership、Top-1000 order、canonical ticket、frozen score/tie identity、tie bounds、schema enums 和 create-once files永远 exact。非有限值、未枚举路径差异和任一界外值 fail closed。

## 3. 状态、角色与选择语义

| 对象 | 作用 | 能否驱动 Phase 4 正式 forecast |
| --- | --- | --- |
| `M0 comparator/fallback` | 均匀固定基数基线；计算相对指标、故障诊断和显式降级输出 | 否；fallback 输出必须标为 diagnostic/non-product，触发 `HOLD_BASELINE_ONLY` |
| `serving_model_by_game` | SSQ、DLT 各自真实历史训练并冻结的 release | 是；正式 forecast 的唯一模型来源 |
| challenger/shadow | AutoResearch 用冻结父模型、允许的参数/特征 diff 训练的下一期候选 | 否，除非后续治理产生新的 serving release |
| 科学状态 | `lift_supported|no_confirmed_lift|worse_than_M0|insufficient_evidence` | 不替代产品功能门；仅限制表述和后续治理 |

`baseline_only` 只可描述比较/诊断现场，不能是 Phase 4 PASS。`serving_model_by_game` 按 game 独立，禁止用一个 game 的数据、参数、资格或状态替代另一个。模型代码可以复用，模型参数、release ID、训练截止、feature snapshot、回测和 forecast 必须分别冻结。

Serving 资格不以“显著胜过 M0”为 Phase 4 前提，而以真实训练、序列安全、概率有效、非退化和可重放为前提。每个 game 预先冻结低容量候选、`selection folds`、只读 `report-only evaluation folds` 和 tie-break；先在 selection folds 剔除泄漏/失败/退化者并按平均联合 log loss（同分依次按较低复杂度、稳定 model ID）选择，再冻结候选身份，之后才允许读取 report-only labels。科学报告只用 report-only 窗口估计选定模型相对 M0 的效果和不确定性，即使不利也不隐藏。没有合格非均匀候选时不得选择 M0冒充产品；没有足够历史形成独立 report-only 窗口时为 `HOLD_BACKTEST_INCOMPLETE`，不得重复使用 selection labels 或假造区间。

## 4. 数据和时间合同

### 4.1 固定输入

训练只消费 Phase 1 release 链中的 canonical `draws.jsonl`、`manifest.json`、规则身份、逐 game 冻结的 canonical issue/calendar order（含 comparator 身份）和逐文件 SHA-256；Phase 0–3 制品只读。每个 game 先用该冻结 comparator 验证唯一、连续性政策、号码规则和 manifest provenance，再形成 `training_dataset_id = SHA256(game || canonical_order_id || ordered_draw_hashes || rule_id || cutoff_issue)`。禁止用 issue 字符串字典序或未经合同定义的数值 `<` 判断先后。

Phase 1 内生历史采用 `retrospective_sequence_safe`：目标期 `q` 在逐 game canonical order 中有唯一位置，feature row、训练标签和任何统计量只读取该位置之前的冻结前缀；训练截止必须位于 forecast target 之前，且最大输入 issue 等于该截止。validator 必须证明 target 不在训练前缀，并拒绝未知 target、跨 game comparator、同/未来位置和乱序输入。Phase 1 历史记录没有也不需要 `available_at`，本阶段禁止为其补造该字段。锁定后不得更换数据 release、特征或模型。回测每个 fold 都重新从其 canonical 历史前缀拟合，禁止用全历史标准化、全历史超参选择或目标期结果。

外部销量、奖池、天气、媒体等时变预测特征属于独立的 `external_point_in_time` 类型，不是 Phase 4 前置。只有同时存在原始值、可信 `available_at`、采集 provenance 和 `available_at < prediction_locked_at` 的真实证据时，才能经新 feature release 启用；该规则只适用于外部特征，绝不能反向要求或补造 Phase 1 历史记录的 `available_at`。缺证据即排除，不阻塞仅使用 Phase 1 内生历史特征的 MVP。

### 4.2 双彩种真实特征工程

SSQ 分为红球 `33选6` 和蓝球 `16选1`；DLT 分为前区 `35选5` 和后区 `12选2`。两者使用相同定义、不同数据和参数：

- `F01_prior_inclusion_rate`：对每个号码，在目标期之前 expanding prefix 中的出现次数，以 Beta-Binomial shrinkage 形成平滑包含率；分区内只使用此前开奖记录。
- `F02_rolling_inclusion`：最近 10/30/60 期出现率；窗口只能读取目标期之前的前缀，窗口不足时按预注册最小暴露规则处理。
- `F03_ewma_inclusion`：半衰期 10/30 的指数衰减出现率，半衰期只可在训练 fold 内的有限网格中选择。
- `F04_recency_gap`：距上次出现期数的 `log1p` 变换并截断；从未出现使用预注册的右端点，不得使用未来记录填充。
- `F05_short_long_trend`：短期窗口与 expanding 长期率的差值。
- `F06_pair_cooccurrence`：带 Beta/Dirichlet 收缩的 pair residual/lift，按候选组合聚合；禁止为 528/595 个号码对拟合无约束独立参数。
- `F07_previous_draw_overlap`：候选组合与上一期开奖结果的重叠数量。
- `F08_sum_quantile`、`F09_span`、`F10_parity_count`、`F11_bucket_counts`、`F12_adjacency`、`F13_tail_diversity`、`F14_gap_statistics`：候选组合的和值/跨度/奇偶/分桶/连号/尾数/间隔结构，均以低维、预注册的数值或分箱统计表达。

正式 serving 必须实际消费至少一个历史变化特征、一个号码关系特征和一个组合结构特征；F01 单独存在或只增加 F02 均不合格。若正则化后某一特征类全部归零，该候选只能标为 `rejected_feature_insufficient`，不能晋升 serving。feature snapshot 按 `(game,target_issue,training_dataset_id,feature_config_id)` 记录每个号码/组合的原始统计、变换值、最大源 position 和 input hashes；全常数、全零、fixture 或预写 ticks 不合格。

## 5. 多特征真实模型路线 P4E2-R

P4E2-R 是低容量、可精确归一和可解释的固定基数加权子集模型。对 game 的每个号码分区 `p` 和合法候选子集 `C`，定义：

`score_p(C) = sum_i_in_C(beta_p · x_p,i) + gamma_p · g_p(C)`

其中 `x` 为 F01–F05 号码级特征，`g` 为 F06–F14 的低维组合特征。号码主效应可以保留以下正权表示：

`w[p,i] = exp(clip(theta[p] · x[p,i], -B, B)) > 0`

其中 `B` 固定以避免溢出。对合法的固定大小子集 `S`：

`P_p(S) = exp(score_p(S)) / sum_{C in Omega_p} exp(score_p(C))`

正式规则下的分区组合空间可完整枚举（SSQ 红区 1,107,568 个，DLT 前区 324,632 个），因此必须使用流式 log-sum-exp、精确枚举或有数学证明的等价算法；未归一的启发式分数不能冒充概率。完整一注的联合概率为各分区概率乘积，所有合法组合严格正且总和为 1。

训练使用带 L2 或 group-lasso 的条件对数似然，正则、标准化和有限候选网格只能在 selection folds 内确定。200 期历史不允许高容量神经网络、无界 pair 参数或随机特征搜索。每个 serving release 必须输出系数、正则、特征组、消融结果和训练目标轨迹。

训练使用按 canonical order 划分且互不重叠的 rolling-origin `selection folds` 与更晚的 `report-only evaluation folds`。仅 selection folds 可用于有限特征窗口、半衰期、正则和组合分数网格及候选选择；候选集合、配置、特征标准化、tie-break 与 model-selection receipt 必须在任何 report-only label 可读前冻结。report-only folds 只评估一次已选配置，不反馈选择、特征或阈值。最终模型可在 `training_cutoff_issue` 之前的完整前缀重拟合，但其科学效果只能引用 report-only 结果。SSQ 与 DLT 独立选择、拟合和发布。网格、fold 边界、最小前缀、Decimal 精度和依赖版本在训练前冻结；随机步骤如存在必须有固定 seed，首选无随机优化。历史不足以同时提供最小 selection 和 report-only 窗口时必须 `HOLD_BACKTEST_INCOMPLETE`。测试 fixture 不得进入正式 fit。

训练输出必须证明：特征读取边界、fold-local transform、目标 label 只在该 fold 评分时读取、最终参数由真实历史目标函数导出。相同代码/依赖、输入 hashes 和配置必须字节级或规范语义级重放相同参数、归一常数、model release ID 和 Top-1000。

该路线的选择理由是：容量小，适配有限彩票历史；联合分布严格正且可归一；号码贡献可解释；固定基数约束原生满足；不需要 `external_point_in_time` 数据；能真实训练但不暗示存在规律。高容量神经网络、强化学习和无界特征搜索不属于 Phase 4 MVP。

## 6. 模型与特征资格门

每个 game 的 serving release 必须通过以下全部条件：

1. 输入是已验证的真实 Phase 1 冻结前缀；逐 game canonical comparator 证明 `training_cutoff_position < target_position` 且 target 不在前缀中，无同/未来位置读取。
2. feature snapshot 完整，F01–F14 的注册定义、截止位置和输入哈希完整；正式 serving 实际消费历史变化、号码关系和组合结构三类特征，参数不是常量、fixture 或手写值。
3. 模型不是 M0，至少一个分区存在非零有效系数和非恒定权重；完整空间至少两个可达规范概率层级。
4. 正概率、归一化、排序、Top-K 与独立实现/枚举在批准误差合同内一致；同输入 replay 稳定。
5. 回测报告分别标识 selection folds 与 report-only evaluation folds，证明选择 receipt 早于 report-only label capability，并覆盖 M0 comparator、逐 fold 指标、聚合方法、置信/不确定性以及所有不利结果。
6. model card 明示数据截止、适用 game、限制、科学状态和禁止表述。

退化检测不只检查 `theta != 0`：还要检查 feature variance、权重动态范围、规范概率 distinct count、完整空间最大 tie 占比、Top-1000 distinct probability count、Top-1000 首尾概率比、排序对输入排列的稳定性，以及在移除概率键后字典序是否会改变跨概率层选择。阈值在训练结果前冻结；硬门至少要求完整空间不为单一 tie、Top-1000 不全等概率、Top-1000 所有相邻跨层顺序均严格按概率。失败为 `HOLD_DEGENERATE_MODEL|HOLD_UNRELIABLE_RANKING|HOLD_BASELINE_ONLY`。

## 7. 正式 Top-1000、tie 与解释

对每个合法组合计算或以可证明等价的 exact k-best 算法导出联合概率，按 `(probability desc, canonical_ticket asc)` 排序。第二键仅在规范概率相同的局部 tie group 内使用。Top-10、100、200 是同一 Top-1000 的严格前缀。

正式 forecast bundle 至少包含：game、target issue、forecast/model/feature/data/config/code/dependency IDs、训练截止、锁定时间、1,000 注、每注联合概率、概率层/tie bounds、全空间 rank、号码级特征贡献、归一化证明和 ranking algorithm ID。解释是排名依据，不是因果或中奖保证。

机器验收从 bundle 反向解析 model release，重新加载冻结 feature snapshot 和参数，抽样及边界重算概率，并独立生成 Top-1000 hash。若实际命令走 M0、内联参数、fixture、未冻结工作树或字典序主选路径，即使输出恰有 1,000 注也失败。

## 8. Release、锁定、评分与审计制品

固定 release 根建议为 `artifacts/phase-4/<release-id>/`，至少包含：

```text
authority/authority-freeze.json
data/<game>/training-input-manifest.json
features/<game>/<feature-release-id>/{feature-snapshot.jsonl,manifest.json}
models/<game>/<model-release-id>/{model.json,training-report.json,model-card.md,manifest.json}
backtests/<game>/<backtest-id>/{selection-fold-metrics.jsonl,report-only-fold-metrics.jsonl,summary.json}
forecasts/<game>/<target-issue>/{forecast.json,top1000.jsonl,explanations.jsonl,lock.json}
research/<game>/{candidate.json,diff.json,decision.json}
replay/replay-report.json
acceptance/local-product-checklist-candidate.md
manifest/delivery-manifest.json
acceptance/{machine-acceptance.json,checklist-release-receipt.json,final-closure.json}
```

所有对象以内容哈希、parent IDs 和规范序列化形成不可变身份。model manifest 必须绑定训练数据截止、feature release、训练配置、代码 commit、dirty=false、依赖 lock、数值精度、训练命令和输出 hashes。锁定采用 create-once/compare-and-swap；锁后任何影响号码、概率、排序、身份或解释的变化产生新 forecast，不覆盖旧对象。

开奖后评分通过 guarded label unlock 读取已核验结果，计算完整组合空间上的 joint log loss、真正的多分类 Brier（包含观测组合项和所有未观测组合项）、校准摘要和完整注的 Top-10/100/200/1000 覆盖；不得只用 `(1-p_observed)^2` 作为 Brier。结果修订以追加 correction closure 传播，不退款、不删除旧实验。Phase 4 完成不等待这些新 forecast 的未来标签：历史 backtest 已验证评分路径，正式未来 forecast 可合法处于 `locked_unscored`。

独立 replay 从 Phase 1 输入和冻结代码/配置重建特征、模型、概率及 Top-1000，不信任顶层 PASS。随后先从正式 E2E/replay 的冻结 IDs 机器生成不可变 checklist candidate，再生成覆盖它及全部 pre-acceptance 文件的 delivery manifest；manifest 不 hash 自身。最终验收不得修改 checklist 或 manifest，只追加 machine acceptance、checklist release receipt 与 final closure。receipt 绑定 checklist hash 和 manifest hash；closure 绑定 pre-acceptance manifest hash 及前两个新文件的 hash，但不 hash 自身，从而无自引用。manifest/checklist checker 还要保护 Phase 0–3 roots 前后不变。恢复使用幂等 work ID 和 checkpoint；失败 attempt 永久保留，不能择优删除。

## 9. CLI 与本地产品验收

面向本地用户的产品流程必须提供等价于以下语义的稳定 CLI（确切 verb 由 authority 同步后的合同冻结）：

```bash
lottery phase4 train --game ssq --phase1-release <id> --cutoff <issue> --output <release>
lottery phase4 train --game dlt --phase1-release <id> --cutoff <issue> --output <release>
lottery phase4 forecast --game ssq --target-issue <issue> --model-release <id> --top-k 1000 --lock
lottery phase4 forecast --game dlt --target-issue <issue> --model-release <id> --top-k 1000 --lock
lottery phase4 inspect forecast --forecast-id <id>
lottery phase4 replay --release <id> --independent
```

`inspect` 必须直接显示 game、目标期、serving model ID（非 M0）、feature snapshot ID、Phase 1 input ID、训练截止、参数摘要、概率 distinct count、首尾概率、局部 tie、每注排名依据和锁定状态。正式本地验收使用真实冻结历史输入，分别得到 SSQ/DLT 各 1,000 注并重放一致。fixture/M0 只能用显式 `--diagnostic` 路径，输出水印 `NON_PRODUCT_BASELINE`，且不能生成正式 lock 或 PASS receipt。

环境资格以命名 workload 的 benchmark/readiness 为准：在目标环境测量双彩种训练、完整空间/Top-1000、replay 的时间和峰值资源，并据 TIMEOUT/磁盘预算裁决；不绑定特定 CPU、内存型号、VPS 或人工角色。远程开发全部机器门通过后才生成本地验收清单；人工验收不是任何开发任务的前置或完成条件。

## 10. AutoResearch 与后续 serving 治理

每个 game 独立运行一轮有界 proposal：从冻结 serving parent 选择一个预注册参数（如正则或 F02 半衰期）或允许特征配置（启用/禁用 F02）的真实 diff，创建 child feature/model release，并用于下一目标期 challenger/shadow forecast。验收必须比较 parent/child 配置、feature snapshot、参数、概率或 Top-1000，证明 diff 确实传播；no-op 不合格。

合成正负控只验证 proposal、错误预算和检测器，不决定真实 serving。真实历史 backtest 只授予 `challenger_eligible` 或 `shadow_candidate`；不得因一次历史选择宣称科学改善。后续 serving 更新必须在新 release 中按冻结治理比较 parent 与 challenger，报告 M0、selection/report-only 窗口、指标和不确定性，通过非退化、`retrospective_sequence_safe`（如启用外部特征则另过 `external_point_in_time`）和重放门，并保留旧 serving 和 M0 fallback。Phase 4 只需证明该治理输入和下一期 shadow 闭环可运行，不要求候选晋升。

## 11. 因果隔离、安全、调度与恢复

- forecast lock 前无目标期 label capability；评分器在锁定且官方结果核验后才获得只读 label。
- 数据、特征、模型、forecast、score、experiment 和 decision 均为追加对象；current view 由 ledger 重建。
- SSQ/DLT 的数据、参数、forecast、研究预算和状态键隔离；跨 game 合并 fail closed。
- 调度只编排 prepare → train/select → forecast → lock → verify/unlock → score → research/shadow；同一 work ID 重试不重复训练 release、锁、评分或支出。
- 调度验收用虚拟时钟和固定历史响应，不等待未来开奖；真实只读 canary 只是 readiness，不作为核心产品证据。
- 故障按可恢复 HOLD 与不可恢复 FAIL 分类；恢复命令、固定输入、checkpoint 和未完成输出写入 receipt。
- 任何 source、feature、模型数学、训练配置、排序或验收语义变化均创建新 release；不得修改已锁定证据。

## 12. 工程终态与科学措辞

工程终态：

- `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`：authority 已同步；SSQ/DLT 全部真实模型、预测、E2E、replay、manifest 和机器门通过。
- `HOLD_AUTHORITY_SYNC`：旧 authority 尚未同步；禁止开发启动。
- `HOLD_BASELINE_ONLY`：任一 game 只有 M0/均匀模型或 serving 缺失。
- `HOLD_DEGENERATE_MODEL`：单一全空间 tie、Top-1000 全等概率或稳健退化检查失败。
- `HOLD_DATA_TIME_CONTRACT|HOLD_BACKTEST_INCOMPLETE|HOLD_MODEL_RELEASE|HOLD_UNRELIABLE_RANKING|HOLD_REPLAY_MISMATCH|HOLD_ENVIRONMENT_READINESS`：对应可恢复阻塞。
- `FAIL_LEAKAGE|FAIL_TAMPERED|FAIL_FALSE_CLAIM`：未来信息、锁后篡改或把诊断/科学不确定性伪装为成功。

科学状态按 `(game,model_release,comparator=M0,selection_window,report_only_window,metric)` 报告，包含点估计、区间/重采样方法和样本量。允许 `worse_than_M0|no_confirmed_lift|insufficient_evidence`；但若连合同规定的独立 report-only 窗口都无法形成，则工程终态是 `HOLD_BACKTEST_INCOMPLETE`，不能用 `insufficient_evidence` 绕过回测完整性门。禁止“最可能”被解释为真实中奖优势，只能指模型内部概率排序。只有后续预注册、足量的前瞻证据才能声明持续改善。

## 13. 交付硬门

最终机器验收必须从底层制品重算并拒绝：任一 `serving_model_by_game=M0`；`baseline_only` 被当作 PASS；缺 feature snapshot/model release/model card/backtest；F01-only 或缺历史变化/号码关系/组合结构任一特征类；canonical order/comparator 身份缺失、target 位于训练前缀、训练截止不合法或 fold 泄漏；selection 与 report-only 未隔离或读取后选择；仅有 fixture/合成证据；完整空间单一 tie；Top-1000 全等概率；字典序决定跨概率层选择；正式 CLI 未使用冻结模型；SSQ/DLT 任一缺 1,000 注、锁或 replay；manifest 未覆盖不可变 checklist candidate；终验改写 pre-acceptance 文件或 closure 自引用；科学措辞未报告 comparator、selection/report-only 窗口、指标和不确定性。

允许正式未来 forecast 尚未开奖，允许 backtest 未证明 lift；不允许以 M0 fallback维持 PASS。所有门通过后机器状态只能是 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`，随后才交给本地用户按清单验收。

## 14. 设计决策与删减

保留：外部 `external_point_in_time`/label capability、内生序列安全、不可变身份、追加账本、双 game 隔离、锁定、修订闭包、幂等恢复、manifest、独立 bottom-up replay、负向 mutation 和相称的 benchmark。

删减：与预测 MVP 无直接关系的固定人工角色排列、重复多层声明/closure、任意硬件绑定，以及把数千条大规模合成序列作为核心成功代理。合成测试仅保留覆盖数学实现、控制器正负响应和错误预算所需的最小确定性样本；若要统计模拟，任务必须预注册问题、seed、区间方法和基于精度/功效的预算，并由 workload benchmark 证明可执行。

## 15. 启动顺序

1. 后续单独任务同步修改 `ROADMAP.md` 与 `tasks/phase4/README.md`，删除其旧成功语义，并在同一 authority commit 冻结新 hashes。
2. authority checker 验证四份文档语义/身份一致，解除 `HOLD_AUTHORITY_SYNC`。
3. 才按详细计划执行实现、正式 release 和验收。

在第 1–2 步完成前，本文只是一份设计候选，不授权用现有旧配置、Schema 或实现制造 Phase 4 PASS。
