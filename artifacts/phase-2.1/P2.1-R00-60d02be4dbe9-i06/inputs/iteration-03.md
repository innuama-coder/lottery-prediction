# Phase 2.1 第三轮迭代修复任务

## 任务身份

- 仓库：`innuama-coder/lottery-prediction`
- 目标分支：`codex/lottery-phase-2.1-20260805`
- 远端 worktree：`/home/royzuo/worktrees/lottery-prediction-lottery-phase-2.1-20260805`
- 远端任务目录：`/home/royzuo/codex-tasks/lottery-phase-2.1-20260805`
- 当前基线提交：`b5ce8cf112dcd2889018ebcdf0fc7f08ec8d20e2`
- 迭代编号：`03`
- 新 release ID：`P2.1-R00-60d02be4dbe9-i03`
- 原 PR：#1；继续使用同一 PR，不创建第二个 PR，不合并 `main`。

## 驳回根因与必须修复项

第三轮必须逐项修复以下阻断问题，并为每项补充先失败、修复后通过的自动化测试：

1. **核心证据 Schema 与输入身份未闭包**
   - `validate_final_bundle()` 必须逐个验证 readiness、gates、qualification、historical audit、power、replay、独立方法复核、独立重放复核、E2E registry、所有正式 receipt 的专用 Schema。
   - 必须验证 release ID、基线提交、Phase 1/Phase 2 冻结输入哈希、任务输入哈希在所有核心 artifact 之间一致。
   - 删除 `results/power.json` 的必需字段、篡改任一 input identity 或构造自洽伪造 metrics 后，最终验证必须失败；不能只依赖 acceptance 汇总字段。

2. **readiness 之后可偷偷新增正式结果**
   - readiness 必须记录正式结果目录的文件清单和快照身份。
   - final validator 必须重新扫描当前 release 目录，并拒绝 readiness 之后新增的未登记正式结果或 receipt；不能仅信任旧 scan receipt。
   - 在临时 bundle 复制 `results/stale-power.json`、新增日志或新增未登记 evidence 后，最终验证必须失败。

3. **独立 replay 与 power 共用同一执行路径**
   - replay 必须使用独立的 engine/path，不能直接调用 power 的 `_power_grid()` 或同一统计实现。
   - 独立重放复核必须复算关键结果、验证独立路径和不同 seed，并能检测主路径被替换或篡改。
   - 保持 240/240 覆盖与 100% 一致率要求；独立不等于只更换 seed。

4. **formal command receipt 伪报退出码**
   - `logs` 或正式命令 receipt 必须保存真实外部命令退出码、stdout/stderr 摘要及哈希。
   - 任何失败或未执行的命令不得记录 `exit_code=0`，不得生成成功终态；校验器必须检查 terminal、status 和 exit_code 的一致性。
   - 增加失败命令回归测试，验证返回码为 2/3/4/5 时 receipt 保持非零且 final acceptance 不能 GO。

5. **当前提交存在 lint 尾随空格**
   - 清除 `docs/research/phase-2.1-overall-design.md` 第 3、4 行尾随空格及当前 head 的所有 diff-check 问题。
   - 必须在当前第三轮提交上重新执行 build、compileall、`git diff --check`，并将真实结果写入本轮 bundle。

6. **E2E 无法从已完成 bundle 重跑**
   - `run_phase2_1_e2e.py` 必须支持明确、可重复的 staging 语义：在没有最终 manifest/acceptance 的隔离副本中生成 E2E，或显式创建新的 staging bundle。
   - 对已完成 bundle 重跑时不得覆盖旧 evidence、不得因已存在 `acceptance/manifest.json` 返回 `INVALID_CONTRACT`。
   - 旧 release 和本轮前置 evidence 必须保留。

7. **失败 receipt 只比较 terminal，不验证退出码**
   - verification-receipt Schema 必须约束 `status`、`terminal`、`exit_code` 的一致关系。
   - E2E-P2.1-02/06/09 必须断言失败 receipt 的 `exit_code != 0`，并通过生产验证路径得到该 receipt，不能手工赋值 terminal。
   - 增加返回码为 0 但 terminal=FAIL、以及返回码非零但 terminal=PASS 的负向测试；两者均必须被拒绝。

## 保持不变的约束

- 不得修改 `artifacts/phase-2/`、旧 rejected release `P2.1-R00-60d02be4dbe9`、旧 iteration-02 bundle、Phase 1 冻结输入及其哈希身份。
- 不得修改阶段 3 的模型、特征、号码排名或投注实现。
- 不得创建第二个 PR，不得合并 `main`。
- 每轮使用唯一 release ID 和独立目录；失败证据不得删除或覆盖。
- 只在远程 VPS 执行实现、测试、readiness、正式运行和验收；正式运行只使用冻结输入与本地 wheelhouse。
- VPS 资源只记录事实与 benchmark，不设通用 CPU/RAM/磁盘硬门槛；真实资源耗尽才算失败。
- 未通过 G0/G1 前不得生成正式 audit 或 power。
- 科学分类与交付状态分离；`indeterminate` 不能解释为证明随机。

## 第三轮完成条件

必须在同一新 bundle `P2.1-R00-60d02be4dbe9-i03` 上完成：

- Phase 2.1 测试、Phase 2 回归、build、lint、readiness；
- G0-G6、qualification、historical audit、power、replay；
- 独立方法复核、独立重放复核、10/10 E2E、manifest 和最终 validator；
- 最终 validator 从底层 evidence 重算结论并递归校验目录闭包。

目标指标：G0-G6 全部 `PASS`；E2E `10/10`；evidence hash closure、historical coverage、power grid coverage、independent replay consistency 均为 `100%`；`blocking_findings=0`；delivery `GO`。

## 交付与报告

完成后必须提交并推送当前分支，报告：

- 新 commit SHA 与 release ID；
- 每项驳回问题的根因、修改位置和负向测试证据；
- 全部命令及真实退出码；
- 最终 acceptance、manifest、run summary 的路径；
- 旧 release、Phase 1 输入和 `artifacts/phase-2/` 未修改的核验结果。

不得自行打开或合并 PR；完成后将控制权交回任务控制者，由其重新打开 PR #1 并核对 head SHA。
