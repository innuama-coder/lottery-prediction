# Phase 4 预测与 AutoResearch 闭环 MVP 总体设计

版本：1.1

状态：实施设计候选；本文件与详细计划合入固定开发基线后，后续预注册和机器验收合同必须按本文冻结点产生新身份

冻结上位合同：Git `0142530a55ddb1b302ecf770907e30e52df63c04` 中的 `ROADMAP.md`（SHA-256 `24ba28e72c33959a91e505fd518718bd0c948c84b7e2e4cd5591a26f0a0b0149`）和 `tasks/phase4/README.md`（SHA-256 `13b099c584c24c2bb7324f5fa852c9fac2dff7ad934245598eae2d117e701a75`）

继承 release：`P3-R07-2c0fa97-20260810-I01`

## 1. 目标、成功定义与边界

Phase 4 把已验收的 Phase 3 历史研究能力扩展成无界面、可部署、可调度、可恢复且可独立验收的双彩种 MVP。SSQ 和 DLT 各自完成 `prepare -> predict -> lock -> ingest -> verify -> unlock -> score -> autoresearch -> decision -> next forecast`；每个有效 Champion 或 shadow forecast 恰好发布 1,000 注合法、唯一的完整组合，按严格正且归一的完整空间联合概率排序，并保留真实 tie 等价类和完整空间 rank。每个新核验标签产生唯一 score 和唯一 AutoResearch decision。参数和允许特征的正向 fixture 必须真正改变下一期 shadow，而历史或合成证据不能修改 Champion。

成功的唯一工程终态是同一冻结 release 上 `P4-MVP-A01` 至 `P4-MVP-A21` 全部通过、blocking findings 为 0、递归证据闭合并由独立验收角色签发 `SYSTEM_MVP_GO`。成功可以同时伴随 `champion_by_game={ssq:M0,dlt:M0}`、模型状态 `baseline_only` 和八个 Top-K 单元 `insufficient_observation`；这不是效果改善声明。

非目标包括 Phase 5 的连续真实前瞻窗口和 SLO、Phase 6 的 Champion 晋升治理、发现真实规律、真实 Top-K lift、WebUI、公共 HTTP API、移动端、购彩、投注、支付、资金和收益系统。Phase 4 不等待未来开奖，不用历史回填冒充 forecast lock，不补造 Phase 1 历史 `available_at_utc`，也不修改 Phase 0–3 任何冻结制品。

### 1.1 权威、基线和受保护制品

权威顺序为上位合同、本文、`docs/plans/phase-4-detailed-plan.md`、结果前预注册/机器合同、实现和正式证据。无法按职责消解冲突时终态为 `HOLD_CONTRACT_CONFLICT`，只能在新 Git 固定提交同步修订；执行者不能选择宽松解释。

Phase 3 正式 acceptance 是 `artifacts/phase-3/P3-R07-2c0fa97-20260810-I01/acceptance/I01/acceptance.json`，当前 SHA-256 为 `415bfc69cc04704265e231fd7d6e36bd2daa06b970b0def30703c4a7f04570c9`，内容为 `PASS / GO / no_shadow_candidate`、blocking findings 0、M0 不变。Phase 4 复用 Phase 3 的固定基数概率模型、滚动前缀、guarded label capability、不可覆盖 attempt、checkpoint、bottom-up replay、角色绑定和显式 manifest 模式；Phase 3 的 Top-1000 只是诊断且没有 Phase 4 所需 full-space tie/rank，不能原样升级为产品事实。

受保护根为整个 `artifacts/phase-1/`、`artifacts/phase-2/`、`artifacts/phase-2.1/`、`artifacts/phase-3/` 及 Phase 0 的正式输入/结果/review/acceptance/manifest。实现只读这些树。`phase1-protected-inventory.json` 必须由固定代码提交的 `git ls-tree` 与逐文件 SHA-256、字节数生成，递归包含 Phase 1 baseline、pointer、releases、runs、reviews、live-validation、acceptance 和历史快照；readiness、canary 和最终 validator 操作前后重算路径集合、类型、大小和哈希，差一项即 `FAIL_PROTECTED_ARTIFACT_MUTATION`。

信任边界分为：数据保管者冻结 genesis/来源与修订；实现作者生成产品事实；运行者触发冻结程序但不能改合同；统计所有者冻结研究空间与错误预算；独立 oracle/replay 不导入产品核心；独立 reviewer 不写被复核实现；acceptance approver 不得是实现作者、运行者或 reviewer。研究执行进程没有数据采集、规则、label store、评分器、状态验收器、既有 forecast/score 或 Champion 的写能力。

## 2. 设计输入冻结登记

合同要求但不能在实现时临场决定的输入按下表关闭。产物都使用规范 JSON、显式相对路径和 SHA-256；“冻结前”允许开发，越过对应门后只能以新 identity 迭代。

| 输入 | 设计归属与选定值 | 最迟冻结点 | 权威产物 | 未冻结/不一致终态 |
| --- | --- | --- | --- | --- |
| 上位合同、代码、Phase 3 | 本节所列固定 Git/文件/release 身份 | T00 完成 | `config/phase4/authority-freeze.json` | `HOLD_AUTHORITY_IDENTITY` |
| Phase 1 genesis | `baseline-v1` 加 manifest/draws/observations 三个固定 SHA，即四项身份 | T00 完成 | `config/phase4/genesis.json` | `HOLD_GENESIS_MISMATCH` |
| 自有路径 | 准备 `artifacts/phase-4-prep/<prep-id>/`；staging `artifacts/phase-4-staging/<staging-id>/`；runtime `artifacts/phase-4-runtime/<runtime-id>/`；正式 `artifacts/phase-4/<release-id>/`；四者互斥且不得用 `latest` | T00 完成 | authority freeze | `HOLD_PATH_CONTRACT` |
| 来源与核验 | SSQ：`swlc` 官方主源、`ydniu` 核对源；DLT：`gdlottery` 官方主源、`ydniu` 核对源；仅 GET、保存原始响应；两源核心事实一致才 verified | T01 合同冻结 | `config/phase4/source-policy.json` | `HOLD_SOURCE_POLICY`/`HOLD_SOURCE_CONFLICT` |
| 日历与截止 | 每个 release 显式列目标期/开奖业务日；prepare 为前一日 12:00、predict/lock 为当日 17:00、硬截止 18:00、结果探测 22:30 及次日 08:30，均 `Asia/Shanghai` | 每个 calendar release 发布前 | `config/phase4/calendar-policy.json` 与 release | `HOLD_CALENDAR_AMBIGUOUS` |
| 时间合同 | `retrospective_sequence_safe`、`external_point_in_time`、`official_result_label` 三类互斥 | T01 | `config/phase4/time-contract.json` | `FAIL_TIME_CLASS_MIXED` |
| CLI/退出码/Schema | 第 6 节固定命令面；`0 PASS`、`20 HOLD`、`30 retryable`、`4 identity reuse`、`5 contract/evidence mismatch`、`6 security/causality failure`、其他非零 FAIL | T01 | `config/phase4/cli-contract.json`、`schemas/phase4/` | `HOLD_MACHINE_CONTRACT` |
| 模型/参数/特征空间 | 第 8 节 P4E1、P01–P04、F01–F02 的闭集 | 首次候选 proposal 前 | model/feature registries | `FAIL_UNREGISTERED_RESEARCH` |
| 概率/tie/指标 | 第 7、9 节算法、精度、公式、窗口和容差 | 首次 forecast/score 前 | model + metric contracts | `HOLD_UNSUPPORTED_TIE_SEMANTICS` 或 `FAIL_METRIC_CONTRACT` |
| alpha/预算/停止 | 第 10 节逐 game/family 初值、spending、零奖励、每周期上限 | 首次 experiment 前 | preregistration | `FAIL_ALPHA_BUDGET` |
| 修订策略 | 第 9.4 节影响闭包、无退款和幂等键 | 首次 ingest 前 | `correction-policy-v1` | `HOLD_CORRECTION_INCOMPLETE` |
| 资格效应/种子/功效 | 第 13 节离散菜单、确定选择规则、三域种子 | power 前冻结设计身份；正式 seed 在资格前 | `qualification-design/*.json` | `HOLD_DESIGN_NOT_POWERED` |
| full-rule oracle | 两彩种数学分布、八 K、独立源码/容差/预期数值 | 产品候选正式运行前 | `qualification-design/full-rule-oracle.json` | `HOLD_ORACLE_NOT_FROZEN` |
| 调度适配器 | 用户级 systemd timer，5 分钟 tick，应用层幂等/并发/补偿 | VPS readiness 前 | unit/timer 模板及安装审计 | `HOLD_SCHEDULER_AUDIT` |
| 依赖/部署/资源 | Python 3.12、哈希 lock/wheelhouse、venv、无服务依赖；预算由批准 workload benchmark 公式导出 | formal release 前 | lock、wheel manifest、benchmark、budget | `HOLD_DEPENDENCY_OR_BUDGET` |
| 角色与 acceptance | 第 14 节冲突矩阵及任务记录哈希 | 准备工作前/正式 release 前两级冻结 | actor assignments、acceptance contract | `HOLD_ROLE_CONFLICT` |

## 3. 技术路线和取舍

| 主题 | 选定方案 | 主要替代方案 | 理由、代价和失效条件 |
| --- | --- | --- | --- |
| 运行形态 | Python 3.12 无状态 CLI 子命令；每次动作短进程；无常驻服务 | Web 服务、Notebook、长驻 worker | 与仓库和离线 replay 相容、故障面小；启动开销可接受。若批准动作 p95 不能满足相对截止预算则 `HOLD`，不能绕开 lock |
| 持久化 | 文件系统不可变对象 + hash-chained 事件目录；`O_EXCL`、临时同目录写/`fsync`/原子 rename；派生 current view 可重建 | SQLite/Postgres、单个可覆盖 JSONL | 无数据库服务和 root 依赖，证据易取回；小文件较多。底层文件系统不支持原子 rename/fsync 或并发锁时 `HOLD_STORAGE_SEMANTICS` |
| Schema/序列化 | JSON Schema 2020-12；`P4-CJSON-1`：UTF-8、键排序、紧凑分隔、无 NaN/Infinity、Decimal/概率作规范字符串、无换行；ID 为规范 body SHA-256 | Protobuf、普通浮点 JSON | 延续仓库 jsonschema，跨 replay 稳定；体积较大。任何未规范 float 或未知字段 fail closed |
| CLI | `python -m lottery_system.phase4 <verb>`，同一 verbs 供 timer/operator/replay 使用 | 多脚本或内部 API | 单一合同面；参数较多但全部显式。隐式 `latest`、glob、mtime 选择非法 |
| 调度 | `systemd --user` `.timer/.service` 每 5 分钟运行 `schedule tick`；`Persistent=true`、`RandomizedDelaySec=0`，应用层计划账本 | cron、APScheduler、system service | 普通用户可审计且支持漏跑触发；不把 timer 当真值。用户 manager/linger 不可用且无法在权限内配置时 `HOLD_SCHEDULER_UNAVAILABLE` |
| checkpoint/恢复 | 每个动作阶段写不可变 checkpoint，绑定 run/plan/ledger-head/input/output hashes；恢复先全量重验，再从最后完整阶段追加 | 覆盖 checkpoint、盲重跑 | RPO 可计算且不重复 side effect；需要更多哈希。身份/头哈希不符即新失败 attempt，禁止续写 |
| 概率/排名 | 量化加性固定基数指数族；Decimal 80 位；整数 log-weight ticks；DP 归一与完整空间 score histogram；best-first Top-1000 | 任意 ML 概率、浮点 `isclose` tie、全空间内存排序 | 精确 tie/rank、严格正概率、预算可控；模型表达力受限。无法实现精确 histogram 的模型不接入或 `HOLD_UNSUPPORTED_TIE_SEMANTICS` |
| AutoResearch | 闭集参数/特征、一次单因素 proposal、likelihood-ratio e-process + family alpha-spending、最多 shadow | 逐 look alpha 拆分的 Hoeffding 门、无界 agent 搜索、结果后挑选 | Ville 控制任意停止错误且在 150 周期内保留功效；只适用于结果前冻结且相对 M0 的可计算 predictive density。未注册搜索或越权 Champion 为 FAIL |
| 独立 replay/oracle | `scripts/phase4_independent/` 只用 stdlib Decimal/itertools/JSON；不导入 `lottery_system.phase4` | 调用产品 validator | 防止同源错误；重复实现成本高。import graph 或作者冲突即 `HOLD_INDEPENDENCE` |
| manifest | 分层递归 manifest：对象 manifest -> evidence manifest -> review/validator closure；逐路径 SHA/bytes/role/parents | tar 文件存在或顶层 PASS | 可 bottom-up 复核并避免自哈希循环；manifest 多。缺失/额外/哈希差异即 HOLD/FAIL |
| 依赖/部署 | `requirements/phase4.lock` 全转移依赖哈希；构建 wheelhouse 后 `--no-index` 安装到 release venv；用户目录部署 | 在线 pip、容器/root 服务 | 不依赖隐式网络或 sudo；wheelhouse 较大。干净环境不能重建则 `HOLD_INSTALL` |

## 4. 组件架构、所有权和依赖方向

代码根固定为 `src/lottery_system/phase4/`，机器合同在 `schemas/phase4/`，配置在 `config/phase4/`，产品测试在 `tests/phase4/`，独立复核在 `scripts/phase4_independent/`。依赖只允许从控制面向纯领域层和端口，适配器实现端口；领域层不能导入 CLI、调度、官方网络或 acceptance。

| 组件 | 唯一事实所有权 | 只允许依赖 | 不得生成/验收的事实 |
| --- | --- | --- | --- |
| `identity_serialization` | canonical bytes、ID、hash vector | stdlib | 科学结论 |
| `rules_probability` | SSQ/DLT 合法空间、P4E1 概率、order/tie/rank、Top-1000 | identity、只读 model contract | 自验收 oracle |
| `data_chain` | Phase 4 genesis、data release、result revision/current view | identity、Phase 1 read port | forecast、score |
| `calendar_schedule` | calendar/schedule release 与 plan identity | identity、rules | 自动猜期号 |
| `forecast_lock` | forecast body、diagnostics、lock receipt | rules、data/calendar/model registries | label/score |
| `label_capability` | verified revision 到 scorer 的进程绑定不可序列化 capability | data chain、ledger、forecast lock | 训练/研究 |
| `metrics_correction` | score、window aggregate、修订影响闭包 | locked forecast、label capability、metric contract | 修改 forecast/alpha |
| `research_controller` | proposal/experiment/decision/candidate/diff/alpha ledger | score current view、registries | 采集、规则、score、Champion |
| `state_projection` | 三类状态的派生 current view | 不可变事件/registries | 原始事件 |
| `ledger_recovery_alert` | event chain、checkpoint、attempt terminal、alerts | identity、storage port | 业务判定 |
| `official_adapter` | 原始 GET observation receipt | source policy、transport port | verified result 单方真值 |
| `cli_orchestrator` | 命令顺序和退出码 | 上述 application ports | 领域算法 |
| `independent_replay` | 独立重算报告/finding | 冻结底层输入 | 产品写路径、acceptance |
| `final_validator` | A01–A21 底层判定 | replay、oracles、manifest | 生成被验收产品事实 |
| `acceptance_approver` | 人工签署和工程终态 | validator + review | 修改任何证据 |

数据流是 `Phase1 genesis + official raw observations -> Phase4 data release -> label-free training snapshot -> probabilities/ranks -> forecast diagnostic -> lock -> verified result revision -> guarded unlock -> score/window -> decision/experiment -> candidate -> next shadow`。控制流由 schedule plan 触发同一 CLI；每一步先解析固定 identity，取得单 plan lease，核对前置终态，追加 `started`，生成不可变对象，再追加唯一终态。组件不能同时生成并独立验收自己的事实。

### 4.1 对象状态机与唯一终态

计划动作：`planned -> claimed -> succeeded|late_completed|missed_deadline|blocked|failed|skipped_idempotent`。forecast：`prepared -> generated -> locked -> published` 或任一步到 `failed|missed_deadline|rejected`；`locked` 后 body 永不变。结果：`observed -> corroborated -> verified -> unlocked`，冲突到 `source_conflict`，修订产生新 revision 而非回退旧状态。实验：`proposed -> registered -> running -> qualified -> shadow_candidate` 或 `rejected|archived|failed|timeout|budget_exhausted`。decision：`started -> no_change|rejected|archived|shadow_candidate_proposal|remediation_completed|failed`。同一对象只能有一个 terminal event；重试创建 attempt 子身份，逻辑 action 的 canonical attempt 选择最早完整成功者，失败全部保留。

### 4.2 主键与身份派生

内容对象 ID 为 `kind-v1:<sha256(P4-CJSON-1(body_without_id_and_derived_fields))>`。正式系统 release 使用可读路径身份 `P4-R<两位序号>-<implementation_commit前12位>-<YYYYMMDD>-I<两位iteration>`，并同时保存整个 release descriptor 的 `release_descriptor_sha256`；二者任一不匹配即拒绝。`data_release_id` 绑定 genesis、直接前驱和本批 revisions；`calendar_release_id` 绑定政策、显式 entries 和时区数据库事实；`schedule_release_id` 绑定 calendar 与 actions；计划键严格为 `(game,target_issue,action,planned_at_utc,schedule_release_id)`。

forecast 使用无循环的两阶段派生：先对含 game/issue/rule/model/model release/config/feature snapshot/data release/training cutoff/calendar/schedule/seed/metric contract/1,000 tickets，但排除 `forecast_id`、`tie_group_id` 和 lock 字段的 core body 求哈希得到 `forecast_id`；再由该 `forecast_id` 生成各 `tie_group_id`，最后对完整 bundle 求 `forecast_bundle_sha256`。独立 replay 必须重复同样顺序。`result_revision_id` 兼容 Phase 1 core fact hash并绑定来源 observations；`score_id` 的键为 `(forecast_id,result_revision_id,metric_contract_id)`；相对 skill 额外绑定 comparator forecast；decision 键为 `(game,target_issue,result_revision_id,decision_contract_id,trigger)`；修订幂等键固定为 `(game,issue_id,new_result_revision_id,correction_policy_version)`；candidate ID 绑定 parent model/config、单一 canonical diff、code/data/feature/qualification identities；alpha event 键绑定 `(game,hypothesis_family,decision_id,experiment_id,ordinal)`。

## 5. Phase 1 genesis 到 Phase 4 追加链

首个 Phase 4 data release 同时声明：`base_phase1_release_id=baseline-v1`、`base_phase1_manifest_sha256=0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1`、`base_phase1_records_sha256=f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1`、`base_phase1_observations_sha256=dc974863c845da1e895ecf623bc6e878ba6aa6710c902357bce68ad5e661966e` 和 `previous_phase4_release_id=null`。它在自有 release 中按 manifest 引用或复制内容寻址的 baseline blobs，离线重算必须得到相同三哈希。后继保持四项 genesis 不变并严格引用直接前驱；每个预测绑定实际读取的链头。

开发、oracle、development-seed 和 power-confirmation 只写 `artifacts/phase-4-prep/<prep-id>/`；runtime 写 `artifacts/phase-4-runtime/<runtime-id>/data-releases/<data-release-id>/`；canary 只写 `artifacts/phase-4-staging/<staging-id>/raw/` 和 `receipts/`；正式 qualification 只写 `artifacts/phase-4/<release-id>/`，并把冻结输入复制到其 `inputs/`。四个根的 ID 均由 controller 显式发放，路径解析后不得互为祖先、不得复用 ID，也不得用 runtime 的可变 current pointer 决定正式身份。派生 `current-view.json` 只是一份可删除重建的投影，不能作为血缘父。

断链、父不存在、同一前驱分叉被拼接、换 genesis、空 baseline、Schema 相容但内容哈希不同、target issue 倒退或 revision 旧链头复活均 `HOLD_DATA_CHAIN`；若已据此锁 forecast 则该 release `FAIL_CAUSAL_INPUT`. 正当修订追加新 result revision/data release、标记旧 current view superseded 并启动第 9.4 节闭包。换 Phase 1 基线必须发布新上位合同和新的 Phase 4 genesis family；不能修订现有链。

## 6. CLI、配置与部署接口

统一入口为 `python -m lottery_system.phase4`，命令固定为：`contract validate`、`data genesis|ingest|verify|release|current`、`calendar build|validate`、`schedule build|tick|audit`、`forecast prepare|generate|lock|show`、`result unlock`、`score one|window|correct`、`research decide|run|resume`、`state project|show`、`replay release`、`validate unit|e2e|final`、`release assemble|accept`。所有改变状态的命令必须显式传 `--runtime-root` 或 `--release-root`、对象 identity、config/contract identity、`--clock`；正式模式禁止 `latest`、通配符和默认网络。只读查询可由显式 current-view receipt 解析，但输出同时显示其底层 head hash。

配置是不可变 registry，不读环境变量中的科学参数。凭据不是系统所需输入：官方接口均公开 GET；transport 拒绝非 allowlist host、重定向跨源、写方法、Cookie 登录和未登记代理。部署为用户拥有的 release venv 和数据根，权限建议目录 `0700`、制品 `0600/0444`；不要求 sudo、Docker daemon、数据库、消息队列、云存储或其他隐式服务。

## 7. SSQ/DLT、概率、tie、rank 与 1,000 注算法

规则身份继承注册的 `ssq-ns-33c6-16c1-v1`（`C(33,6)*C(16,1)=17,721,088`）和 `dlt-ns-35c5-12c2-v1`（`C(35,5)*C(12,2)=21,425,712`）。每区号码严格升序，区内唯一，跨 game/rule/config/data/alpha/metric/status 的 join 全部拒绝。两彩种只能共享纯函数代码。

Phase 4 可接入概率族限定为 P4E1：每区固定基数指数族，号码 `i` 的 canonical log-weight 是整数 tick，固定 scale `1/1024`。唯一规范化规则是从全向量减去号码 1 的原始 tick，使 `w_1=0`；规范化后每个 `w_i` 必须在 `[-4096,4096]`，否则该配置拒绝。扩大的边界是 qualification 正控可完成性所需的表示合同，不授权任意连续参数或跳过 registry。组合未归一 log mass 是所含 ticks 之和；分区 partition function 用 Decimal 80 位的 elementary-symmetric DP 计算 `Z_k`，联合概率为两个分区概率乘积。所有合法组合因 `exp(w_i/1024)>0` 而严格正；归一证明为两个 DP 分区和之积，在 `abs=1e-45, rel=1e-40` 内等于 1。两种彩票每注共选 7 个号码，规范组合 score 在 `[-28672,28672]`，任意两注 log-mass 差至多 56；因此最小规范概率大于 `exp(-56)/21,425,712 > 1e-32`，50 位小数 Decimal 序列化不会把正概率写成零。log score 以 Decimal 80 位计算；零、负、溢出、NaN、Infinity、下溢成零或容差外结果拒绝。

从训练前缀到 ticks 的唯一估计也冻结。先按 P02 取最近 `50|100|150` 或 expanding 的严格历史窗口；P03 为 `none` 时每期权重 1，为半衰期 `h` 时距训练截止 `age` 期的权重为 `2^(-age/h)`。令 `c_i=sum(weight*1[i appeared])`、`n=sum(weight)`、`e=n*k/N`，按 Phase 3 可复算收缩式计算 `r_i=ln((c_i+lambda)/(e+lambda))/max(lambda+e,1)`，其中 lambda 只能取 P01 闭集。原始 tick 为 `round_half_even(1024*r_i)`，再减去号码 1 的 tick；P04 或 F02 的注册整数 offset 只能在此后相加，再次以号码 1 锚定并检查 `[-4096,4096]`。禁止 clipping，因为 clipping 会改变未登记的等价类；越界候选直接 rejected。M0 不经过估计，全部 ticks 固定为 0。

`probability_order_key` 是 `P4Q1024-<score+28672 的 5 位十进制>`，其中 joint score 为当前彩种前、后两个分区所选号码 ticks 的整数和，范围 `[-28672,28672]`。字符串降序与概率降序严格一致。`tie_key=sha256(model_contract_id|probability_order_key)`；`tie_group_id=sha256(forecast_id|tie_key)`，摘要碰撞时必须比较完整 key。只有 order key 完全相同才 tie；禁止 `isclose` 聚类、运行时 float 文本或输入遍历顺序。

完整空间 tie histogram 使用 sparse integer map：每区可用按 `(position,chosen_count,tick_sum)` 的 DP，独立 oracle 另以该区合法组合直接枚举；随后只对实际存在的前/后区 score keys 做精确卷积。必须分别证明分区 histogram 计数为 `C(N,k)`、联合计数为完整空间 `M`。对 score `s`，`tie_group_size=h[s]`，`tie_rank_lower=1+sum_{u>s}h[u]`，`tie_rank_upper=sum_{u>=s}h[u]`，`tie_midrank=(lower+upper)/2`。M0 全 ticks 为 0，故一个 group 的 size 正好是完整空间大小、rank `[1,M]`、midrank `(M+1)/2`。

Top-1000 使用两个层次的确定 best-first：各区按 `(tick_sum desc, combination lexicographic asc)` 产生前 1,000 个固定基数组合；再在前/后区单调乘积格上以 heap 按 `(joint_tick_sum desc, full_ticket lexicographic asc)` 弹出恰好 1,000 个。证明：任一分区索引大于 1,000 的 pair 至少有 1,000 个不低分且 tie-break 更早的 pair，不可能进入规范前 1,000。输出位置 1–1,000 唯一，Top-10/100/200 是同一数组严格前缀；每行同时写 display position、probability/order/tie 字段及全空间 rank。若 `U_f,U_b` 是两个区实际 reachable score 数，histogram DP 为 `O(sum_zone N*k*U_zone)`、精确卷积为 `O(U_f*U_b)`、内存为 `O(U_f+U_b+U_joint)`；独立枚举 oracle 至多枚举 `C(35,5)+C(12,2)` 个分区组合而不是 2,100 万张整票。Top-K 为 `O(K*(k_front+k_back)*log K)`。T10 对边界 ticks、菜单三档、M0、A10 full-rule 和 adversarial unique-sum fixtures 核对 histogram/Top-1000/Decimal；T15 对两条真实规则各 20 次记录 p50/p95、峰值 RSS、reachable counts、bytes 和 hash。若批准预算不能正确完成，候选未接入或工程 `HOLD`，绝不近似 rank。

## 8. Champion、shadow 与候选边界

M0 永久存在且每 game 独立。Phase 4 不提供 `promote champion` 命令。`champion_by_game` 事件只能由 genesis contract 写入 M0；任何历史/合成路径写新 Champion 为 `FAIL_GOVERNANCE_BYPASS`。shadow 必须已有 `shadow_candidate` 资格 receipt，绑定 game、parent、candidate config、qualification 和 expiry；过期、修订待处理或守护失败不进入 next forecast。

允许研究空间是闭集：P01 `shrinkage in {1,5,20,100}`；P02 `training_window in {50,100,150,expanding}`；P03 `recency_half_life in {26,52,104,none}`；P04 单号码 raw offset 为整数且 proposal 应用、重新锚定后全部 canonical ticks 必须在 `[-4096,4096]`，每个 proposal 最多改变一个预声明 tick group。F01 是 strictly-earlier `prior_draw_frequency`；F02 是最多 8 类的 `external_context_categorical_v1`，只有每个原子值有真实 `available_at_utc < prediction_locked_at` 才可启用；真实 runtime 默认禁用 F02，合成 useful-feature fixture 可用独立 fixture capability。一次 experiment 只能修改一个 P 或 F family，diff 是规范 JSON Patch 的排序闭集，禁止任意代码、任意表达式、未知字段、全量特征搜索和高容量模型。

`candidate_id` 由 parent model/config、单一 diff、game、hypothesis family、code/data/feature/prereg identities 派生。生命周期严格为 `proposal -> registered -> historical/synthetic qualification -> rejected|archived|shadow_candidate -> prospective shadow`。正向参数/特征 E2E 都必须比较 parent/child config 和下一 forecast 的 probability vector/Top-1000 hash；只有文件 diff 而输出未变为失败。

## 9. 时间、锁、评分、窗口和修订

### 9.1 三类互斥时间合同

`retrospective_sequence_safe` 只适用于 Phase 1 历史开奖号特征：source issue 必须严格早于 target，拟合只读前缀；不要求也禁止补造 `available_at_utc`。`external_point_in_time` 适用于外部时变预测字段：每个值必须保存原始证据并满足真实 `available_at_utc < prediction_locked_at`；当前页、抓取时间倒填、开奖日推断均不合格。`official_result_label` 只能在不可变 forecast lock 后，由两源核心事实一致的 verified revision 授予：`prediction_locked_at < result_verified_at <= label_unlocked_at`。canary 已公开历史结果不成为前瞻证据。

trainer spawn 在解析 label-free payload 前进入永久 quarantine，不能打开 runtime/release label-bearing 树、派生进程或持有 scorer capability。scorer capability 为 PID-bound opaque object，不可序列化；label store 在首次读取号码前重验 ledger 连续头、forecast/lock 当前哈希、release/run/game/issue/model/data/calendar/metric/result revision 全身份及最新 matching lock。锁后 mutation、pre-lock read 或错误 capability 是不可恢复因果失败。

### 9.2 forecast 诊断

诊断键为 `(forecast_id,metric_contract_id)`，在 lock 前计算并与 forecast 一起锁定：完整空间 normalization proof、ticket 合法/唯一、Top-K 嵌套、`coverage@K=sum_{position<=K}p(ticket)`、order/key 一致、histogram 总数、每行 tie/rank、M0 单 group。诊断绝不绑定 result revision，修订时不重算。

### 9.3 逐预测 score 与窗口公式

score 键为 `(forecast_id,result_revision_id,metric_contract_id)`；relative skill 另含同 issue 的 `comparator_forecast_id`，固定为该 game 锁定 Champion。公式为：`hit@K=1[y in locked prefix K]`；`joint_log_score=-ln p(y)`；`skill=ln p_model(y)-ln p_champion(y)`；每区 inclusion probability由固定基数 DP 得到，`inclusion_brier=(sum_i(q_i-1[i observed])^2)/(N_front+N_back)`，同时保存两区分值；实际组合 rank 使用第 7 节 histogram，`midrank_percentile=(tie_midrank-0.5)/M`。

窗口主键为 `(game,model_id,comparator_champion_id,model_release_id,window_id,metric_contract_id)`，window contract 显式列唯一 issue 集合和每期 current score ID。一个 issue 只计最新 verified revision一次。所有聚合最小样本量固定 30；不足只写 aggregate `insufficient_observation` 和 count，不写伪数值，也不代填 Top-K 科学状态。满足后保存均值 log score/skill/Brier/rank percentile、累计 hit rate 与 Wilson 95% 区间、10 个左闭右开等宽 inclusion-probability bins（最后含 1）的 reliability/ECE、相邻 forecast inclusion vector 的平均绝对变化 stability。reliability 把窗口内每期、每个分区、每个号码的 `(inclusion_probability,observed_0_or_1)` 展平后分箱；stability 只比较同 game/model/config 的相邻 forecast，并对全部前后区 inclusion 分量取平均绝对差，config 变化处断开而不跨接。空 bin 保存 count 0，不伪造 rate。数值容差 Decimal 指标 `abs=1e-40,rel=1e-35`，最终展示 float 不参与验收；零概率或错误 comparator 直接拒绝。

独立数值 oracle 使用 `itertools` 小空间和 Decimal 直接枚举，不导入产品概率/metric；真实规则用独立 DP。Phase 3 的 600 条实际概率/log score/Brier 是回归输入，但 Phase 4 新 tie/window 字段不能由 Phase 3 汇总自报验收。

### 9.4 官方核验和修订闭包

主源与核对源的 `(game,issue,date,sorted numbers)` 完全一致后生成 verified revision；网络失败为 retryable observation terminal，单源为 `pending_corroboration`，冲突为 `source_conflict`，均不 unlock。官方主源同 issue 核心事实变化且新核对完成时，新 revision 引用 `supersedes_revision_id`。

修订事务以固定幂等键执行并追加：新 data release/current-view replacement；每个受影响 forecast 的 corrected score；所有含该 issue 窗口的 corrected aggregate；列出旧/新 score/aggregate/decision/experiment/candidate 的 impact manifest；`trigger=official_result_revision` remediation decision；依策略将依赖旧标签的候选 `archived_pending_requalification` 后从同一冻结输入重放或重新资格；修复完成前阻止其进入未锁 forecast。旧结果、旧派生物、实验、spending 和已锁 forecast 全保留；alpha 不退款、不重置，修订不是新独立检验机会。部分传播、重复 score/spend、漏列影响、旧 revision 复活或同 issue 多计观察 fail closed；checkpoint 恢复必须得到相同闭包和 head hash。

## 10. AutoResearch、顺序检验与恢复

假设族逐 game 分为 `static_parameter`、`slow_drift_parameter`、`context_feature`，初始 alpha wealth 均为 `W0=0.006`，合计每 game `0.018`。family 内第 t 个注册实验（1-based）spend `alpha_t=W0/(t*(t+1))`，首个实验 `alpha_1=0.003`，累计永不超过 W0；Phase 4 reward 固定为 0，修订也不退款。每期开奖/合成周期最多注册 1 个实验，优先顺序为 preregistration 中最早 eligible proposal 的 `(family,canonical_diff)`；每 family 每 decision 至多一个 proposal。wealth 不能为负，spend 在 experiment start 前原子追加；重复 identity 返回原 event。三个 family 的终身并集错误上界为 `0.018 < A07 0.05`，留下了使 1,000-sequence aggregate uniform 门高概率通过所需的余量，而没有降低 A07。

每个 experiment 最多看 150 个周期，最早 30；在观察第 t 个结果前，preregistration 必须已冻结 Champion 条件分布 `p0_t`、候选条件分布 `p1_t` 及其输入。定义 likelihood-ratio e-process `E_n=product_{t=1..n} p1_t(Y_t)/p0_t(Y_t)`，实现保存 Decimal 80 位 `log_E_n=sum(log p1_t(Y_t)-log p0_t(Y_t))`。Phase 4 Champion 固定为 M0；在注册 null 下 `p0_t` 严格为正，且 `p1_t` 只依赖严格先前数据/冻结 context，故 `E_n` 是非负均值 1 martingale。实验在任一 `30<=n<=150` 首次满足 `E_n >= 1/alpha_t`，且正确方向/config、稳定性、Brier、概率、时间和治理 guards 全通过时 proposal；否则到 150 停止。Ville 不等式给出 `P0(exists n:E_n>=1/alpha_t)<=alpha_t`，无需也禁止再把 `alpha_t` 逐 look 拆分。跨实验按可预测注册顺序应用 alpha spending 和 union bound。每个 look、未过门和停止均写入；最大 150、wealth 不足、无 eligible hypothesis、guard hold、effect wrong direction、timeout 分别得到机器终态。`experiment_count=0` 的 decision 理由只允许 `no_eligible_hypothesis|budget_exhausted|guard_hold|scheduled_no_change`。

checkpoint 绑定 decision/experiment、已消费 score IDs、look ordinal、sufficient statistics、alpha event、RNG state（实际为派生计数器）、ledger head 和输出 hashes。恢复先验证所有绑定，再从下一个未提交 look 追加；spend、proposal、decision、next shadow 均以 identity 去重。checkpoint 不完整保留失败 attempt，新 attempt 引用它但不能重复 side effect。任何永远 `no_change` 的实现会被参数/特征正向 E2E 和 A09 拒绝。

## 11. 日历、UTC 映射和实际调度

calendar release 不用星期递推猜期号；它显式列出经来源政策核对的 `(game,target_issue,draw_business_date,rule_id)`，必须严格递增且不得与官方已知状态冲突。所有业务时间使用 IANA `Asia/Shanghai`；转换使用 `zoneinfo.ZoneInfo`，保存 local wall time、zone name、UTC `Z` 和当时 tzdata/解释器身份。中国标准时间当前无 DST，但实现仍以 zoneinfo 转换并独立重算，不硬写服务器本地时区。

每个 entry 产生 `prepare`、`predict_lock`、`result_probe_primary`、`result_probe_compensation` 和在 verified 后动态幂等触发的 `unlock_score_research` 计划。硬截止 18:00 CST；17:00 action 若在 18:00 前启动可完成并锁，否则 `missed_deadline`，绝不补造有效 forecast。结果 22:30 未得则次日 08:30 补偿；仍无结果记录 `result_pending` 告警，operator 可通过新显式 probe plan 重试，不改变原计划。

用户级 systemd timer 每五分钟运行 `schedule tick --clock system`。service 固定 absolute venv executable、release config、工作目录、`UMask=0077`、`Type=oneshot`；timer 固定 `OnCalendar=*-*-* *:0/5:00 Asia/Shanghai`、`Persistent=true`、`AccuracySec=1s`、`RandomizedDelaySec=0`。应用以 plan key 的原子 lease 拒绝并发；重复 trigger 返回同 run ID 或 `skipped_idempotent`。一个 game 的 action/lease/alert 分离，不能阻塞另一 game。安装审计反查 `systemctl --user cat/show/list-timers` 的 unit hash、ExecStart、WorkingDirectory、时区、下次触发、Persistent 和并发策略；它只是快照，不声称持续 SLO。虚拟时钟覆盖准时、早触发、迟到、漏跑、重启、并发、截止后、期号倒退和跨 game 故障，不等未来开奖。

## 12. 状态、告警、故障和安全

工程状态键 `(system_release_id)`，Phase 4 只写 `HOLD|FAIL|SYSTEM_MVP_GO`。模型键 `(game,model_id,comparator_champion_id,model_release_id,window_id)`，只写 `baseline_only|shadow_candidate`；`prospective_improvement_confirmed` 禁用。Top-K 键 `(game,K,model_id,comparator_champion_id,model_release_id,window_id)`，Phase 4 真实单元只能 `insufficient_observation`；`no_confirmed_lift|confirmed_lift` 禁用。Schema 删除任一维度、跨 game/K/window 外推或全局 `improved=true` 均拒绝。

结构化 alert 至少含 `alert_id,severity,game,object_id,reason_code,first_seen,last_event_id,runbook_ref,ack_state`。reason codes 固定覆盖：source/network/conflict/revision、calendar ambiguity、late/missed deadline、concurrent trigger、lock mutation、label denial、probability/tie/rank、metric/comparator、alpha exhausted/negative、checkpoint mismatch、disk/write/hash、dependency、protected artifact、manifest/replay/review。日志不得含凭据或完整外部响应；原始公开响应按 hash 保存并限制权限。

故障矩阵：可重试网络/单源缺失为 HOLD/pending；确定的非法输入、预算不足、依赖/磁盘/进程中断为 HOLD 并可从 checkpoint 恢复；因果泄漏、锁后改写、选择性删除、证据伪造、越权 Champion 和受保护树变化为 FAIL。已锁 forecast 和 ledger RPO=0；单个未提交计算的 RPO 是最近完整 checkpoint；资格恢复演练 RTO 门为 `max(60s,4*recovery_action_p95)` 且不得越过 fixture 截止。真实连续运行、95% lock、24h score、4h 控制面 RTO 属于 Phase 5，不由 Phase 4 readiness 快照声称。

安全依靠最小权限、公开只读 allowlist GET、no-shell 参数、路径 resolve/relative 检查、O_EXCL、hash chain、actor conflict、trainer/scorer capability 隔离和正式 qualification 禁网。应用不需要 secret、root 或 sudo；如果部署环境要求这些才能实现所选语义，readiness 为 HOLD。

## 13. Qualification design、功效、oracle 和资源预算

A07–A09 使用小空间 `N=10,k=3`、每彩种/每世界 1,000 序列、每序列恰好 150 周期。uniform 的 sequence-level 事件为是否在任一 family/任一 look 出现错误 shadow proposal，率必须 `<=5%`。正控分别为静态偏差、慢漂移和 useful feature；逐 game/类型的 sequence-level recovery 是在 150 周期内由正确 family、正确方向/config 产生 proposal，全部非统计 guards 通过且 next shadow hash 改变，率各 `>=90%`。正控 fixture 只启用一个预注册的正确-family 首实验，因此使用 `alpha_1=0.003`，而不是结果后搜索多个候选。

生成器和效应菜单完全结果盲。令 `v=[1,1,1,0,0,0,0,-1,-1,-1]`，幅度菜单按弱到强固定为 `q in [1536,1792,2048]` ticks；应用 `q*v` 后以号码 1 重新锚定，最强配置落在 `[-4096,0]`。静态从周期 1 起固定为 `q*v`；slow drift 在周期 t 使用 `round_half_even(q*min(t,100)/100)*v`；feature 使用严格交替且每 150 周期 75/75 平衡的 context bit，context 1 使用 `q*v`、context 0 使用 `-q*v`，context 在抽取开奖号前固定。每期从相应固定基数指数分布独立抽取一个组合；uniform 使用 120 个组合等概率。完整 design 候选顺序为三个幅度的 `(static=q,slow=q/ramp100,feature=q)`，再按 canonical config bytes。development 必须运行全部候选；确定选择规则先保留 T10 中 uniform/positive aggregate 解析下界均 `>=0.99` 且 positive sequence 解析下界 `>=0.93` 的候选，再要求 product 与 independent reducer 的逐 sequence terminal/LR/guard/hash 一致率 100%，最后取排序首项。development 的经验 rate 只作描述和错误诊断，不参与强弱选择；首项不一致时 HOLD 修实现，不能跳到更强 q 掩盖错误，也不能在 power/formal 结果后改变菜单。

在任何 development simulation 前，T10 的独立解析 feasibility checker 对每个候选固定计算。对正控令 `Z_t=log(p1_t(Y_t)/p0_t(Y_t))`、`mu=sum_t E1[Z_t]`、`R_t=max_y Z_t-min_y Z_t`、`h=log(1/0.003)`；当 `mu>h` 时，独立性和 Hoeffding 给出 sequence recovery 的保守下界 `p_seq >= 1-exp(-2*(mu-h)^2/sum_t R_t^2)`，因为 terminal `log E_150>=h` 已蕴含曾越过门。最弱 `q=1536` 的静态/feature 有 `mu>=176.99,sum R_t^2<=12150.01,p_seq>=0.9919`；最难的 100 周期 slow ramp 有 `mu>=103.4133,sum R_t^2<=6790.653,p_seq>=0.93954`。uniform 由 Ville + 三 family spending 得 `q_seq<=0.018`。把这些界代入冻结的 1,000 序列门，`G0(q)=P[Binom(1000,q)<=50]` 和 `G+(p)=P[Binom(1000,p)>=900]`，得到 `G0(0.018)>0.9999999999`、最坏 `G+(0.93954)>0.99999950`。checker 固定 Decimal 80 位、枚举全部 120 组合、对 amplitudes/ramp 逐周期计算、binomial recurrence、向不利方向取整并输出公式输入/中间量/source hash；这些是设计存在通过机会的结果盲解析 certificate，不替代 T13 独立模拟。

种子派生为 `uint256(sha256('P4-SEED-v2|' + design_id + '|' + domain + '|' + game + '|' + world + '|' + sequence_ordinal))`；domain 严格为 `development`、`power-confirmation`、`formal-qualification`，集合哈希和交集 0 写入制品。development 每 game/world/design 固定 2,000 序列，仅能用于上述确定选择。选出 candidate design 后先冻结 candidate、产品 controller command/code hashes；独立 power driver 不导入产品核心，用未参与选择的 power-confirmation domain 生成 draws 并通过黑盒 CLI 驱动该冻结 controller，对 8 个 game/world cell（2 个 uniform、6 个正控）各运行 `R=20,000` 序列；独立 reducer 再从 raw draws 和冻结 `p0_t,p1_t` 重算每个终态。qualification-design 的正式门对象相应是 2 个 uniform aggregate gates 和 6 个 positive aggregate gates；不得把两个 game pooling 后掩盖单元失败。

power-confirmation 对每个 cell 先报告 sequence-level 计数、率和 simultaneous 95% Clopper–Pearson 区间：8 个双侧区间用 Bonferroni tail `eta=0.05/(2*8)`，端点由 Decimal 80 位 binomial CDF 单调二分到绝对宽度 `1e-12`。随后才计算“正式聚合门预计通过概率”：它是一个新的、与 power 数据独立的 1,000-sequence formal batch 在冻结生成器/控制器下通过合同计数门的概率，不是 power batch 的恢复率，也不是将来一次 formal 结果。uniform 点估计/区间为 `G0(q_hat)` 和 `[G0(q_U),G0(q_L)]`；positive 为 `G+(p_hat)` 和 `[G+(p_L),G+(p_U)]`，均用 Decimal 80 位 binomial recurrence。每个 cell 必须同时满足 sequence interval 的不利端 `q_U<=0.05` 或 `p_L>=0.90`、aggregate point estimate `>=0.90`、aggregate simultaneous lower bound `>=0.90`，否则 `HOLD_DESIGN_NOT_POWERED`。选择完成后的 power 数据不能反馈到同一 design；任何控制器、幅度、ramp、alpha、重复数或算法变更都产生新 design ID，回到 development 并使用由新 ID 派生的新 seeds，旧失败原样保留。

T15 只在 T13 PASS 后冻结 formal master seed 和全部 8,000 序列身份（2 games × 4 worlds × 1,000）。T16 对每个 formal cell 只报告实际 success count、sequence rate、门 `<=50/1000` 或 `>=900/1000` 的布尔结果和底层 terminals；一次 1,000-sequence 的实际率不得命名为“正式门通过概率”。重试只能恢复同一序列 checkpoint，正式结果失败不能换 seed、效应或 design 重跑。

A10 full-rule 生成分布使用独立数学规格 P4E1：对每个长度 `N>=8` 的分区，原始 ticks 在位置 1–4 依次为 `32,24,16,8`，位置 `N-3..N` 依次为 `-8,-16,-24,-32`，其余位置为 0，再按 P4E1 减去位置 1 的 tick 规范化；这一定落入 `[-64,0]`。SSQ 后区 `N=16`、DLT 后区 `N=12` 也使用同一明确规则，不是从候选输出反推。能力用 candidate 是结果前 fixture registry 中恰好声明上述 ticks 的 `P4E1-full-rule-known-answer-v1`，产品路径必须自行从该 config 生成 Top-K，oracle 不读取其输出。oracle 在正式 candidate 前冻结源码 hash、rule IDs、Decimal 80 位、八个 K 和每单元 `M0=K/M`、candidate 覆盖、差值及误差界；不导入产品模块。八单元 candidate 真覆盖都必须严格大于 M0，产品/oracle 超容差或任一不优即 A10 FAIL，不换分布、K 或 oracle。

benchmark 覆盖 `probability_rank_forecast`、`one_cycle_e2e`、`qualification_sequence`、`formal_8000_sequences` 的代表 batch、`correction_closure`、`checkpoint_resume`、`independent_replay`、`final_validator`，每项 warm-up 后 20 次记录 p95、RSS、bytes。预算公式为各批准单位数量乘 p95/bytes 后加 25%，timeout 为 `max(60s,4*p95)`，并行度从 `{1,2,4}` 的开发 benchmark 中取在证据 hash 相同前提下最小墙钟者；checkpoint 每 10 序列及每 target action。预算只裁决批准 workload 能否正确完成，不设 CPU/内存/磁盘/架构通用门。超预算先允许用新 benchmark identity 调整并行、batch/checkpoint，不改序列、算法、阈值或证据；仍不能正确完成则 HOLD。

## 14. 正负 E2E、独立复核和 release 装配

正向 E2E 为两彩种完整 cycle、参数调整、特征调整、no-change、断点恢复、修订全链、M0 tie、跨 K tie、systemd 虚拟 tick。负向至少覆盖三类时间混用、历史 PIT 补造、未来字段、pre-lock label、锁后 mutation、错误 identity/hash、非法组合/概率/order key、非传递近似、重复 Top-1000、跨 game 状态/alpha、直接 Champion、预算耗尽后搜索、重复 spending、截止后补 forecast、并发/漏跑/倒退期号、data 断链/换 genesis/空 baseline、source conflict/network、错误 comparator/window/revision、partial correction、checkpoint tamper、manifest/replay/acceptance tamper、Phase 1 写入和越界科学措辞。每例在隔离 staging 做真实单点 mutation，以独立进程观察注册 guard code；无关异常不算命中。

正式 release 的 `contracts/`、`inputs/`、`qualification/`、`e2e/`、`readiness/`、`runs/`、`replay/`、`validator/`、`review/`、`signatures/`、`manifest/`、`acceptance/` 全在同一 `artifacts/phase-4/<release-id>/`。装配先冻结 code/input/contracts/seeds/dependencies/workload identities，正式运行后不可换。evidence manifest 显式递归列每个证据文件的 path/role/SHA/bytes/parents；独立 replay 从 genesis/raw fixtures/ledger events 重新计算而不是信顶层 PASS；final validator 从 replay 与底层事实逐项推导 A01–A21、blocking findings、三类状态和工程终态。reviewer 和人工签署者验证措辞；acceptance approver 最后原子写 acceptance。失败 attempt、qualification 序列和 acceptance iteration 不能删除、覆盖或只挑成功。

readiness 只读 official canary 可读取已经公开期次，保存 raw/transport/parser/revision/dedup/compatibility receipts到 staging；前后重算 Phase 1 inventory。它不写 Phase 1、不等新开奖、不产生真实前瞻结论。证据取回只按 manifest 显式路径，以源/接收端文件数、字节和 SHA 全匹配为 PASS；不使用 `latest`、glob 或 mtime。

## 15. P4-MVP-A01 至 A21 设计映射

| 验收项 | 设计章节 | 实现组件 | 主要验证证据 | 不满足终态 |
| --- | --- | --- | --- | --- |
| P4-MVP-A01 | 1、4、9、10 | orchestrator/lock/label/research | 双 game cycle E2E、事件时钟、capability、next shadow | `HOLD_CYCLE_INCOMPLETE`；泄漏为 FAIL |
| P4-MVP-A02 | 7 | rules_probability/forecast | rule known-answer、1000 唯一/前缀、replay | `FAIL_TOP1000_CONTRACT` |
| P4-MVP-A03 | 7 | rules_probability/serialization | 独立概率/order/tie oracle、扰动及非法数负控 | `HOLD_UNSUPPORTED_TIE_SEMANTICS` 或 FAIL |
| P4-MVP-A04 | 7 | M0/rank DP | 两彩种 M0 full-space histogram known-answer | `FAIL_M0_TIE` |
| P4-MVP-A05 | 8、10 | research_controller/forecast | parameter diff 与 child next shadow E2E | `HOLD_PARAMETER_ADJUSTMENT_MISSING` |
| P4-MVP-A06 | 8、10 | feature registry/controller | feature snapshot/diff/lineage/output E2E | `HOLD_FEATURE_ADJUSTMENT_MISSING` |
| P4-MVP-A07 | 10、13 | e-process qualifier | 2,000 uniform formal terminals、Ville/解析 certificate、独立率重算 | `FAIL_UNIFORM_FALSE_PROPOSAL_RATE` |
| P4-MVP-A08 | 8、10、12 | alpha ledger/governance | 全 experiment 独立 wealth/stop 重算、越权负控 | `FAIL_ALPHA_OR_GOVERNANCE` |
| P4-MVP-A09 | 10、13 | qualifier/power | 六个 1,000-sequence rates、sequence 与 aggregate power/区间、seed isolation | `HOLD_DESIGN_NOT_POWERED` 或 FAIL |
| P4-MVP-A10 | 7、13 | full-rule oracle/product rank | 八单元数值、误差界、独立 import audit | `FAIL_FULL_RULE_CAPABILITY` |
| P4-MVP-A11 | 9、12 | time gate/label/metric lock | sequence/PIT/capability/time-travel/tamper E2E | `FAIL_CAUSALITY_OR_TAMPER` |
| P4-MVP-A12 | 4、7、8、12 | game-scoped registries/state | cross-game/governance negative E2E | `FAIL_GAME_OR_GOVERNANCE_ISOLATION` |
| P4-MVP-A13 | 4、10、12 | ledger/checkpoint/recovery | 每阶段故障注入、side-effect count、head hashes | `HOLD_RECOVERY_MISMATCH`；重复事实为 FAIL |
| P4-MVP-A14 | 5、9.4、14 | data chain/official adapters | fixed response、read-only canary、genesis/protection/chain tests | `HOLD_DATA_SOURCE_OR_CHAIN`；保护变化为 FAIL |
| P4-MVP-A15 | 3、14 | independent replay | bottom-up probabilities/rank/metric/state/alpha/manifest match | `HOLD_REPLAY_MISMATCH` |
| P4-MVP-A16 | 14 | validator/review/acceptance | 六类交付覆盖、blocking 0、人工措辞签署 | `HOLD_DELIVERY_INCOMPLETE` 或 FAIL |
| P4-MVP-A17 | 3、13、14 | dependency/deploy/readiness | wheel manifest、clean rebuild、smoke/recovery/replay/return | `HOLD_INSTALL_OR_WORKLOAD` |
| P4-MVP-A18 | 12 | state_projection/schemas | 完整键、允许/禁用转换与维度污染负控 | `FAIL_STATE_MATRIX` |
| P4-MVP-A19 | 11 | calendar/schedule/systemd | virtual clock E2E、unit audit、plan/alert replay | `HOLD_SCHEDULER_AUDIT` 或 deadline FAIL |
| P4-MVP-A20 | 9 | diagnostics/metrics/window | 两彩种独立数值 oracle、Phase3 回归、边界负控 | `FAIL_METRIC_ORACLE_MISMATCH` |
| P4-MVP-A21 | 5、9.4、10 | correction/current view/research | revision closure、中断恢复、current-view/alpha 独立重算 | `HOLD_CORRECTION_INCOMPLETE`；重复/覆盖为 FAIL |

## 16. 风险、不可完成条件、降级和决策记录

主要风险是扩展 tick 后 full-space tie/rank 的 reachable-state 预算、官方页面结构/可用性、用户 systemd 能力、文件系统原子语义、e-process 实现或功效确认偏差、证据体积和独立实现同源。对应封闭方式分别是 sparse exact histogram + 分区枚举 oracle + 边界 benchmark、固定响应加只读 canary/冲突终态、安装审计、原子语义 probe、解析 certificate + 独立 20,000-sequence power gate、manifest-driven batching、禁止独立路径 import 产品模块。

不可完成条件包括：上位合同或四项 genesis 不一致；Phase 1 保护清单不能稳定重算；选定文件系统不能提供所需原子持久化；两个彩种任一不能正确生成严格正归一概率或 exact tie/rank；正式 design 未 powered；批准 workload 在不改科学语义下仍不能完成；官方适配 readiness 或 systemd 用户适配器不能在权限内安装审计；角色不能分离；独立 replay/manifest 不能闭合。它们都产生明确 HOLD；因果泄漏、锁后改写、选择性证据、伪造或越权产生 FAIL。

允许降级仅是：单 source/network 暂停 unlock、单 game 故障隔离、无 eligible hypothesis 的诚实 no-change、未支持 candidate 不接 registry、减少并行并从同 checkpoint 恢复、M0 继续 Champion。禁止降级概率精度、tie/rank、时间证据、资格数量/阈值、独立性、递归证据或角色分离；不能用展示位冒充 rank、合成证据冒充真实改善或用顶层 PASS 代替重算。

决策记录：ADR-P4-001 选择 filesystem content-addressed ledger；ADR-P4-002 选择 P4-CJSON-1；ADR-P4-003 选择边界 `[-4096,4096]` 的 P4E1 quantized additive family、sparse exact histogram 和边界 benchmark；ADR-P4-004 选择 user systemd timer；ADR-P4-005 选择 LR e-process、每 family `W0=0.006`、alpha-spending/zero reward；ADR-P4-006 选择 staged recursive manifest；ADR-P4-007 official canary 只作 readiness；ADR-P4-008 M0 永久 Champion 且 Phase 4 无 promotion surface；ADR-P4-009 区分 sequence rate 与 prospective 1,000-sequence aggregate gate probability，并采用 simultaneous CP 传播。任何改变必须新 ADR、影响分析、新合同/seed/release identity，并保留旧失败证据。
