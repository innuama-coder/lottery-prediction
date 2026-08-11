# Phase 4 预测与 AutoResearch 闭环 MVP 详细实施计划

版本：1.0

状态：可执行计划候选；只定义后续开发、资格与验收，不在本任务中实现或运行 Phase 4

上位合同：Git `0142530a55ddb1b302ecf770907e30e52df63c04` 中 `ROADMAP.md`、`tasks/phase4/README.md`

总体设计：`docs/research/phase-4-overall-design.md`

## 1. 执行原则、角色和身份

所有下游只消费上游 `PASS` 且内容哈希固定的输出。每个任务有一个责任所有者和独立验收路径；作者不能仅以文件存在、自报 `PASS` 或自己生成的顶层汇总验收。可从底层重算的事实必须由独立脚本或角色重算。Phase 0–3 全部只读，Phase 4 staging/runtime/formal 三个 namespace 严格分离。

角色固定为 `release_controller`、`data_custodian`、`contract_owner`、`implementation_author`、`statistical_owner`、`run_operator`、`vps_operator`、`independent_oracle_author`、`independent_reviewer`、`acceptance_engineer`、`acceptance_approver`。`independent_oracle_author`/`independent_reviewer` 不得写被复核产品核心；`acceptance_engineer` 不得是实现作者或正式运行者；最终 `acceptance_approver` 不得是实现作者、运行者、oracle 作者或 reviewer。准备期和正式期 actor assignment 分别绑定 actor/task/session/任务记录 SHA-256，并只能新增版本。

身份变量由 release controller 发放，值不能含 `latest`、通配符、斜杠或 mtime 选择：

```bash
P4_PREP_ID=p4-prep-controller-issued-i01
P4_PREP_ROOT=artifacts/phase-4-prep/$P4_PREP_ID
P4_RUNTIME_ID=p4-runtime-readiness-i01
P4_RUNTIME_ROOT=artifacts/phase-4-runtime/$P4_RUNTIME_ID
P4_RELEASE_ID=P4-R01-<implementation-commit-first12>-<freeze-date-YYYYMMDD>-I01
P4_RELEASE_ROOT=artifacts/phase-4/$P4_RELEASE_ID
P4_PREP_ACTORS=$P4_PREP_ROOT/control/actor-assignments-preparation.json
P4_FORMAL_ACTORS=$P4_RELEASE_ROOT/control/actor-assignments-formal.json
```

标准退出码：`0=PASS/READY`、`20=HOLD`、`30=retryable terminal recorded`、`4=identity reuse`、`5=contract/evidence mismatch`、`6=security/causality failure`，其他非零为 FAIL。每项 receipt 固定为 `<prep-or-release>/work-items/<task-id>/receipt.json`，记录输入/输出哈希、执行命令、进程退出码、角色、起止时间、正负测试和 `PASS|HOLD|FAIL`；统一用以下独立 receipt checker 重算：

```bash
PYTHONPATH=src python3 scripts/phase4_independent/validate_work_item.py \
  --receipt "$RECEIPT" --actor-assignments "$ACTORS" --expected-task "$TASK_ID"
```

## 2. 无环依赖图、并行与关键路径

```text
T00 authority/genesis/protection
 -> T01 result-blind contracts, preregistration skeleton, CLI and Schemas
    -> T02 immutable ledger and Phase-4 data chain ---------+
    -> T03 official adapters, verification and calendar ----+
    -> T04 probability, exact tie/rank and Top-1000 --------+
    -> T05 forecast lock, time gates and label capability --+--> T09 integration CLI/state
    -> T06 metrics and correction closure ------------------+       |
    -> T07 AutoResearch and alpha controller ---------------+       +--> T11 product validation/E2E
    -> T08 scheduler, recovery and alerts ------------------+       |      |
    -> T10 independent numerical/full-rule oracles -----------------+      +--> T12 development qualification
                                                                              -> T13 independent power confirmation
T09 + T11 -> T14 dependency freeze and clean offline rebuild ------------------+
T13 + T14 -> T15 benchmark, resource/seed/identity freeze and formal release
 -> T16 formal A07-A10 qualification
 -> T17 formal positive/negative E2E, correction and protected-tree canary
 -> T18 VPS user-systemd readiness and evidence-return audit
 -> T19 single-release assembly and recursive evidence manifest
 -> T20 independent bottom-up replay, final validator, review and human signatures
 -> T21 independent final delivery acceptance
```

T02–T08 可在 T01 后按互不重叠模块并行；T10 可与产品实现并行但不得读取或导入产品核心。T12 只能使用 development seed；T13 冻结 design 后只能使用 power-confirmation seed；T15 才冻结 formal master seed、依赖、工作量、code/input/contract acceptance identity；T16 以前不得生成正式资格结果。T16–T21 全部串行且消费同一 `P4_RELEASE_ID`。关键路径是 `T00 -> T01 -> max(T02..T10) -> T09/T11 -> T12 -> T13 -> T14 -> T15 -> T16 -> T17 -> T18 -> T19 -> T20 -> T21`。

## 3. 任务合同

### T00：权威、genesis 与受保护树冻结

- **目标/执行角色：** `release_controller` 只建立不可变输入 inventory；不设计模型。
- **前置输入及固定身份：** Git `0142530a55ddb1b302ecf770907e30e52df63c04`；`ROADMAP.md` SHA-256 `24ba28e72c33959a91e505fd518718bd0c948c84b7e2e4cd5591a26f0a0b0149`；`tasks/phase4/README.md` SHA-256 `13b099c584c24c2bb7324f5fa852c9fac2dff7ad934245598eae2d117e701a75`；Phase 3 `P3-R07-2c0fa97-20260810-I01` acceptance SHA-256 `415bfc69cc04704265e231fd7d6e36bd2daa06b970b0def30703c4a7f04570c9`；Phase 1 四项 genesis（`baseline-v1`、`0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1`、`f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1`、`dc974863c845da1e895ecf623bc6e878ba6aa6710c902357bce68ad5e661966e`）。无任务依赖。
- **允许修改/禁止修改：** 允许 `config/phase4/authority-freeze.json`、`config/phase4/genesis.json`、`schemas/phase4/{authority-freeze,genesis,protected-inventory}.schema.json`、`scripts/phase4/freeze_authority.py`、`$P4_PREP_ROOT/control/`；禁止 Phase 0–3、产品代码、正式 release。
- **交付物及接口：** 上述两个严格 Schema JSON、`phase1-protected-inventory.json`（path/type/bytes/SHA）、preparation actor assignment/task records 和 T00 receipt；T00 同时拥有并验收三个仅描述 authority 的 Schema，不依赖 T01。
- **依赖/执行命令：** 无；`PYTHONPATH=src python3 scripts/phase4/freeze_authority.py --prep-id "$P4_PREP_ID" --commit 0142530a55ddb1b302ecf770907e30e52df63c04 --phase3-release P3-R07-2c0fa97-20260810-I01 --output "$P4_PREP_ROOT/work-items/T00" --actor-assignments "$P4_PREP_ACTORS"`。
- **独立验收标准与方法：** data custodian 从 Git 对象和文件字节重算所有 SHA、Phase 1 全路径集合及四项内容；路径/哈希/计数/角色绑定 100%，dirty 修改为 0。正测当前基线；负测换 commit、空 baseline、改一字节、删/加受保护文件、`latest` 路径。
- **失败终态/证据/取回：** 可恢复缺失为 `HOLD_AUTHORITY_IDENTITY`，上游篡改为 `FAIL_PROTECTED_ARTIFACT_MUTATION`；证据在 `$P4_PREP_ROOT/work-items/T00/`，按 receipt 显式清单取回并逐哈希。

### T01：结果盲合同、Schema、CLI 和 acceptance 冻结

- **目标/执行角色：** `contract_owner` 把总体设计的时间、身份、来源、日历、概率、指标、修订、研究、调度、故障/SLO 边界、角色、CLI、Schema 与 A01–A21 判定写成机器合同；不实现算法。
- **前置输入及固定身份：** T00 PASS receipt/hash、两份上位合同和总体设计提交身份。
- **允许修改/禁止修改：** 允许 `config/phase4/*.json`、`schemas/phase4/*.schema.json`、`docs/runbooks/phase-4-mvp-runtime.md`、`requirements/phase4.in` 候选；禁止产品实现、测试结果、正式 seeds/results 和 Phase 0–3。
- **交付物及接口：** source/calendar/time/model/feature/metric/correction/decision/alpha/schedule/fault/SLO/CLI contracts，qualification preregistration skeleton、E2E registry、所有 data-release/calendar/schedule/forecast/ranking/metric/experiment/decision/champion/model-status/top-k-status/alpha/manifest/review/acceptance Schema；未知字段拒绝，三类状态键和值按总体设计固定。
- **依赖/执行命令：** T00；`PYTHONPATH=src python3 scripts/phase4/validate_contract_bundle.py --config config/phase4 --schemas schemas/phase4 --authority-receipt "$P4_PREP_ROOT/work-items/T00/receipt.json" --output "$P4_PREP_ROOT/work-items/T01" --actor-assignments "$P4_PREP_ACTORS"`。
- **独立验收标准与方法：** acceptance engineer 逐 Schema 构造最小正例和维度删除/未来阶段状态/未知字段负例，检查 CLI verbs/参数/退出码无省略，A01–A21 各有底层断言且六类交付物齐全。负测时间类混用、全局 improved、Champion promotion verb、宽松概率和隐式外部服务。
- **失败终态/证据/取回：** `HOLD_MACHINE_CONTRACT`；若弱化上位合同为 `FAIL_CONTRACT_WEAKENED`。证据 `$P4_PREP_ROOT/work-items/T01/`，manifest 明列合同文件。

### T02：不可变账本、存储和 Phase 4 data release 链

- **目标/执行角色：** `implementation_author` 只实现 P4-CJSON-1、hash identity、事件链、原子存储、checkpoint 和 genesis/后继 data release。
- **前置输入及固定身份：** T00 genesis/protected inventory、T01 serialization/data-release/ledger/checkpoint Schema hashes。
- **允许修改/禁止修改：** 允许 `src/lottery_system/phase4/{serialization,identity,storage,ledger,checkpoint,data_chain}.py`、对应 `tests/phase4/`；禁止官方网络、模型、指标、Phase 0–3 写入。
- **交付物及接口：** `create_genesis`、`append_data_release`、`append_event`、`load_checkpoint` ports；runtime 只写 `artifacts/phase-4-runtime/<id>/`；T02 fixtures/receipt。
- **依赖/执行命令：** T01；`PYTHONPATH=src python3 -m unittest tests.phase4.test_identity tests.phase4.test_ledger tests.phase4.test_data_chain -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 data genesis --runtime-root "$P4_RUNTIME_ROOT" --genesis config/phase4/genesis.json --clock fixture:2026-01-01T00:00:00Z`。
- **独立验收标准与方法：** independent checker 从 baseline bytes 重建 genesis、逐事件 previous hash 和 current view；crash 前后只允许旧完整对象或新完整对象。正测连续三 release/同 identity resume；负测断链、换 genesis、空内容、分叉拼接、重复 sequence、torn write、checkpoint wrong head、Phase 1 path resolve。
- **失败终态/证据/取回：** `HOLD_STORAGE_SEMANTICS|HOLD_DATA_CHAIN`；写到保护树或覆盖历史为 FAIL。证据 `$P4_PREP_ROOT/work-items/T02/` 和隔离 runtime manifest。

### T03：官方适配器、核验、修订识别和显式日历

- **目标/执行角色：** `data_custodian` 负责来源 port/adapter、raw receipt、双源核验、dedup/revision 与显式 calendar release；不评分。
- **前置输入及固定身份：** T01 source/calendar/correction contracts，T02 storage/data-chain ports；固定 source IDs 为 SSQ `swlc+ydniu`、DLT `gdlottery+ydniu`。
- **允许修改/禁止修改：** 允许 `official_adapter.py`、`verification.py`、`calendar.py`、固定响应 fixtures/tests；禁止模型/score/research、未经 allowlist 网络和 Phase 1 文件。
- **交付物及接口：** transport observation、verified result revision、calendar release/build validator；`config/phase4/calendar-policy.json` 显式 CST/UTC mapping；fixture outputs。
- **依赖/执行命令：** T01,T02；`PYTHONPATH=src python3 -m unittest tests.phase4.test_official_adapter tests.phase4.test_calendar -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 calendar build --fixture tests/phase4/fixtures/calendar/valid.json --output "$P4_PREP_ROOT/work-items/T03/calendar" --clock fixture:2026-01-01T00:00:00Z`。
- **独立验收标准与方法：** 从 raw bytes 用独立 parser 重算两 game target/result/date/numbers/revision，zoneinfo 重算所有 UTC；fixed responses 覆盖连接/解析/修订/去重/Schema compatibility。负测单源、冲突、HTML/JSON 结构漂移、跨 host redirect、POST、倒退/重复/多义期号、DST/服务器时区扰动。
- **失败终态/证据/取回：** 网络/单源 `HOLD_SOURCE_PENDING`，冲突 `HOLD_SOURCE_CONFLICT`，多义日历 `HOLD_CALENDAR_AMBIGUOUS`；证据 `$P4_PREP_ROOT/work-items/T03/`。

### T04：严格概率、exact tie/rank 与确定 Top-1000

- **目标/执行角色：** `implementation_author` 只实现 SSQ/DLT rules、P4E1 Decimal 概率、order/tie key、完整空间 histogram/rank 和 1,000 注算法。
- **前置输入及固定身份：** T01 rule/model/probability/ranking contracts；T02 canonical serialization。
- **允许修改/禁止修改：** 允许 `rules.py`、`probability.py`、`ranking.py` 及测试；禁止 float `isclose` tie、模型搜索、指标/acceptance。
- **交付物及接口：** `distribution`, `normalization_proof`, `rank_histogram`, `top1000` pure APIs，hash vectors 和 product known-answer。
- **依赖/执行命令：** T01,T02；`PYTHONPATH=src python3 -m unittest tests.phase4.test_rules_probability_ranking -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 validate unit --scope probability-ranking --output "$P4_PREP_ROOT/work-items/T04"`。
- **独立验收标准与方法：** T10 的 direct enumeration 小空间和独立 DP 真实空间逐值对比；严格正/归一、1000 数量/唯一/前缀、key 顺序、tie group/rank、M0 两空间 group size 全部 100%。负测 zero/negative/NaN/Inf、量化越界、输入排列、非传递近似、key 碰撞、跨 Top-K tie、非法号码。
- **失败终态/证据/取回：** `HOLD_UNSUPPORTED_TIE_SEMANTICS`；错误结果被发布为 FAIL。证据 `$P4_PREP_ROOT/work-items/T04/`。

### T05：forecast、原子 lock、三类时间与 label capability

- **目标/执行角色：** `implementation_author` 只实现 label-free snapshot、Champion/shadow forecast body/diagnostic、deadline lock 和 scorer-only unlock。
- **前置输入及固定身份：** T01 time/forecast contracts；T02 ledger；T03 calendar/data ports；T04 probability API。
- **允许修改/禁止修改：** 允许 `forecast.py`、`lock.py`、`time_gate.py`、`label_capability.py` 及测试；禁止 score/research、锁后修改和 historical `available_at` 合成。
- **交付物及接口：** `prepare/generate/lock/unlock` application ports、lock/unlock receipts、trainer quarantine probe。
- **依赖/执行命令：** T02,T03,T04；`PYTHONPATH=src python3 -m unittest tests.phase4.test_forecast_lock tests.phase4.test_label_capability -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 validate unit --scope forecast-time-label --output "$P4_PREP_ROOT/work-items/T05"`。
- **独立验收标准与方法：** verifier 在第一次 label read 前核对 lock/event/file/hash/identity/clock；三时间类型正例各自通过且互换失败。负测 pre-lock、future prefix、external no PIT、锁后 mutation、wrong release/run/game/issue/model/data/calendar/metric/result、trainer filesystem/child-process/capability access、deadline 后补锁。
- **失败终态/证据/取回：** 可恢复 identity 缺失 HOLD；label leak/锁后改写 `FAIL_CAUSALITY_OR_TAMPER`。证据 `$P4_PREP_ROOT/work-items/T05/`。

### T06：forecast 诊断、score、窗口与官方修订闭包

- **目标/执行角色：** `implementation_author` 只实现总体设计第 9 节公式、aggregate current view 和 correction transaction。
- **前置输入及固定身份：** T01 metric/correction contracts；T02 data/ledger；T04 probability/rank；T05 locked forecast/unlock。
- **允许修改/禁止修改：** 允许 `metrics.py`、`windows.py`、`correction.py` 及测试；禁止改 forecast/metric contract/alpha 历史。
- **交付物及接口：** diagnostic、score、window、impact manifest、corrected score/aggregate/current replacement APIs。
- **依赖/执行命令：** T02,T04,T05；`PYTHONPATH=src python3 -m unittest tests.phase4.test_metrics tests.phase4.test_correction -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 score correct --fixture tests/phase4/fixtures/correction/valid.json --runtime-root "$P4_RUNTIME_ROOT" --clock fixture:2026-01-02T00:00:00Z`。
- **独立验收标准与方法：** T10 oracle 从 ticket/label/inclusion vectors 重算全部 Decimal 值、30 样本门、bin boundary/Wilson/rank；从旧/新 revision 反向列影响闭包。负测诊断绑 result、首期 wrong comparator、小样本伪数值、0.1 bin 边界、跨 tie、零概率、同 issue 多 revision 计数、部分传播、退款/重复 spend、旧链头复活。
- **失败终态/证据/取回：** `FAIL_METRIC_ORACLE_MISMATCH` 或 `HOLD_CORRECTION_INCOMPLETE`；覆盖/重复为 FAIL。证据 `$P4_PREP_ROOT/work-items/T06/`。

### T07：AutoResearch、candidate/diff、alpha 和 shadow lifecycle

- **目标/执行角色：** `statistical_owner` 冻结控制器，`implementation_author` 实现；该任务只拥有 research control plane。
- **前置输入及固定身份：** T01 model/feature/decision/alpha contracts；T02 ledger/checkpoint；T06 current scores/correction hold port。
- **允许修改/禁止修改：** 允许 `research/{registry,proposal,sequential,alpha,controller}.py` 及测试；禁止采集、规则、label、score、acceptance、Champion mutation和任意代码搜索。
- **交付物及接口：** parameter/feature canonical diff、candidate ID、one-experiment-per-cycle decision、wealth events、resume、next-shadow eligibility。
- **依赖/执行命令：** T02,T06；`PYTHONPATH=src python3 -m unittest tests.phase4.test_research_controller -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 research decide --fixture tests/phase4/fixtures/research/parameter-positive.json --runtime-root "$P4_RUNTIME_ROOT" --clock fixture:2026-01-03T00:00:00Z`。
- **独立验收标准与方法：** independent ledger reducer 重算 family wealth/spend/look bounds/stop/decision；parameter 和 feature 正例均生成新 ID/diff 并改变 next shadow；no eligible/budget/guard/no-change 产生零实验理由。负测多个 family 同 experiment、unregistered diff、negative wealth、look after stop、duplicate spending、revision refund、direct Champion、config change no output。
- **失败终态/证据/取回：** `FAIL_ALPHA_OR_GOVERNANCE`；能力未实现 `HOLD_ADJUSTMENT_CAPABILITY`。证据 `$P4_PREP_ROOT/work-items/T07/`。

### T08：计划触发、并发、恢复和告警

- **目标/执行角色：** `implementation_author` 只实现 schedule build/tick、plan lease、补偿、checkpoint orchestration 和 structured alerts；不拥有业务事实。
- **前置输入及固定身份：** T01 schedule/fault contracts；T02 storage；T03 calendar；T05–T07 application ports。
- **允许修改/禁止修改：** 允许 `scheduler.py`、`orchestrator.py`、`recovery.py`、`alerts.py`、`deploy/systemd-user/`、测试；禁止 root unit、cron 隐式配置和跨 game cancellation。
- **交付物及接口：** plan ledger、virtual clock、systemd user unit/timer templates、audit parser、runbook sections。
- **依赖/执行命令：** T02,T03,T05,T06,T07；`PYTHONPATH=src python3 -m unittest tests.phase4.test_scheduler_recovery -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 schedule tick --schedule tests/phase4/fixtures/schedule/dual-game.json --runtime-root "$P4_RUNTIME_ROOT" --clock fixture:2026-01-03T09:00:00Z`。
- **独立验收标准与方法：** virtual-clock reducer 重算每 plan 唯一 run/terminal/alert；正测双 game 准时与 compensation/restart；负测 early/late/missed/deadline、duplicate/concurrent、crash each stage、wrong checkpoint、issue rollback、one-game network failure。重复 side effects 全为 0。
- **失败终态/证据/取回：** `HOLD_RECOVERY_MISMATCH|HOLD_SCHEDULER_AUDIT`；deadline 后有效 lock 或重复 side effect 为 FAIL。证据 `$P4_PREP_ROOT/work-items/T08/`。

### T09：统一 CLI、状态矩阵和组件集成

- **目标/执行角色：** `implementation_author` 只接线 CLI/config/application ports，生成三类状态投影；不重写领域算法。
- **前置输入及固定身份：** T02–T08 PASS receipts、T01 CLI/state schemas。
- **允许修改/禁止修改：** 允许 `cli.py`、`__main__.py`、`state_projection.py`、packaging entrypoint；禁止组件循环 import、未来阶段状态和全局 improved。
- **交付物及接口：** 总体设计第 6 节全部 verbs、stable exits、`state project/show`、integration receipt。
- **依赖/执行命令：** T02–T08；`PYTHONPATH=src python3 -m unittest tests.phase4.test_cli_state_integration -v`；对每 verb 执行 `--help` 和固定 fixture smoke；`PYTHONPATH=src python3 -m lottery_system.phase4 state project --runtime-root "$P4_RUNTIME_ROOT" --output "$P4_PREP_ROOT/work-items/T09/state"`。
- **独立验收标准与方法：** 依赖图无环；CLI registry 与 parser 双向集合相等；完整键逐 event 重算，Phase 4 仅允许工程/模型/Top-K 指定值。负测删 game/K/comparator/release/window、跨 game join、future transition、global improved、implicit latest/外部服务。
- **失败终态/证据/取回：** `FAIL_STATE_MATRIX|HOLD_CLI_CONTRACT`；证据 `$P4_PREP_ROOT/work-items/T09/`。

### T10：独立概率、指标和 full-rule oracle

- **目标/执行角色：** `independent_oracle_author` 编写不导入产品包的直接枚举/Decimal DP 参考路径；不修改产品实现。
- **前置输入及固定身份：** T01 mathematical contracts；可以读取 T04/T06 的接口格式但不能复制其核心源码或正式输出。
- **允许修改/禁止修改：** 只允许 `scripts/phase4_independent/oracle_*.py`、`tests/phase4_oracle/`、`qualification-design/full-rule-spec-candidate.json`；禁止 `src/lottery_system/phase4/` 和顶层 PASS 信任。
- **交付物及接口：** 小空间概率/rank/metric vectors、full-rule 八单元 oracle、import audit、误差界、独立 source hash。
- **依赖/执行命令：** T01；`python3 scripts/phase4_independent/run_known_answers.py --spec config/phase4 --output "$P4_PREP_ROOT/work-items/T10"`；`python3 scripts/phase4_independent/check_import_independence.py scripts/phase4_independent`。
- **独立验收标准与方法：** acceptance engineer 静态检查无产品 import，并用 hand-calculated tiny cases交叉核对；相同数学输入 hash 稳定。负测产品输出反构造 distribution、少 K、布尔-only better、容差/规则缺失、import 产品 normalization/top-k。
- **失败终态/证据/取回：** `HOLD_ORACLE_NOT_FROZEN|HOLD_INDEPENDENCE`；证据 `$P4_PREP_ROOT/work-items/T10/`。

### T11：产品单元/Schema/正负 E2E 与 final validator 资格

- **目标/执行角色：** `acceptance_engineer` 维护测试 registry 和隔离 mutation harness；实现作者只修产品，不能批准结果。
- **前置输入及固定身份：** T01 E2E/A01–A21 contracts，T09 integrated CLI，T10 oracles。
- **允许修改/禁止修改：** 允许 `tests/phase4/`、`scripts/phase4/validate_bottom_up.py`；产品修复仅回到对应 T02–T09 并新 receipt；禁止删失败用例、硬编码 actual terminal。
- **交付物及接口：** 单元/属性/Schema/两 game cycle/adjustment/revision/recovery/time/governance/state/scheduler E2E，registered guard map，pre-acceptance final-validator harness。
- **依赖/执行命令：** T09,T10；`PYTHONPATH=src python3 -m unittest discover -s tests/phase4 -p 'test_*.py' -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 validate e2e --registry config/phase4/e2e-registry.json --output "$P4_PREP_ROOT/work-items/T11/e2e" --clock fixture`。
- **独立验收标准与方法：** registry/receipt 双向差集为空，实际 isolated mutation + distinct validator process，期望 guard/exit/terminal 命中率 100%；无关 missing/malformed failure 不能算通过；Phase 3 regression suite通过。
- **失败终态/证据/取回：** `HOLD_E2E_INCOMPLETE`；负向被接受或伪造 terminal 为 FAIL。全部失败 receipts 原样保存在 `$P4_PREP_ROOT/work-items/T11/`。

### T12：development-seed 资格设计选择

- **目标/执行角色：** `statistical_owner` 只在总体设计固定效应菜单和控制器空间内，用 development domain 选择最弱可行 design；不是正式功效或资格。
- **前置输入及固定身份：** T07 controller、T10 oracle、T11 qualification harness、T01 prereg skeleton。
- **允许修改/禁止修改：** 允许 `$P4_PREP_ROOT/qualification-design/development/` 和候选 design；禁止 power/formal seed、正式 release、菜单外调参和把开发结果用于 acceptance。
- **交付物及接口：** 全菜单运行、逐 design 失败也保留、确定选择 receipt、candidate design ID、generator/controller source hashes。
- **依赖/执行命令：** T07,T10,T11；`PYTHONPATH=src python3 -m lottery_system.phase4 research run --mode development-design-selection --preregistration config/phase4/qualification-preregistration.json --output "$P4_PREP_ROOT/qualification-design/development" --seed-domain development --clock fixture`。
- **独立验收标准与方法：** 独立脚本重派生 development seeds、重算菜单顺序和“首个可行”选择，未运行/删除/挑选为 0；结果明确 `non_formal=true`。边界测试无可行 design 必须诚实 HOLD，不自动加大菜单。
- **失败终态/证据/取回：** `HOLD_NO_DESIGN_CANDIDATE`；证据 `$P4_PREP_ROOT/qualification-design/development/`。

### T13：独立 power-confirmation 与 qualification-design 冻结

- **目标/执行角色：** `independent_reviewer` 使用未参与选择的 power-confirmation seeds，确认 uniform 门与六个正控门预计通过概率均至少 90%，随后冻结 design；不允许反馈调参。
- **前置输入及固定身份：** T12 selected design ID/hash、T01 seed derivation/confidence contract、T10 oracle。
- **允许修改/禁止修改：** 允许 `$P4_PREP_ROOT/qualification-design/power/` 和 signed design freeze；禁止产品/selected design 原地修改、development/formal seed 和删除失败 power。
- **交付物及接口：** 7 个门的 pass probability/95% interval、seed-set hashes/intersection report、full-rule spec/oracle expected values、`qualification-design.json` 签署。
- **依赖/执行命令：** T12；`python3 scripts/phase4_independent/confirm_power.py --design "$P4_PREP_ROOT/qualification-design/development/selected-design.json" --seed-domain power-confirmation --output "$P4_PREP_ROOT/qualification-design/power"`。
- **独立验收标准与方法：** acceptance engineer 从序列终态重算概率/区间、domain strings/集合交集 0、设计 hash；所有门 `>=0.90` 才 PASS。边界含 0.899999、缺序列、重复 seed、事后 design change。
- **失败终态/证据/取回：** `HOLD_DESIGN_NOT_POWERED`；后续必须新 design ID 和确定新 power seeds，旧目录保留。证据按 power manifest 取回。

### T14：依赖冻结、wheelhouse 和干净离线重建

- **目标/执行角色：** `release_controller` 冻结全转移依赖并在一次性干净目录验证安装；不运行正式资格。
- **前置输入及固定身份：** T09 package、T11 tests；Python `>=3.12,<3.13`；T01 dependency policy。
- **允许修改/禁止修改：** 允许 `requirements/phase4.lock`、`pyproject.toml` Phase4 package data/entrypoint、`$P4_PREP_ROOT/wheelhouse/` 和 receipts；禁止未锁依赖、部署状态、正式 release 结果。
- **交付物及接口：** hash lock、wheelhouse manifest、sdist/wheel hashes、fresh venv offline install receipt、CLI/fixture/checkpoint/replay smoke。
- **依赖/执行命令：** T09,T11；`python3 -m pip wheel --require-hashes -r requirements/phase4.lock --wheel-dir "$P4_PREP_ROOT/wheelhouse"`；`python3 scripts/phase4/verify_offline_rebuild.py --wheelhouse "$P4_PREP_ROOT/wheelhouse" --lock requirements/phase4.lock --output "$P4_PREP_ROOT/work-items/T14"`。
- **独立验收标准与方法：** 新临时目录断网、`--no-index --find-links` 安装，记录 OS/arch/Python/resources facts、commands/exits；无网络请求、缺 wheel、版本/hash 差异和隐式 service。负测移除 wheel、改 lock、清空 pip cache、错误 Python。
- **失败终态/证据/取回：** `HOLD_INSTALL_OR_DEPENDENCY`；证据 `$P4_PREP_ROOT/work-items/T14/` 和 wheel manifest。

### T15：benchmark、资源/seed/acceptance identity 冻结并创建正式 release

- **目标/执行角色：** `release_controller` 把观察 benchmark 代入固定公式，冻结批准 workload、formal actor assignment、formal seed、code/input/contracts/dependencies 和唯一空 release；这是正式结果前最后门。
- **前置输入及固定身份：** T00–T14 全 PASS；T13 design；T14 wheelhouse；当前 clean Git commit。
- **允许修改/禁止修改：** 允许 `$P4_RELEASE_ROOT/{control,contracts,inputs,qualification-design,readiness,work-items/T15}`；禁止运行 formal sequence、修改 prep evidence/Phase 0–3 和预存成功结果。
- **交付物及接口：** 8 benchmark units×20、p95/RSS/bytes、并行选择、25% budget/timeout/checkpoint；formal 8,000 sequence identities/master hash、artifact whitelist、commands、actor assignment、acceptance contract、formal authorization=false->true receipt。
- **依赖/执行命令：** T13,T14；`PYTHONPATH=src python3 -m lottery_system.phase4 release assemble --phase prepare-formal --prep-root "$P4_PREP_ROOT" --release-root "$P4_RELEASE_ROOT" --design "$P4_PREP_ROOT/qualification-design/power/qualification-design.json" --actor-assignments "$P4_FORMAL_ACTORS" --output "$P4_RELEASE_ROOT/work-items/T15"`。
- **独立验收标准与方法：** verifier 重算全部 inputs/hashes/seed disjoint/workload formula、release 原先不存在、正式结果计数 0、dirty 0、角色无冲突、evidence-return canary path可写。负测预算缺 unit、并行改变 hash、release reuse、预存 result、角色自审、formal seed overlap。
- **失败终态/证据/取回：** `HOLD_DEPENDENCY_OR_BUDGET|HOLD_FORMAL_FREEZE`；身份已发放则封存，重试新 release ID。证据 `$P4_RELEASE_ROOT/work-items/T15/`。

### T16：正式小空间资格与 full-rule A07–A10

- **目标/执行角色：** `run_operator` 只执行冻结 workload；不能修改代码、设计、seeds、thresholds 或选择输出。
- **前置输入及固定身份：** T15 formal authorization、同一 release contracts/design/8,000 sequence identities、离线 venv。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/qualification/`、ledger/checkpoints/logs、T16 receipt；禁止网络、输入/代码/阈值、删除失败序列和 Champion。
- **交付物及接口：** 2,000 uniform、六个各 1,000 正控的逐 sequence terminals，alpha events，formal summary；八个 full-rule product/oracle 数值和误差界。
- **依赖/执行命令：** T15；先 `PYTHONPATH=src python3 -m lottery_system.phase4 research run --mode formal-qualification --release-root "$P4_RELEASE_ROOT" --stop-after-sequences 10` 得受控 exit 20/checkpoint；再同 identity `--resume` 完成；随后 `python3 scripts/phase4_independent/run_full_rule_oracle.py --release-root "$P4_RELEASE_ROOT" --output "$P4_RELEASE_ROOT/qualification/full-rule-oracle"`。
- **独立验收标准与方法：** independent reducer 从 8,000 terminals 重算 uniform false proposal `<=5%`、六 recovery `>=90%`、wealth/stop match 100%、Champion changes 0；八 K candidate coverage 严格大于 `K/M` 且产品/oracle在容差内。负测 missing/duplicate sequence、换 seed/effect、resume wrong hash、budget after exhaustion、选择性删除。
- **失败终态/证据/取回：** 数值门失败为 `FAIL_FORMAL_QUALIFICATION`（不是换配置重试）；可恢复中断为 HOLD；证据 `$P4_RELEASE_ROOT/qualification/` 全量按 manifest 取回。

### T17：同 release 正负 E2E、修订、canary 与 Phase 1 保护

- **目标/执行角色：** `acceptance_engineer` 运行冻结 fixture/virtual-clock E2E 和只读官方 canary；不修实现、不创造结论。
- **前置输入及固定身份：** T16 PASS、T15 E2E registry/source policy/protected inventory，同一 code/input contracts。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/e2e/`、`readiness/official-canary/`、T17 receipt和隔离 `artifacts/phase-4-staging/<canary-id>/`；禁止 Phase 1 写入、等待未来开奖、真实 performance 结论。
- **交付物及接口：** 所有正负 receipts、correction interruption/resume、virtual clock、canary raw/parse/dedup/revision/compatibility/network terminals、Phase 1 before/after inventories。
- **依赖/执行命令：** T16；`PYTHONPATH=src python3 -m lottery_system.phase4 validate e2e --registry "$P4_RELEASE_ROOT/contracts/e2e-registry.json" --release-root "$P4_RELEASE_ROOT" --output "$P4_RELEASE_ROOT/e2e" --clock fixture`；`PYTHONPATH=src python3 -m lottery_system.phase4 data ingest --mode readonly-canary --source-policy "$P4_RELEASE_ROOT/contracts/source-policy.json" --staging-root artifacts/phase-4-staging/$P4_RELEASE_ID-canary --output "$P4_RELEASE_ROOT/readiness/official-canary"`。
- **独立验收标准与方法：** registry双向覆盖/guard命中 100%；两 game 已公开期次解析、rule/revision/dedup/Phase1 Schema compatible；网络失败命中注册 terminal也可作为失败语义证据，但每个 game 都至少有一个成功官方主源响应才 A14 readiness PASS。Phase 1 before/after exact match。
- **失败终态/证据/取回：** canary不可恢复时 `HOLD_DATA_SOURCE_READINESS`，负向被接受 FAIL，Phase1变化 `FAIL_PROTECTED_ARTIFACT_MUTATION`。证据为显式 canary/E2E manifests。

### T18：VPS 用户级 systemd readiness、恢复和证据回传

- **目标/执行角色：** `vps_operator` 在普通用户权限的目标 VPS 安装/反查 user unit，并在全新环境执行 CLI smoke、fixture、checkpoint resume、release replay 和 evidence-return；不宣称持续 SLO。
- **前置输入及固定身份：** T17 PASS、T14 wheelhouse、T15 frozen schedule/unit hashes、同一 release。
- **允许修改/禁止修改：** 允许用户级 venv/runtime、`~/.config/systemd/user/` 两个冻结 unit、`$P4_RELEASE_ROOT/readiness/vps/`；禁止 sudo/root/system unit、正式 evidence mutation、网络 pip 和等待真实开奖。
- **交付物及接口：** install commands/exits、environment facts、`systemctl --user cat/show/list-timers` audit、virtual plan trigger、concurrency/compensation/restart/deadline receipts、recovery timing、evidence-return source/destination hashes。
- **依赖/执行命令：** T17；`python3 scripts/phase4/install_user_systemd.py --release-root "$P4_RELEASE_ROOT" --runtime-root "$P4_RUNTIME_ROOT" --output "$P4_RELEASE_ROOT/readiness/vps"`；`PYTHONPATH=src python3 -m lottery_system.phase4 schedule audit --release-root "$P4_RELEASE_ROOT" --runtime-root "$P4_RUNTIME_ROOT" --output "$P4_RELEASE_ROOT/readiness/vps/scheduler-audit.json"`。
- **独立验收标准与方法：** independent reviewer 核对 absolute executable/args/workdir/timezone/5-minute timer/Persistent/RandomizedDelay/concurrency/next plan，与 frozen schedule 100% 一致；clean offline install/smoke/recovery/replay/return全 PASS，批准 workload benchmark 预算内。无任意硬件门。
- **失败终态/证据/取回：** user manager/linger在权限内不可用 `HOLD_SCHEDULER_UNAVAILABLE`；install/replay/return `HOLD_INSTALL_OR_WORKLOAD`。证据 `$P4_RELEASE_ROOT/readiness/vps/`；卸载不删除审计证据。

### T19：单一 release 装配和递归 evidence manifest

- **目标/执行角色：** `release_controller` 只装配 T15–T18 的同 identity 证据并生成 evidence manifest；不重算科学结论。
- **前置输入及固定身份：** T15–T18 receipts，同一 code/input/contracts/seeds/dependencies/release ID。
- **允许修改/禁止修改：** 允许 `$P4_RELEASE_ROOT/{reports,manifest/evidence-manifest.json,work-items/T19}`；禁止修改已列文件、添加另一个正式 release、隐式 glob/latest/mtime selection。
- **交付物及接口：** 六类交付 coverage map、逐文件 path/role/sha/bytes/parents、inventory hash、分彩种工程/科学摘要（只列矩阵）、evidence-return package list。
- **依赖/执行命令：** T18；`PYTHONPATH=src python3 -m lottery_system.phase4 release assemble --phase evidence --release-root "$P4_RELEASE_ROOT" --whitelist "$P4_RELEASE_ROOT/control/artifact-whitelist.json" --output "$P4_RELEASE_ROOT/manifest/evidence-manifest.json"`。
- **独立验收标准与方法：** manifest checker 从磁盘逐文件重算，缺失/额外/哈希/parent mismatch 0；六类覆盖 100%；禁止措辞 scan 0；manifest 不包含自身 hash 循环，后置允许集仅 T20/T21 明列路径。
- **失败终态/证据/取回：** `HOLD_MANIFEST_NOT_CLOSED`；选择性删除/伪造为 FAIL。取回只消费 manifest paths并在两端重哈希。

### T20：独立 bottom-up replay、最终 validator、review 与人工签署

- **目标/执行角色：** `independent_reviewer` 执行 replay/review；`acceptance_engineer` 执行 final validator；指定人工签署者审科学措辞。三者均不修改产品或既有证据。
- **前置输入及固定身份：** T19 evidence manifest SHA、T15 actor/acceptance contract、同一 frozen release；不以产品 summary 为真值。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/{replay,validator,review,signatures,manifest/review-closure.json,work-items/T20}`；禁止 `src/`、contracts/inputs/qualification/e2e/readiness/runs 和 acceptance。
- **交付物及接口：** 独立从 genesis/raw fixtures/events 重算 forecast IDs/Top-K/probability/rank/metrics/Champion/三状态/wealth/decisions/correction/current views/manifest；A01–A21 每项 PASS/HOLD/FAIL 与 finding；review independence；人工 delivery/scientific wording signatures。
- **依赖/执行命令：** T19；`python3 scripts/phase4_independent/replay_release.py --release-root "$P4_RELEASE_ROOT" --manifest "$P4_RELEASE_ROOT/manifest/evidence-manifest.json" --output "$P4_RELEASE_ROOT/replay"`；`PYTHONPATH=src python3 scripts/phase4/validate_bottom_up.py --release-root "$P4_RELEASE_ROOT" --replay "$P4_RELEASE_ROOT/replay/replay.json" --output "$P4_RELEASE_ROOT/validator/final-validator.json" --actor-assignments "$P4_FORMAL_ACTORS"`。
- **独立验收标准与方法：** replay match 100%、A01–A21 全由底层断言、blocking 0、六类覆盖 100%、角色冲突 0；人工确认无真实 improvement/中奖/收益宣称。负测隔离副本中 ledger/event/seed/score/state/review/manifest mutation，正确 guard 才算通过。
- **失败终态/证据/取回：** 可恢复 mismatch `HOLD_REPLAY_OR_REVIEW`，泄漏/伪造/选择性删除/越权 FAIL。全部 findings 和失败 replay 永久保留于 T20 路径。

### T21：独立最终交付验收（最后任务）

- **目标/执行角色：** 仅 `acceptance_approver` 对 T20 已验证的同一冻结 release 从底层证据签发唯一工程终态；作者不得验收自己的实现。
- **前置输入及固定身份：** T20 review closure、replay、final validator、两个人工签署、T19 manifest、T15 acceptance contract/actor assignment，全部同 `P4_RELEASE_ID` 和固定 SHA。
- **允许修改/禁止修改：** 只允许新建 `$P4_RELEASE_ROOT/acceptance/I01/{acceptance.json,postcheck.json}` 和 T21 receipt；禁止修改任何既有文件、重新跑/挑选 qualification、改变状态或结论。
- **交付物及接口：** acceptance Schema 包含 A01–A21 derived results、blocking findings、六类 coverage、工程状态、逐 game/model/K 科学矩阵、Champion、review/signature/manifest hashes；只在全部门通过时 `status=PASS, engineering_status=SYSTEM_MVP_GO`。
- **依赖/执行命令：** T20；`PYTHONPATH=src python3 -m lottery_system.phase4 release accept --release-root "$P4_RELEASE_ROOT" --iteration I01 --validator "$P4_RELEASE_ROOT/validator/final-validator.json" --review "$P4_RELEASE_ROOT/review/review.json" --actor-assignments "$P4_FORMAL_ACTORS" --output "$P4_RELEASE_ROOT/acceptance/I01"`；随后 `python3 scripts/phase4_independent/post_acceptance_check.py --release-root "$P4_RELEASE_ROOT" --acceptance "$P4_RELEASE_ROOT/acceptance/I01/acceptance.json"`。
- **独立验收标准与方法：** approver 不信顶层 PASS，抽取 validator 的每个底层引用再重算 manifest closure；A01–A21=PASS、blocking=0、delivery=100%、postcheck无未登记 extra/changed file、角色冲突 0 才 exit 0。模型最多 `shadow_candidate`，Top-K 必为 `insufficient_observation`，Champion仍 M0；任何全局 improved 拒绝。
- **失败终态/证据/取回：** 可恢复未完成 `HOLD`，不可恢复因果/篡改/伪造/越权 `FAIL`；不得写 `SYSTEM_MVP_GO`。失败 I01 不覆盖，修复按第 6 节用新 iteration/release。证据从 `$P4_RELEASE_ROOT/acceptance/I01/` 和 T21 receipt 取回。

## 4. A01–A21 双向追踪矩阵

命令简称：`U=unittest tests/phase4`，`E=validate e2e`，`Q=T16 formal qualification`，`O=T10/T16 independent oracle`，`C=T17 canary`，`S=T18 schedule audit`，`R=T20 replay`，`V=T20 final validator`，`A=T21 accept`。所有简称均指上面任务卡的完整命令和对应 receipt。

| 验收项 | 总体设计章节 | 子任务 | 交付物 | 验收命令/正式证据 |
| --- | --- | --- | --- | --- |
| P4-MVP-A01 | 4、9、10 | T05,T07,T09,T11,T17 | 双 game cycle/lock/capability/next forecast | E,R,V；`e2e/*cycle*`, `replay/replay.json` |
| P4-MVP-A02 | 7 | T04,T05,T10,T11 | forecast/ranking Schema、1000 tickets | U,O,E,R |
| P4-MVP-A03 | 7 | T04,T10,T11 | probability/order/tie vectors | O,E,R,V |
| P4-MVP-A04 | 7 | T04,T10,T16 | M0 full-space known answers | O,Q,R |
| P4-MVP-A05 | 8、10 | T07,T11,T17 | parameter diff/child shadow | E,R,V |
| P4-MVP-A06 | 8、10 | T07,T11,T17 | feature snapshot/diff/shadow | E,R,V |
| P4-MVP-A07 | 10、13 | T12,T13,T16 | 2,000 uniform sequence terminals | Q,R,V；`qualification/uniform/` |
| P4-MVP-A08 | 8、10、12 | T07,T11,T16 | alpha ledger/stop/governance | Q,E,R |
| P4-MVP-A09 | 10、13 | T12,T13,T16 | power + six 1,000 positive cells | Q,R,V |
| P4-MVP-A10 | 7、13 | T10,T13,T16 | full-rule spec/eight numeric cells | O,Q,R |
| P4-MVP-A11 | 9、12 | T01,T05,T11,T17 | time/label/tamper receipts | E,R,V |
| P4-MVP-A12 | 4、8、12 | T07,T09,T11,T17 | game/governance isolation | E,R,V |
| P4-MVP-A13 | 4、10、12 | T02,T07,T08,T11,T18 | checkpoints/fault terminals | U,E,S,R |
| P4-MVP-A14 | 5、9.4、14 | T00,T02,T03,T17 | genesis/data chain/canary/protection | C,E,R,V |
| P4-MVP-A15 | 3、14 | T10,T19,T20 | independent replay/manifest | R,V |
| P4-MVP-A16 | 14 | T01,T19,T20,T21 | coverage/review/signatures/acceptance | V,A |
| P4-MVP-A17 | 3、13、14 | T14,T15,T18 | lock/wheelhouse/rebuild/benchmark/readiness | S,R,V |
| P4-MVP-A18 | 12 | T01,T09,T11,T20 | state Schemas/projections | U,E,R,V |
| P4-MVP-A19 | 11 | T03,T08,T11,T18 | calendar/schedule/systemd audit | E,S,R |
| P4-MVP-A20 | 9 | T06,T10,T11,T17 | diagnostic/score/window oracles | O,E,R,V |
| P4-MVP-A21 | 5、9.4、10 | T02,T06,T07,T11,T17 | correction impact/remediation/current view | E,R,V |

反向检查：每个 T00–T21 的任务卡都至少产生一项被上表、六类交付矩阵或 readiness/治理硬门消费的可观察交付；不存在只能与另一个碎片一起才可观察的空任务。T21 仅消费同一 release，且是最后一步。

## 5. 六类交付物覆盖矩阵

| 上位合同六类交付物 | 负责子任务 | 固定主要路径 | 覆盖门 |
| --- | --- | --- | --- |
| 1 定义与合同 | T00,T01,T13,T15 | `docs/`、`config/phase4/`、formal `contracts/qualification-design` | T19 coverage + T20 validator |
| 2 实现 | T02–T09 | `src/lottery_system/phase4/`、`deploy/systemd-user/` | T11 E2E、T20 replay |
| 3 机器接口 | T01,T09,T14 | `schemas/phase4/`、CLI、config、`requirements/phase4.lock` | Schema/CLI/clean install |
| 4 验证资产 | T10–T13,T16,T17 | `tests/phase4*`、`scripts/phase4_independent/`、`qualification/`、`e2e/` | oracle/qualification/E2E 100% |
| 5 运行材料 | T01,T08,T14,T15,T18 | runbook、wheelhouse manifest、benchmark、VPS readiness | T18 + evidence-return |
| 6 正式证据 | T15–T21 | `artifacts/phase-4/<release-id>/` | manifest/replay/review/signatures/acceptance |

任何一类 coverage 小于 100% 时 P4-MVP-A16 和 T21 必须 HOLD。

## 6. 冻结、正式运行和不可变迭代边界

T00 冻结 authority/genesis/protection；T01 冻结语义和机器接口；T12 只能选择 design；T13 在 power 前冻结 design 并在结果后禁止反馈修改；T14 冻结依赖；T15 同时冻结 code/input/contracts/dependencies、正式 seeds/sequence identities、workload/resource budget、formal actors、E2E/acceptance identity，并确认正式结果为 0。只有 T15 PASS 才能运行 T16。T16 后任何影响数学、seed、阈值、资格、metric、time、source、calendar、状态或 acceptance 的变更都必须新 `P4_RELEASE_ID`，旧 release 原样封存；不能在当前 release 修补语义。

可恢复的环境/网络/中断使用同一逻辑对象的新 attempt，从验证通过的 checkpoint 继续，失败 attempt 永久存在。实现 bug 若不改变合同，可在 prep 阶段回到最早 T02–T11 节点，产生新 code commit、receipt 并重新执行全部依赖节点；正式 T15 后发现则必须新 release。power 不足创建新 design ID 和新 deterministic power seed set；formal threshold 失败不能改 effect/controller/seed重跑。acceptance 可在同一完全未改变 evidence release 上最多补一次纯验收材料 iteration `I02`，只能修复签名/manifest引用等不改变底层证据的问题；任何底层文件变化均新 release。

失败、超时、负向、低功效、不利 sequence、source conflict、canary network failure、review finding 和 acceptance attempt 不得删除、覆盖、重命名成成功或从 manifest 中选择性遗漏。`FAIL` 现场立即封存；`HOLD` receipt 必须写最早恢复节点、固定输入、未完成输出和唯一恢复命令。证据取回始终按显式 manifest 路径核对源端/接收端路径集合、bytes 和 SHA，不通过 `latest`、glob 或修改时间。

## 7. 可完成性和边界自检

逐任务输入在依赖完成时均存在：T00 使用仓库冻结事实；T01 使用 T00；T02–T10 使用 T01 固定合同；T09 等待产品端口；T11 等待集成和独立 oracle；T12/T13 分离种子；T14 使用成形 package；T15 消费全部结果前证据；T16–T21 只消费同一正式 release。每项输出都有具体路径、Schema/接口、命令、正负验收、失败终态和 manifest 取回路线。

没有任务依赖未来真实开奖：T03/T11/T16/T17 使用固定或合成 fixture/虚拟时钟，T17 canary 读取已公开期次；T18 只审计安装快照。没有任务要求为 Phase 1 历史记录补造 PIT；外部特征没有真实 `available_at` 就 fail closed。未声明外部服务为 0：正式资格断网，仅 readiness 使用四个 source policy 中的公开 GET；无数据库、队列、公共 API。没有 sudo/root 任务；systemd 是 `--user`。没有通用硬件阈值；T15 只以批准 workload 的 20 次 benchmark 和固定公式裁决。没有 Phase 0–3 修改权限；T00/T17/T20 递归前后保护。

复杂工作量只能通过新 benchmark identity 调整并行度、batch 和 checkpoint 频率；sequence 数量/长度、效应、seed、控制器、阈值、概率/tie/rank、metric 或 evidence 不得降低。正确 full-space tie/rank、正式 8,000 序列、八个 full-rule 单元、独立 replay 或递归证据在批准预算内仍不能完成时，工程终态为 HOLD，不生成近似科学或概率结果。
