# Phase 3 历史模型研究基线总体设计

版本：1.0

状态：权威实施设计；仅完成设计交付，Phase 3 尚未实施、运行或验收

冻结上位定义：`tasks/phase3/README.md`（SHA-256 `0b1bcc329c8063a8336e188e7e88b99542c038cc28a51387b81867d5953e1cdf`）

## 1. 定位、目标与成功含义

本设计将 Phase 3 定义为一次受约束的**历史模型研究基线**：在不使用未来信息、每个外层目标期只评价一次、结果前完成预注册的条件下，分别比较双色球（SSQ）和超级大乐透（DLT）的均匀模型 M0 与低复杂度 challenger，形成可重复、可独立复算的历史证据。

Phase 3 成功表示研究合同、实现、运行、失败记录、独立 replay 和验收形成闭环；不要求找到有用模型。以下均可成为诚实且成功的科学结果：存在 `shadow_candidate`、`no_shadow_candidate` 或 `indeterminate`。Phase 3 不是生产预测、真实未来期 shadow、Champion 晋升或投注阶段。

M0 是现在和 Phase 3 结束后的永久默认 Champion。任何历史回测都不能替换 M0。M1 是必须实现并运行的 challenger；M2–M4 只有满足结果前冻结的开放条件才可运行，否则保留 `not_opened`。失败找到优于 M0 的模型不是阶段失败。

## 2. 权威顺序与当前交付状态

出现冲突时按以下顺序处理：

1. `tasks/phase3/README.md` 是唯一当前冻结的 Phase 3 权威。
2. 本总体设计解释该权威；`docs/plans/phase-3-detailed-plan.md` 在本设计边界内规定实施顺序。
3. 下表中的已验收制品提供冻结输入身份和已知科学边界，不得被 Phase 3 改写。
4. `docs/roadmap/phase-2-randomness-audit-plan.md`、`docs/research/phase-2.1-overall-design.md` 与 `docs/research/lottery-autoresearch-technical-strategy.md` 只提供已核对的背景和方法上下文，不是当前 Phase 3 权威。
5. `tasks/research/v1.0.0-lottery-autoresearch-roadmap/` 仅是历史、非权威研究分解；其中旧交接路径和任务状态不得用作当前输入或验收事实。

本次文档工作只交付总体设计和详细计划。它不创建 Phase 3 正式 release，不是一次正式模型运行，也不是 Phase 3 科学验收。实施完成前，Phase 3 交付状态保持“未交付/未验收”。

## 3. 正式输入合同

未来 Phase 3 实施必须在读取模型结果前，将下列仓库路径、SHA-256、Git 提交、记录计数和相互引用写入不可变 input manifest。路径已在本设计基线的 `HEAD` 验证存在。

| 输入角色 | 当前路径与冻结身份 | 允许用途 |
| --- | --- | --- |
| Phase 1 最终验收 | `artifacts/phase-1/acceptance/phase1-acceptance.json`；`959b1dddacf453dbff347786d572de4cd8c52d1b7eb2e7a3805cffa2a166bb18`；`PASS` | 证明 Phase 1 数据交付闭包 |
| Phase 1 发布 manifest | `artifacts/phase-1/baseline-v1/manifest.json`；`0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1`；`published` | 冻结数据、记录数和发布身份 |
| Phase 1 开奖数据 | 由上述 manifest 绑定的 `artifacts/phase-1/baseline-v1/draws.jsonl`；`f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1` | 历史结果标签；只有通过 Phase 3 point-in-time 门的先前开奖结果才可派生预测特征 |
| 注册规则版本 | `artifacts/phase-2/contracts/input-manifest.json`；`36ad90a204a2d0ebab5ddbfff3a4246f267e02cdd2cfe961200e515c27ef90ad` | 冻结游戏、号码空间、公开开奖流程、奖金/活动非生成分段和机制元数据状态 |
| Phase 2.1 最终验收 | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json`；`d5dde1d4488290e41998c1e7f6d04b1b3ae094408716571ceb5451324cb8e8b4` | 最终 `PASS / GO`、科学分类 `indeterminate`、0 个 blocking finding |
| Phase 2.1 递归清单 | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/manifest.json`；`c2fb2e4a60ed214ce4648a93a1d8b11aed2ebd41b920dd549158e5adc821e3c6` | 验证接受 bundle 的 56 个文件及递归证据身份 |
| Phase 2.1 历史审计 | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/historical-audit.json`；`a3d0f1f2dc371e3ff53256c6f09d5b47471f84567e33feaa8efa9c8349b8a8d1` | 五个 family × 两个彩种的已注册历史结果、限制和负控 |
| Phase 2.1 功效结果 | `artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/power.json`；`99bca12e9452435fbc32c67686d4dc905ea4771b8bb7b7d62c02983e24b98a10` | 240 个格点、功效/精度边界和 `not_identified_*` 结果 |

注册规则版本为：DLT 号码空间 `dlt-ns-35c5-12c2-v1`、公开流程 `dlt-process-documented-random-device-35c5-12c2-v1`；SSQ 号码空间 `ssq-ns-33c6-16c1-v1`、公开流程 `ssq-process-documented-draw-machine-33c6-16c1-v1`。Phase 3 不把奖金变化或 DLT 促销当成号码生成分段，也不把未知的机器、球组或摇出顺序补写成特征。

Phase 2.1 的 `indeterminate` 表示当前注册检验和功效不能形成更强结论，不是“证明彩票随机”，也不是 `candidate_signal`。历史审计中的任何异常只能成为普通、预注册 challenger 假设，不能获得模型开放、分类或晋级特权。

## 4. 数据、时间和 point-in-time 边界

冻结数据共 400 个独立 `DrawRecord`：DLT 200 期（2025034–2026083），SSQ 200 期（2025037–2026085）。800 个 `SourceObservation` 只提供血缘，不能增加样本量。两个彩种不得合并训练、折、指标、置信区间或科学结论。

所有 Phase 1 记录均为 `retrospective_current_view` 且 `available_at_utc=null`。因此 Phase 3 必须 fail closed：

- 对每个 `(game, target_issue)` 明确定义 `prediction_locked_at`，并建立只追加的 availability ledger。
- 特征的每个原子输入都必须有证据证明 `available_at < prediction_locked_at`；仅有开奖日期、当前网页值、`first_seen_at`、`retrieved_at` 或修订后当前视图不构成证明。
- 目标期号码只作为外层评价标签。没有时间证据的历史结果不得自动成为后续目标期的预测特征。
- 任何窗口、归一化、分箱、缺失填补、特征选择、超参数和模型权重只可在对应外层目标期之前的数据上拟合。
- 若冻结输入无法支持 M1 所需的合法训练历史，正确终态是 `HOLD` 或科学 `indeterminate`，不得虚构可用时间。

网络只允许用于正式运行前的准备。网络获取的材料只有在独立核对、保存内容、记录来源/获取时间、生成 SHA-256、写入输入 manifest 并在预注册之前冻结后，才可成为未来正式运行输入。正式实验、replay 和 acceptance 必须离线；网络、live 页面或“最新”指针永远不是正式实验输入。

### 4.1 外层时间隔离

评估采用按彩种独立的 expanding-window rolling-origin。预注册必须列出全部外层目标期、训练截止点、最小训练长度、内层折和排除理由。每个外层目标期只进入一次最终评价：

```text
外层训练/内层选择：仅 target_issue 之前且通过 point-in-time 门的数据
外层评价：只在冻结模型和参数后读取 target_issue 的结果
滚动：完成该期不可变预测与评分后才可进入下一目标期
```

外层评价期不得参与训练、变换拟合、特征选择、超参数选择、阈值选择或模型开放判断。看见外层结果后不得重切折、补搜特征或更改主指标；任何方法变更必须使用新 preregistration 和新 release identity，旧运行原样保留。

## 5. 联合概率合同

每个实际运行模型必须对当前规则版本下的完整合法固定基数组合空间给出联合概率质量函数 `p(S)`，并满足：

- SSQ：`C(33,6) × C(16,1) = 17,721,088` 个合法组合；
- DLT：`C(35,5) × C(12,2) = 21,425,712` 个合法组合；
- 只给合法、区内不重复、基数正确、顺序无关的组合分配概率；
- 对每个组合 `p(S) >= 0`，完整空间概率和在预注册数值容差内等于 1；
- 对实际开奖号给出的概率必须有限且可复算；抽样器、Top-1000 或独立边际分类器不能代替完整联合分布；
- 分区相乘只在模型明确注册跨区条件独立时允许；有跨区项时必须直接证明整个联合空间的归一化；
- 任何边际模型只有经过确定、固定基数的联合概率投影并通过 known-answer 检查后才可统一评分。

M0 对全部合法组合等概率。M1 每区使用固定基数指数族：

```text
p(S | theta) = exp(sum(i in S) theta_i) / sum(|A|=k) exp(sum(j in A) theta_j)
```

分母必须用精确枚举或经 known-answer 验证的动态规划计算；`theta=0` 时逐组合严格退化为 M0。所有模型必须在小空间精确枚举、真实空间归一化审计、固定种子 replay 和非法输入测试中通过同一合同。

## 6. 模型与特征范围

| 模型 | Phase 3 身份 | 开放条件和允许终态 |
| --- | --- | --- |
| M0 均匀固定基数无放回 | 必须实现、运行；永久默认 Champion | 无开放门；实现或复算失败是阶段阻断问题，历史结果永不改变其 Champion 身份 |
| M1 静态贝叶斯加权子集 | 必须实现、运行的 challenger | point-in-time、先验/收缩、参数范围、内层选择和零参数退化均在结果前冻结；可为 `rejected`、`archived`、`shadow_candidate` 或 `indeterminate` |
| M2 动态状态空间加权 | 初始 `not_opened` | 只有结果前已有、独立冻结的前瞻证据证明 M1 仍有稳定 skill 且存在预注册动态性假设，并且合成资格/功效、错误预算和资源预算全部可承担时，下一唯一 release 才可开放；Phase 3 本次历史结果不能在同一 release 内触发开放 |
| M3 固定基数二阶交互 | 初始 `not_opened` | 只有结果前的机制证据或已验收统计输入指定有限交互，交互清单/符号/收缩预注册，合成资格达到冻结功效且无全量号码对搜索时才可开放；当前 Phase 2.1 `indeterminate` 本身不满足条件 |
| M4 GBDT 或正则逻辑回归边际模型 | 初始 `not_opened`，仅诊断 challenger | 只有特征全部通过 point-in-time、嵌套调参和搜索预算冻结、固定基数联合投影通过精确/归一化测试、且预注册错误预算允许时才可开放；不得以边际改善或 Top-1000 表现替代联合评分 |
| M5 热冷号、遗漏、Markov | 负控或复现实验 | 可运行但永远不能成为 `shadow_candidate` 或 Champion |
| M6 LSTM、Transformer、RL | 禁止 | Phase 3 不开放 |
| M7 文献模型 | 默认未开放 | 只有相同冻结输入、折、联合概率合同和评估器下完成复现后，才可在未来新 preregistration 审查 |

每个特征必须登记 `feature_id`、数学定义、原始字段、窗口、适用彩种/规则、`available_at` 证明、缺失策略、拟合范围、泄漏风险、负向测试、消融组和终态。未知机器/球组信息、当期销售额、奖池、中奖注数、开奖后说明、未来期结果、全数据归一化和无时间证明的当前视图一律不得进入预测。

## 7. 预注册、实验和评价

在首次正式历史模型结果生成前，预注册必须冻结：输入和规则身份、彩种、availability ledger、外层/内层折、主/辅/守护指标、模型与特征注册表、M2–M4 开放判定、搜索空间、种子派生、数值容差、错误预算、工作量预算、超时/重试、停止规则、分类门和 E2E registry。任何正式实验原则上只改变一个模型、特征族或训练因素；组合变更必须预先说明可归因理由。

主选择指标是相对 M0 的逐期联合 log-score skill。边际 inclusion Brier、校准、可靠性、分折稳定性、负控、敏感性和概率守护是辅助或阻断证据。Top-1000 只验证合法性、确定性、覆盖概率和输出接口；完整命中、排名或 Top-1000 覆盖率不能成为主选择指标、开放门或分类门。

`shadow_candidate` 的结果前门至少要求：按冻结错误预算计算的主 skill 下置信界越过预注册的正向边界；改善不由单一期、单折或单规则段驱动；校准、Brier、负控、敏感性和全部守护门没有预注册意义上的恶化；独立 replay 复算分类一致。确切边界和容差必须根据冻结可用样本与 Phase 2.1 功效边界在预注册中给出，不能根据 Phase 3 分数补写。违反基本概率、泄漏或完整性合同者为 `rejected`；过程合格但无继续价值者可为 `archived`；证据不能跨过冻结分类门者为 `indeterminate`。任何一个时间折的较好分数都不能单独产生 `shadow_candidate`。

正式实验 ledger 必须为每个注册实验记录 hypothesis、父实验、输入/代码/环境/配置身份、折、种子、预算、开始/结束、最后命令、日志、产物、指标和终态。成功、失败、超时、崩溃、反向结果、淘汰和未开放实验全部保留。失败证据不得删除、重写为成功或被同名运行覆盖。

历史回测的 challenger 科学分类只允许：`rejected`、`archived`、`shadow_candidate`、`not_opened`、`indeterminate`；M0 的 Champion 身份是与科学分类分离的永久运行角色。阶段级科学汇总另外允许 `no_shadow_candidate`。`shadow_candidate` 只表示有资格在未来另行批准的真实前瞻 shadow 中接受验证；它不能直接晋升 Champion、发布非均匀预测或进入投注。`indeterminate` 和 `no_shadow_candidate` 都是有效科学结果。

## 8. 独立 replay、交付包和最终验收

独立复核路径不得编写被复核实现或批准自己的模型分类。它只读取冻结输入和正式制品，独立重建外层折、availability 选择、特征快照和核心模型输入；用独立参考路径复算 M0/M1 小世界及选定真实期联合概率、逐期 log score、Brier、汇总指标和最终分类。相同输入/代码/环境/种子必须得到规范化相同制品；不同实现比较使用预注册容差。超差、泄漏或分类不一致形成 blocking finding。

Phase 3 最终交付包必须完整包含：

1. 本总体设计和机器可读 acceptance contract；
2. 预注册、输入 manifest、availability ledger 及 Phase 1/Phase 2.1/规则身份；
3. 模型注册表、特征注册表和泄漏风险清单；
4. 研究实现、统一 CLI、环境/依赖锁和配置；
5. 全部结果 Schema、单元/known-answer/集成/负向/E2E 测试；
6. 完整实验 registry、ledger、失败/超时/崩溃证据；
7. 分彩种、逐目标期、逐折的冻结预测和评价结果；
8. 联合 log score、Brier、校准、稳定性、负控、敏感性和 Top-1000 接口结果；
9. 模型分类及 `no_shadow_candidate`/`indeterminate` 汇总；
10. 独立 replay、独立 review 和未关闭 finding 清单；
11. 显式最终 evidence manifest；
12. 唯一 Phase 3 acceptance 制品与人工签署。

最终 validator 必须从 manifest 指定的输入、逐期预测、ledger 和 replay 制品重新计算核心指标、覆盖率、模型分类和 blocking finding 数，不能只信顶层报告或自动选择 `latest`。必需正向与负向 E2E 必须覆盖正常全链路、输入/规则篡改、未来/开奖后字段、外层污染、非法/负/不归一概率、遗漏或覆盖失败实验、历史越权晋级、replay 不一致、acceptance/manifest 篡改，以及没有 challenger 合格时的 `GO / no_shadow_candidate`。

## 9. GO、HOLD 与 FAIL / STOP

| 交付状态 | 条件 | 科学结果关系 |
| --- | --- | --- |
| `GO` | 输入/规则/PIT 闭合；M0/M1 和所有已开放模型通过合法性与隔离门；注册实验终态完整；指标、E2E、replay、manifest 和人工验收均通过；blocking finding=0；无越界表述或动作 | 可伴随 `shadow_candidate`、`no_shadow_candidate` 或 `indeterminate`；M0 仍是 Champion |
| `HOLD` | 可恢复的输入时间证据、冻结身份、工作量、运行、证据回传或独立复核不完整；或预算内证据不足但尚可按预注册恢复 | 保留现场、列出已完成证据和精确恢复条件，不得伪装为 GO |
| `FAIL / STOP` | 不可恢复的泄漏、非法联合概率、选择性删除/覆盖、证据伪造、结果后改门、Champion 越权晋级、非均匀发布或投注越界 | 与是否找到模型无关；这是过程/完整性失败 |

找不到有用模型、M2–M4 保持 `not_opened`、得到 `no_shadow_candidate` 或 `indeterminate` 均不构成 Phase 3 失败。

## 10. VPS 正式执行和恢复

准备环境只记录事实：远端主机/操作系统/架构、Python、逻辑处理器、内存、磁盘、依赖锁、代码提交和实测 benchmark。预注册依据 factual environment 与 benchmark 冻结本 release 的工作量预算；完成后记录实际完成的折、实验、世界数、墙钟、峰值资源和制品体积。不得设置或传播通用 VPS 架构、CPU、内存或磁盘数值门槛；只有实际命令出现的可复现错误才按失败/HOLD 分类。

每次未来正式运行使用全局唯一 `run_id` 和 `release_id`，只写新的、不可覆盖证据目录。目录必须包含显式 manifest、命令 receipt、事件日志、最后命令状态、输入/代码/配置/环境哈希和终态。不得使用 `latest`、通配符或目录修改时间选择正式证据。失败、重试和 acceptance iteration 都使用新 identity，并通过 `supersedes`/父子关系引用旧证据；旧证据永不删除或重写。

执行顺序为：本地/准备区核对与离线依赖准备 → VPS 隔离 worktree readiness → 结果前冻结 → 正式运行和可恢复 checkpoint → 独立 replay → 证据按 manifest 回传并逐文件重哈希 → acceptance iteration。监控断线先做只读恢复，不盲目重启。必须逐项确认 task ID、远端 worktree、branch、log、artifact path 和 last command state；精确规则是：

> if monitoring recovery cannot confirm task ID, remote worktree, branch, log, artifact path, and last command state, report `NEEDS_INPUT`, preserve the scene, and wait for explicit recovery information.

确认已有进程仍在运行时只恢复监控；确认受控中断且 checkpoint/ledger 完整时，才按同一注册命令恢复。无法证明时不得启动重复正式运行或覆盖现场。

## 11. 最终交接条件

只有唯一 acceptance 制品给出 `PASS / GO`、显式 evidence manifest 哈希闭合、独立复核无阻断、M0 Champion 身份未变且科学措辞通过人工检查后，Phase 3 才可交接。交接内容是可信历史研究基线和（如有）未来 shadow 候选清单，不是预测发布授权。

任何后续前瞻 shadow 必须是新的任务、合同、release、预测锁定账本和错误预算；Champion 晋升还需真实未来期的预注册证据与人工批准。Phase 3 本身永远不得进入生产服务、非均匀公开预测、自动购彩、收益主张或投注。
