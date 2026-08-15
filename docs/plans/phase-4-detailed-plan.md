# Phase 4 真实模型预测 MVP 详细实施计划

版本：2.0（规划候选）

状态：`D00_AUTHORITY_SYNCED`。本计划与其余三份 Phase 4 authority 文档在同一 `P4_AUTHORITY_COMMIT` 冻结；只有 D00 两个 checker 均通过且 receipt 证明 clean 后才授权 D01。

## 1. 规划方法、固定边界与共同合同

本计划按四个审查阶段形成：总体范围与成功态重构；内聚任务合同拆分；逐任务逻辑/可独立验收审查；全图覆盖、依赖和最终验收审查。任务是未来工作，不声称产品已经实现。

共同固定输入为：同步后的四份 Phase 4 authority 文档及 commit；Phase 1 canonical release 链和 `draws.jsonl`/manifest/规则身份；Phase 3 正式验收只作为只读先验；目标仓库和冻结实现 commit。禁止修改 Phase 0–3 冻结制品。所有正式输出进入同一 `artifacts/phase-4/<release-id>/`，运行时可用临时根，但须以 manifest 显式封装。

所有任务均须交付 task receipt，至少记录：task ID、输入/输出 hashes、命令、exit code、开始/结束时间、实现 commit、dirty 状态、断言结果、HOLD/FAIL reason 和恢复点。下文的路径是最低合同；实现时可增加内部文件，但必须进入 manifest 和相应 Schema。

失败语义统一为：缺少可恢复输入/环境/资格是 `HOLD_*`；泄漏、篡改、伪造、越权或虚假科学声明是 `FAIL_*`。HOLD 保留现场并给出唯一恢复命令；不得删除失败 attempt 或选择性提交最佳结果。

验收命令中的 CLI 名称是必须实现的语义入口；D01 authority 同步时冻结确切可执行名和参数。若现有仓库入口不同，可保持兼容别名，但正式 receipt 必须证明加载冻结 release，而非 fixture、内联参数或工作树默认值。

## 2. DAG、阶段门与并行性

```text
D00 authority sync
  -> D01 contracts/time/data
      -> D02 feature engine
          -> D03 SSQ training ----\
          -> D04 DLT training -----+-> D05 backtest & serving selection
                                    -> D06 probability/ranking
D01 -> D07 immutable ledger/lock/score
D05 + D06 + D07 -> D08 product CLI
D05 + D07 -> D09 AutoResearch
D08 + D09 -> D10 scheduler/recovery/readiness
D08 + D10 -> D11 formal dual-game E2E
D11 -> D12 independent replay
D12 -> D14 immutable checklist candidate
D14 -> D13 pre-acceptance delivery manifest
D13 -> D15 final machine acceptance and handoff
```

任务 ID 为稳定引用而非执行序号，因此 D14 在 D13 前执行。唯一合法的并行组：`{D03,D04}`（不同 game 输出根）；`{D06,D07}`（分别拥有概率排序和账本状态模块，接口已由 D01 冻结）。D05 之后的任务不并行写同一正式 release。图无环；D14 从 D11/D12 冻结证据生成不可变清单候选，D13 随后把 D14 和全部 pre-acceptance 文件纳入 manifest，D15 显式依赖全部非延后任务并只追加终验输出。

阶段门：D00解除 authority 阻塞；D01–D10为实现准备/组件门；D11为正式产品门；D12、D14、D13依次完成重放、不可变清单候选和 pre-acceptance manifest；D15是唯一最终机器交付门。任何前置 HOLD 阻断全部后继，不以 M0 fallback 绕过；D15 PASS 前不得把 D14 材料交给人类。

## 3. 独立任务合同

### D00：同步并冻结 Phase 4 authority

- **目标：** 在后续授权任务中使 `ROADMAP.md`、`tasks/phase4/README.md`、总体设计和详细计划共同要求双彩种真实训练模型，生成唯一 `P4_AUTHORITY_COMMIT`，解除 `HOLD_AUTHORITY_SYNC`。
- **固定输入：** 本版两份设计候选、固定旧 authority commit、任务授权中机器可读的允许路径范围。
- **允许/禁止修改：** 仅允许四份 authority 文档及 authority freeze 配置/Schema/测试；禁止产品代码、Phase 0–3 制品、训练或 forecast。当前设计任务不执行 D00。
- **交付物：** `config/phase4/authority-freeze.json`，Schema 至少含四路径 hashes、commit、祖先关系、`serving_model_by_game` 硬门和旧语义禁止项；`artifacts/phase-4/<release-id>/authority/authority-freeze.json`。
- **实现方法：** 原子同步上位语义；checker 解析所有成功/HOLD条款并冻结同一 clean commit，扫描 M0/`baseline_only` 是否仍可作为 MVP PASS。
- **验收方法：** 运行 `freeze_authority.py --check --require-serving-model-per-game --reject-baseline-only-pass`，再由独立 semantic checker 解析四文档的成功/HOLD 状态机；对固定禁语义做扫描，并断言四路径、commit、hash、结构化 requirement IDs 和 cross-reference 集合完全一致。
- **通过判据：** 两个独立 checker 均 exit 0；四文件在同一 commit；两 game 非 M0 serving 硬门一致；旧 M0 成功态为 0；hash/结构断言全过且 dirty=false，不需要人工确认、签字或豁免。
- **失败/HOLD：** 允许路径合同缺失、语义/hash不一致或 checker 失败为 `HOLD_AUTHORITY_SYNC`；不得以人工确认或豁免启动 D01。
- **依赖/下游：** 无；所有后续任务依赖其 authority receipt。

### D01：数据、时间、Schema 与模型合同冻结

- **目标：** 冻结 Phase 1 `retrospective_sequence_safe` 输入、可选 `external_point_in_time` 特征、训练、model release、serving、forecast、CLI 和科学措辞的机器合同。
- **固定输入：** D00 authority freeze；Phase 1 draw/dataset Schemas和正式 release identity；总体设计第 3–13 节。
- **允许/禁止修改：** 允许 `config/phase4/`、`schemas/phase4/`、Phase 4 合同测试和对应文档；禁止训练实现、真实结果读取、旧制品重写。
- **交付物/Schema：** `training-input-manifest.schema.json`、`feature-snapshot.schema.json`、`model-release.schema.json`、`training-report.schema.json`、`backtest.schema.json`、`serving-selection.schema.json`、修订后的 forecast/ranking/CLI/acceptance contracts；字段必须覆盖 game、canonical order/comparator ID、target position、cutoff position、所有 release IDs、代码/依赖身份、selection/report-only 窗口、概率层、tie、解释和 lock，并禁止 Phase 1 draw 出现补造的 `available_at`。
- **实现方法：** 以 unknown-field fail closed 的 JSON Schema 和状态机冻结逐 game canonical comparator、`retrospective_sequence_safe` prefix、target 不在训练前缀、selection/report-only label capability、M0/diagnostic隔离、科学状态正交性及命令显式模型参数；不得用 issue 字符串字典序或未定义数值 `<`。仅 `external_point_in_time` 特征应用 `available_at < prediction_locked_at`。
- **验收方法：** 运行合同 validator；逐 Schema 构造最小双 game 正例，并构造未来期、缺模型、M0 serving、fixture serving、单一 tie、全等 Top-1000、未知字段负例。
- **通过判据：** 所有正例通过、负例被精确 reason code 拒绝；CLI 无隐式“latest M0”；正式状态不能表达 `baseline_only PASS`。
- **失败/HOLD：** `HOLD_CONTRACT_INCOMPLETE|FAIL_CONTRACT_WEAKENED`。
- **依赖/下游：** D00；为 D02–D10 提供稳定接口。

### D02：真实序列安全历史特征引擎

- **目标：** 从 Phase 1 冻结序列为 SSQ/DLT 生成 `retrospective_sequence_safe` F01，并支持预注册 F02；证明每行只读 canonical target 位置之前的数据。
- **固定输入：** D01 Schema/时间合同；显式 Phase 1 release、game、cutoff/target、规则身份。
- **允许/禁止修改：** 允许 Phase 4 `features` 模块、CLI、单元/属性测试；禁止外部无 `external_point_in_time` 证据的数据、训练/排序、修改 Phase 1，以及为 Phase 1 draw 添加 `available_at`。
- **交付物/Schema：** `features/<game>/<feature-release-id>/feature-snapshot.jsonl` 与 manifest；逐号码包含 raw count、exposure、F01/F02值、canonical_order_id、target/max-source position、input hashes和配置。
- **实现方法：** 用 Phase 1 冻结的逐 game canonical order/comparator 定位 target，按其严格前缀计算 Beta-Binomial shrinkage F01 和冻结半衰期 F02；fold 内独立变换、规范序列化和内容寻址。issue 字符串仅作身份，不承担顺序比较。
- **验收方法：** 对手算微型序列和真实 Phase 1 前缀以独立 canonical-order oracle 重算；做 prefix-invariance、target 混入训练前缀、未知 target、字符串字典序陷阱、乱序、跨 game、常量特征、重复 issue、给 Phase 1 补 `available_at` 及外部伪造时间证据负测。
- **通过判据：** 两 game 真实 snapshot存在且F01非恒定；order/comparator ID 正确、max source position 严格早于 target 且 target 不在训练前缀；重复运行 hash 一致；同/未来位置和不合格 `external_point_in_time` 输入被拒绝。
- **失败/HOLD：** `HOLD_FEATURE_INPUT|HOLD_FEATURE_CONSTANT|FAIL_LEAKAGE`。
- **依赖/下游：** D01；D03、D04消费。

### D03：SSQ 训练与模型 release

- **目标：** 用 SSQ 真实历史前缀训练 P4E1-R，并冻结 SSQ 非均匀 model release。
- **固定输入：** D02 SSQ feature releases；D01训练网格/folds/精度；SSQ规则 `33选6 + 16选1`。
- **允许/禁止修改：** 允许模型训练公共模块、SSQ配置和测试；只写 `models/ssq`/训练临时根；禁止 DLT参数、fixture当正式输入、手写theta。
- **交付物/Schema：** `models/ssq/<id>/{model.json,training-report.json,model-card.md,manifest.json}`，含真实目标函数轨迹、参数、归一常数验证、cutoff和依赖身份。
- **实现方法：** rolling-origin内选择有界theta/正则配置，最终对SSQ完整截止前缀重拟合；用 elementary-symmetric DP 归一。
- **验收方法：** 正式 train CLI；独立用输入 snapshot 重算目标函数和参数选择；clean replay；改变一条早期真实 draw 应改变 feature/model ID。
- **通过判据：** model != M0；F01 consumption可追踪；至少一分区非零有效系数/非恒定权重；所有概率正且分区归一；cutoff合法；replay一致。
- **失败/HOLD：** `HOLD_BASELINE_ONLY|HOLD_DEGENERATE_MODEL|HOLD_MODEL_RELEASE|FAIL_LEAKAGE`。
- **依赖/下游：** D02；D05、D06消费。

### D04：DLT 训练与模型 release

- **目标：** 用 DLT 真实历史前缀训练 P4E1-R，并冻结 DLT 非均匀 model release。
- **固定输入：** D02 DLT feature releases；D01训练合同；DLT规则 `35选5 + 12选2`。
- **允许/禁止修改：** 允许模型训练公共模块、DLT配置和测试；只写 `models/dlt`/训练临时根；禁止 SSQ参数和任何正式 fixture代替物。
- **交付物/Schema：** `models/dlt/<id>/{model.json,training-report.json,model-card.md,manifest.json}`，字段与D03同构但身份独立。
- **实现方法：** DLT独立 rolling-origin选择和最终重拟合；固定基数DP归一，禁止复制SSQ参数。
- **验收方法：** 正式 DLT train CLI、独立目标函数/参数重算、clean replay、早期 draw mutation敏感性。
- **通过判据：** model != M0；真实F01被消费；非零有效系数和非恒定权重；严格正/归一；合法cutoff；与SSQ model/data/feature IDs不同且无跨game读取。
- **失败/HOLD：** `HOLD_BASELINE_ONLY|HOLD_DEGENERATE_MODEL|HOLD_MODEL_RELEASE|FAIL_GAME_ISOLATION|FAIL_LEAKAGE`。
- **依赖/下游：** D02；D05、D06消费。

### D05：时间切分回测与 serving 选择

- **目标：** 对两 game 如实比较真实候选与 M0，并各选出一个合格非均匀 `serving_model_by_game`。
- **固定输入：** D03/D04 releases；D01 预注册候选、指标、互不重叠的 selection/report-only 窗口、最小样本和选择 tie-break。
- **允许/禁止修改：** 允许 backtest/evaluation/selection模块、配置和测试；禁止改训练结果、只报有利fold、以显著性作为是否存在真实模型的替代门。
- **交付物/Schema：** `backtests/<game>/<id>/{selection-fold-metrics.jsonl,report-only-fold-metrics.jsonl,summary.json}`、不可变 `models/<game>/model-selection-receipt.json`；`models/serving-selection.json` 映射两 game release IDs并记录 M0 comparator。
- **实现方法：** 每 fold 重新 fit 且只用 canonical 前缀；仅用 selection folds 选配置并先内容寻址冻结 receipt，之后才授予 report-only label capability。report-only folds 对选定配置只评估一次，报告联合 log loss、Brier/校准摘要、Top-K 覆盖、相对 M0 差、区间/blocked bootstrap和样本量；最终模型可在合法截止前全部数据重拟合，但不得把 refit 结果倒灌效果报告。
- **验收方法：** 独立从 fold forecast/labels 重算指标和区间；断言窗口按 canonical order 不重叠、receipt hash/时间先于 report-only label access、report-only 结果不改变候选或 tie-break；注入读取后重选、窗口重叠、无 lift/负 lift和历史不足案例。
- **通过判据：** SSQ/DLT 均有非 M0 serving release；选择证据与 report-only 效果报告隔离且无遗漏，结论允许 `worse_than_M0|no_confirmed_lift|insufficient_evidence` 并与区间一致；退化候选被剔除。无合格候选不得产生选择文件，独立窗口不足不得产出 serving PASS。
- **失败/HOLD：** `HOLD_BASELINE_ONLY|HOLD_BACKTEST_INCOMPLETE|FAIL_SELECTION_BIAS|FAIL_FALSE_CLAIM`。
- **依赖/下游：** D03,D04；D06,D08,D09消费。

### D06：非均匀联合概率与 exact Top-1000

- **目标：** 从冻结 serving model生成可验证的完整空间概率语义和各 game精确Top-1000。
- **固定输入：** D01概率/rank合同；D03/D04模型；D05 serving selection；独立小空间 known answers。
- **允许/禁止修改：** 允许 probability/ranking模块、测试和独立oracle；禁止训练/selection、float近似tie、字典序跨概率层主选。
- **交付物/Schema：** normalization proof、probability-layer histogram、rank/tie API、`top1000.jsonl`生成器和独立oracle报告。
- **实现方法：** 分区 elementary-symmetric normalization；组合联合概率乘积；exact k-best/可证明等价枚举按概率降序，canonical ticket仅局部tie-break。
- **验收方法：** 小空间全枚举；真实规则用第二种DP/分区枚举核对总质量、边界概率、Top-1000 hash；输入排列/mutation、NaN/零/溢出负测和字典序主导检测。
- **通过判据：** 概率全正且归一；完整空间至少两个层级且非单一tie；Top-1000恰1000合法唯一、至少两个概率值、前缀嵌套；跨层顺序100%由概率决定。
- **失败/HOLD：** `HOLD_DEGENERATE_MODEL|HOLD_UNRELIABLE_RANKING|FAIL_PROBABILITY_ORACLE`。
- **依赖/下游：** D01,D03,D04,D05；D08消费。

### D07：不可变账本、forecast lock、解锁与评分

- **目标：** 提供数据→特征→模型→forecast的追加血缘、原子锁、guarded label和可修订评分。
- **固定输入：** D01对象/时间/状态Schema；Phase 1身份和固定历史结果fixture。
- **允许/禁止修改：** 允许ledger/storage/lock/score/correction模块及测试；禁止模型数学、采集器写入、锁后覆盖和提前标签读取。
- **交付物/Schema：** ledger events、lock、label-unlock、score/window metric、correction closure、current-view reducer和checkpoint。
- **实现方法：** create-once内容寻址对象、CAS锁、capability分离、追加修订和幂等work ID。
- **验收方法：** 虚拟时钟正向流程；提前label、未来feature、lock后mutation、重复score、结果修订、崩溃点恢复和跨game污染负测。
- **通过判据：** 每对象唯一终态；锁后字节不变；评分只在核验后；修订保留旧事实并传播；重试无重复事件。
- **失败/HOLD：** `HOLD_STATE_INCOMPLETE|FAIL_LEAKAGE|FAIL_TAMPERED|FAIL_GAME_ISOLATION`。
- **依赖/下游：** D01；D08–D10消费。

### D08：正式产品 CLI 组合

- **目标：** 提供真实 train/forecast/inspect/lock/replay CLI，使两 game正式命令显式加载冻结 serving release。
- **固定输入：** D05 selection、D06 probability/ranking、D07 ledger；D01 CLI contract。
- **允许/禁止修改：** 允许CLI/provider组合和集成测试；禁止隐式M0、fixture默认、工作树模型、未锁正式输出。
- **交付物/Schema：** train/forecast/inspect/replay verbs；`forecasts/<game>/<target>/{forecast.json,top1000.jsonl,explanations.jsonl,lock.json}`。
- **实现方法：** 命令必须解析并hash核验model/feature/data/code/dependency IDs，生成前校验cutoff，输出概率/解释后原子lock；diagnostic M0用隔离verb或显式flag和水印。
- **验收方法：** 用真实Phase 1输入分别执行SSQ/DLT训练与明确目标期forecast；inspect字段核对；strace/import log或provider receipt证明读取指定model files；缺/换model、M0、fixture、dirty code负测。
- **通过判据：** 各1,000注且D06硬门全过；显示非M0 model、feature snapshot、cutoff、概率差异、排名依据和locked；clean replay hash一致。
- **失败/HOLD：** `HOLD_PRODUCT_CLI|HOLD_BASELINE_ONLY|FAIL_UNFROZEN_MODEL_PATH`。
- **依赖/下游：** D05,D06,D07；D10,D11消费。

### D09：AutoResearch 真实参数/特征调整与 shadow

- **目标：** 对每 game从当前 serving parent产生一个真实参数或F02配置diff，训练child并影响下一期shadow。
- **固定输入：** D05 serving parents及隔离的 selection/report-only 报告；D02 feature engine；D07 ledger；冻结proposal菜单和预算。
- **允许/禁止修改：** 允许research controller/candidate/diff/shadow模块及测试；禁止直接改serving、修改评分器、无界搜索、把合成结果宣称真实lift。
- **交付物/Schema：** `research/<game>/{candidate.json,diff.json,decision.json}`、child feature/model release和下一目标期shadow forecast lineage。
- **实现方法：** 每周期每game最多一个有界proposal；child重跑对应真实 `retrospective_sequence_safe` feature/train；no-op拒绝；候选治理复用 D05 的选择/只读报告窗口隔离，历史结果只授予challenger/shadow状态。
- **验收方法：** 参数正例与feature正例各至少一条确定性测试；正式两game各执行一个允许diff，比较parent/child snapshot、参数、概率/Top-1000；预算耗尽、未来数据、direct promotion负测。
- **通过判据：** diff非空、child ID新、下一期shadow实际加载child且输出可观察变化；serving selection未被越权修改；科学措辞含M0/窗口/指标/不确定性。
- **失败/HOLD：** `HOLD_ADJUSTMENT_CAPABILITY|FAIL_GOVERNANCE|FAIL_FALSE_CLAIM`。
- **依赖/下游：** D02,D05,D07；D10,D11消费。

### D10：调度、恢复与 workload readiness

- **目标：** 编排双game周期、崩溃恢复和相称资源资格，不依赖人工中途确认或未来开奖。
- **固定输入：** D08 CLI、D09 research、D07 checkpoints；冻结虚拟日历/历史响应和目标环境预算。
- **允许/禁止修改：** 允许scheduler、用户级部署样例、readiness脚本/测试；禁止sudo/VPS配置变更、硬编码硬件型号、降低产品语义。
- **交付物/Schema：** schedule plan/events、checkpoint/recovery receipts、benchmark report、readiness decision和runbook。
- **实现方法：** work ID + lease + checkpoint编排；用已公开历史结果/虚拟时钟走完整周期；测量每game train、Top-1000、replay的wall time/峰值RSS/磁盘，按命名workload预算裁决。
- **验收方法：** 在prepare/train/forecast/lock/score/research各故障点注入中断并resume；并发重复触发；目标环境连续执行基准样本（默认每workload 5次，若区间精度不足才按预注册上限扩展）。
- **通过判据：** 无重复对象/支出；所有恢复到唯一终态；p95或保守上界满足冻结TIMEOUT/资源预算；不需root和人工批准。
- **失败/HOLD：** `HOLD_RECOVERY|HOLD_ENVIRONMENT_READINESS`；给出缩batch/并行度等不改变数学的恢复方案。
- **依赖/下游：** D08,D09；D11消费。

### D11：同一 release 的正式双彩种 E2E

- **目标：** 在clean frozen commit上用真实Phase 1历史完成SSQ/DLT训练、选择、正式Top-1000、lock及AutoResearch shadow闭环。
- **固定输入：** D08产品CLI、D09 AutoResearch、D10 readiness、D00 authority；明确双game target issues与合法cutoffs。
- **允许/禁止修改：** 只允许新的正式release E2E/receipt路径；产品/合同/前序release只读；禁止fixture或M0替换正式输入。
- **交付物/Schema：** 两game training inputs、feature/model/backtest/selection、forecast/lock、research child/shadow、E2E report及protected-root inventories。
- **实现方法：** 离线或固定输入执行正式CLI；forecast后做负向mutation；开奖后路径用历史目标期E2E fixture验证，不等待新forecast未来开奖。
- **验收方法：** 单一E2E runner从空runtime执行；独立检查每个target的cutoff、provider access log、model lineage、1,000注和lock；前后Phase0–3 inventory exact match。
- **通过判据：** 两game所有功能门通过；serving均非M0；正式Top-1000非均匀且概率主排；shadow确实变化；blocking=0。
- **失败/HOLD：** `HOLD_FORMAL_E2E|HOLD_BASELINE_ONLY|FAIL_PROTECTED_ARTIFACT_CHANGED`。
- **依赖/下游：** D08,D09,D10；D12消费。

### D12：独立 bottom-up replay 与 mutation

- **目标：** 不信产品summary，从Phase1输入重建两game特征、参数、概率、Top-1000和锁定身份。
- **固定输入：** D11 frozen release及代码/依赖/输入manifest；独立replay实现。
- **允许/禁止修改：** 只允许 `replay/`和独立测试；禁止导入产品核心概率/训练函数、修改任何被重放文件。
- **交付物/Schema：** `replay/replay-report.json`、逐事实comparison、import audit和mutation findings。
- **实现方法：** 独立解析Phase1、重算F01、训练目标/选择、DP归一和Top-1000；重算所有hash/parent关系。
- **验收方法：** clean隔离目录replay；分别突变一条早期draw、cutoff、feature值、theta、model ID、概率、Top-1000顺序、lock和CLI provider引用。
- **通过判据：** 原release逐事实100% match；每类mutation命中预期guard；产品核心import=0；两game均覆盖。
- **失败/HOLD：** `HOLD_REPLAY_MISMATCH|HOLD_REPLAY_INDEPENDENCE|FAIL_TAMPERED`。
- **依赖/下游：** D11；D14消费。

### D14：本地产品验收材料准备

- **目标：** 从已冻结正式证据生成本地用户可执行的不可变 checklist candidate，但在 D15 PASS 前不交给人类。
- **固定输入：** D11 的冻结 model/forecast/E2E manifests、D12 replay manifest、同一 release 的正式 CLI/runbook 和不可变 IDs；不依赖尚未生成的 D13 delivery manifest。
- **允许/禁止修改：** 只允许创建一次 `acceptance/local-product-checklist-candidate.md`及 candidate receipt；禁止产品修复、后续改写、人工签字占位、调用人类确认或写机器 ready 状态。
- **交付物/Schema：** 清单列双game真实输入命令、明确target/cutoff/model IDs、预期1000行、inspect字段、hash/replay检查和证据路径。
- **实现方法：** 从 D11/D12 底层 manifests 自动填充不可变 IDs，命令不使用`latest`、glob、fixture或M0；说明科学状态不等于lift；内容寻址后标记 `CANDIDATE_NOT_RELEASED`。
- **验收方法：** 在干净副本机器dry-run所有只读inspect/replay命令，核对路径存在和hash；静态检查训练/forecast复现命令参数完整，且没有人工确认步骤。
- **通过判据：** 清单可分别观察模型身份、feature snapshot、训练截止、概率差异、排序依据和lock；仍未交给人类，未声称机器或人工验收完成。
- **失败/HOLD：** `HOLD_LOCAL_ACCEPTANCE_PREPARATION`；阻断D15。
- **依赖/下游：** D12；D13消费。

### D13：pre-acceptance delivery manifest 与交付矩阵

- **目标：** 在 D14 之后封装可从磁盘枚举的完整 pre-acceptance release，使不可变 checklist candidate 和全部前序正式证据处于同一 hash 闭包。
- **固定输入：** D00–D12 正式证据、D14 checklist candidate/receipt、authority freeze和允许路径政策。
- **允许/禁止修改：** 只允许创建 `manifest/delivery-manifest.json`及manifest receipt；全部输入只读，不复制或改写证据，manifest 不列出也不 hash 自身。
- **交付物/Schema：** 每个 pre-acceptance 文件的 size/hash/provenance/producer、父子关系、两 game 产品交付矩阵、protected inventory，以及 D14 checklist hash。
- **实现方法：** 从磁盘递归枚举白名单并明确排除 manifest 自身和未来 D15 输出；检查正式 forecast 可追到 model→feature→Phase1 input→authority/code/dependencies，且 D14 candidate/receipt 均被覆盖。
- **验收方法：** 独立 checker 重枚举并重算所有 hash；隐藏 prep root 后只用 release 读取/重放；删除 model/feature/backtest/checklist、添加未登记文件、令 manifest hash 自身等负测。
- **通过判据：** pre-acceptance 文件集合和 hash exact；D14 在闭包内；两 game lineage闭合；定义/实现/接口/验证/运行/正式证据覆盖100%；无自引用、秘密或生成垃圾。
- **失败/HOLD：** `HOLD_MANIFEST_NOT_CLOSED|FAIL_SELECTIVE_EVIDENCE|FAIL_SELF_REFERENCE`。
- **依赖/下游：** D14；D15消费。

### D15：最终机器交付验收与人类交接（最后任务）

- **目标：** 从 D13 pre-acceptance manifest 和其已覆盖的 D14 候选清单签发唯一机器工程终态；只有 PASS 后才用单独 receipt 释放清单给人类。
- **固定输入：** D00–D14全部receipts、D12 replay、D13 manifest、D14清单、冻结acceptance assertions。
- **允许/禁止修改：** 只允许追加 `acceptance/machine-acceptance.json`、`acceptance/checklist-release-receipt.json` 和 `acceptance/final-closure.json`；禁止修改 D14 checklist、D13 manifest 或任何 pre-acceptance 文件，禁止回退M0、重选模型、修复缺陷或人工豁免。
- **交付物/Schema：** machine acceptance 含逐项底层引用/重算值、blocking findings、工程/科学状态；checklist release receipt 引用 D14 checklist hash 与 D13 manifest hash；final closure 引用 manifest、machine acceptance 和 receipt hashes。
- **实现方法：** validator 从磁盘和 manifest 派生事实，先写 machine acceptance，再写绑定 checklist/manifest 的 release receipt，最后写绑定 manifest 及前两项 D15 输出 hash 的 closure；closure 不 hash 自身，D15 输出不回填 D13，因而没有自引用。顶层自报 PASS 不是输入真值。
- **验收方法：** 正式 validator 及独立二次 checker；mutation 必须覆盖 M0 serving、缺 feature/model、错误 canonical comparator、target 入训练前缀、selection/report-only 重叠或读取后重选、fixture-only、全空间单 tie、Top-1000 全等、字典序主排、未加载冻结 model、D14 未入 D13、D15 改写 pre-acceptance 文件和 closure 自引用。
- **通过判据：** 两game serving非M0；release/feature/cutoff/CLI lineage合法；非均匀与Top-1000门通过；E2E/replay/pre-acceptance manifest/不可变清单100%；D15 前后所有 pre-acceptance hashes 不变；三项追加输出引用闭合且无自引用；科学报告完整。只有此时写 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE` 并签发 checklist release receipt。
- **失败/HOLD：** 任一功能门失败为对应HOLD，泄漏/篡改/虚假声明为FAIL；不生成ready状态，不交给人类。
- **依赖/下游：** 依赖D00–D14全部非延后任务，是DAG唯一末节点；PASS后才交给人类验收。

## 4. 最终机器验收断言

| ID | 必须从底层证明 | 拒绝条件 | 主要任务/证据 |
| --- | --- | --- | --- |
| P4-R01 | authority新语义已同步 | 旧authority仍允许M0 PASS | D00 freeze |
| P4-R02 | SSQ真实 `retrospective_sequence_safe` F01 snapshot | fixture、恒定、错误 comparator、target 入前缀、补造 `available_at` | D01,D02,D03,D15 |
| P4-R03 | DLT真实 `retrospective_sequence_safe` F01 snapshot | fixture、恒定、错误 comparator、target 入前缀、补造 `available_at` | D01,D02,D04,D15 |
| P4-R04 | SSQ非M0 model release | M0、手写theta、缺card | D03,D05 |
| P4-R05 | DLT非M0 model release | M0、复制SSQ、缺card | D04,D05 |
| P4-R06 | 时间切分回测诚实完整 | selection/report-only 重叠、读取后重选、无M0、独立窗口不足却PASS | D05,D15 |
| P4-R07 | 两game serving选择合法 | 任一`baseline_only`或缺release | D05,D15 |
| P4-R08 | 联合概率正、归一、非均匀 | 完整空间单一tie/退化 | D06,D12 |
| P4-R09 | 正式Top-1000概率主排 | 全等概率、字典序跨层主排 | D06,D08,D12 |
| P4-R10 | CLI加载冻结模型 | inline/fixture/worktree/M0 provider | D08,D11 |
| P4-R11 | 双game各1000注并锁定 | 缺game、重复/非法、未锁 | D08,D11 |
| P4-R12 | 血缘、时间、修订、恢复 | 提前label、覆盖、重复事实 | D07,D10,D11 |
| P4-R13 | AutoResearch diff影响shadow | no-op、直接改serving | D09,D11 |
| P4-R14 | 独立replay逐事实一致 | 产品核心import、任一mismatch | D12 |
| P4-R15 | manifest与保护树闭合 | D14未入manifest、缺/额外/择优证据、自引用或旧树变化 | D13,D15 |
| P4-R16 | 科学结论有比较器/窗口/指标/不确定性 | 把运行写成lift或中奖保证 | D05,D09,D15 |
| P4-R17 | 本地验收只在机器交付后 | 人工成为开发前置或替代机器门 | D14,D15 |

D15必须显式拒绝：任一 game serving=M0、任一正式forecast完整空间单一tie、Top-1000全等概率、model/feature release缺失、canonical order/comparator 缺失或 target 位于训练前缀、selection/report-only 未隔离、仅有fixture、排名由字典序主导、实际命令未使用冻结模型、D14 未被 D13 覆盖、D15 改写 checklist/manifest 或 closure 自引用。任何一个拒绝项命中都不能输出ready。

## 5. 双向追踪矩阵

### 5.1 交付物/验收标准 → 任务

| 交付物或标准 | 生产任务 | 验证任务 | 最终证据 |
| --- | --- | --- | --- |
| authority同步启动门 | D00 | D15 | authority freeze/receipt |
| 数据/时间合同 | D01 | D02,D07,D12,D15 | canonical comparator Schemas、prefix tests、`external_point_in_time` guards |
| SSQ/DLT feature snapshots | D02 | D03,D04,D12,D15 | `features/ssq`, `features/dlt` |
| SSQ model release | D03 | D05,D11,D12,D15 | `models/ssq/<id>` |
| DLT model release | D04 | D05,D11,D12,D15 | `models/dlt/<id>` |
| 回测与科学报告 | D05 | D12,D15 | selection receipt、report-only metrics |
| `serving_model_by_game` | D05 | D08,D11,D15 | serving-selection.json |
| 非均匀概率/Top-1000 | D06 | D11,D12,D15 | proofs、两game top1000 |
| lock/评分/修订 | D07 | D10,D11,D12,D15 | ledger/lock/score/closure |
| 本地CLI产品面 | D08 | D11,D14,D15 | provider receipts/inspect |
| AutoResearch child/shadow | D09 | D11,D15 | diff/child/shadow lineage |
| 调度恢复/readiness | D10 | D11,D15 | recovery/benchmark reports |
| 正式双game E2E | D11 | D12,D15 | E2E report |
| 独立replay | D12 | D14,D15 | replay report/mutations |
| 本地验收准备 | D14 | D13,D15 | immutable checklist candidate/receipt |
| pre-acceptance交付manifest | D13 | D15 | 覆盖D14的delivery manifest |
| 最终机器状态与交接 | D15 | 唯一最终任务 | machine acceptance/release receipt |

### 5.2 任务 → 交付物/验收标准反向检查

| 任务 | 唯一主要责任 | 被哪些硬门消费 |
| --- | --- | --- |
| D00 | authority身份与语义 | R01,R17 |
| D01 | 机器合同 | R01,R02–R13,R16 |
| D02 | 双game真实 `retrospective_sequence_safe` 特征 | R02,R03 |
| D03 | SSQ模型 | R04,R07,R08 |
| D04 | DLT模型 | R05,R07,R08 |
| D05 | 回测/serving选择 | R06,R07,R16 |
| D06 | 概率/rank/Top-1000 | R08,R09 |
| D07 | 账本/时间/锁/评分 | R12 |
| D08 | 产品CLI/正式forecast | R09–R11 |
| D09 | AutoResearch真实变化 | R13,R16 |
| D10 | 调度/恢复/环境 | R12 |
| D11 | 同release正式E2E | R02–R13 |
| D12 | 独立底层重放 | R02–R14 |
| D14 | 不可变本地接手候选材料 | R17 |
| D13 | 含D14的pre-acceptance交付闭包 | R15,R17 |
| D15 | 最终机器裁决与交接 | R01–R17 |

每个任务只承担一个主要责任，有具体固定输入、允许/禁止范围、路径/Schema、方法、验收、通过门、失败终态和依赖；所有任务均被至少一个硬门消费，不以 registry、接口、fixture 或合成证据单独代表产品完成。

## 6. 可完成性、预算与完整性审查

计划不等待未来开奖：训练和回测使用 Phase 1 已冻结历史；正式未来forecast允许`locked_unscored`；评分E2E用结果已知但在虚拟时钟中严格延后unlock的历史目标期。Phase 1 F01/F02 只按 canonical 历史前缀生成且不补造 `available_at`；无真实证据的 `external_point_in_time` 特征直接排除。没有开发子任务依赖人工确认、签字或豁免；D14仅机器生成候选材料，D15通过全部远程机器门后才把材料交给人类。

统计模拟不是产品成功代理。小空间全枚举用于证明概率/rank正确；最小确定性正负fixture用于控制器和泄漏guard；若区间或恢复率需要Monte Carlo，执行任务必须先写明估计量、seed、区间宽度/功效目标和停止上限，再用D10 benchmark裁决。不得沿用与真实模型交付无关的数千序列或角色排列硬门。

任务逻辑审查结论：D00–D15均内聚、可独立机器验收、输入由前置任务产生、失败有可观察终态；D03/D04可安全并行且文件隔离；D06/D07接口由D01冻结且无共享写路径；D12→D14→D13→D15 依次消费 replay、不可变清单、pre-acceptance manifest，D15只追加输出。全图完整性审查结论：R01–R17全部有生产和最终验证节点；D15为唯一最后验收任务并核对所有前序任务；D14不触发人类，D13覆盖D14，图无环且无 hash 自引用。

## 7. 最终交付定义

D00–D15全部PASS后，远程开发才达到机器可交付状态 `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`。此时必须同时存在：SSQ/DLT真实 feature snapshots、各自训练 model releases、隔离 selection/report-only 的回测与M0比较、非M0 serving选择、由其联合概率产生并锁定的各1,000注、解释、AutoResearch child/shadow、调度恢复、正式E2E、独立replay、不可变候选清单、覆盖该清单的闭合 pre-acceptance manifest，以及追加的 machine acceptance/checklist release receipt/final closure。科学状态可以是`no_confirmed_lift`或`worse_than_M0`，但产品路径不能是M0。

D15 PASS 后才释放D14清单供本地用户验收，但不签代用户结论。任何缺失或退化保持HOLD；不得用fixture、合成正控、known-answer、M0 fallback、人工豁免或“基础设施已就绪”替代预测MVP。
