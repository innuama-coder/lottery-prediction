# Phase 4 预测与 AutoResearch 闭环 MVP 详细实施计划

版本：1.4

状态：可执行计划候选；只定义后续开发、资格与验收，不在本任务中实现或运行 Phase 4

上位合同内容锚点：`ROADMAP.md` SHA-256 `24ba28e72c33959a91e505fd518718bd0c948c84b7e2e4cd5591a26f0a0b0149`、`tasks/phase4/README.md` SHA-256 `13b099c584c24c2bb7324f5fa852c9fac2dff7ad934245598eae2d117e701a75`；具体 `P4_AUTHORITY_COMMIT` 由 T00 在开发开始前从 `origin/main` 冻结，必须原子包含两份上位合同、本详细计划和 `docs/research/phase-4-overall-design.md`，不能使用尚未进入 `main` 或仍含旧版设计/计划的提交

总体设计：`docs/research/phase-4-overall-design.md`

## 1. 执行原则、角色和身份

所有下游只消费上游 `PASS` 且内容哈希固定的输出。每个任务有一个责任所有者和在任务完成时已经存在的独立验收路径；不得把未来任务的实现或 oracle 当成本任务验收依赖。作者不能仅以文件存在、自报 `PASS` 或自己生成的顶层汇总验收。可从底层重算的事实必须由独立脚本或角色重算。Phase 0–3 全部只读；Phase 4 preparation `artifacts/phase-4-prep/<prep-id>/`、staging `artifacts/phase-4-staging/<staging-id>/`、runtime `artifacts/phase-4-runtime/<runtime-id>/`、formal `artifacts/phase-4/<release-id>/` 四个 namespace 严格分离，路径解析后不得互为祖先或复用 ID。

角色固定为 `release_controller`、`data_custodian`、`contract_owner`、`implementation_author`、`statistical_owner`、`run_operator`、`vps_operator`、`independent_oracle_author`、`independent_power_operator`、`independent_replay_operator`、`independent_reviewer`、`acceptance_engineer`、`human_signatory`、`acceptance_approver`。独立性不按自报角色判定：每个产物必须记录 `producer_actor_id,task_id,session_id,source_commit`，机器由实际写路径派生 `product_producer_set` 和各类 `evidence_producer_set`。任何写 `src/lottery_system/phase4/` 或命令 provider 的 actor，包括 T03 的 adapter 作者，都视为 product producer。`independent_power_operator` 必须与 product producer、T12 statistical owner 和 oracle author 不同；T16/T17 `run_operator` 必须与 `acceptance_engineer` 不同；T22 `independent_reviewer` 必须与 T00–T21 其审查的所有 product/evidence producer、controller、operator 和 validator 不同；`acceptance_engineer` 不得是 product producer 或正式证据运行者；`human_signatory` 必须是 T00 前明确接受职责且不在被签署 producer/reviewer 集合中的人类 actor；T24 `acceptance_approver` 必须与 T00–T23 全部 producer、controller、operator、oracle、replay、reviewer、validator 和 signatory actor 不同。准备期和正式期 actor assignment 分别绑定 provenance 与任务记录 SHA-256，并只能新增版本；缺少人类签署者时 T00 立即 HOLD，不能推迟到最终验收。

任务级验收执行者不是占位词：T00 由 `data_custodian` 验收；T01–T10、T12–T20 由 `acceptance_engineer` 验收；T11、T22、T23 由 `release_controller` 验收；T21 由 `independent_reviewer` 验收；T24 由与所有 T00–T23 actor 不相交的 `acceptance_approver` 直接完成终局裁决。每份 task receipt 同时列 task producer set、验收 actor provenance 和机器派生的不等式；除 T24 的终局裁决外，验收 actor 与本任务全部产物 producer actor 有交集即 `HOLD_ROLE_CONFLICT`。任务卡中的 independent checker、verifier、reducer 或 manifest checker 均指本段绑定角色执行对应独立路径，不能由任务 owner 自行认领。

身份变量由 release controller 发放，值不能含 `latest`、通配符、斜杠或 mtime 选择：

```bash
P4_AUTHORITY_COMMIT=<origin-main-commit-containing-all-four-authority-files>
P4_IMPLEMENTATION_COMMIT=<clean-descendant-commit-frozen-at-T15>
P4_PREP_ID=p4-prep-controller-issued-i01
P4_PREP_ROOT=artifacts/phase-4-prep/$P4_PREP_ID
P4_STAGING_ID=p4-staging-canary-i01
P4_STAGING_ROOT=artifacts/phase-4-staging/$P4_STAGING_ID
P4_RUNTIME_ID=p4-runtime-readiness-i01
P4_RUNTIME_ROOT=artifacts/phase-4-runtime/$P4_RUNTIME_ID
P4_RELEASE_ID=P4-R01-<implementation-commit-first12>-<freeze-date-YYYYMMDD>-I01
P4_RELEASE_ROOT=artifacts/phase-4/$P4_RELEASE_ID
P4_RELEASE_VENV=$P4_RUNTIME_ROOT/release-venvs/$P4_RELEASE_ID
P4_RELEASE_PYTHON=$P4_RELEASE_VENV/bin/python
P4_FROZEN_SCRIPTS=$P4_RELEASE_ROOT/inputs/execution-scripts
P4_PREP_ACTORS=$P4_PREP_ROOT/control/actor-assignments-preparation.json
P4_FORMAL_ACTORS=$P4_RELEASE_ROOT/control/actor-assignments-formal.json
```

标准退出码：`0=PASS/READY`、`20=HOLD`、`30=retryable terminal recorded`、`4=identity reuse`、`5=contract/evidence mismatch`、`6=security/causality failure`，其他非零为 FAIL。每项 receipt 固定为 `<prep-or-release>/work-items/<task-id>/receipt.json`，记录输入/输出哈希、执行命令、进程退出码、task producer set、验收 actor provenance、起止时间、正负测试和 `PASS|HOLD|FAIL`；统一用以下独立 receipt checker 重算：

```bash
PYTHONPATH=src python3 scripts/phase4_independent/validate_work_item.py \
  --receipt "$RECEIPT" --actor-assignments "$ACTORS" --expected-task "$TASK_ID"
```

上述 `PYTHONPATH=src` checker 仅适用于 T00–T14 准备期。T14 的产品 wheel 必须记录 clean `built_from_commit`；T15 要求它精确等于 `P4_IMPLEMENTATION_COMMIT`，从 T14 的显式 wheelhouse manifest 离线创建 `$P4_RELEASE_VENV`，并从该 Git 对象导出 T16–T24 所需的 repository-relative 独立脚本到 `$P4_FROZEN_SCRIPTS`。T15 在 `control/execution-environment.json` 固定解释器 realpath/Python 版本、lock/wheel/wheelhouse manifest、安装后 distribution RECORD tree hash、脚本 path/SHA 和 implementation commit。T16–T24 每项命令前必须先由 release controller 对照 T15 receipt 核对 verifier 自身 SHA，再运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4_independent/verify_execution_environment.py" --manifest "$P4_RELEASE_ROOT/control/execution-environment.json" --expected-commit "$P4_IMPLEMENTATION_COMMIT"` 并把 PASS receipt 作为本任务输入。产品命令只能使用 `$P4_RELEASE_PYTHON -m lottery_system.phase4`；独立命令只能使用同一解释器和 `$P4_FROZEN_SCRIPTS` 中的固定脚本。禁止正式任务读取工作树 `src/`、`scripts/` 或另一个 venv；启动前不一致为 `HOLD_EXECUTION_IDENTITY`，已有正式输出后发现漂移为 `FAIL_EXECUTION_DRIFT`。

## 2. 无环依赖图、并行与关键路径

```text
T00 authority/genesis/protection
 -> T01 result-blind contracts, preregistration skeleton, CLI and Schemas
    -> T02 storage, data chain and CLI kernel --------+-> T03 source review/adapters/calendar --+
    |                                                  +-> T04 probability/rank (needs T10) ------+
    -> T10 result-blind numerical/full-rule oracles ----------------------------------------------+
T02 + T03 + T04 + T10 -> T05 forecast/diagnostic/time/label
T02 + T04 + T05 + T10 -> T06 score/window/score-side correction
T02 + T06 -> T07 AutoResearch/research-side remediation
T02 + T03 + T05 + T06 + T07 -> T08 scheduler/recovery/early systemd probe
T02..T08 -> T09 CLI composition/state
T09 + T10 -> T11 product validation/E2E -> T12 prep-resource gate + development qualification -> T13 power confirmation
T09 + T11 -> T14 dependency freeze and clean offline rebuild
T13 + T14 -> T15 benchmark, resource/seed/identity freeze and formal release
 -> T16 formal A07-A10 qualification
 -> T17 formal positive/negative E2E, correction and protected-tree canary
 -> T18 VPS user-systemd readiness and evidence-return audit
 -> T19 single-release assembly and recursive evidence manifest
 -> T20 independent bottom-up replay
 -> T21 final validator
 -> T22 independent release review
 -> T23 human wording signature
 -> T24 independent final delivery acceptance
```

T02 与 T10 可在 T01 后并行；T03 在 T02 后开始，T04 等待 T02 与 T10，随后 T05–T08 按任务卡依赖推进，不能伪称全部并行。T10 只消费 T01 的结果盲数学合同，不能读取、导入或复制产品核心；它在 T04/T05/T06 开始前提供独立 known-answer/oracle。T12 必须先在目标 VPS 完成 preparation workload 动态预算门，通过后才能发放 development seed 并运行选择；T13 先绑定 T12 candidate hash，再只能使用 power-confirmation seed，结果不得反馈到该 design；T14 可在 T11 后与 T12–T13 并行；T15 才续验 authority，自包含封装全部 preparation evidence，并冻结 formal master seed、依赖、工作量、code/input/contract acceptance identity；T16 以前不得生成正式资格结果。T16–T24 全部串行且消费同一 `P4_RELEASE_ID`。关键路径仍是 `T00 -> T01 -> T02/T10 -> T03/T04 -> T05 -> T06 -> T07 -> T08 -> T09 -> T11 -> T12/T14 -> T13 -> T15 -> T16 -> T17 -> T18 -> T19 -> T20 -> T21 -> T22 -> T23 -> T24`。

## 3. 任务合同

### T00：权威、genesis 与受保护树冻结

- **目标/执行角色：** `release_controller` 只建立不可变输入 inventory；不设计模型。
- **前置输入及固定身份：** controller 先 `git fetch origin main`，再显式提供在本版设计/计划合入后选定的 `P4_AUTHORITY_COMMIT`；该提交必须是 `origin/main` 祖先，同时包含 SHA-256 分别为 `24ba28e72c33959a91e505fd518718bd0c948c84b7e2e4cd5591a26f0a0b0149`、`13b099c584c24c2bb7324f5fa852c9fac2dff7ad934245598eae2d117e701a75` 的 `ROADMAP.md`、`tasks/phase4/README.md`，并同时含有本文和 `docs/research/phase-4-overall-design.md`。四文件的内容 SHA 从该 Git 对象在 T00 receipt 中冻结；不把文档自哈希写入文档本身。另固定 Phase 3 `P3-R07-2c0fa97-20260810-I01` acceptance SHA-256 `415bfc69cc04704265e231fd7d6e36bd2daa06b970b0def30703c4a7f04570c9` 和 Phase 1 四项 genesis。所有 preparation actor、完整不等式以及已确认职责的人类签署者必须在运行前给出。无任务依赖。
- **允许修改/禁止修改：** 允许 `config/phase4/authority-freeze.json`、`config/phase4/genesis.json`、`schemas/phase4/{authority-freeze,genesis,protected-inventory}.schema.json`、`scripts/phase4/freeze_authority.py`、`$P4_PREP_ROOT/control/`；禁止 Phase 0–3、产品代码、正式 release。
- **交付物及接口：** 上述两个严格 Schema JSON、四份 authority 文件的 Git path/blob/content SHA inventory、覆盖 `artifacts/phase-0/`、`artifacts/phase-0-multisource/`、`artifacts/phase-1/`、`artifacts/phase-2/`、`artifacts/phase-2.1/`、`artifacts/phase-3/` 的 `protected-artifact-inventory.json`（path/type/bytes/SHA）、四根互斥 path contract、preparation actor assignment、human-signatory acknowledgement、任务记录和 T00 receipt；T00 同时拥有三个仅描述 authority 的 Schema，不依赖 T01。
- **依赖/执行命令：** 无；先执行 `git merge-base --is-ancestor "$P4_AUTHORITY_COMMIT" origin/main`，再对 `ROADMAP.md`、`tasks/phase4/README.md`、`docs/research/phase-4-overall-design.md`、`docs/plans/phase-4-detailed-plan.md` 逐一执行 `git show "$P4_AUTHORITY_COMMIT:<path>"` 并记录 blob ID/字节数/SHA-256，然后执行 `PYTHONPATH=src python3 scripts/phase4/freeze_authority.py --prep-id "$P4_PREP_ID" --commit "$P4_AUTHORITY_COMMIT" --phase3-release P3-R07-2c0fa97-20260810-I01 --protected-root artifacts/phase-0 --protected-root artifacts/phase-0-multisource --protected-root artifacts/phase-1 --protected-root artifacts/phase-2 --protected-root artifacts/phase-2.1 --protected-root artifacts/phase-3 --output "$P4_PREP_ROOT/work-items/T00" --actor-assignments "$P4_PREP_ACTORS"`。
- **独立验收标准与方法：** data custodian 从 Git 对象和文件字节重算所有 SHA、六个受保护根的全路径集合及 Phase 1 四项内容；机器确认 authority commit 属于 `origin/main`、两份上位合同 hash 精确匹配、设计与计划两路径均存在且 receipt 中的四文件 hash 全部由同一提交重算一致、人类签署者已确认且 actor 不等式成立；四个 Phase 4 根 realpath 互不嵌套、ID 不复用且无 `latest`；路径/哈希/计数/角色绑定 100%，dirty 修改为 0。负测分支-only commit、只含相同上位合同但仍是旧版设计/计划的 commit、换 commit、空 baseline、改一字节、任一受保护根删/加文件、缺签署者、角色复用、`latest`、prep 写入 staging/runtime/formal。
- **失败终态/证据/取回：** 未合入 main 为 `HOLD_AUTHORITY_NOT_ON_MAIN`，身份/签署缺失为 `HOLD_AUTHORITY_IDENTITY|HOLD_ROLE_OR_SIGNATORY_MISSING`，上游篡改为 `FAIL_PROTECTED_ARTIFACT_MUTATION`；证据在 `$P4_PREP_ROOT/work-items/T00/`，按 receipt 显式清单取回并逐哈希。

### T01：结果盲合同、Schema、CLI 和 acceptance 冻结

- **目标/执行角色：** `contract_owner` 把总体设计的时间、身份、来源、日历、概率、指标、修订、研究、调度、故障/SLO 边界、角色、CLI、Schema 与 A01–A21 判定写成机器合同；不实现算法。
- **前置输入及固定身份：** T00 PASS receipt/hash 及其从同一 `P4_AUTHORITY_COMMIT` 冻结的两份上位合同、总体设计和本详细计划四文件身份。
- **允许修改/禁止修改：** 允许 `config/phase4/*.json`、`schemas/phase4/*.schema.json`、`docs/runbooks/phase-4-mvp-runtime.md`、`requirements/phase4.in` 候选；禁止产品实现、测试结果、正式 seeds/results 和 Phase 0–3。
- **交付物及接口：** source-policy review Schema/calendar/time/model/feature/metric/correction/decision/alpha/schedule/fault/SLO/CLI-kernel/provider contracts；含 `producer_actor_id,task_id,session_id,source_commit,path,role` 的严格 provenance Schema、task producer set/验收 actor receipt Schema、按实际写路径派生 product/evidence producer 集合的算法和完整 actor 不等式；qualification preregistration 明列 P4E1 `scale=1024,bounds=[-4096,4096]`、三档 effect、LR e-process、每 family `W0=0.006`、150 周期、三种 seed domain、2,000 development/20,000 power/1,000 formal、simultaneous CP 和 aggregate binomial 算法；E2E registry；所有 data-release/calendar/schedule/forecast/ranking/metric/experiment/decision/champion/model-status/top-k-status/alpha/manifest/review/signature/acceptance Schema。未知字段拒绝，三类状态键和值按总体设计固定。
- **依赖/执行命令：** T00；`PYTHONPATH=src python3 scripts/phase4/validate_contract_bundle.py --config config/phase4 --schemas schemas/phase4 --authority-receipt "$P4_PREP_ROOT/work-items/T00/receipt.json" --output "$P4_PREP_ROOT/work-items/T01" --actor-assignments "$P4_PREP_ACTORS"`。
- **独立验收标准与方法：** acceptance engineer 逐 Schema 构造最小正例和维度删除/未来阶段状态/未知字段负例，检查 CLI verbs/参数/退出码无省略，A01–A21 各有底层断言且六类交付物齐全。provenance 负测必须覆盖：把 T03 代码作者标成 data custodian 企图逃避 product set、T13 power operator 与 oracle/statistical/product actor 重合、T16/T17 run operator 与 acceptance engineer 重合、T22/T24 只换 role label 但 actor ID 重合。其他负测包括时间类混用、全局 improved、Champion promotion verb、宽松概率和隐式外部服务。
- **失败终态/证据/取回：** `HOLD_MACHINE_CONTRACT`；若弱化上位合同为 `FAIL_CONTRACT_WEAKENED`。证据 `$P4_PREP_ROOT/work-items/T01/`，manifest 明列合同文件。

### T02：不可变账本、存储、Phase 4 data release 链和 CLI kernel

- **目标/执行角色：** `implementation_author` 只实现 P4-CJSON-1、hash identity、事件链、原子存储、checkpoint、genesis/后继 data release，以及供后续任务注册命令的稳定 CLI kernel。
- **前置输入及固定身份：** T00 genesis/protected inventory、T01 serialization/data-release/ledger/checkpoint Schema hashes。
- **允许修改/禁止修改：** 允许 `src/lottery_system/phase4/{serialization,identity,storage,ledger,checkpoint,data_chain,cli_kernel,__main__}.py`、`commands/{contract,data_core}.py` 和对应测试；禁止官方网络、模型、指标、Phase 0–3 写入。后续任务不得修改 kernel，只能新增自己的 provider。
- **交付物及接口：** `create_genesis`、`append_data_release`、`append_event`、`load_checkpoint` ports；文件和父目录持久化、per-ledger advisory lock、expected-head CAS；CLI provider registry、稳定退出码及 `contract validate|data genesis|release|current` provider；未注册 verb 返回 `HOLD_COMMAND_NOT_IMPLEMENTED`；runtime 只写 `artifacts/phase-4-runtime/<id>/`；T02 fixtures/receipt。
- **依赖/执行命令：** T01；`PYTHONPATH=src python3 -m unittest tests.phase4.test_identity tests.phase4.test_ledger tests.phase4.test_data_chain -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 data genesis --runtime-root "$P4_RUNTIME_ROOT" --genesis config/phase4/genesis.json --clock fixture:2026-01-01T00:00:00Z`。
- **独立验收标准与方法：** independent checker 从 baseline bytes 重建 genesis、逐事件 previous hash 和 current view；故障注入覆盖 temp write、file fsync、rename、directory fsync、head CAS 每个边界，crash 后只允许旧完整对象或新完整对象。两个 game 并发计算并争用共享 ledger 时必须串行提交且 head 无丢失更新；若使用 per-game ledger，则跨 ledger 的父引用必须显式且可重放。正测连续三 release/同 identity resume、provider 注册和未实现 verb HOLD；负测断链、换 genesis、空内容、分叉拼接、重复 sequence、torn write、lock bypass、stale expected head、checkpoint wrong head、任一 Phase 0–3 protected path resolve。
- **失败终态/证据/取回：** `HOLD_STORAGE_SEMANTICS|HOLD_DATA_CHAIN`；写到保护树或覆盖历史为 FAIL。证据 `$P4_PREP_ROOT/work-items/T02/` 和隔离 runtime manifest。

### T03：官方适配器、核验、修订识别和显式日历

- **目标/执行角色：** `data_custodian` 对来源政策、raw receipt、双源核验和 calendar 事实负责；adapter/provider 代码的实际作者另记 `implementation_author` provenance，即使两职责由同一 actor 承担，该 actor 也必须进入 `product_producer_set`；不评分。
- **前置输入及固定身份：** T01 source-review/calendar/correction contracts，T02 storage/data-chain/CLI-kernel ports；候选 source IDs 为 SSQ `swlc+ydniu`、DLT `gdlottery+ydniu`，但 Phase 1 的低频研究政策不能直接复用为 Phase 4 授权。
- **允许修改/禁止修改：** 允许 `official_adapter.py`、`verification.py`、`calendar.py`、`commands/{data_official,calendar}.py`、`config/phase4/source-policy.json`、source-review/early-canary 脚本、固定响应 fixtures/tests；禁止模型/score/research、未经 allowlist 网络和任一 Phase 0–3 protected root。
- **交付物及接口：** 新的 Phase 4 source review/policy，明确用途、review/expiry、精确 endpoint、publisher、GET/rate/redirect/response-size 策略及 `scheduled_internal_mvp_collection_approved`；目标 VPS early canary；transport observation、verified result revision、calendar release/build validator和对应 CLI provider；`config/phase4/calendar-policy.json` 显式 CST/UTC mapping；fixture outputs；每份 policy/code/evidence 的 producer provenance。
- **依赖/执行命令：** T01,T02；先执行 source review validator，再从目标 VPS运行 `python -m lottery_system.phase4 data ingest --mode early-readonly-canary --source-policy config/phase4/source-policy.json --staging-root "$P4_STAGING_ROOT" --output "$P4_PREP_ROOT/work-items/T03/early-canary"`；随后运行单元测试和 `calendar build` fixture。
- **独立验收标准与方法：** policy 不能继承已过期或 `production_collection_approved=false` 的权限结论；四个必需 endpoint 均成功，publisher 独立，每个 game 至少一个已公开重叠 issue 的两份核心事实一致，review 在预计 formal 窗口内有效。从 raw bytes 用独立 parser 重算 target/result/date/numbers/revision，zoneinfo 重算所有 UTC；fixed responses 覆盖修订/去重/Schema compatibility。负测任一必需源缺失、冲突、政策过期/用途不匹配、HTML/JSON 漂移、跨 host redirect、POST、倒退/重复/多义期号、DST/服务器时区扰动。early canary 前后六个 protected roots exact match。
- **失败终态/证据/取回：** 政策未获准/过期为 `HOLD_SOURCE_POLICY`，网络/单源为 `HOLD_SOURCE_READINESS`，冲突为 `HOLD_SOURCE_CONFLICT`，多义日历为 `HOLD_CALENDAR_AMBIGUOUS`；在昂贵 qualification 前停止。证据 `$P4_PREP_ROOT/work-items/T03/`。

### T04：严格概率、exact tie/rank 与确定 Top-1000

- **目标/执行角色：** `implementation_author` 只实现 SSQ/DLT rules、P4E1 Decimal 概率、order/tie key、完整空间 histogram/rank 和 1,000 注算法。
- **前置输入及固定身份：** T01 rule/model/probability/ranking contracts；T02 canonical serialization/CLI kernel；T10 已冻结的结果盲概率、tie/rank 和 full-rule known-answer vectors。
- **允许修改/禁止修改：** 允许 `rules.py`、`probability.py`、`ranking.py`、`commands/probability_validation.py` 及测试；禁止 float `isclose` tie、模型搜索、指标/acceptance。
- **交付物及接口：** `distribution`, `normalization_proof`, `rank_histogram`, `top1000` pure APIs，hash vectors 和 product known-answer。
- **依赖/执行命令：** T02,T10；`PYTHONPATH=src python3 -m unittest tests.phase4.test_rules_probability_ranking -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 validate unit --scope probability-ranking --oracle "$P4_PREP_ROOT/work-items/T10/known-answers" --output "$P4_PREP_ROOT/work-items/T04"`。
- **独立验收标准与方法：** T10 的 direct enumeration 小空间和独立分区枚举/DP 对真实空间逐值对比；对 `[-4096,4096]` 边界、资格菜单三档、A10 的 `[-64,0]`、M0 和 adversarial unique-sum fixtures 检查 Decimal 80 位、5 位 order key、reachable counts、严格正/归一、1000 数量/唯一/前缀、tie group/rank 全部 100%。负测 zero/negative/NaN/Inf、量化越界、50 位序列化成零、输入排列、非传递近似、key 碰撞、跨 Top-K tie、非法号码；无法 exact full-space rank 必须未接入或 HOLD。
- **失败终态/证据/取回：** `HOLD_UNSUPPORTED_TIE_SEMANTICS`；错误结果被发布为 FAIL。证据 `$P4_PREP_ROOT/work-items/T04/`。

### T05：forecast 诊断、原子 lock、三类时间与 label capability

- **目标/执行角色：** `implementation_author` 只实现 label-free snapshot、Champion/shadow forecast body/diagnostic、deadline lock 和 scorer-only unlock。
- **前置输入及固定身份：** T01 time/forecast/diagnostic contracts；T02 ledger；T03 calendar/data ports；T04 probability API；T10 已冻结 diagnostic known-answer vectors。
- **允许修改/禁止修改：** 允许 `forecast.py`、`lock.py`、`time_gate.py`、`label_capability.py`、`commands/{forecast,result_unlock}.py` 及测试；禁止 score/research、锁后修改和 historical `available_at` 合成。
- **交付物及接口：** `prepare/generate/lock/unlock` application ports和 CLI provider；完整空间 normalization、Top-K coverage/order/tie/rank/M0 诊断并与 forecast 同时锁定；lock receipt、不含号码的 unlock-eligibility receipt、`label_store.acquire_for_scoring` 进程内 capability API、trainer quarantine probe。任何持久化对象都不得承载 capability 或可绕过 label store 的标签 payload。
- **依赖/执行命令：** T02,T03,T04,T10；`PYTHONPATH=src python3 -m unittest tests.phase4.test_forecast_lock tests.phase4.test_label_capability tests.phase4.test_forecast_diagnostic -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 validate unit --scope forecast-diagnostic-time-label --oracle "$P4_PREP_ROOT/work-items/T10/known-answers" --output "$P4_PREP_ROOT/work-items/T05"`。
- **独立验收标准与方法：** T10 从 forecast 底层分布重算全部诊断并证明不绑 result revision。verifier 分别启动 `result unlock` 与新的 `score one` 模拟进程，证明持久化 receipt 不含 capability/号码，scorer 只有在自身 PID 内重新核对 lock/event/file/hash/identity/clock 后才能首次读取标签；跨 PID capability 复用和仅凭 receipt 直接读取均失败。三时间类型正例各自通过且互换失败。负测 diagnostic 绑 result、pre-lock、future prefix、external no PIT、锁后 mutation、wrong release/run/game/issue/model/data/calendar/metric/result、trainer filesystem/child-process/capability access、deadline 后补锁。
- **失败终态/证据/取回：** 可恢复 identity 缺失 HOLD；label leak/锁后改写 `FAIL_CAUSALITY_OR_TAMPER`。证据 `$P4_PREP_ROOT/work-items/T05/`。

### T06：score、窗口与评分侧修订事实

- **目标/执行角色：** `implementation_author` 只实现总体设计第 9.3 节公式、aggregate current view 和修订闭包第一段 score/window 事实；不生成尚未由 T07 实现的 research 对象。
- **前置输入及固定身份：** T01 metric/correction contracts；T02 data/ledger/CLI kernel；T04 probability/rank；T05 locked forecast/unlock；T10 已冻结的独立 metric/window/correction vectors。
- **允许修改/禁止修改：** 允许 `metrics.py`、`windows.py`、`correction.py`、`commands/score.py` 及测试；T05 forecast diagnostic 只读；禁止生成 decision/experiment/candidate/remediation 或改写 alpha 历史。
- **交付物及接口：** score、window、corrected score/aggregate/current replacement APIs，以及只列 score/aggregate 和待处理 research object IDs 的 `score_correction_impact`。
- **依赖/执行命令：** T02,T04,T05,T10；`PYTHONPATH=src python3 -m unittest tests.phase4.test_metrics tests.phase4.test_correction -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 score correct --fixture tests/phase4/fixtures/correction/valid.json --oracle "$P4_PREP_ROOT/work-items/T10/known-answers" --runtime-root "$P4_RUNTIME_ROOT" --clock fixture:2026-01-02T00:00:00Z`。
- **独立验收标准与方法：** T10 oracle 从 ticket/label/inclusion vectors 重算全部 Decimal 值、30 样本门、bin boundary/Wilson/rank；从旧/新 revision 反向列出完整 score/window 影响和待处理 research IDs。在不存在 T07 对象的 fixture 上仍可独立 PASS。负测首期 wrong comparator、小样本伪数值、0.1 bin 边界、跨 tie、零概率、同 issue 多 revision 计数、部分 score/window 传播、未授权写 research/alpha、旧链头复活。
- **失败终态/证据/取回：** `FAIL_METRIC_ORACLE_MISMATCH` 或 `HOLD_CORRECTION_INCOMPLETE`；覆盖/重复为 FAIL。证据 `$P4_PREP_ROOT/work-items/T06/`。

### T07：AutoResearch、candidate/diff、alpha 和 shadow lifecycle

- **目标/执行角色：** `implementation_author` 只实现已经由 T01 的 `statistical_owner` 签署冻结的控制器和修订闭包第二段 research remediation；该任务只拥有 research control plane，不能在实现时重新决定统计规则。
- **前置输入及固定身份：** T01 model/feature/decision/alpha contracts及 statistical-owner signature；T02 ledger/checkpoint；T06 current scores/correction hold port。
- **允许修改/禁止修改：** 允许 `research/{registry,proposal,sequential,alpha,controller}.py`、`commands/research.py` 及测试；禁止采集、规则、label、score、acceptance、Champion mutation和任意代码搜索。
- **交付物及接口：** parameter/feature canonical diff、candidate ID、one-experiment-per-cycle decision、每 family `W0=0.006` 和 `alpha_t=W0/(t(t+1))` wealth events、Decimal LR e-process looks/stop、resume、next-shadow eligibility；消费 T06 `score_correction_impact` 的 remediation decision、candidate archive/requalification 和 alpha-history-unchanged proof。
- **依赖/执行命令：** T02,T06；`PYTHONPATH=src python3 -m unittest tests.phase4.test_research_controller -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 research decide --fixture tests/phase4/fixtures/research/parameter-positive.json --runtime-root "$P4_RUNTIME_ROOT" --clock fixture:2026-01-03T00:00:00Z`。
- **独立验收标准与方法：** independent ledger reducer 从每期冻结的 `p0_t,p1_t,Y_t` 重算 `log E_n`、首次 `E_n>=1/alpha_t`、family wealth/spend/stop/decision；uniform exact enumeration 验证每个 LR increment 在 M0 下的条件均值为 1，跨所有 family 总 spend `<=0.018`。parameter 和 feature 正例均生成新 ID/diff 并改变 next shadow；no eligible/budget/guard/no-change 产生零实验理由。对 T06 correction fixture 重算 remediation/candidate 终态，并证明修订前后 alpha event 集合和 wealth 完全相同。负测逐 look 再拆 alpha、未来数据构造 `p1_t`、多个 family 同 experiment、unregistered diff、negative wealth、look after stop、duplicate spending、revision refund/reset、漏 archive/requalification、direct Champion、config change no output。
- **失败终态/证据/取回：** `FAIL_ALPHA_OR_GOVERNANCE`；能力未实现 `HOLD_ADJUSTMENT_CAPABILITY`。证据 `$P4_PREP_ROOT/work-items/T07/`。

### T08：计划触发、并发、恢复和告警

- **目标/执行角色：** `implementation_author` 只实现 schedule build/tick、plan lease、补偿、checkpoint orchestration 和 structured alerts；不拥有业务事实。
- **前置输入及固定身份：** T01 schedule/fault contracts；T02 storage；T03 calendar；T05–T07 application ports。
- **允许修改/禁止修改：** 允许 `scheduler.py`、`orchestrator.py`、`recovery.py`、`alerts.py`、`commands/schedule.py`、`deploy/systemd-user/`、测试；禁止 root unit、cron 隐式配置和跨 game cancellation。
- **交付物及接口：** plan ledger、virtual clock、CLI provider、systemd user unit/timer templates、audit parser、runbook sections，以及目标 VPS 的早期 user-manager/linger/timer-parse/绝对路径/工作目录/写权限只读 capability probe；probe 不安装正式 unit。修订编排只在 T06 score-side 和 T07 research-side receipt/hash 均存在后追加 `correction_closed`，此前对受影响候选 fail closed。
- **依赖/执行命令：** T02,T03,T05,T06,T07；`PYTHONPATH=src python3 -m unittest tests.phase4.test_scheduler_recovery -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 schedule tick --schedule tests/phase4/fixtures/schedule/dual-game.json --runtime-root "$P4_RUNTIME_ROOT" --clock fixture:2026-01-03T09:00:00Z`。
- **独立验收标准与方法：** virtual-clock reducer 重算每 plan 唯一 run/terminal/alert；对 correction 在 score-side 后、research-side 中和 close 前逐点中断，恢复必须得到相同两段 hashes、唯一 `correction_closed`、零重复 score/spend。目标 VPS 早期 probe 必须证明 `systemctl --user` 可查询、timer 表达式可解析、release/runtime 目标可写且不需要 sudo；失败在 T08 早期 HOLD。正测双 game 准时与 compensation/restart；负测 early/late/missed/deadline、duplicate/concurrent、crash each stage、wrong checkpoint、partial correction、issue rollback、one-game network failure。重复 side effects 全为 0。
- **失败终态/证据/取回：** `HOLD_RECOVERY_MISMATCH|HOLD_SCHEDULER_AUDIT`；deadline 后有效 lock 或重复 side effect 为 FAIL。证据 `$P4_PREP_ROOT/work-items/T08/`。

### T09：CLI provider 组合、状态矩阵和组件集成

- **目标/执行角色：** `implementation_author` 只组合 T02–T08 已交付的 CLI provider、接线 application ports并生成三类状态投影；不在此任务首次实现任何前序 verb或领域算法。
- **前置输入及固定身份：** T02–T08 PASS receipts、T01 CLI/state schemas。
- **允许修改/禁止修改：** 允许 provider composition registry、`state_projection.py`、`commands/state.py`、packaging entrypoint；`cli_kernel.py`、各前序 provider 只读，发现缺口必须退回拥有该 provider 的任务修复并重验；禁止组件循环 import、未来阶段状态和全局 improved。
- **交付物及接口：** 总体设计第 6 节全部 verbs、stable exits、`state project/show`、integration receipt。
- **依赖/执行命令：** T02–T08；`PYTHONPATH=src python3 -m unittest tests.phase4.test_cli_state_integration -v`；对每 verb 执行 `--help` 和固定 fixture smoke；`PYTHONPATH=src python3 -m lottery_system.phase4 state project --runtime-root "$P4_RUNTIME_ROOT" --output "$P4_PREP_ROOT/work-items/T09/state"`。
- **独立验收标准与方法：** 依赖图无环；CLI registry 与 parser 双向集合相等；完整键逐 event 重算，Phase 4 仅允许工程/模型/Top-K 指定值。负测删 game/K/comparator/release/window、跨 game join、future transition、global improved、implicit latest/外部服务。
- **失败终态/证据/取回：** `FAIL_STATE_MATRIX|HOLD_CLI_CONTRACT`；证据 `$P4_PREP_ROOT/work-items/T09/`。

### T10：结果盲独立概率、指标和 full-rule oracle

- **目标/执行角色：** `independent_oracle_author` 在任何产品概率/指标实现和任何 development 结果前，编写不导入产品包的直接枚举/Decimal DP 参考路径并证明资格候选空间存在可完成设计；不修改产品实现。
- **前置输入及固定身份：** 仅 T01 mathematical/metric/qualification contracts和总体设计 7/9/10/13 节；输入/输出 Schema 已由合同冻结，不读取 T04/T06 源码、接口实现或任何产品输出。
- **允许修改/禁止修改：** 只允许 `scripts/phase4_independent/{oracle_*.py,check_qualification_feasibility.py}`、`tests/phase4_oracle/`、`qualification-design/{full-rule-spec-candidate,analytic-feasibility-spec}.json`；禁止 `src/lottery_system/phase4/`、development/power/formal results 和顶层 PASS 信任。
- **交付物及接口：** 小空间概率/rank/metric vectors；P4E1 边界/menu/A10/M0/adversarial exact tie/Top-1000/Decimal vectors；full-rule 八单元 oracle；解析 feasibility certificate，逐候选列 `mu,sum_range_squared,sequence_bound,G0/G+`；import audit、误差界、独立 source/input hash。
- **依赖/执行命令：** T01；在 T04/T06 开始前运行 `python3 scripts/phase4_independent/run_known_answers.py --spec config/phase4 --tick-bound 4096 --output "$P4_PREP_ROOT/work-items/T10/known-answers"`、feasibility checker 和 import-independence checker，并冻结全部输出/source hash。
- **独立验收标准与方法：** acceptance engineer 静态检查无产品 import，以 hand-calculated tiny cases 交叉核对并从全部 120 个组合重算 LR 输入；checker 必须固定 `N=10,k=3,scale=1024,n=150,W0=0.006,alpha1=0.003,q=[1536,1792,2048],ramp=100`，最弱候选的 uniform aggregate 下界 `>0.9999999999`、六个 positive 中最坏 sequence 下界 `>=0.93954` 且 aggregate 下界 `>0.99999950`。真实规则 exact histogram 总数、Top-1000 hash、50 位概率非零且跨两条独立路径一致；相同数学输入 hash 稳定。负测产品输出反构造 distribution、恢复旧 32-tick/Hoeffding 组合、改变幅度/alpha/ramp、少 K、布尔-only better、容差/规则缺失、import 产品 normalization/top-k。
- **失败终态/证据/取回：** `HOLD_ORACLE_NOT_FROZEN|HOLD_INDEPENDENCE`；证据 `$P4_PREP_ROOT/work-items/T10/`。

### T11：产品单元/Schema/正负 E2E 与 final validator 资格

- **目标/执行角色：** `acceptance_engineer` 维护测试 registry 和隔离 mutation harness；实现作者只修产品，不能批准结果。
- **前置输入及固定身份：** T01 E2E/A01–A21 contracts，T09 integrated CLI，T10 oracles。
- **允许修改/禁止修改：** 允许 `tests/phase4/`、`scripts/phase4/{validate_bottom_up,benchmark_prequalification}.py`；产品修复仅回到对应 T02–T09 并新 receipt；禁止删失败用例、硬编码 actual terminal。
- **交付物及接口：** 单元/属性/Schema/两 game cycle/adjustment/revision/recovery/time/governance/state/scheduler E2E，registered guard map，pre-acceptance final-validator harness；一个只执行冻结黑盒 controller/checkpoint/无损分片/manifest/evidence-return 路径并按固定公式外推的 prequalification benchmark harness；`tests/phase4/fixtures/benchmark/registry.json` 固定 `benchmark_fixture_id`、确定性输入和各代表 batch 尺寸/hash，明确 `non_scientific=true`、不属于三种 qualification seed domain 且不得进入候选或门判定。
- **依赖/执行命令：** T09,T10；`PYTHONPATH=src python3 -m unittest discover -s tests/phase4 -p 'test_*.py' -v`；`PYTHONPATH=src python3 -m lottery_system.phase4 validate e2e --registry config/phase4/e2e-registry.json --output "$P4_PREP_ROOT/work-items/T11/e2e" --clock fixture`。
- **独立验收标准与方法：** release controller 从冻结 registry 和底层 receipts 重算双向差集为空，实际 isolated mutation + distinct validator process，期望 guard/exit/terminal 命中率 100%；该 controller actor 与 T11 acceptance-engineer producer actor 不同；无关 missing/malformed failure 不能算通过；Phase 3 regression suite通过。
- **失败终态/证据/取回：** `HOLD_E2E_INCOMPLETE`；负向被接受或伪造 terminal 为 FAIL。全部失败 receipts 原样保存在 `$P4_PREP_ROOT/work-items/T11/`。

### T12：准备期资源门与 development-seed 资格设计选择

- **目标/执行角色：** `statistical_owner` 先在目标 VPS 为全部 preparation simulation/evidence 导出动态资源预算；只有该门 PASS 后，才在 T01/T10 已冻结且解析可行的三个 effect design 内发放 development seeds 并选择最弱可行 design；不是正式功效或资格。
- **前置输入及固定身份：** T07 LR e-process controller source/hash、T10 analytic certificate/oracle hashes、T11 qualification/prequalification-benchmark harness 与 `benchmark_fixture_id`/registry hashes、T01 prereg/seed/confidence skeleton。
- **允许修改/禁止修改：** 允许 `$P4_PREP_ROOT/qualification-design/{preflight-benchmark,development}/` 和一个 candidate design descriptor；preflight PASS 前禁止创建 development terminal 或发放其 seed；全程禁止 power/formal seed、正式 release、菜单/alpha/150 周期/门外调参和把开发结果用于 acceptance。
- **交付物及接口：** 第一段交付 20 次代表 batch 的 p50/p95/RSS/文件数/压缩前后 bytes/evidence-return 速率，并对 development `48,000 sequences/7,200,000 draw observations` 和 power `160,000 sequences/24,000,000 draw observations` 分别外推加 25% 的时间、存储、分片、checkpoint 和取回预算。第二段交付三个幅度 design × 8 game/world cells × 每 cell 2,000 development terminals 及对应 7,200,000 个 draw observations，全菜单失败也保留；经验 rate 明标 `descriptive_non_selection=true`；确定选择 receipt、candidate design ID、generator/controller/analytic source hashes和 `non_formal=true`。
- **依赖/执行命令：** T07,T10,T11；先运行 `python3 scripts/phase4/benchmark_prequalification.py --controller-command config/phase4/power-controller-command.json --benchmark-fixtures tests/phase4/fixtures/benchmark/registry.json --target-development-sequences 48000 --target-power-sequences 160000 --cycles-per-sequence 150 --runs 20 --output "$P4_PREP_ROOT/qualification-design/preflight-benchmark"`；只有 receipt PASS 后才运行 `PYTHONPATH=src python3 -m lottery_system.phase4 research run --mode development-design-selection --preregistration config/phase4/qualification-preregistration.json --feasibility "$P4_PREP_ROOT/work-items/T10/feasibility/certificate.json" --sequences-per-cell 2000 --output "$P4_PREP_ROOT/qualification-design/development" --seed-domain development --clock fixture`。
- **独立验收标准与方法：** verifier 先核对 benchmark 发生在任何 development seed/terminal 之前，`benchmark_fixture_id` 与 T11 registry 完全一致且不从任何 qualification seed domain 派生；样本路径真实执行黑盒 controller、checkpoint、无损压缩分片、manifest 重算和证据取回，外推算术、磁盘可用量和 25% 余量正确；benchmark 输出进入候选选择或统计门的引用数为 0，不设通用硬件门。然后独立脚本从 `P4-SEED-v2` 重派生全部 development seeds，验证与尚未生成的 power/formal domain 不同，重算 150 个 LR looks、非统计 guards 和 next-shadow hash。选择算法严格为：T10 uniform/positive aggregate 解析下界各 `>=0.99` 且 positive sequence 下界 `>=0.93`；product/independent terminal、LR、guard、hash 一致率 100%；再取 `q ascending,canonical config bytes` 首项。三项均运行、未运行/删除/越序挑选为 0；经验 rate 不得进入 predicate。首项实现不一致必须 HOLD 修复，不能跳到强 effect；无解析可行 design 也必须 HOLD。
- **失败终态/证据/取回：** 预算不可行为 `HOLD_PREQUALIFICATION_BUDGET`，且 development terminal 数必须为 0；设计不可行为 `HOLD_NO_DESIGN_CANDIDATE`。证据 `$P4_PREP_ROOT/qualification-design/{preflight-benchmark,development}/`。

### T13：独立 power-confirmation 与 qualification-design 冻结

- **目标/执行角色：** `independent_power_operator` 先签收 T12 candidate、preflight budget 与冻结产品 controller hashes，再用未参与选择的 power-confirmation seeds，以独立生成器驱动该 controller 的黑盒 CLI，分别确认 8 个 game/world 的 sequence-level 性能以及各自新的 1,000-sequence formal batch 的 aggregate gate pass probability；随后冻结 design，结果绝不反馈调参。该 actor 必须与 product producer、T12 statistical owner 和 T10 oracle author 不同。
- **前置输入及固定身份：** T12 preflight PASS receipt/budget/sharding contract、selected design ID/hash 与完整 development manifest、T01 `P4-SEED-v2`/simultaneous confidence/binomial contract、T10 analytic certificate/source hash；运行前 power 目录必须不存在且 formal seed 尚未发放。
- **允许修改/禁止修改：** 只允许新建 `$P4_PREP_ROOT/qualification-design/power/` 并在所有对象内绑定 candidate design ID/hash、冻结 controller command/code hash 和 signed design freeze；独立脚本可把冻结 CLI 当子进程调用，但禁止 import/复制产品 controller、修改产品或 selected design、使用 development/formal seed、pooling games、删除失败 power 或把 power terminal 写入 formal。
- **交付物及接口：** 8 个 cell 每个恰好 20,000 个独立 sequence terminals，共 160,000 个序列和 24,000,000 个 draw observations；按 T12 批准的无损分片合同保存底层 draws/terminals/provenance；sequence success count/rate 和 Bonferroni simultaneous 95% Clopper–Pearson `[L,U]`；prospective aggregate point/interval；seed-set hashes/intersection=0；analytic comparison；full-rule spec/oracle expected values；`qualification-design.json` 签署。字段必须分名为 `sequence_rate_estimate`、`sequence_rate_simultaneous_interval`、`formal_1000_gate_pass_probability_estimate`、`formal_1000_gate_pass_probability_interval`，禁止把 rate 写成 gate probability。
- **依赖/执行命令：** T12；`python3 scripts/phase4_independent/confirm_power.py --design "$P4_PREP_ROOT/qualification-design/development/selected-design.json" --selection-receipt "$P4_PREP_ROOT/qualification-design/development/selection-receipt.json" --controller-command config/phase4/power-controller-command.json --seed-domain power-confirmation --sequences-per-cell 20000 --confidence-family 0.95 --output "$P4_PREP_ROOT/qualification-design/power"`；再运行 `python3 scripts/phase4_independent/reduce_power.py --input "$P4_PREP_ROOT/qualification-design/power" --formal-sequences 1000 --uniform-max-successes 50 --positive-min-successes 900 --decimal-precision 80 --output "$P4_PREP_ROOT/qualification-design/power/aggregate-gates.json"`。
- **独立验收标准与方法：** acceptance engineer 从 160,000 个序列的 24,000,000 个原始 draw observations、黑盒 CLI terminals 和冻结 `p0_t,p1_t` 独立重算 seed、LR 首次越门、guard、next-shadow hash、success count 和 8 个区间；CLI 与独立 reducer 对逐序列终态必须 100% 一致，实际资源和证据字节不得超过 T12 批准预算。每区间双侧 tail 固定 `0.05/(2*8)`，CP 端点用 binomial CDF 二分到 `1e-12`。再独立计算 uniform `G0(q)=P[Binom(1000,q)<=50]` 的点值/`[G0(U),G0(L)]`，positive `G+(p)=P[Binom(1000,p)>=900]` 的点值/`[G+(L),G+(U)]`。每个 cell 必须同时满足不利 sequence 端 `U<=0.05` 或 `L>=0.90`、aggregate 点值 `>=0.90`、aggregate lower bound `>=0.90`；analytic certificate 也仍 PASS，domain 集合交集 0、独立脚本产品 import 0、actor 不等式成立、controller/candidate/design hash 未变。边界含 0.899999、把一次 1,000 rate 当概率、错误单/双尾、pooling、缺/重序列、重复 seed、事后 design 或 controller change。
- **失败终态/证据/取回：** 实际工作量/证据超过 T12 批准预算为 `HOLD_PREQUALIFICATION_BUDGET`，统计门任一项失败为 `HOLD_DESIGN_NOT_POWERED`，T15 都不得启动；后续必须发布新 benchmark 或 design ID，回到 T10/T12 并由新 ID 确定新 development/power seeds，旧 power 目录和不利结果保留。证据按 power manifest 显式取回。

### T14：依赖、产品 wheel 身份、wheelhouse 和干净离线重建

- **目标/执行角色：** `release_controller` 冻结全转移依赖并在一次性干净目录验证安装；不运行正式资格。
- **前置输入及固定身份：** T09 package、T11 tests；当前 clean candidate source commit；Python `>=3.12,<3.13`；T01 dependency policy。
- **允许修改/禁止修改：** 允许 `requirements/phase4.lock`、`pyproject.toml` Phase4 package data/entrypoint、`$P4_PREP_ROOT/wheelhouse/` 和 receipts；禁止未锁依赖、部署状态、正式 release 结果。
- **交付物及接口：** hash lock、逐文件 wheelhouse manifest、产品 sdist/wheel hashes及唯一 `built_from_commit`、从该 Git 对象派生的产品源文件 path/SHA 清单、fresh venv offline install receipt、安装后 distribution RECORD tree hash、CLI/fixture/checkpoint/replay smoke。
- **依赖/执行命令：** T09,T11；先确认 `git diff --quiet && git diff --cached --quiet` 并记录 `git rev-parse HEAD`，再用 `python3 -m pip wheel --no-deps --no-build-isolation . --wheel-dir "$P4_PREP_ROOT/wheelhouse"` 构建产品 wheel，并运行 `python3 -m pip wheel --require-hashes -r requirements/phase4.lock --wheel-dir "$P4_PREP_ROOT/wheelhouse"` 构建依赖；`python3 scripts/phase4/verify_offline_rebuild.py --wheelhouse "$P4_PREP_ROOT/wheelhouse" --lock requirements/phase4.lock --built-from-commit "$(git rev-parse HEAD)" --output "$P4_PREP_ROOT/work-items/T14"`。
- **独立验收标准与方法：** verifier 从 `built_from_commit` 的 Git 对象重算产品源文件 path/SHA，解包首次构建且已冻结 SHA 的 wheel 后逐文件比较；允许标准 wheel 元数据存在非语义构建差异，不要求二次构建的 wheel 字节级相同。随后在新临时目录断网、`--no-index --find-links` 安装，重算 distribution RECORD tree hash并记录 OS/arch/Python/resources facts、commands/exits；无网络请求、缺 wheel、产品源文件/版本/hash/commit 差异和隐式 service。负测换 source commit、改 wheel 内产品文件、移除 wheel、改 lock、清空 pip cache、错误 Python。
- **失败终态/证据/取回：** `HOLD_INSTALL_OR_DEPENDENCY`；证据 `$P4_PREP_ROOT/work-items/T14/` 和 wheel manifest。

### T15：benchmark、执行环境、资源/seed/acceptance identity 冻结并创建正式 release

- **目标/执行角色：** `release_controller` 只消费 T13 已签署 PASS design，续验 authority、封装自包含 preparation evidence，并把正式 benchmark 代入固定公式，冻结批准 workload、formal actor assignment、formal seed、code/input/contracts/dependencies 和唯一空 release；这是正式结果前最后门，不能重新选择 design。
- **前置输入及固定身份：** T00–T14 全 PASS；T00 `P4_AUTHORITY_COMMIT` 及四文件 blob inventory；T01 contract-bundle inventory；T13 signed design、完整 development/power manifests、两个 uniform 的 sequence error-rate intervals、六个 positive 的 sequence recovery-rate intervals 和八个 aggregate probability intervals；T14 wheelhouse/product wheel manifest及 `built_from_commit`；当前 clean `P4_IMPLEMENTATION_COMMIT`；T00 全量 protected inventory、完整正式 provenance-derived actor 不等式及已确认的人类签署者；formal root 和 `$P4_RELEASE_VENV` 原先均不存在且 formal terminal count 为 0。
- **允许修改/禁止修改：** 允许 `$P4_RELEASE_ROOT/{control,contracts,inputs/preparation-evidence,inputs/wheelhouse,inputs/execution-scripts,qualification-design,readiness,work-items/T15}` 和新建 `$P4_RELEASE_VENV`；只读 `$P4_PREP_ROOT`；禁止运行 formal sequence、从工作树复制正式脚本、修改或只复制有利 prep evidence、外部引用 prep 路径代替内容复制、重算/重选 effect、修改 Phase 0–3 和预存成功结果。
- **交付物及接口：** authority-continuity receipt，证明 implementation commit 是 authority commit 后代、四份 authority blobs 与 T00 一致且 T01 contracts 未漂移；正式 release 内完整的 `inputs/wheelhouse/` 及 manifest；`control/execution-environment.json`，证明 T14 `built_from_commit=P4_IMPLEMENTATION_COMMIT`、release venv 仅由该显式 wheelhouse manifest 离线安装、解释器/lock/wheel/distribution RECORD tree hashes 固定，并列出从同一 Git 对象导出的全部 T16–T24 独立脚本 path/SHA；仅用 T11 固定 `benchmark_fixture_id` 对 probability/rank 边界、两条真实规则、one-cycle、formal 8,000 外推、correction/recovery/self-contained replay/validator 等冻结 benchmark units 各 warm-up 后 20 次的 p95/RSS/reachable counts/bytes/hash；并行选择、25% budget/timeout/checkpoint；benchmark 不生成 formal terminal，完成后才冻结 formal 8,000 sequence identities/master hash；`inputs/preparation-evidence/` 中完整复制且逐 SHA 绑定的 T10 oracle/analytic、T12 preflight 及全部 48,000 development sequences/7,200,000 observations、T13 全部 160,000 power sequences/24,000,000 observations、reducers/seeds/receipts 无损压缩分片和 signed design；artifact whitelist、commands、per-file producer provenance、actor assignment、acceptance contract、formal authorization=false->true receipt。
- **依赖/执行命令：** T13,T14；先在任何 release 路径创建前核对 `git rev-parse HEAD` 等于 `$P4_IMPLEMENTATION_COMMIT`、worktree/index clean、T14 `built_from_commit` 相等，再由该 clean commit 的 `scripts/phase4/bootstrap_release_environment.py` 使用 T14 显式 manifest 和 `--no-index --find-links` 创建 `$P4_RELEASE_VENV`、从同一 Git 对象导出冻结脚本并生成 execution manifest；随后只运行 `"$P4_RELEASE_PYTHON" -m lottery_system.phase4 release assemble --phase prepare-formal --authority-commit "$P4_AUTHORITY_COMMIT" --implementation-commit "$P4_IMPLEMENTATION_COMMIT" --authority-receipt "$P4_PREP_ROOT/work-items/T00/receipt.json" --contract-receipt "$P4_PREP_ROOT/work-items/T01/receipt.json" --execution-manifest "$P4_RELEASE_ROOT/control/execution-environment.json" --benchmark-fixtures tests/phase4/fixtures/benchmark/registry.json --prep-root "$P4_PREP_ROOT" --release-root "$P4_RELEASE_ROOT" --design "$P4_PREP_ROOT/qualification-design/power/qualification-design.json" --actor-assignments "$P4_FORMAL_ACTORS" --output "$P4_RELEASE_ROOT/work-items/T15"`。
- **独立验收标准与方法：** verifier 先执行 `git merge-base --is-ancestor "$P4_AUTHORITY_COMMIT" "$P4_IMPLEMENTATION_COMMIT"`，再从两个 Git 对象重算四份 authority blobs 和 T01 contract inventory；任一漂移都停止。它重算 T14 product wheel 的 source commit/hash、release venv 安装清单和 RECORD tree、冻结脚本 Git blob/SHA，要求全部等于 execution manifest且工作树引用数为 0。然后从正式 release 内部单独解压 preparation shards，在 prep root 不可访问时重算 T10–T13 的路径集合、record counts、producer provenance、seeds和selection；两个 uniform cell 各满足 sequence error-rate interval 上端 `q_U<=0.05`，六个 positive cell 各满足 sequence recovery-rate interval 下端 `p_L>=0.90`，八个 cell 各自的 aggregate point estimate 和 simultaneous lower bound 均 `>=0.90`。development/power/formal seed disjoint，workload formula和真实规则 exact histogram/Top-1000/Decimal hash 一致。所有 benchmark receipt 必须绑定 T11 `benchmark_fixture_id`，三种 qualification seed/terminal 引用数为 0；release/venv 原先不存在、正式结果计数 0、dirty 0、全部 actor 不等式和 human-signatory acknowledgement 有效、evidence-return canary path可写；六个 protected roots 与 T00 inventory exact match。负测错误 sequence 方向、非 authority 后代 commit、wheel 的 source commit/hash 不同、改 venv RECORD 或冻结脚本、正式命令引用工作树、改任一 authority/contract blob、漏复制失败 development/power shard、外链 prep path、record count 不符、预算缺 unit、benchmark 使用 qualification seed/terminal、边界 tick benchmark不 exact、并行改变 hash、release/venv reuse、预存 result、角色自审/缺签署者、formal seed overlap、T13 后 design byte change、任一 protected file 变化。
- **失败终态/证据/取回：** `HOLD_AUTHORITY_IDENTITY|FAIL_CONTRACT_DRIFT|HOLD_PREPARATION_EVIDENCE_INCOMPLETE|HOLD_EXECUTION_IDENTITY|HOLD_DEPENDENCY_OR_BUDGET|HOLD_FORMAL_FREEZE`；身份已发放则封存，重试新 release ID。证据 `$P4_RELEASE_ROOT/work-items/T15/`。

### T16：正式小空间资格与 full-rule A07–A10

- **目标/执行角色：** `run_operator` 只执行冻结的 8,000-sequence formal workload 和 A10 oracle workload；不能估计概率、修改代码/设计/seeds/thresholds 或选择输出。
- **前置输入及固定身份：** T15 formal authorization、同一 release contracts/signed design/8,000 sequence identities、T13 aggregate gate定义、`control/execution-environment.json`、`$P4_RELEASE_PYTHON` 和冻结脚本快照。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/qualification/`、ledger/checkpoints/logs、T16 receipt；禁止网络、输入/代码/阈值、删除失败序列和 Champion。
- **交付物及接口：** 2 个 uniform 和 6 个正控各恰好 1,000 的逐 sequence terminals、LR looks/alpha events、每 cell `success_count,sequence_rate,gate_threshold,gate_pass` formal summary；八个 full-rule product/oracle 数值和误差界。formal summary 禁止出现 `gate_pass_probability`，因为一次实现批次只观测门结果。
- **依赖/执行命令：** T15；先通过本计划第 1 节 execution preflight，再运行 `"$P4_RELEASE_PYTHON" -m lottery_system.phase4 research run --mode formal-qualification --release-root "$P4_RELEASE_ROOT" --stop-after-sequences 10` 得受控 exit 20/checkpoint；再同 identity `--resume` 完成；随后运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4_independent/run_full_rule_oracle.py" --release-root "$P4_RELEASE_ROOT" --output "$P4_RELEASE_ROOT/qualification/full-rule-oracle"`。
- **独立验收标准与方法：** acceptance engineer 运行不读取 formal summary 判定字段的 independent reducer，从 8,000 terminals 重算两个 uniform error counts 各 `<=50`、六个 correct recovery counts 各 `>=900`、LR/wealth/stop match 100%、next-shadow changes 与 recovery count 一一对应、Champion changes 0；T16 run operator 与 acceptance engineer 的 actor ID 必须不同。再核对 T13 的 prospective probability 字段未被 formal rate 覆盖。八 K candidate coverage 严格大于 `K/M`，产品/oracle在容差内，expanded tick 边界的 exact tie/rank/Top-1000 回归仍 PASS。负测 missing/duplicate sequence、把 rate 重命名为 probability、换 seed/effect/design、resume wrong hash、budget after exhaustion、选择性删除。
- **失败终态/证据/取回：** 数值门失败为 `FAIL_FORMAL_QUALIFICATION`（不是换配置重试）；可恢复中断为 HOLD；证据 `$P4_RELEASE_ROOT/qualification/` 全量按 manifest 取回。

### T17：同 release 正负 E2E、修订、全来源 canary 与 Phase 0–3 保护

- **目标/执行角色：** `run_operator` 运行冻结 fixture/virtual-clock E2E 和只读官方 canary；不修实现、不创造结论。该 actor 生成正式 E2E/readiness 证据，必须与独立验收这些证据并在 T21 运行 final validator 的 `acceptance_engineer` 不同。
- **前置输入及固定身份：** T16 PASS、T15 E2E registry/source policy/protected inventory，同一 code/input contracts。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/e2e/`、`readiness/official-canary/`、T17 receipt和冻结的隔离 `$P4_STAGING_ROOT`；禁止任一 Phase 0–3 protected root、prep/runtime 写入、等待未来开奖、真实 performance 结论。
- **交付物及接口：** 所有正负 receipts、correction interruption/resume、virtual clock、四个必需来源的 canary raw/parse/dedup/revision/compatibility/network terminals、六个 protected roots before/after inventories。
- **依赖/执行命令：** T16；先通过 execution preflight，再运行 `"$P4_RELEASE_PYTHON" -m lottery_system.phase4 validate e2e --registry "$P4_RELEASE_ROOT/contracts/e2e-registry.json" --release-root "$P4_RELEASE_ROOT" --output "$P4_RELEASE_ROOT/e2e" --clock fixture`；`"$P4_RELEASE_PYTHON" -m lottery_system.phase4 data ingest --mode readonly-canary --source-policy "$P4_RELEASE_ROOT/contracts/source-policy.json" --staging-root "$P4_STAGING_ROOT" --output "$P4_RELEASE_ROOT/readiness/official-canary"`。
- **独立验收标准与方法：** acceptance engineer 从底层 receipt 独立重算 registry 双向覆盖和 guard 命中，均为 100%；source policy 仍在有效期，SSQ 的 `swlc+ydniu` 和 DLT 的 `gdlottery+ydniu` 四个 endpoint 全部成功，每个 game 至少一个已公开重叠 issue 两源核心事实一致，rule/revision/dedup/Phase1 Schema compatible；网络失败命中注册 terminal 可证明失败语义，但不能替代正式 readiness PASS。六个 protected roots before/after exact match；T17 run operator 与 acceptance engineer 的 actor ID 相同则 `HOLD_ROLE_CONFLICT`。
- **失败终态/证据/取回：** 任一必需来源失败/政策过期为 `HOLD_DATA_SOURCE_READINESS`，角色冲突为 `HOLD_ROLE_CONFLICT`，负向被接受 FAIL，任一 protected root 变化为 `FAIL_PROTECTED_ARTIFACT_MUTATION`。证据为显式 canary/E2E manifests。

### T18：VPS 用户级 systemd readiness、恢复和证据回传

- **目标/执行角色：** `vps_operator` 在普通用户权限的目标 VPS 安装/反查 user unit，并在全新环境执行 CLI smoke、fixture、checkpoint resume、release replay 和 evidence-return；不宣称持续 SLO。
- **前置输入及固定身份：** T17 PASS、T14 wheelhouse、T15 frozen schedule/unit hashes、T08 已通过的同一目标 VPS capability probe、同一 release。
- **允许修改/禁止修改：** 允许只读使用 `$P4_RELEASE_VENV`、写 Phase 4 runtime data、`~/.config/systemd/user/` 两个冻结 unit和 `$P4_RELEASE_ROOT/readiness/vps/`；禁止修改/替换 release venv、sudo/root/system unit、正式 evidence mutation、网络 pip 和等待真实开奖。
- **交付物及接口：** install commands/exits、environment facts、`systemctl --user cat/show/list-timers` audit、virtual plan trigger、concurrency/compensation/restart/deadline receipts、recovery timing、evidence-return source/destination hashes。
- **依赖/执行命令：** T17；先通过 execution preflight，再运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4/install_user_systemd.py" --release-root "$P4_RELEASE_ROOT" --runtime-root "$P4_RUNTIME_ROOT" --python "$P4_RELEASE_PYTHON" --output "$P4_RELEASE_ROOT/readiness/vps"`；`"$P4_RELEASE_PYTHON" -m lottery_system.phase4 schedule audit --release-root "$P4_RELEASE_ROOT" --runtime-root "$P4_RUNTIME_ROOT" --output "$P4_RELEASE_ROOT/readiness/vps/scheduler-audit.json"`。
- **独立验收标准与方法：** acceptance engineer 先确认 T08 capability facts 未漂移，再从 T18 底层命令、systemd 反查和哈希记录核对 absolute executable/args/workdir/timezone/5-minute timer/Persistent/RandomizedDelay/concurrency/next plan，与 frozen schedule 100% 一致；clean offline install/smoke/recovery/replay/return 全 PASS，批准 workload benchmark 预算内。该验收记录进入 validator evidence，不由 T22 reviewer 提前生成；无任意硬件门。
- **失败终态/证据/取回：** user manager/linger在权限内不可用 `HOLD_SCHEDULER_UNAVAILABLE`；install/replay/return `HOLD_INSTALL_OR_WORKLOAD`。证据 `$P4_RELEASE_ROOT/readiness/vps/`；卸载不删除审计证据。

### T19：单一 release 装配和递归 evidence manifest

- **目标/执行角色：** `release_controller` 只装配 T15–T18 的同 identity 证据并生成 evidence manifest；不重算科学结论。
- **前置输入及固定身份：** T15–T18 receipts，同一 code/input/contracts/seeds/dependencies/release ID。
- **允许修改/禁止修改：** 允许 `$P4_RELEASE_ROOT/{reports,manifest/evidence-manifest.json,work-items/T19}`；禁止修改已列文件、添加另一个正式 release、隐式 glob/latest/mtime selection。
- **交付物及接口：** 六类交付 coverage map、逐文件 path/role/sha/bytes/parents/producer provenance、inventory hash、分彩种工程/科学摘要（只列矩阵）、evidence-return package list；manifest 必须递归覆盖 T15 `inputs/preparation-evidence/` 内 T10–T13 全部底层分片、`inputs/wheelhouse/`、`control/execution-environment.json` 和 `inputs/execution-scripts/`，不得只列其摘要 manifest。
- **依赖/执行命令：** T18；先通过 execution preflight，再运行 `"$P4_RELEASE_PYTHON" -m lottery_system.phase4 release assemble --phase evidence --release-root "$P4_RELEASE_ROOT" --whitelist "$P4_RELEASE_ROOT/control/artifact-whitelist.json" --output "$P4_RELEASE_ROOT/manifest/evidence-manifest.json"`。
- **独立验收标准与方法：** acceptance engineer 运行 manifest checker，从磁盘逐文件重算，缺失/额外/哈希/parent/provenance mismatch 0；隐藏 prep root 和工作树后仍能从正式 release 枚举并解压 T10–T13 底层分片、核对全部冻结脚本，并由 `$P4_RELEASE_PYTHON` 完成 execution preflight，record counts 必须为 development 48,000 sequences/7,200,000 observations 与 power 160,000 sequences/24,000,000 observations；六类覆盖 100%；禁止措辞 scan 0；manifest 不包含自身 hash 循环，后置允许集仅 T20–T24 明列路径；六个 protected roots 与 T00/T15/T17 inventory exact match。
- **失败终态/证据/取回：** `HOLD_MANIFEST_NOT_CLOSED`；选择性删除/伪造为 FAIL。取回只消费 manifest paths并在两端重哈希。

### T20：独立 bottom-up replay

- **目标/执行角色：** `independent_replay_operator` 只执行已冻结的独立 replay；不 review、不运行 final validator、不签署或 acceptance。
- **前置输入及固定身份：** T19 evidence manifest SHA、T15 actor contract、同一 frozen release；不以产品 summary 为真值。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/{replay,manifest/replay-closure.json,work-items/T20}`；禁止产品、既有 evidence、validator/review/signatures/acceptance。
- **交付物及接口：** 从正式 release 自包含的 genesis/raw fixtures/draw observations/terminals/events 重算 T10 analytic/oracle、T12 development selection、T13 power intervals/aggregate gates/seed disjointness，以及 formal forecast IDs/Top-K/probability/rank/metrics/Champion/三状态/wealth/decisions/correction/current views/evidence manifest；逐事实 match 和 findings；replay closure 绑定 T19 manifest及全部 replay 文件。
- **依赖/执行命令：** T19；先通过 execution preflight，再运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4_independent/replay_release.py" --release-root "$P4_RELEASE_ROOT" --manifest "$P4_RELEASE_ROOT/manifest/evidence-manifest.json" --output "$P4_RELEASE_ROOT/replay"`，随后用同一冻结脚本集合生成并独立核验 replay closure。
- **独立验收标准与方法：** acceptance engineer 在 prep root 不可访问的环境中，从底层记录和全量 hash/relation checker 核对 T10–T18 replay match 100%、产品 import 0、actor 不等式成立；隔离副本中删任一 preparation shard、改一条 draw/terminal 或换 producer actor 都必须产生对应 finding。
- **失败终态/证据/取回：** `HOLD_REPLAY_MISMATCH`，泄漏/伪造/选择性删除为 FAIL；失败 replay 原样保留。

### T21：最终 validator

- **目标/执行角色：** `acceptance_engineer` 只从 T19 evidence 与 T20 replay 执行 A01–A21 final validator；不 review、不签署、不写 acceptance。
- **前置输入及固定身份：** T20 replay closure、T19 manifest、T15 acceptance/actor contracts，同一 release。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/{validator,manifest/validator-closure.json,work-items/T21}`；前序文件只读。
- **交付物及接口：** A01–A21 每项底层 assertions、PASS/HOLD/FAIL、blocking findings、六类 coverage、三类状态和 `engineering_status_candidate`；validator closure 绑定 replay closure。
- **依赖/执行命令：** T20；先通过 execution preflight，再运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4/validate_bottom_up.py" --release-root "$P4_RELEASE_ROOT" --replay "$P4_RELEASE_ROOT/replay/replay.json" --output "$P4_RELEASE_ROOT/validator/final-validator.json" --actor-assignments "$P4_FORMAL_ACTORS"`，随后用同一冻结脚本集合生成 validator closure。
- **独立验收标准与方法：** independent reviewer 不信顶层字段，逐项抽取底层引用并重算；A01–A21 assertion coverage 100%、blocking 0、六类覆盖 100%、角色冲突 0。mutation harness 对 ledger/event/seed/score/state/manifest 每类至少一例命中注册 guard。
- **失败终态/证据/取回：** `HOLD_VALIDATOR_INCOMPLETE`，接受负向或伪造结论为 FAIL。

### T22：独立 release review

- **目标/执行角色：** `independent_reviewer` 只审查 T21 validator、T20 replay、证据完整性和独立性；不运行产品、不修改 validator、不签署或 acceptance。
- **前置输入及固定身份：** T21 validator closure、T20 replay closure、T19 evidence manifest、正式 actor contract。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/{review,manifest/review-closure.json,work-items/T22}`；所有前序路径只读。
- **交付物及接口：** review findings、independence audit、A01–A21 review disposition、科学措辞候选及 review closure。
- **依赖/执行命令：** T21；先通过 execution preflight，再执行正式 release 内冻结的 review checklist 和 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4_independent/check_review_closure.py" --release-root "$P4_RELEASE_ROOT" --validator-closure "$P4_RELEASE_ROOT/manifest/validator-closure.json" --output "$P4_RELEASE_ROOT/review"`。
- **独立验收标准与方法：** release controller 只校验 review Schema、actor/task/session identity、全部 findings disposition 和 closure hashes；从 T19 per-file provenance 和 T20/T21 receipts 派生被审查 producer 集合，T22 reviewer 与任一 product/contract/oracle/development/power/formal/E2E/readiness/manifest/replay/validator producer、controller 或 operator 重合都为 HOLD，不能以更换 role label 规避。blocking finding 非 0 不能进入 T23。
- **失败终态/证据/取回：** `HOLD_REVIEW_INCOMPLETE|HOLD_ROLE_CONFLICT`；隐瞒 finding 为 FAIL。

### T23：人工交付与科学措辞签署

- **目标/执行角色：** T00 已确认的 `human_signatory` 只审阅 T22 提供的最终交付矩阵和科学措辞，明确确认没有把合成能力写成真实改善；不改任何证据。
- **前置输入及固定身份：** T22 review closure、T21 validator closure、T00/T15 signatory assignment，同一 release。
- **允许修改/禁止修改：** 只允许 `$P4_RELEASE_ROOT/{signatures/human-signature.json,manifest/signature-closure.json,work-items/T23}`；签名对象只能引用既有 hash并记录 signer identity/time/decision/comment，不得生成技术事实。
- **交付物及接口：** 一份同时覆盖 delivery completeness 与 scientific wording 的人工签署和 signature closure；上位合同只要求人工签署，不擅自增加第二个人类签署者。
- **依赖/执行命令：** T22；先通过 execution preflight，由人类签署者使用冻结签署入口确认，随后运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4_independent/validate_human_signature.py" --signature "$P4_RELEASE_ROOT/signatures/human-signature.json" --review-closure "$P4_RELEASE_ROOT/manifest/review-closure.json" --actor-assignments "$P4_FORMAL_ACTORS"`。
- **独立验收标准与方法：** release controller 只验证 signer 正是 T00/T15 actor、不在任何被签署 producer/reviewer 集合中、明确 `decision=APPROVED`、引用 hash 精确且 actor 不等式成立；机器或未指派 actor 冒签、空白批准、引用旧 review 均失败。`acceptance_approver` 在 T23 不执行、不验签、不生成 receipt，首次且仅在 T24 介入。
- **失败终态/证据/取回：** 缺少或拒绝签署为 `HOLD_HUMAN_SIGNATURE`；伪造为 FAIL。

### T24：独立最终交付验收（最后任务）

- **目标/执行角色：** 仅 `acceptance_approver` 对 T23 已闭合的同一冻结 release 从底层证据签发唯一工程终态；作者不得验收自己的实现。
- **前置输入及固定身份：** T23 signature closure、T22 review closure、T21 validator closure、T20 replay closure、T19 manifest、T15 acceptance/actor contracts，全部同 `P4_RELEASE_ID` 和固定 SHA。
- **允许修改/禁止修改：** 只允许新建 `$P4_RELEASE_ROOT/acceptance/I01/{acceptance.json,postcheck.json}` 和 T24 receipt；禁止修改任何既有文件、重新跑/挑选 qualification、改变状态或结论。
- **交付物及接口：** acceptance Schema 包含 A01–A21 derived results、blocking findings、六类 coverage、工程状态、逐 game/model/K 科学矩阵、Champion 和 staged closure hashes；只在全部门通过时 `status=PASS, engineering_status=SYSTEM_MVP_GO`。
- **依赖/执行命令：** T23；先通过 execution preflight，再运行 `"$P4_RELEASE_PYTHON" -m lottery_system.phase4 release accept --release-root "$P4_RELEASE_ROOT" --iteration I01 --validator "$P4_RELEASE_ROOT/validator/final-validator.json" --review "$P4_RELEASE_ROOT/review/review.json" --signature "$P4_RELEASE_ROOT/signatures/human-signature.json" --actor-assignments "$P4_FORMAL_ACTORS" --output "$P4_RELEASE_ROOT/acceptance/I01"`；随后运行 `"$P4_RELEASE_PYTHON" "$P4_FROZEN_SCRIPTS/scripts/phase4_independent/postcheck_acceptance.py" --release-root "$P4_RELEASE_ROOT" --iteration I01 --execution-manifest "$P4_RELEASE_ROOT/control/execution-environment.json"`，从 T19–T23 closure 链重算允许路径及六个 protected roots。
- **独立验收标准与方法：** approver 不信顶层 PASS；从 T00–T23 全部 provenance/receipts 派生禁止 actor 集合，acceptance approver 与其任一重合都 HOLD。A01–A21=PASS、blocking=0、delivery=100%、T10–T13 preparation evidence 可从正式 release 底层重算、staged closure 无断链、postcheck 无未登记 extra/changed file、六个 protected roots exact match、角色冲突 0 才 exit 0。模型最多 `shadow_candidate`，Top-K 必为 `insufficient_observation`，Champion仍 M0；任何全局 improved 拒绝。
- **失败终态/证据/取回：** 可恢复未完成 `HOLD`，不可恢复因果/篡改/伪造/越权 `FAIL`；不得写 `SYSTEM_MVP_GO`。失败 I01 不覆盖，修复按第 6 节用新 iteration/release。证据从 `$P4_RELEASE_ROOT/acceptance/I01/` 和 T24 receipt 取回。

## 4. A01–A21 双向追踪矩阵

命令简称：`U=unittest tests/phase4`，`E=validate e2e`，`Q=T16 formal qualification`，`O=T10/T16 independent oracle`，`C=T03 early + T17 formal canary`，`S=T08 early probe + T18 schedule audit`，`R=T20 replay`，`V=T21 final validator`，`W=T22 review + T23 human signature`，`A=T24 accept`。所有简称均指上面任务卡的完整命令和对应 receipt。

| 验收项 | 总体设计章节 | 子任务 | 交付物 | 验收命令/正式证据 |
| --- | --- | --- | --- | --- |
| P4-MVP-A01 | 4、9、10 | T05,T07,T09,T11,T17 | 双 game cycle/lock/capability/next forecast | E,R,V；`e2e/*cycle*`, `replay/replay.json` |
| P4-MVP-A02 | 7 | T04,T05,T10,T11 | forecast/ranking Schema、1000 tickets | U,O,E,R |
| P4-MVP-A03 | 7 | T04,T10,T11,T15 | expanded-bound probability/order/tie vectors、exact benchmark | O,E,R,V |
| P4-MVP-A04 | 7 | T04,T10,T16 | M0 full-space known answers | O,Q,R |
| P4-MVP-A05 | 8、10 | T07,T11,T17 | parameter diff/child shadow | E,R,V |
| P4-MVP-A06 | 8、10 | T07,T11,T17 | feature snapshot/diff/shadow | E,R,V |
| P4-MVP-A07 | 10、13 | T07,T10,T12,T13,T16 | Ville/解析 certificate、power intervals、2,000 formal uniform terminals | O,Q,R,V；`qualification/uniform/` |
| P4-MVP-A08 | 8、10、12 | T07,T11,T16 | alpha ledger/stop/governance | Q,E,R |
| P4-MVP-A09 | 10、13 | T07,T10,T12,T13,T16 | sequence power + prospective aggregate probability、六个 1,000 formal cells | O,Q,R,V |
| P4-MVP-A10 | 7、13 | T10,T13,T16 | full-rule spec/eight numeric cells | O,Q,R |
| P4-MVP-A11 | 9、12 | T01,T05,T11,T17 | time/label/tamper receipts | E,R,V |
| P4-MVP-A12 | 4、8、12 | T07,T09,T11,T17 | game/governance isolation | E,R,V |
| P4-MVP-A13 | 4、10、12 | T02,T07,T08,T11,T18 | checkpoints/fault terminals | U,E,S,R |
| P4-MVP-A14 | 5、9.4、14 | T00,T02,T03,T17,T24 | genesis/data chain/early+formal canary/Phase0–3 protection | C,E,R,V,A |
| P4-MVP-A15 | 3、14 | T10,T15,T19,T20 | self-contained preparation evidence/independent replay/manifest | R,V |
| P4-MVP-A16 | 14 | T01,T19,T20,T21,T22,T23,T24 | coverage/replay/validator/review/human signature/acceptance | V,W,A |
| P4-MVP-A17 | 3、13、14 | T12,T14,T15,T18 | prep/formal benchmark、lock/wheelhouse/rebuild/readiness | S,R,V |
| P4-MVP-A18 | 12 | T01,T09,T11,T20 | state Schemas/projections | U,E,R,V |
| P4-MVP-A19 | 11 | T03,T08,T11,T18 | calendar/schedule/systemd audit | E,S,R |
| P4-MVP-A20 | 9 | T05,T06,T10,T11,T17 | locked diagnostic/score/window oracles | O,E,R,V |
| P4-MVP-A21 | 5、9.4、10 | T02,T06,T07,T08,T11,T17 | two-stage correction impact/remediation/closure/current view | E,R,V |

反向检查：每个 T00–T24 的任务卡都至少产生一项被上表、六类交付矩阵或 readiness/治理硬门消费的可观察交付；每项在其依赖完成时即可独立验收。T24 仅消费同一 release，且是最后一步。

## 5. 六类交付物覆盖矩阵

| 上位合同六类交付物 | 负责子任务 | 固定主要路径 | 覆盖门 |
| --- | --- | --- | --- |
| 1 定义与合同 | T00,T01,T10,T13,T15 | `docs/`、`config/phase4/`、formal `contracts/qualification-design`、`inputs/preparation-evidence/` | T19 coverage + T21 validator |
| 2 实现 | T02–T09 | `src/lottery_system/phase4/`、`deploy/systemd-user/` | T11 E2E、T20 replay、T21 validator |
| 3 机器接口 | T01,T09,T14 | `schemas/phase4/`、CLI、config、`requirements/phase4.lock` | Schema/CLI/clean install |
| 4 验证资产 | T10–T13,T16,T17 | `tests/phase4*`、`scripts/phase4_independent/`、`inputs/preparation-evidence/`、`qualification/`、`e2e/` | oracle/qualification/E2E 100% |
| 5 运行材料 | T01,T08,T12,T14,T15,T18 | runbook、formal `inputs/wheelhouse/` 及 manifest、prep/formal benchmark、VPS readiness | T12 resource gate + T18 evidence-return |
| 6 正式证据 | T15–T24 | `artifacts/phase-4/<release-id>/` | staged manifest/replay/validator/review/signature/acceptance closure |

任何一类 coverage 小于 100% 时 P4-MVP-A16 和 T24 必须 HOLD。

## 6. 冻结、正式运行和不可变迭代边界

T00 只在 authority 内容已经进入 `main` 后冻结 commit/genesis/全量 protection、角色/人类签署者和四类路径合同；T01 冻结表示边界、LR/alpha、效应菜单、重复数、置信与 aggregate 算法等语义和机器接口；T03 在昂贵工作前冻结并实测 Phase 4 source policy；T10 在任何产品概率/指标实现和任何随机结果前冻结独立 vectors/certificate/source hash；T12 在任何 development/power 结果前冻结目标 VPS preparation budget，然后只能用 development domain 按固定顺序选择 candidate；T13 在读取 power terminal 前先签收 candidate hash，随后只生成独立 power confirmation，结果后禁止反馈修改同一 design。T14 冻结依赖、产品 wheel hash及其 source commit；T15 重验 authority ancestry/四文件/T01 contracts，证明 T14 source commit 等于 `P4_IMPLEMENTATION_COMMIT`，离线创建并冻结 release venv/安装清单/独立脚本快照，将 T10–T13 全部底层证据自包含封装入正式 release，再冻结 code/input/contracts/dependencies/execution、正式 seeds/sequence identities、formal workload/resource budget、formal actors、E2E/acceptance identity，确认正式结果为 0。只有 T15 PASS 才能运行 T16。T16–T24 每项先复核 execution manifest且禁止工作树入口；T16 后任何影响数学、seed、阈值、资格、metric、time、source、calendar、状态、执行身份或 acceptance 的变更都必须新 `P4_RELEASE_ID`，旧 release 原样封存；不能在当前 release 修补语义。

可恢复的环境/网络/中断使用同一逻辑对象的新 attempt，从验证通过的 checkpoint 继续，失败 attempt 永久存在。实现 bug 若不改变合同，可在 prep 阶段回到最早 T02–T11 节点，产生新 code commit、receipt 并重新执行全部依赖节点；正式 T15 后发现则必须新 release。power 不足封存当前 design 的 development/power 全部结果；任何修订先回到 T10/T12，创建新 design ID，并由该 ID 派生全新的 development 与 power seed sets，不能只换 power seed 重抽。formal threshold 失败不能改 effect/controller/seed重跑。acceptance 可在同一完全未改变 evidence release 上最多补一次纯验收材料 iteration `I02`，只能修复签名/manifest引用等不改变底层证据的问题；任何底层文件变化均新 release。

失败、超时、负向、低功效、不利 sequence、source conflict、canary network failure、review finding 和 acceptance attempt 不得删除、覆盖、重命名成成功或从 manifest 中选择性遗漏。`FAIL` 现场立即封存；`HOLD` receipt 必须写最早恢复节点、固定输入、未完成输出和唯一恢复命令。证据取回始终按显式 manifest 路径核对源端/接收端路径集合、bytes 和 SHA，不通过 `latest`、glob 或修改时间。

## 7. 可完成性和边界自检

逐任务输入在依赖完成时均存在：T00 显式验证同一 `P4_AUTHORITY_COMMIT` 原子绑定四份 authority 文件、actor 和 human signatory；T01 使用 T00；T02 与 T10 使用 T01；T03 使用 T02；T04 使用 T02/T10；T05 消费 T10 诊断 oracle 并独占 diagnostic；T06 只交付 score/window correction，T07 只消费其明确 impact 交付 research remediation，T08 才编排两段闭包，不再引用未来对象；T09 只组合已存在 provider；T11 等待集成和独立 oracle；T12 先交付动态资源门再发放 development seed，T13 使用独立 power seed；T14 使用成形 package并绑定其 source commit；T15 续验 authority、从 T14 wheel 离线创建正式执行环境并把全部结果前底层证据和固定脚本复制进正式 release；T16–T24 只消费同一正式 release和 execution manifest。T15 的 bootstrap 只使用当场验证为 exact clean implementation commit 的代码且在 formal authorization 前结束，因此没有循环依赖；独立脚本使用同一解释器只固定依赖环境，不授权 import 产品核心。每项验收依赖都已列入对应任务，不引用未来任务；每项输出都有具体路径、Schema/接口、命令、正负验收、失败终态和 staged closure 取回路线。

没有任务依赖未来真实开奖：T03/T11/T16/T17 使用固定或合成 fixture/虚拟时钟，两次 canary 只读取已公开且两源重叠的期次；T08 早期 probe和 T18 安装审计都不等待真实周期。没有任务要求为 Phase 1 历史记录补造 PIT；外部特征没有真实 `available_at` 就 fail closed。正式资格断网，仅 T03/T17 readiness 使用 source policy 中的四个公开 GET；无数据库、队列或公共 API。没有 sudo/root 任务；systemd 是 `--user`。没有通用硬件阈值；T12 和 T15 分别只以 preparation/formal 批准 workload 的 20 次 benchmark 和固定公式裁决。没有 Phase 0–3 修改权限；T00/T15/T17/T19/T24 递归前后保护。

复杂工作量只能通过新 benchmark identity 调整并行度、batch 和 checkpoint 频率；sequence 数量/长度、效应、seed、控制器、A07/A09 阈值、概率/tie/rank、metric 或 evidence 不得降低。扩大 P4E1 边界后仍必须用 sparse exact histogram/独立分区枚举正确完成 full-space tie/rank、50 位正概率和 Top-1000；正式 8,000 序列、八个 full-rule 单元、独立 replay 或递归证据在批准预算内仍不能完成时，工程终态为 HOLD，不接入候选，也不生成近似科学或概率结果。
