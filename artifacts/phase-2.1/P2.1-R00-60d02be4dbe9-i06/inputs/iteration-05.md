# Phase 2.1 第五轮迭代修复任务

## 任务身份

- 仓库：`innuama-coder/lottery-prediction`
- 目标分支：`codex/lottery-phase-2.1-20260805`
- 远端 worktree：`/home/royzuo/worktrees/lottery-prediction-lottery-phase-2.1-20260805`
- 远端任务目录：`/home/royzuo/codex-tasks/lottery-phase-2.1-20260805`
- 当前基线提交：`b5ce8cf112dcd2889018ebcdf0fc7f08ec8d20e2`
- 迭代编号：`05`
- 新 release ID：`P2.1-R00-60d02be4dbe9-i05`
- 原 PR：#1；继续使用同一 PR，不创建第二个 PR，不合并 `main`。

## 第三轮与第四轮终态

第三轮远端 Codex 已停止，`runs/03/exit-code.txt=124`，未生成 `result.md`。运行时间达到外层 120 分钟限制；最后事件停留在 E2E negative-suite 对生产 final validator 的基准 power 重算阶段。未发现资源耗尽、测试断言失败或仓库损坏证据。

第四轮为启动层失败，`runs/04/exit-code.txt=127`，原因是非交互式启动环境中 `codex` 不在 PATH，Codex 任务本身没有开始。`runs/04` 证据必须保留，不得覆盖。

第五轮必须基于远端 worktree 中第三轮留下的未提交修改继续，不得覆盖、删除或回滚这些修改。先定位并修复第三轮超时根因，再完成原第三轮所有阻断问题。禁止仅通过扩大超时时间掩盖慢路径；可以拆分 E2E staging、缓存只读基准证据、降低负向用例重复重算、增加超时保护和进度 receipt，但不得削弱最终验收的真实性。

## 必须修复项

1. **修复第三轮超时根因**
   - 定位 E2E negative-suite 反复触发 power 基准重算的具体路径。
   - 让负向 E2E 能够复用同一 staging bundle 的冻结基准证据，或以独立 staging 方式重放必要证据，避免每个负向用例重新执行完整 power grid。
   - 保留最终正式 bundle 的完整 qualification、audit、power、replay、E2E 和最终验收，不得因为优化负向套件而跳过生产 validator。
   - 增加可观察证据：E2E registry、run summary 或 receipt 必须记录每个用例的输入 bundle、执行命令、退出码、耗时和终态。

2. **核心证据 Schema 与输入身份闭包**
   - `validate_final_bundle()` 必须逐个验证 readiness、gates、qualification、historical audit、power、replay、独立方法复核、独立重放复核、E2E registry、所有正式 receipt 的专用 Schema。
   - 必须验证 release ID、基线提交、Phase 1/Phase 2 冻结输入哈希、任务输入哈希在所有核心 artifact 之间一致。
   - 删除 `results/power.json` 的必需字段、篡改任一 input identity 或构造自洽伪造 metrics 后，最终验证必须失败。

3. **readiness 后新增正式结果必须被拒绝**
   - readiness 必须记录正式结果目录的文件清单和快照身份。
   - final validator 必须重新扫描当前 release 目录，并拒绝 readiness 之后新增的未登记正式结果或 receipt。
   - 临时 bundle 中新增 `results/stale-power.json`、新增日志或新增未登记 evidence 后，最终验证必须失败。

4. **独立 replay 必须独立于 power 路径**
   - replay 必须使用独立 engine/path，不能直接调用 power 的 `_power_grid()` 或同一统计实现。
   - 独立重放复核必须复算关键结果、验证独立路径和不同 seed，并能检测主路径被替换或篡改。
   - 保持 240/240 覆盖与 100% 一致率要求。

5. **formal command receipt 必须保存真实退出码**
   - 正式命令 receipt 必须保存真实外部命令退出码、stdout/stderr 摘要及哈希。
   - 任何失败或未执行的命令不得记录 `exit_code=0`，不得生成成功终态。
   - 校验器必须检查 `terminal`、`status` 和 `exit_code` 的一致性。

6. **清除 lint 和 diff-check 问题**
   - 清除当前 head 的所有尾随空格和 `git diff --check` 问题。
   - 必须重新执行 build、compileall、`git diff --check`，并将真实结果写入本轮 bundle。

7. **E2E 必须支持 staging bundle 重跑**
   - `run_phase2_1_e2e.py` 必须支持明确、可重复的 staging 语义。
   - 对已完成 bundle 重跑时不得覆盖旧 evidence，不得因已存在 `acceptance/manifest.json` 返回 `INVALID_CONTRACT`。
   - 旧 release 和本轮前置 evidence 必须保留。

8. **失败 receipt 必须验证退出码一致性**
   - verification-receipt Schema 必须约束 `status`、`terminal`、`exit_code` 的一致关系。
   - E2E-P2.1-02/06/09 必须断言失败 receipt 的 `exit_code != 0`。
   - 返回码为 0 但 `terminal=FAIL`、以及返回码非零但 `terminal=PASS` 的负向测试均必须被拒绝。

## 保持不变的约束

- 不得修改 `artifacts/phase-2/`、旧 rejected release `P2.1-R00-60d02be4dbe9`、旧 iteration-02 bundle、旧 iteration-03 bundle、Phase 1 冻结输入及其哈希身份。
- 不得修改阶段 3 的模型、特征、号码排名或投注实现。
- 不得创建第二个 PR，不得合并 `main`。
- 每轮使用唯一 release ID 和独立目录；失败证据不得删除或覆盖。
- 只在远程 VPS 执行实现、测试、readiness、正式运行和验收；正式运行只使用冻结输入与本地 wheelhouse。
- VPS 资源只记录事实与 benchmark，不设通用 CPU/RAM/磁盘硬门槛；真实资源耗尽才算失败。
- 未通过 G0/G1 前不得生成正式 audit 或 power。
- 科学分类与交付状态分离；`indeterminate` 不能解释为证明随机。

## 第五轮完成条件

必须在同一新 bundle `P2.1-R00-60d02be4dbe9-i05` 上完成：

- Phase 2.1 测试、Phase 2 回归、build、lint、readiness；
- G0-G6、qualification、historical audit、power、replay；
- 独立方法复核、独立重放复核、10/10 E2E、manifest 和最终 validator；
- 最终 validator 从底层 evidence 重算结论并递归校验目录闭包。

目标指标：G0-G6 全部 `PASS`；E2E `10/10`；evidence hash closure、historical coverage、power grid coverage、independent replay consistency 均为 `100%`；`blocking_findings=0`；delivery `GO`。

## 交付与报告

完成后必须提交并推送当前分支，报告：

- 新 commit SHA 与 release ID；
- 第三轮超时根因、修复位置和耗时改善证据；
- 每项驳回问题的根因、修改位置和负向测试证据；
- 全部命令及真实退出码；
- 最终 acceptance、manifest、run summary 的路径；
- 旧 release、Phase 1 输入和 `artifacts/phase-2/` 未修改的核验结果。

不得自行打开或合并 PR；完成后将控制权交回任务控制者，由其重新打开 PR #1 并核对 head SHA。
