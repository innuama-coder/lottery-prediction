# Phase 4：真实模型预测与 AutoResearch 闭环 MVP 定义

版本：3.3（path-classified portable verifier authority）

状态：`D00_AUTHORITY_SYNCED`。本文件与 `ROADMAP.md`、`docs/research/phase-4-overall-design.md`、`docs/plans/phase-4-detailed-plan.md` 在同一 `P4_AUTHORITY_COMMIT` 冻结；只有 D00 两个 checker 均通过且 receipt 证明 clean 后才解除 `HOLD_AUTHORITY_SYNC` 并启动 D01。

本轮 authority 冻结 `P4S10HE1` score order contract：有限 binary64 输入以其精确有理数值按 `1e-10` decimal quantum、`ROUND_HALF_EVEN` 生成稳定整数 tick；Top-1000 排名按 tick 降序、canonical ticket 升序，score/tie identity 与 tie bounds 从该 tick 精确派生。raw binary64 bits 不再承担身份或排名语义。

完整 macOS replay 证明 `18b25e21`/`eb9c124d` 的 17-ULP 假设不完整：旧 profile 下共有 163 failures，Top-1000 shadow display probability 达 31 ULP，model context 内派生 F04 达 151 ULP，coefficient 至少达 15 ULP。它们必须保留为失败证据，不得形成 release。

修订 authority 冻结 `P4-LOCAL-PATH-CLASSIFIED-BINARY64-4`。派生 feature snapshot 与 model context feature/normalization 路径统一进入 `derived_feature_context_v2`（absolute `3 * 2^-53`、relative `3e-14`、151 ULP）；coefficient 与 objective gradient 独立进入 `derived_coefficient_v1`（absolute/relative 仍为 `1e-12`、16 ULP）；三个 Top-1000 display-probability 路径进入 `top1000_derived_probability_display_v3`（absolute `2^-71`、relative `17 / 2^52`、32 ULP）；其余 tight 路径保持原 `1e-12/1e-12/8 ULP`。所有 profile 继续 finite 且三界 conjunction；任何 exact identity/integrity surface 均不路由到数值 profile。

## 1. 权威范围

本文件定义 Phase 4 产品成功语义；总体设计定义模型、数据、概率、时间与治理合同；详细计划定义 D00–D15 DAG、允许写路径和验收顺序；路线图定义跨阶段边界。四份文档必须作为同一 authority 集合解释，任何冲突都 fail closed。

Phase 4 固定读取 Phase 1 canonical 冻结历史和 Phase 3 正式验收先验，不修改 Phase 0–3 任何冻结输入、artifacts、review、acceptance、manifest 或历史结果。所有正式输出位于唯一 `artifacts/phase-4/<release-id>/`。

## 2. 产品成功定义

Phase 4 必须同时交付：

1. SSQ 与 DLT 各自从真实 Phase 1 canonical 历史构造 `retrospective_sequence_safe` 特征；不得为历史开奖补造 `available_at`。
2. 两个 game 各自训练、冻结和实际加载一个可追踪、非退化、非均匀的 P4E2-R 或总体设计允许的等价低容量多特征真实模型；历史 P4E1-R 单一特征版本只作为不可变历史交付。
3. `serving_model_by_game.ssq != M0` 且 `serving_model_by_game.dlt != M0`。M0 只能作为 comparator、diagnostic 或显式 fallback；diagnostic 输出必须带 `NON_PRODUCT_BASELINE` 水印且不能正式 lock。
4. 每个明确目标期由对应冻结 serving release 的联合概率降序生成恰好 1,000 注合法、唯一、严格正概率的完整组合；完整空间和 Top-1000 都至少有两个规范概率层。
5. forecast 可 create-once 锁定、inspect、评分、修订传播和重放；AutoResearch 产生非空真实 diff、新 child ID 和可观察变化的下一期 challenger/shadow，且不得改写 serving。
6. CLI、调度、checkpoint、崩溃恢复、重复运行、跨 game 隔离、安装和命名 workload readiness 全部通过机器验证。
7. D11–D15 在同一冻结 release 上形成正式 E2E、独立 bottom-up replay/mutation、不可变 checklist candidate、覆盖该 candidate 的 pre-acceptance manifest 和只追加的最终验收闭包。
8. D14 提供不含 VPS absolute path 的单一只读本地入口，支持 clean CPython 3.12 patch/platform；它验证历史 Phase 2/2.1 formal receipts/hash 而不重跑其环境。仅逐路径枚举的浮点叶可按 finite + absolute/relative/ULP conjunction 比较，身份、hash、lineage、ticket/order、score/tie identity 和 create-once 文件保持 exact。

不要求证明预测 lift，也不允许伪称 lift。科学状态可以如实为 `lift_supported|no_confirmed_lift|worse_than_M0|insufficient_evidence`；任一不利或证据不足结果必须保留。科学状态不能把产品功能门的 HOLD/FAIL 覆盖成 PASS。

## 3. 数据、时间和模型合同

### 3.1 固定历史与特征

训练只消费 Phase 1 release 链的 canonical `draws.jsonl`、manifest、规则身份、逐 game canonical comparator/order 和文件 SHA-256。issue 先后只能由冻结 comparator 决定，不能使用字符串或未定义数值比较。

历史内生特征类型固定为 `retrospective_sequence_safe`。每个 target 的特征、训练标签和统计量只读取 canonical order 中 target 之前的前缀；target 不得在训练前缀中。Phase 1 历史没有也不需要 `available_at`。可选外部时变特征单独使用 `external_point_in_time`，必须具有真实 `available_at` 与 provenance 且早于 lock；缺证据即排除。

P4E2-R 的正式特征必须覆盖三类：历史水平与变化（F01 expanding rate、F02 rolling 10/30/60、F03 EWMA、F04 recency gap、F05 short/long trend）、号码关系（F06 收缩 pair residual/lift、F07 上期开奖重叠）和组合结构（F08 和值、F09 跨度、F10 奇偶、F11 分桶、F12 连号、F13 尾数、F14 间隔）。F01 单独存在或只增加 F02 不合格；正式 serving 必须实际消费三类中的至少一个特征。pair 只允许低维聚合和强收缩，不得拟合无界号码对参数。SSQ 使用 `33选6 + 16选1`，DLT 使用 `35选5 + 12选2`，两者的快照、参数、release ID、cutoff、回测和 forecast 必须隔离。

### 3.2 训练与回测

模型必须实际消费 F01–F14 中覆盖三类的多特征集合，参数由真实历史目标函数导出并存在非零有效系数和非恒定权重；组合项必须通过精确枚举或流式 log-sum-exp 归一。data/feature/config/code/dependency/model-card 身份全部冻结；fixture、合成正控、预写 ticks、内联参数或工作树默认值不得进入正式 fit/serving。

候选选择只读取 `selection folds`。候选身份冻结后，才允许一次性读取互不重叠的 `report-only evaluation folds`；报告完整注 joint log loss、真正的多分类 Brier（不能只计算观测组合项）、校准、完整组合 Top-10/100/200/1000 指标和不确定性，不利结果不得隐藏或反馈重选。无法形成独立 report-only 窗口时为 `HOLD_BACKTEST_INCOMPLETE`。

## 4. 概率、Top-1000 与锁定

P4E2-R 每个号码分区的组合分数为 `score(C)=sum(beta*x_i)+gamma*g(C)`，固定大小子集概率为 `exp(score(C))` 在完整合法分区空间上的归一化；完整 ticket 概率为各分区概率乘积。所有合法组合概率严格正且总和为 1。P4E1-R 仅保留用于历史回放，不得作为新的 serving。

正式顺序键为 `(joint_probability desc, canonical_ticket asc)`；第二键只在真实局部等概率组内 tie-break。Top-10/100/200 必须是同一 Top-1000 的严格前缀。每行必须包含概率层、tie bounds、full-space rank、lineage 和解释字段。

以下路径必须 HOLD/FAIL：

- 任一 game serving 为 M0、缺真实 model/feature release 或训练 cutoff 不合法；
- 完整空间单一 tie、Top-1000 全等概率或字典序跨概率层主选；
- 仅有 fixture/known-answer/合成证据，或正式命令没有读取指定冻结模型；
- 组合不足/超过 1,000、非法、重复、非正概率、排序或 replay 不一致；
- lock 后改写 forecast、概率、rank、解释或 lineage。

## 5. CLI、账本、评分与 AutoResearch

D01 必须冻结稳定产品 CLI，至少提供以下等价语义：

```text
lottery phase4 train --game <ssq|dlt> --phase1-release <id> --cutoff <issue> --output <release>
lottery phase4 forecast --game <ssq|dlt> --target-issue <issue> --model-release <id> --top-k 1000 --lock
lottery phase4 inspect forecast --forecast-id <id>
lottery phase4 score --forecast-id <id> --result-release <id>
lottery phase4 research --game <ssq|dlt> --parent-model-release <id> --target-issue <issue>
lottery phase4 schedule --release <id>
lottery phase4 replay --release <id> --independent
lottery phase4 validate --release <id>
```

正式 provider 必须核验并记录 model/data/feature/config/code/dependency hashes。账本 append-only；lock 为 create-once/CAS；label unlock 只在结果核验后授予；修订追加新事实并传播 score/aggregate/remediation，不删除旧事实、不退款、不重复 spending。重复触发和 checkpoint resume 必须得到相同 work ID 或明确幂等终态。

AutoResearch 每 game 每周期最多执行冻结菜单内的有界 proposal。正式验收必须证明 parent/child 配置或特征不同、child ID 不同、shadow 概率或 Top-1000 可观察变化，且 serving selection 字节不变。`no-op`、未来数据、无界搜索或 direct promotion 必须拒绝。

## 6. D00–D15 权威顺序

唯一执行顺序为：

```text
D00 -> D01 -> D02 -> {D03,D04} -> D05 -> D06 -> D08 -> D09 -> D10 -> D11
               D01 -> D07 --------------------^                 |
                                                                 v
                     D12 -> D14 -> D13 -> D15
```

D00 checker 通过前不得启动 D01。D11 之后的正式 release 输入只读；D12 独立重算且包含负向 mutation；D14 只生成内容寻址并标记 `CANDIDATE_NOT_RELEASED` 的清单候选；D13 必须覆盖 D14；D15 只能追加 machine acceptance、checklist release receipt 和 final closure，不得修改任何 pre-acceptance 文件。D15 PASS 前不得让人类参与开发门、确认结果、签字或豁免。

## 7. 最终机器验收矩阵

| ID | 必须证明 | 拒绝条件 |
| --- | --- | --- |
| P4-R01 | 四份 authority 同一 clean commit 且两个 checker 通过 | 旧 M0/`baseline_only` 产品 PASS 语义仍存在 |
| P4-R02 | SSQ 真实 sequence-safe F01–F14 snapshot，覆盖三类特征 | fixture、恒定、错误 comparator、target 入前缀、无界 pair 或补造 `available_at` |
| P4-R03 | DLT 真实 sequence-safe F01–F14 snapshot，覆盖三类特征 | fixture、恒定、错误 comparator、target 入前缀、无界 pair 或补造 `available_at` |
| P4-R04 | SSQ 非 M0 model release | M0、手写 theta、缺 model card/lineage |
| P4-R05 | DLT 非 M0 model release | M0、复制 SSQ、缺 model card/lineage |
| P4-R06 | selection/report-only 隔离且科学报告完整 | fold 重叠、读取后重选、隐藏不利结果 |
| P4-R07 | 两 game 非 M0 serving selection | `baseline_only` 或缺 release 形成产品 PASS |
| P4-R08 | 联合概率正、归一、非均匀 | 完整空间单一 tie 或退化 |
| P4-R09 | 正式 Top-1000 概率主排序 | 全等概率或字典序跨概率层主排 |
| P4-R10 | CLI 实际加载冻结模型 | inline/fixture/worktree/M0 provider |
| P4-R11 | 双 game 各 1,000 注且锁定 | 缺 game、重复/非法或未锁 |
| P4-R12 | 血缘、时间、评分、修订和恢复正确 | 提前 label、覆盖、重复事实或跨 game 污染 |
| P4-R13 | AutoResearch diff 影响 child/shadow | no-op 或直接改 serving |
| P4-R14 | 独立 replay/mutation 逐事实一致 | 导入产品核心或任一 mismatch 未被捕获 |
| P4-R15 | manifest、D14 与保护树闭合 | 缺/额外/择优证据、自引用或旧树变化 |
| P4-R16 | 科学结论有 comparator、窗口、指标与不确定性 | 把运行、参数变化或合成结果写成 lift |
| P4-R17 | 本地验收只在机器交付后释放 | 人工成为开发前置或代替机器门 |

P4-R01–P4-R17、D00–D15 receipts、Phase 4/3/2.1/2 回归、独立 replay、manifest 覆盖率和 pre-acceptance 不变率全部通过且 blocking findings 为 0 后，唯一机器状态为：

```text
READY_FOR_LOCAL_PRODUCT_ACCEPTANCE
```

任何可恢复缺失保持对应 HOLD；因果泄漏、锁后篡改、选择性删除、伪造证据或越权 serving 变更为 FAIL。M0 comparator 可永久保留，但不能形成正式 lock 或产品 PASS。

## 8. 交付目录与产品边界

唯一 release 至少包含：

```text
authority/authority-freeze.json
data/<game>/training-input-manifest.json
features/<game>/<feature-release-id>/{feature-snapshot.jsonl,manifest.json}
models/<game>/<model-release-id>/{model.json,training-report.json,model-card.md,manifest.json}
backtests/<game>/<backtest-id>/{selection-fold-metrics.jsonl,report-only-fold-metrics.jsonl,summary.json}
forecasts/<game>/<target>/{forecast.json,top1000.jsonl,explanations.jsonl,lock.json}
research/<game>/{candidate.json,diff.json,decision.json}
replay/replay-report.json
acceptance/local-product-checklist-candidate.md
manifest/delivery-manifest.json
acceptance/{machine-acceptance.json,checklist-release-receipt.json,final-closure.json}
```

Phase 4 不建设 WebUI、移动端、公开 HTTP API、购彩、代购、支付、资金、收益或中奖保证功能；不访问生产系统、不要求 root；不等待未来开奖。正式未来 forecast 可为 `locked_unscored`，评分 E2E 使用结果已知但在虚拟时钟中严格延后 unlock 的历史 target。
