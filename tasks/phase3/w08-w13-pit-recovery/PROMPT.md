# Phase 3 W08-W13 PIT Recovery and Formal Delivery

你在远程 VPS 的 `agent-cli` Docker 容器中执行本任务。目标是为 Phase 3 建立一个新的、不可覆盖的 PIT 证据准备迭代；只有在两个彩种的实际特征输入都满足 `available_at_utc < prediction_locked_at` 且证据闭包完整时，才允许继续正式 W08-W13。不能修改现有候选合同或历史正式制品。

## 总体方案

1. 读取并锁定权威来源：`tasks/phase3/README.md`、`docs/research/phase-3-overall-design.md`、`docs/plans/phase-3-detailed-plan.md`、`docs/runbooks/phase-3-historical-research-runtime.md` 以及现有 Phase 1/2/2.1 身份。
2. 建立唯一的 PIT preparation iteration/release identity 和独立目录，保存所有原始响应、PDF/归档原件、HTTP receipt、解析结果、内容 SHA-256、时区和复核记录。失败、缺失和冲突证据必须保留。
3. 对 DLT 验证官方逐期 PDF 覆盖；对 SSQ 只接受能同时绑定期号、号码和历史可用时间的官方或可审计归档原件。不得使用开奖日期、当前页面、`first_seen_at`、`retrieved_at`、HTTP `Date`、当前 CMS `PublishDate` 或计划播出时间推断可用时间。
4. 生成新的 evidence manifest、availability ledger、data-time contract 和结果盲 preregistration。当前 `config/phase3/` 候选文件及 `artifacts/phase-1/`、`artifacts/phase-2/`、`artifacts/phase-2.1/` 正式制品只读保留。
5. 运行独立 PIT validator、负向篡改测试和交叉核验。PIT 覆盖不足时，交付 `HOLD_PENDING_PIT_EVIDENCE` 证据报告，不启动正式 W08-W13；覆盖达到 100% 时，冻结新合同后再运行 W08-W13、replay、E2E 和 acceptance。

## 详细计划

### W08-PIT-01 只读恢复与身份

- 记录 VPS、容器镜像、worker、spec-executor doctor、Git HEAD、远端 main 和已有任务查询结果。
- 复用旧任务只在 task id、workdir、日志和输出目录全部可验证时允许；否则使用新的唯一 identity。

### W08-PIT-02 证据采集

- 公网仅用于准备期采集，正式运行必须离线。
- 每条原件保存来源 URL、收集时间、内容类型、字节数、SHA-256、解析器版本和证据语义。
- 逐期交叉核验期号、开奖日期、分区号码与冻结 Phase 1 标签；不一致即记录冲突并阻断。

### W08-PIT-03 PIT 合同冻结

- 新 ledger 必须逐 `(game, target_issue, source_field)` 覆盖实际特征输入，并显式记录 `prediction_locked_at`、`available_at_utc`、证据方法、证据引用和 reason code。
- 只允许真实证据证明的严格时间顺序；未知或不确定一律 `unknown`/`HOLD`。
- 新 manifest、ledger、data-time contract 和 preregistration 互相绑定哈希；旧候选合同不得原地编辑。

### W08-PIT-04 独立复核

- 独立路径重算覆盖率、时间排序、号码一致性、哈希闭包和负向篡改结果。
- 失败证据保留；不得删除、覆盖或选择性报告。

### W09-W13 正式阶段（仅 PIT 100% 时）

- 在同一结果盲 release 上执行 qualification、历史滚动评估、replay、11/11 E2E、最终 acceptance 和独立复核。
- 两种彩票分开训练、评估和报告；M0 永久保持 Champion，科学分类与交付状态分离。
- 任一前置门失败则终止正式运行并保留 HOLD 证据。

## 必须交付

- `docs/research/phase3-pit-evidence-preparation.md`：来源、时间语义、覆盖、限制、结论和恢复步骤。
- 新 PIT evidence bundle：原始原件、receipt、解析结果、manifest、ledger、data-time contract、preregistration、validator 和复核报告。
- 若 PIT 未闭合：明确的 `HOLD_PENDING_PIT_EVIDENCE` 终态和缺口清单，且正式结果数量为 0。
- 若 PIT 闭合：同一 release 的正式 W08-W13 结果、replay、E2E、acceptance 和最终证据 manifest。

## 验收

- 运行仓库现有 Phase 3、Phase 2.1、Phase 2 回归测试。
- 新增 PIT validator 和负向篡改测试全部通过。
- 只读 verifier 重新计算所有哈希和覆盖率；不接受顶层自报。
- 成功交付必须同时满足 `acceptance_verdict=ACCEPTED`、`delivery_state=DELIVERED_SUCCESS`、`delivery_verified=true`。证据不足只能是 `BLOCKED/evidence_only`，不能冒充成功。

完成后必须执行 `git add -A`、`git commit`，并保留提交 SHA。不得修改受保护历史制品，不得生成生产预测、投注或收益结论。
