# Iteration 02 for lottery-phase-2.1-20260805

先阅读远端任务目录中的 `prompt.md`、`iteration-01.md`、本文件，以及当前任务分支的 Git 状态、已有提交、已有 Phase 2.1 bundle 和 runs 证据。继续使用当前 worktree 和当前分支，不得 reset、删除、覆盖或重写任何已有 runs、已提交 release 或历史正式制品。

## 触发原因

PR #1 已被关闭并驳回。审查结论不是 CI 或 PR 元数据问题，而是指出上一轮验收实现存在可绕过的生产逻辑缺陷。上一轮 release `P2.1-R00-60d02be4dbe9` 必须作为 rejected evidence 保留，不得原地修复或覆盖。当前任务分支仍为 `codex/lottery-phase-2.1-20260805`，基线和历史身份继续保留。

## 已确认的根因

1. `workflow.py` 的 E2E-P2.1-02、06、09 只对字节追加后重新计算哈希，并手工赋予失败状态，没有修改隔离副本，也没有调用生产验证路径；测试即使验证逻辑失效仍会通过。
2. `SOURCE_PATHS` 漏掉实际导入执行的 `src/lottery_research/phase2`，release identity 没有覆盖完整运行代码。
3. `accept()` 无条件写入 `status=PASS` 和 `delivery_status=GO`，即使 G6 失败也会产生矛盾的验收结论。
4. `validate_final_bundle.py` 只验证 Schema、手工 PASS 字段和已有 manifest 闭包，没有从底层证据重算 gates/metrics；acceptance 又未被有效纳入完整性约束。
5. readiness 直接信任输入中的 `formal_historical_result_count=0`，没有扫描实际 release 和受保护结果目录。
6. historical audit 和 power 大量复制旧 Phase 2 结果，只重算 slow-drift 部分，没有在同一个 Phase 2.1 final bundle 上完整重跑。
7. `prepare_phase2_1.py` 硬编码 `/home/royzuo/codex-tasks/lottery-phase-2.1-20260805`，换任务输入目录后无法复现。
8. `cli.py logs` 没有执行外部 build/lint/test/readiness，也没有读取真实 receipt，却静态写入 `exit_code=0`。

## 修复设计

### A. 生产验证路径和篡改 E2E

- 为输入身份、预注册、递归 manifest 和 acceptance 建立可调用的生产验证函数或 CLI 路径。
- E2E-P2.1-02、06、09 必须在临时隔离副本中真实修改对应文件，调用生产验证路径，并断言返回非零、Schema 合法的失败 receipt；不得通过直接赋值 `EVIDENCE_MISMATCH`、`PASS` 或其他预期结果替代调用。
- 负向测试必须先证明旧实现会暴露该缺陷，再证明修复后真实路径拒绝篡改；正常 E2E 不得依赖手工构造的成功 receipt。

### B. 完整 source identity

- source manifest 必须覆盖实际执行闭包，至少包括 `src/lottery_research/phase2_1` 和 `src/lottery_research/phase2`；按实际导入关系补齐其他仓库内运行时代码。
- 增加测试：从 manifest 删除 `src/lottery_research/phase2` 或修改其中任一文件时，readiness、gates 或最终验证必须失败。
- 新 release 的 identity、合同、所有输入和 evidence manifest 必须使用同一个不可变 release ID。旧 release 不得复用或覆盖。

### C. acceptance 和最终 bundle

- `accept()` 必须从同一最终 bundle 的底层 receipts、gates、qualification、audit、power、replay、E2E 和 reviews 重新推导结果；任一 G0-G6 失败时必须输出 `status=FAIL`、`delivery_status=NO-GO`，不得强制 PASS。
- `validate_final_bundle.py` 必须独立重算关键 metrics、gate verdicts、结果覆盖率、哈希闭包和科学分类，并拒绝手工篡改 acceptance 汇总字段。`indeterminate` 仍不得解释为证明随机。
- manifest 必须覆盖声明的全部交付文件，并枚举实际目录；新增任意未登记文件必须导致验证失败。manifest 自身和为避免循环而排除的 acceptance 文件必须有明确、可验证的固定规则，不能成为未受保护的旁路。
- 增加负向测试：篡改 acceptance、G6 receipt、metrics、manifest 或新增未登记文件，最终验证必须失败；恢复后必须通过。

### D. readiness 和正式历史结果

- readiness 必须从实际 release、输入目录和受保护历史结果目录扫描并计算正式历史结果数量，再与合同声明交叉校验；不能只信任输入字段。
- 在没有正式历史结果时输出 `formal_historical_result_count=0`；若发现任一正式结果，必须阻止 READY。资源值继续只记录事实，不恢复通用 CPU、内存、磁盘或架构硬门槛。

### E. audit/power 完整重跑

- 在同一新 final bundle、同一冻结输入和本地 wheelhouse 上完整重跑所有注册的 historical audit 和 power 网格；不得复制、改名或只绑定旧 Phase 2 正式结果。
- upstream Phase 2 文件只能作为冻结输入和身份对照，不能作为本轮 audit/power 结果来源。结果必须写明本轮 release、输入 identity、运行参数、seed 和真实计算 receipt。
- 独立 replay 必须使用独立 seed/路径，并能与本轮 power 结果逐项对账。

### F. 可移植准备和真实 logs

- `prepare_phase2_1.py` 必须接受显式 `--task-input-dir`（或等价参数），并通过参数验证 `prompt.md`、当前 iteration 输入和其哈希；禁止硬编码本贡献者的任务目录。
- `logs` 命令必须执行声明的 build/lint/test/readiness，或只接受同一 run 中已经真实执行且可验证的 receipt；每个命令记录真实 command、开始/结束时间、stdout/stderr 摘要和退出码。任何跳过、失败或缺失 receipt 都不得写成 0/PASS。
- 增加测试：切换临时 task-input-dir 能成功；缺少输入、伪造 receipt、命令失败或未执行时必须拒绝生成成功日志。

## 新 release 约束

- 旧的 `P2.1-R00-60d02be4dbe9` rejected bundle、上一轮 commit、`runs/00`、`runs/01` 和 `artifacts/phase-2/` 必须保留。
- 新轮不得覆盖旧 release 目录。使用唯一且可追溯的新 release ID，例如 `P2.1-R00-60d02be4dbe9-i02`；实际采用的 ID 必须在合同、preregistration、所有 receipts、manifest、acceptance 和最终报告中完全一致，并在设计文档中说明与原始基线及 iteration-02 的关系。
- 仍使用原任务分支和原 worktree；PR #1 已关闭，修复完成后由任务控制者重新打开同一个 PR，不创建第二个 PR，不合并 `main`。

## 必须验证

仅在远程 VPS 执行，不以本机测试替代 readiness。正式运行只使用冻结输入和本地 wheelhouse。至少执行：

1. 仓库实际 build 命令和 lint 命令，并保存真实 receipt。
2. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v`
3. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v`
4. `python3 scripts/phase2_1/validate_phase2_1_readiness.py`
5. CLI 合同、E2E、readiness、独立方法复核、qualification、audit、power、replay、独立重放复核和最终 acceptance。
6. 在 G0/G1 通过前不得生成正式 audit 或 power；所有正式结果必须来自同一新 final bundle。

## 通过条件

- 所有八项审查缺陷均有生产路径修复和先失败后通过的测试。
- 新 release readiness 为 `READY`，正式历史结果数量由扫描得出且为 0。
- G0-G6 全部 PASS，10/10 E2E 达到预期终态，方法复核和独立重放复核通过。
- evidence hash closure、结果覆盖率和独立重放一致率均为 100%，blocking findings 为 0。
- final acceptance 从底层证据重算，不能靠手工汇总字段通过；科学分类与 delivery status 分离。
- Phase 1 冻结输入、`artifacts/phase-2/` 历史制品和旧 rejected release 均未修改。
- 当前分支提交并推送后，任务控制者重新打开 PR #1，确认 base=`main`、head 为当前验收 SHA、可合并；不合并 main。

## 最终报告

最终报告第一行必须为 `COMPLETED:`、`NEEDS_INPUT:` 或 `FAILED:`。列出根因修复、测试与命令退出码、新 release ID、bundle 路径、提交 SHA、PR #1 状态和剩余限制；不得把 `indeterminate` 解释为证明随机。
