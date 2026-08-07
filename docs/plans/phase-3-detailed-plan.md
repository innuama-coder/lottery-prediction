# Phase 3 详细集成实施计划

版本：1.0

状态：待实施；本计划不是 Phase 3 正式运行或验收

上位权威：`tasks/phase3/README.md`

总体设计：`docs/research/phase-3-overall-design.md`

## 1. 执行原则和唯一依赖链

本计划是一个端到端执行流，不把 Phase 3 拆成可独立验收的子阶段或任务包。唯一依赖顺序如下；允许并行的工作也只能写各自临时/候选路径，必须回到同一个冻结点、正式 release、evidence manifest 和最终 acceptance：

```text
W01 权威/输入核对
 -> W02 PIT 与规则数据合同
 -> W03 预注册设计、角色和预算规则审查
 -> W04 Schema/CLI/研究实现
 -> W05 模型与特征 registry 冻结
 -> W06 资格、负控和故障注入
 -> W07 VPS readiness 与正式 release 冻结
 -> W08 分彩种外层滚动正式运行
 -> W09 统一评价、ledger 闭包和模型分类
 -> W10 独立 replay/review
 -> W11 正向/负向 E2E 与最终 validator
 -> W12 证据回传、manifest、报告和唯一验收
 -> W13 验收迭代或最终交接
```

W03 在任何模型历史结果生成前完成结果盲的科学合同设计；W07 用 W04 的合成 benchmark 按 W03 已冻结的换算规则形成数值工作量，并签署完整正式预注册，是最后结果前冻结点。W08 以后不得修改输入、折、指标、模型开放门、搜索空间、错误预算或分类规则。W08 可让 SSQ 和 DLT 进程并行读取同一冻结 release，但二者不得共享样本、训练、指标或输出文件，也不得绕过 W09–W13 的统一闭包。

每个正式命令都先创建唯一目录和 ledger `started` 事件，再执行计算，最后只追加终态。每个 work item 的失败都保存输入身份、命令、stdout/stderr、事件、checkpoint、部分产物和明确终态；不得删除、重写或复用 run ID。

## 2. 集成工作项

### W01：权威、分支和冻结输入核对

**依赖与输入：** 无前置工作项；读取 `tasks/phase3/README.md`、Phase 1 acceptance/manifest/draws、Phase 2 注册规则 manifest、Phase 2.1 最终 acceptance/recursive manifest/historical audit/power，以及总体设计列出的背景文档。记录计划基线 Git 提交和每个文件的 SHA-256。

**执行与输出：**

- 生成候选 Phase 3 input inventory，逐项记录仓库相对路径、类型、大小、行数/记录数、SHA-256、上游状态和用途。
- 重算 Phase 1 为 400 个 `DrawRecord`（DLT=200、SSQ=200）及 800 个仅供血缘的 observation；拒绝样本膨胀。
- 验证 Phase 2.1 release 精确为 `P2.1-R00-61a99a2c3732-i07-r02`、`PASS / GO / indeterminate`、递归 manifest 闭包和 blocking findings=0。
- 验证注册规则版本、号码空间和期号覆盖；旧 `tasks/research/...` 只登记为 historical/non-authoritative，不进入输入选择。

**失败处理与证据：** 路径缺失、哈希/记录数不一致、release 错误、规则覆盖不唯一或权威冲突时终态为 `HOLD`；若发现上游不可恢复篡改则建议 `FAIL / STOP`。保存 inventory 差异、重算脚本 receipt 和原始错误，禁止修复历史 artifacts。

**验收方法：** 独立运行输入检查器；要求路径存在率、哈希匹配率、计数匹配率和规则 join 覆盖率均为 100%，不安全 `latest`/通配符引用为 0。通过后输出 W01 receipt 的路径和 SHA-256。

### W02：point-in-time、数据用途和规则合同

**依赖与输入：** W01 PASS 的 inventory；Phase 1 draws；注册规则 manifest；可选的准备期来源证据。当前数据的 `knowledge_class=retrospective_current_view` 且 `available_at_utc=null` 是强制起点。

**执行与输出：**

- 定义每个彩种和目标期的 `prediction_locked_at`、训练截止、标签解锁时点及规则段。
- 构造逐 `(game, target_issue, source_field)` 的 append-only availability ledger；每项记录来源内容哈希、事件时间、可用时间、证明方法和 `eligible|ineligible|unknown`。
- 生成数据/时间合同，明确结果标签、允许特征原子输入、禁止字段、修订视图、未知机器/球组/摇出顺序和规则变更处理。
- 只把有证据满足 `available_at < prediction_locked_at` 的字段纳入后续特征候选；`unknown` 与 `ineligible` 一律 fail closed。
- 若准备期使用网络，先保存内容并独立核对，再将来源、获取时间和哈希冻结；正式输入不引用网络、live 或 latest。

**失败处理与证据：** 无法证明先前开奖结果或 M1 输入在目标锁前可用时，不推定常识时间，记录受影响目标期和最小可用训练长度。若仍能按预注册最小样本执行，则排除不合格期；否则 `HOLD`。任何未来期、开奖后字段或全局变换混入均 `FAIL / STOP` 当前候选 release，并保留泄漏报告。

**验收方法：** Schema 校验、逐字段时间不等式检查、目标期故障注入、修订时间故障注入和双向 coverage 检查；合格特征中无证明项=0、未来/开奖后字段=0、规则零匹配/多匹配=0。

### W03：实验预注册设计、角色、错误预算和工作量换算规则

**依赖与输入：** W01 inventory、W02 合格目标/字段、Phase 2.1 audit/power 边界、总体设计的概率和模型合同。不得读取任何 Phase 3 历史模型结果。

**执行与输出：**

- 分 SSQ/DLT 列出全部 expanding-window outer targets、训练截止、最小训练长度和 inner rolling folds；每个 outer target 恰好一次。
- 冻结主指标（相对 M0 的逐期联合 log-score skill）、辅助指标（inclusion Brier、校准、可靠性、稳定性）、守护指标、敏感性、负控和 Top-1000 仅诊断角色。
- 冻结 M0/M1 配置、M2–M4 开放条件、所有搜索/先验/超参数范围、种子层级、数值容差、单变更规则、分类门和停止规则。
- 根据 Phase 2.1 功效限制和实际可用 outer targets 冻结假设/错误预算；证据不足时允许最终 `indeterminate`，不得为获得结论扩大未注册搜索。
- 指派数据保管、研究实现、统计负责人、独立 replay/review、最终验收等角色；被复核实现作者不得批准自己的分类。
- 冻结把 W04 小型 benchmark 转换为正式实验数、折数、重试数、墙钟/资源/制品预算的确定公式和裁决规则；W07 只代入观察事实，不得改变科学范围。不存在通用 VPS 规格门槛。

**失败处理与证据：** PIT 合格期不足、角色冲突、错误预算无法覆盖 M1、分类门或折设计未闭合时 `HOLD`。看见 Phase 3 结果后的语义修改必须废止当前候选 preregistration、保留其身份，并创建新 preregistration/release，不得原地编辑。

**验收方法：** JSON Schema、registry 双向差集、outer target 唯一性、inner/outer 时间不交叉、角色冲突和预算换算规则检查；独立方法预审 blocking findings=0 后签署结果盲合同候选 hash。W07 必须验证正式 preregistration 只增加按该公式计算的环境/workload 事实，没有改变 W03 科学语义。

### W04：统一 Schema、CLI、环境锁和研究实现

**依赖与输入：** W02 数据/时间接口、W03 预注册候选结构；只能使用合成/小世界和明确非正式的 benchmark 数据开发，不得生成正式历史模型结果。

**执行与输出：**

- 实现 Phase 3 input/preregistration/model/feature/fold/forecast/metric/experiment-ledger/replay/review/manifest/acceptance Schema，拒绝未知字段和非法状态转换。
- 提供统一离线 CLI：validate、qualify、run、evaluate、replay、verify-e2e、accept；每个命令稳定退出码并输出一个机器终态 receipt。
- 实现 M0、M1、合法组合/概率归一检查、outer/inner rolling evaluator、指标、Top-1000 诊断、checkpoint 和只追加 ledger。
- M1 使用固定基数联合概率和强收缩；验证零参数逐组合退化为 M0。M4 投影接口即使未开放也必须有拒绝未验证边际输出的合同测试。
- 锁定依赖、序列化、浮点容差和种子派生；固定输入顺序和规范化输出。
- 在实际执行环境运行冻结的小型 benchmark，记录事实和单位工作量，不设置架构/CPU/内存/磁盘通用门槛。

**失败处理与证据：** 非法概率、非确定输出、外层污染、ledger 可覆盖或依赖无法离线重建时实现不得进入 W05；保存失败测试和 benchmark 错误。实际 OOM、写盘失败、wheel 缺失等按观察错误 `HOLD`/失败，不从资源快照推断。

**验收方法：** 单元、known-answer、Schema、CLI、恢复和回归测试；小空间穷举误差在冻结容差内、非法组合=0、负概率=0、概率和失败=0、同种子规范化哈希一致率=100%、M1 零参数与 M0 一致率=100%。

### W05：模型/特征 registry 和开放判定冻结

**依赖与输入：** W02 合格字段、W03 预注册、W04 可执行合同、Phase 2.1 限制。仍不得读取 Phase 3 外层结果。

**执行与输出：**

- 建立模型 registry，记录数学定义、完整联合概率构造、输入、训练、超参数、消融、风险、开放条件、允许终态和代码/config hash。
- 建立特征 registry，逐项记录定义、原始字段、窗口、`available_at` 证明、规则适用、拟合范围、缺失策略、泄漏测试、消融组和终态。
- 固定 M0 为永久 Champion；M1 为 mandatory challenger。
- 对 M2、M3、M4 分别执行总体设计中的结果前开放条件检查，输出 `opened` 或 `not_opened` 及机器理由。当前证据不因 Phase 2.1 `indeterminate` 自动开放任何模型；同一 release 的 Phase 3 结果也不能回头开放模型。
- M5 明确为不可晋级负控，M6 明确禁止；M7 未完成同 harness 复现则不开放。

**失败处理与证据：** 未注册模型/特征、缺失时间证明、无界搜索、全号码对搜索、M4 未投影或模型状态与开放证据不符时拒绝冻结。保留每个 `not_opened` 的检查 receipt；`not_opened` 不是缺失实验。

**验收方法：** model/feature/preregistration 三方 registry 双向集合检查；所有实际运行模型注册率、所有特征时间证明率和 M2–M4 开放判定覆盖率均为 100%，越界模型=0。

### W06：资格、负控、泄漏和恢复验证

**依赖与输入：** W03–W05 冻结候选、合成小空间、均匀世界和预注册偏差世界；不得使用正式 outer evaluation 结果调试。

**执行与输出：**

- 精确枚举 M0/M1 小世界；测试真实号码空间概率规范、合法性、排序无关和分区/联合关系。
- 运行纯均匀、静态权重和已开放模型对应的注入偏差资格场景；测量评估器是否在均匀世界频繁选择假规律以及能否找回已知方向。
- 注入未来结果、开奖后字段、错误修订时间、全数据归一化、外层目标调参、规则混用、非法组合、负概率、不归一概率、失败 ledger 删除/覆盖、历史结果晋级 Champion 和 Top-1000 主门。
- 测试受控中断、checkpoint 恢复、重复命令、同 run ID 写入和部分 artifact 回传。
- 输出 qualification report、负控结果、失败 receipt 和 blocking finding 清单。

**失败处理与证据：** 任一概率/泄漏/隔离/ledger/越权陷阱未被拒绝即阻断 W07；修复实现后重新运行 W04–W06。若改变方法语义，回到 W03 并创建新 preregistration identity。所有失败资格运行永久保留。

**验收方法：** 独立 known-answer 对照和完整测试 registry；必需用例执行覆盖率=100%、预期终态命中率=100%、非法概率接受数=0、泄漏漏检=0、外层污染漏检=0、失败覆盖成功数=0。

### W07：VPS readiness、结果前冻结和正式 release 创建

**依赖与输入：** W01–W06 全部 PASS、独立方法预审、环境锁、benchmark 和批准工作量。这里是正式历史结果前最后冻结点。

**执行与输出：**

- 在 VPS 隔离 worktree 验证 task ID、绝对 worktree、task branch、精确 Git commit、dirty state、输入/prereg/registry/代码/依赖哈希和离线依赖可用性。
- 记录 factual environment 和 benchmark；据此填完批准模型×彩种×折×种子、最大重试、墙钟、checkpoint 和制品预算，只评估该 workload 是否可执行。
- 创建全局唯一 `release_id`；每条命令使用唯一 `run_id` 和新的不可覆盖目录。先写 run manifest/`started` 事件，再运行。
- 冻结显式 formal-run registry、artifact whitelist、日志路径、回传目标、监控信息和 acceptance iteration 规则；验证正式结果计数为 0。
- 正式执行切换到网络禁用/无网络输入模式。

**失败处理与证据：** 身份/哈希/dirty state/白名单不符、依赖缺失、正式结果路径已占用或回传 canary 失败时 `HOLD`，不得生成模型结果。唯一 identity 已创建后即永久保留失败 readiness 证据；重试用新 release/run identity。

**验收方法：** VPS 上运行只读 readiness validator；要求输入和代码哈希匹配率=100%、正式路径占用=0、预存正式结果=0、workload/命令/输出映射覆盖=100%、evidence-return canary PASS。

### W08：SSQ 与 DLT 外层滚动正式运行

**依赖与输入：** W07 冻结 release，只读 input manifest、availability ledger、preregistration、registry、代码和环境。正式进程不得联网。

**执行与输出：**

- 分彩种按预注册顺序执行 expanding-window rolling-origin；每个 outer target 先从 `t-1` 前合格数据建立特征快照，在 inner folds 内选择参数，再冻结并预测 `t`。
- 每个目标期至少运行 M0 和 M1；只运行 W05 已标记 `opened` 的 M2–M4。M5 若注册则独立标记负控。
- 在读取目标标签前保存模型/特征/训练截止/参数/随机种子、目标组合联合概率及预测制品哈希；读取标签后只追加评分事件。
- 逐期输出合法性、非负、归一化证明；生成 Top-1000 接口记录及覆盖概率，但不用于选择。
- 持续追加 experiment ledger、资源/墙钟/完成工作量、checkpoint、失败/超时/崩溃和最后命令状态。

**失败处理与证据：** 单实验失败不得被静默跳过或覆盖；保存部分制品并给出 `failed|timeout|crashed` 终态。预注册允许的确定性重试使用新 run ID 并引用父失败；超预算或可恢复环境错误 `HOLD`。泄漏、非法概率或 outer target 被二次最终评价时停止 release，建议 `FAIL / STOP`，不得继续挑选其他结果。

**验收方法：** 在线守护加运行后只读扫描；每个 `(game, outer_target, registered_open_model)` 恰有一个预期终态，每期最终评价次数=1，M0/M1 覆盖率=100%，跨彩种输出/训练交叉=0，正式网络请求=0，概率守护通过率=100%。

### W09：统一评价、ledger 闭包和冻结分类

**依赖与输入：** W08 的全部成功与失败运行、逐期预测、目标标签、预注册分类规则；不得新增模型、特征、折或指标。

**执行与输出：**

- 从逐期联合概率重算相对 M0 log-score skill；分彩种输出 outer-target、fold 和汇总结果。
- 计算 inclusion Brier、校准/可靠性、分折/时间/规则稳定性、负控、敏感性和所有守护指标；单独报告 Top-1000 合法性、确定性、覆盖概率和命中观察。
- 将正式 registry 与 ledger 双向反连接，确保成功、失败、超时、崩溃、未开放和淘汰均有终态。
- 依据预注册规则将每个模型分类为 `rejected|archived|shadow_candidate|not_opened|indeterminate`，并生成阶段科学汇总（可为 `no_shadow_candidate` 或 `indeterminate`）。
- 明确 M0 仍是 Champion；阻止任何历史结果触发 Champion 变更、非均匀发布、生产或投注。

**失败处理与证据：** 指标行缺失、结果后改口径、选择性排除、分类不唯一、Top-1000 成为主门或越权动作时停止验收。可恢复的单纯缺失计算用新 evaluate run 补齐并保留旧失败；语义问题返回 W03 新建 release，不能修补当前结果。

**验收方法：** 独立从逐期制品复算，registry/ledger/result 双向差集为空；两个彩种分别报告；主/辅/守护/负控/敏感性覆盖率=100%；失败删除/覆盖=0；模型分类集合合法率=100%；Champion 变更数=0。

### W10：独立 replay 和复核

**依赖与输入：** W07 冻结身份、W08 原始预测与 ledger、W09 评价；复核者只读正式证据，不使用主实现的顶层汇总作为真值。

**执行与输出：**

- 独立重建输入选择、规则 join、outer/inner folds、特征快照和选定核心模型输入。
- 独立参考实现复算 M0/M1 小空间与抽样真实期的联合概率、逐期 log score、Brier 和汇总；验证同种子规范化重放。
- 对所有 outer targets 复算折身份和一次评价不变量；复算 model classification 和阶段汇总。
- 输出 replay artifact、逐项差异、independence 声明、review report 和 blocking findings；复核者不批准自己编写的实现。

**失败处理与证据：** 超出预注册容差、输入/折/概率/指标/分类不一致或独立性冲突时 `HOLD` 并形成 blocking finding。泄漏、伪造或选择性删除建议 `FAIL / STOP`。不得由主实现者直接改写 review；修复产生新证据和新 review identity。

**验收方法：** replay Schema、同种子哈希、独立 known-answer 和逐项容差检查；输入/折一致率、选定概率/指标复算一致率和分类一致率均为 100%，blocking findings=0。

### W11：正式 E2E 和最终 validator 资格

**依赖与输入：** W01–W10 正式制品、冻结 E2E registry、隔离 staging 副本。E2E 不修改正式输入或正式 run。

**执行与输出：**

- 执行正常全链路以及输入/规则不一致、PIT/开奖后泄漏、非法/负/不归一概率、outer 污染、失败实验删除/覆盖、历史越权晋级、replay 不一致和 manifest/acceptance 篡改。
- 执行“无 challenger 合格”正例，必须得到诚实的 `GO / no_shadow_candidate`；执行证据不足正例，允许 `GO / indeterminate`。
- 验证 final validator 必须从底层逐期预测、ledger、replay 和显式 manifest 重算，而不是信顶层 status。
- 输出每个 E2E 的唯一 receipt、命令、预期/实际退出码、终态、断言和哈希。

**失败处理与证据：** 必需 E2E 缺失、重复、预期终态不符或负向用例被接受时阻断 W12。修复实现后回到 W04；若修改冻结研究语义则回到 W03 并创建新 release。失败 E2E 永不删除。

**验收方法：** E2E registry 与 receipts 双向差集；必需用例执行率和预期终态命中率=100%，未注册用例进入正式结论数=0，validator 自报而未重算字段数=0。

### W12：证据回传、显式 manifest、研究报告和唯一验收

**依赖与输入：** W01–W11 全部正式制品与 reviews；只接受冻结 release 下的明确路径和哈希。

**执行与输出：**

- 从 VPS 按 whitelist 回传合同、预注册、input manifest、registries、实现身份、Schema/tests、完整 ledger、预测/评价、replay/reviews、E2E、日志和 workload completion；逐文件重哈希。
- 生成不可变 final evidence manifest，显式列路径、SHA-256、run/release identity、行数/大小和交付物映射；禁止 `latest`、通配符、隐式目录或修改时间选择。
- 编写分彩种研究报告，逐结论引用结构化证据；清楚区分交付状态、模型分类和阶段科学汇总。
- 最终验收人运行离线 validator，从底层重算身份、覆盖、核心指标、分类、Champion 不变、blocking findings 和禁区动作；人工复核科学措辞。
- 原子写入唯一 acceptance artifact；成功时只能给出 `PASS / GO`，失败按合同给出 `HOLD` 或 `FAIL / STOP`。

**失败处理与证据：** 回传缺失/哈希差异、manifest 不闭合或 validator/review 未通过时不得签 GO。可恢复问题进入 W13 新 iteration；保留当前 manifest 候选、validator 输出和现场。不得原地把失败 acceptance 改成 PASS。

**验收方法：** 交付包项目覆盖率、manifest 血缘覆盖率、哈希匹配率、注册实验终态覆盖率和必需 E2E 覆盖率均为 100%；blocking findings=0；禁止措辞/Champion 变更/非均匀发布/投注动作均为 0；人工签署身份无角色冲突。

### W13：验收迭代、恢复或最终交接

**依赖与输入：** W12 acceptance 尝试、validator findings、完整场景和最后命令状态。

**执行与输出：**

- 若 W12 为 GO，冻结 release/manifest/acceptance，记录实际完成 workload，并交接历史研究基线、限制及可能的 `shadow_candidate` 清单；M0 继续为 Champion。
- 若为可恢复 HOLD，按 finding 判断最早受影响的 W01–W12 节点，创建唯一 iteration 和 run/release identity，引用而不覆盖旧证据，仅重做依赖于修复的后续步骤。
- 若为 FAIL / STOP，封存现场、输入、日志、部分制品、finding 和越界证据，停止正式计算。
- 任何未来 shadow 工作只作为新任务提出，不在本计划内启动。

**失败处理与证据：** 不允许用人工备注覆盖机器阻断，不允许把“没有好模型”误判为失败，也不允许为取得 shadow 候选修改历史门槛。每次 iteration 保存父 identity、变更原因、影响分析、重复工作和新 acceptance。

**验收方法：** GO 只在唯一最终 acceptance 为 `PASS / GO` 且 manifest 哈希闭合时成立；`no_shadow_candidate` 和 `indeterminate` 均可 GO。交接检查确认 M0 Champion、无生产/发布/投注动作、后续授权为空。

## 3. VPS 监控、恢复和证据检索协议

正式运行前登记 task ID、remote worktree、branch、commit、log、artifact path、run/release ID、命令和 PID/调度身份。监控只读事件日志、ledger 和 checkpoint；状态更新报告当前 work item、已完成折/实验、观察到的资源事实、剩余注册 workload 和最后成功命令，不给泛化 CPU/内存/磁盘门槛。

监控中断后按以下顺序恢复：

1. 从既有任务登记确认 task ID、远端绝对 worktree 和 branch/commit。
2. 确认唯一日志和 artifact path，读取 manifest/ledger/checkpoint，不先启动新命令。
3. 确认 last command state：仍运行、成功、受控中断、失败或未知。
4. 仍运行则只恢复监控；已成功则进入下一注册命令；受控中断且 checkpoint 完整才按冻结恢复命令继续；失败则保存终态并按 W08/W13 处理。

精确停止规则是：

> if monitoring recovery cannot confirm task ID, remote worktree, branch, log, artifact path, and last command state, report `NEEDS_INPUT`, preserve the scene, and wait for explicit recovery information.

在收到明确恢复信息前，不推测目录、不盲重跑、不创建同 ID 进程、不覆盖日志或 artifacts。证据检索只按显式 manifest 路径进行；回传后同时核对源端和接收端 SHA-256、文件数、字节数及缺失/额外路径。

## 4. 最终 acceptance 检查表

最终验收人必须能逐项回答“是”：

- 当前权威只有 `tasks/phase3/README.md`，所有正式输入路径存在且身份匹配；历史 research roadmap 未被当成权威。
- Phase 1 400 个 draw 的用途和 `available_at_utc=null` 限制得到 fail-closed 执行。
- SSQ/DLT 分开训练、评价和分类；outer evaluation 从未参与训练、调参或选择。
- M0 永久 Champion，M1 完整运行，M2–M4 均有结果前开放决定或合法 `not_opened`。
- 每个实际模型给出合法、非负、归一的固定基数联合概率；M1 零参数退化为 M0。
- 主指标是相对 M0 联合 log-score skill；Top-1000 不是主选择门。
- 预注册先于结果；所有正式实验和失败终态完整、不可覆盖、可归因。
- 独立 replay 复算输入、折、概率、指标和分类；blocking findings=0。
- 合同、预注册、input manifest、registries、实现、Schema/tests、ledger、结果、review、manifest 和 acceptance 全部交付并哈希闭合。
- 科学结果只使用允许枚举；`GO / no_shadow_candidate` 和 `GO / indeterminate` 可正常形成。
- 没有 Champion 晋级、非均匀公开预测、生产服务、自动购彩、投注或收益/中奖保证。

任一项为“否”时不得 `PASS / GO`；按 W12/W13 返回精确 HOLD 或 FAIL / STOP 证据。
