# Phase 3 point-in-time 证据准备与恢复（W08 PIT）

版本：1.0

状态：历史证据记录，已由 Phase 3 总体设计 v1.1 废止为启动前置条件；当次终态仍为 `HOLD_PENDING_PIT_EVIDENCE`，正式结果数量 0

> **废止说明（2026-08-09）：** 本文保留此前不可覆盖的失败尝试及其当时结论，但其“每个历史开奖号必须有历史网页发布时间证据”的前提不再是 Phase 3 权威合同。历史回测现在使用 `retrospective_sequence_safe`：来源期严格早于目标期，预测先于标签解锁。本文不得再用于阻断 W08；只有未来引入外部时变字段时，真实 `available_at_utc` 证据要求才重新适用。

冻结上位定义：`tasks/phase3/README.md`（SHA-256 `0b1bcc329c8063a8336e188e7e88b99542c038cc28a51387b81867d5953e1cdf`）

本迭代身份：`p3-pit-prep-20260808-i02`，父迭代 `p3-pit-prep-20260808-i01`

证据目录：`artifacts/phase-3-pit/p3-pit-prep-20260808-i02/`
状态文件：`artifacts/phase-3-pit-preparation/phase3-pit-preparation-status.json`

## 1. 目的与范围

本文档记录 Phase 3 的一次新的、不可覆盖的 **point-in-time（PIT）证据准备迭代**。它的目标不是产生正式模型结果，而是：

1. 在独立目录中以唯一身份保存一次 PIT 证据采集尝试，保留原始 receipt、解析语义、内容 SHA-256、时区与复核记录。
2. 用机器可复核的方式回答：当前冻结的 400 期实际特征输入是否都能证明 `available_at_utc < prediction_locked_at`。
3. 若 PIT 证据不足，交付诚实的 `HOLD_PENDING_PIT_EVIDENCE` 终态、缺口清单和 0 个正式结果；不得伪装为成功。

本迭代**不修改** `config/phase3/` 候选合同，也**不修改** `artifacts/phase-1/`、`artifacts/phase-2/`、`artifacts/phase-2.1/` 任何受保护制品，**不创建** `artifacts/phase-3/<release-id>`，**不生成**生产预测、投注或收益结论。

## 2. 权威来源与只读恢复（W08-PIT-01）

本迭代只读恢复并锁定下列权威来源，作为采集与判定的基准：

- `tasks/phase3/README.md`、`docs/research/phase-3-overall-design.md`、`docs/plans/phase-3-detailed-plan.md`、`docs/runbooks/phase-3-historical-research-runtime.md`。
- Phase 1/2/2.1 冻结身份（由 `validate_prerun_contract` 中的 `FROZEN_INPUTS` 逐项重哈希校验）。
- 既有正式拒绝证据 `artifacts/phase-3-development/p3-formal-refusal-20260807-i01/receipt.json`（本迭代以 `supersedes_parent_release` 引用它，不覆盖）。

恢复上下文记录在 `recovery-context.json`：容器指标（`/.dockerenv` 存在）、worker、Git HEAD、远端 `origin/main` HEAD、分支、`requirements/phase3.lock`、已有任务查询结果，以及“只读不变量”清单。环境依赖按 `requirements/phase3.lock`（以及 Phase 2/2.1 锁）安装；正式运行与 replay 必须离线。

因为 task id、worktree、branch、log 与既有产物路径都可在仓库内验证，本迭代使用**新的唯一身份** `p3-pit-prep-20260808-i02`，并以 `i01` 作为只读父证据而非复用或改写它，以保持每次迭代不可覆盖。

## 3. 时间语义与 PIT 绑定规则（W08-PIT-02）

冻结数据共 400 期（DLT 200 期 `2025034–2026083`，SSQ 200 期 `2025037–2026085`）。每条 `DrawRecord` 的 `knowledge_class` 均为 `retrospective_current_view`，且 `available_at_utc=null`。唯一与时间相关的字段是 `draw_date_local`（开奖日期）。

因此 Phase 3 必须 fail closed。本迭代采用如下严格绑定规则（实现在 `src/lottery_research/phase3/pit_recovery.py`）：

- 对每个 `(game, target_issue, source_field=prior_draw_result)` 明确 `prediction_locked_at` 与 `available_at_utc`。
- 只有 `evidence_method=archived_publication` 且存在独立归档原件，才能把一行从 `unknown` 变为 `eligible`。
- 该归档原件必须同时绑定 `(game, issue_id, front_numbers, back_numbers)` 与冻结 Phase 1 记录一致，并从**允许的** basis 派生可用时间。
- 允许的 basis：`independent_archive_capture_timestamp`（独立归档抓取时间戳）。
- **禁止**作为可用时间来源：`draw_date`、`http_date`、`retrieved_at`、`first_seen_at`、`current_page`、`current_view`、`cms_publish_date`、`scheduled_broadcast`、`planned_air_time`。换言之，不得用开奖日期、当前页面、`first_seen_at`、`retrieved_at`、HTTP `Date`、当前 CMS `PublishDate` 或计划播出时间推断可用时间。
- `unknown` 行必须 fail closed：`evidence_method=none`、两个时间戳为空、`reason_code` 属于失败闭合集合（如 `PIT_AVAILABILITY_UNPROVEN`）。

## 4. 采集尝试与负向证据

公网仅用于准备期采集。本迭代通过 `scripts/phase3/pit_collect_recon.py` 对独立归档（Internet Archive Wayback Machine availability API）做只读侦察，目标为官方 SSQ 与 DLT 的主页与逐期结果端点，receipt 逐条保存于 `evidence-collection/http-receipts/`。该侦察**不派生任何 eligibility**。

实测结果（`collection-attempt.json -> reconnaissance`）：

| 目标 | 类型 | 归档快照 | 能否绑定逐期结果 |
| --- | --- | --- | --- |
| `ssq-official-home`（cwl.gov.cn 主页） | 当前视图 | 有 | 否 |
| `ssq-result-api`（逐期结果接口） | 逐期结果 | 无 | 否 |
| `dlt-official-home`（lottery.gov.cn 主页） | 当前视图 | 有 | 否 |
| `dlt-history-api`（逐期历史接口） | 逐期结果 | 无 | 否 |

结论：官方主页虽有当前视图归档快照，但主页快照不能绑定任一期结果；唯一能绑定“期号+号码”的逐期结果端点没有归档快照。因此没有任何归档原件能同时绑定 `(game, issue, numbers, availability time)`。采集失败、缺失与空结果均被保留（`missing_or_failed_evidence_preserved=true`），未删除或选择性报告。

## 5. PIT 合同冻结（W08-PIT-03）

本迭代在自身目录中重新生成新的、互相绑定哈希的合同（**不原地编辑候选合同**）：

- `input-manifest.json`：由 `FROZEN_INPUTS` 重新派生，逐项重算 SHA-256 与字节数。
- `availability-ledger.json`：400 行，全部 `eligibility=unknown`、`evidence_method=none`、`available_at_utc=null`、`prediction_locked_at=null`、`reason_code=PIT_AVAILABILITY_UNPROVEN`，`append_only=true`。
- `data-time-contract.json`：绑定 `input_manifest_sha256` 与 `availability_ledger_sha256`，`unknown_availability_policy=fail_closed_hold`。
- `preregistration.json`：结果盲，`formal_run_authorized=false`，`outer_targets=[]`，`results=[]`，绑定上述三者哈希。

`manifest.json` 显式枚举核心合同、复核文件以及 `evidence-collection/` 下每一份原始回执的路径、角色、SHA-256、字节数、行数与 `inventory_sha256`。`pit-validation.json`、`receipt.json` 和顶层状态文件是由这些已闭合输入派生的终态摘要，不纳入自身清单，避免循环依赖；写状态前会再次从磁盘独立重算 bundle。

## 6. 覆盖、独立复核与负向篡改测试（W08-PIT-04）

独立校验器 `validate_pit_preparation_bundle`（`scripts/phase3/pit_recovery.py validate`）只从磁盘重算，不接受顶层自报：

- 重算输入身份、抽签清单、逐 `(game, target_issue)` 覆盖；
- 重算合同与清单的哈希闭包（manifest↔ledger↔contract↔preregistration）；
- 对每行用 `assess_availability_entry` 判定 eligibility，重算 `available_at_utc < prediction_locked_at`、号码绑定与 basis 合法性；
- 任何“声称 eligible 却无真实归档绑定”的行被视为伪造/推断，**直接拒绝**（抛出阻断错误）。

实测覆盖（`pit-validation.json`）：

- `eligible_feature_coverage = 0.0`（0/400）；
- `availability_ledger_coverage = 1.0`，`input_identity_coverage = 1.0`，`draw_inventory_coverage = 1.0`；
- `blocking_findings = 0`，`formal_result_count = 0`；
- 全部 binding checks 为真。

负向篡改矩阵（`negative-tamper-report.json`，9 例全部符合预期，`synthetic_only=true`）：

1. T1 eligible 行未用 `archived_publication` 方法 → 拒绝；
2. T2 eligible 行缺失归档原件 → 拒绝；
3. T3 可用时间来自禁止 basis（`draw_date`）→ 拒绝；
4. T4 归档原件号码与冻结记录不一致 → 拒绝；
5. T5 `available_at_utc >= prediction_locked_at` → 拒绝；
6. T6 unknown 行擅自断言可用时间 → 拒绝；
7. T7 unknown 行缺少失败闭合 reason → 拒绝；
8. **T8 正向对照**：真实构造的归档绑定被接受（证明门控会区分，而非一律拒绝）；
9. T9 越权动作（`champion_promotion`）被阻断。

单元与集成测试见 `tests/phase3/test_pit_recovery.py`（含只读不变量校验：构建前后 `config/phase3/*.json` 逐字节不变）。

## 7. 结论与交付状态

PIT 证据**未闭合**。缺口清单：

1. 400/400 可用性行仍为 `unknown`（PIT 未证）；
2. 需要全部 400 行以 `archived_publication` 绑定才能达到 100% 覆盖，当前 0 行；
3. 独立归档侦察中，逐期结果端点无快照，主页快照属当前视图、不可作为可用时间证据。

因此本迭代终态为：

- `terminal = HOLD_PENDING_PIT_EVIDENCE`，`status = HOLD`，`exit_code = 20`；
- `formal_run_authorized = false`，`formal_result_count = 0`；
- `delivery_state = HOLD`，`acceptance_verdict = BLOCKED`，`evidence_only = true`，`delivery_verified = true`。

`delivery_verified=true` 表示“该 HOLD 已被独立重算核实为真”，**不是**成功。证据不足只能表现为 `BLOCKED/evidence_only`，不得冒充 `ACCEPTED/DELIVERED_SUCCESS`。M0 仍为永久 Champion；本次不产生 `shadow_candidate`，不触发任何晋级、发布或投注。

## 8. 当时定义的恢复步骤（现已废止）

以下条目只解释当次 HOLD 的历史判定，不再是 Phase 3 启动条件。不得继续采集或补造 400 期网页归档来满足它们：

1. 对 DLT 取得官方逐期 PDF / 对 SSQ 取得能同时绑定期号、号码与历史可用时间的官方或可审计归档原件；对全部 400 个目标期完成绑定。
2. 每个归档原件写入 `evidence-collection/archived-publication/<game>/<issue>.json`，`availability_basis` 属于允许集合，号码与冻结 Phase 1 记录一致。
3. 把对应 ledger 行改为 `eligible` + `archived_publication`，填入严格满足 `available_at_utc < prediction_locked_at` 的时间戳。
4. 重新绑定 `data-time-contract`、`preregistration` 与 `manifest` 的哈希闭包。
5. 用 `validate_pit_preparation_bundle` 重算，直到 `eligible_feature_coverage = 1.0`、`blocking_findings = 0`，方可进入结果盲冻结、正式运行、replay、11/11 E2E、最终 acceptance 与独立复核。

本次失败证据继续保持不可修改。新的权威 W02 验证使用详细计划 1.2 节给出的完整命令，必须显式传入 `--identity`、准备期 actor assignment、W01 upstream receipt 和 `--output`；它要求 300 个 outer targets 和 37,350 条严格历史序列关系全部通过，但不把本历史 bundle 改写为成功。

## 9. 产物索引

- 证据目录：`artifacts/phase-3-pit/p3-pit-prep-20260808-i02/`
- 状态文件：`artifacts/phase-3-pit-preparation/phase3-pit-preparation-status.json`
- 模块：`src/lottery_research/phase3/pit_recovery.py`
- CLI：`scripts/phase3/pit_recovery.py`（`build`/`validate`/`tamper`）、`scripts/phase3/pit_collect_recon.py`
- 归档原件 schema：`schemas/phase3/pit-archived-publication.schema.json`
- 测试：`tests/phase3/test_pit_recovery.py`
- 回归命令：
  ```bash
  PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p "test_*.py" -v
  PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
  PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
  python3 scripts/phase3/pit_recovery.py tamper
  python3 scripts/phase3/pit_recovery.py validate --bundle artifacts/phase-3-pit/p3-pit-prep-20260808-i02
  ```
