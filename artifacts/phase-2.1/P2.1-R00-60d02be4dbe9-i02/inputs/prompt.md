# 任务 lottery-phase-2.1-20260805

## 目标

在远程 VPS 的独立 worktree 中，为 `innuama-coder/lottery-prediction` 的 `main` 分支完成版本化 Phase 2.1（运行标签 `P2.1-R00`），实现统计与验收修复，并在同一最终 bundle 上完成 G0-G6、10/10 E2E、独立方法复核和独立重放复核。不可变 release identity 的构造规则为字符串 `P2.1-R00-` 加本轮目标分支基线 SHA 的前 12 位；将其记为 `$RELEASE_ID`，并把最终交付放入远端 `artifacts/phase-2.1/$RELEASE_ID/`。

## 仓库与分支

- 仓库：`innuama-coder/lottery-prediction`
- 目标分支：`main`
- 当前任务分支：`codex/lottery-phase-2.1-20260805`
- 远端执行用户：`royzuo`
- 本任务运行目录：`/home/royzuo/codex-tasks/lottery-phase-2.1-20260805`
- 本任务 worktree：由任务控制者创建的独立 worktree；不得使用主 clone 开发。

## 必须完成

1. 建立 Phase 2.1 合同，补充预注册和不可变 release identity；明确科学结论与交付状态分离。
2. 完成 VPS `P2.1-R00`：资源预检、隔离环境、wheelhouse、benchmark 和证据回传校验。
3. 修复慢漂移模型、结果 Schema、known-answer 验证和功效覆盖。
4. 让最终验收从底层证据重算指标，递归验证哈希，并自动推导科学分类；不得依赖手工填写的汇总指标。
5. 在同一个最终 bundle 上完成 qualification、audit、power、replay、E2E 和最终验收；不得混用不同 release 或不同输入快照的证据。
6. 完成独立方法复核与独立重放复核，独立过程不得只调用被验收实现的同一路径得出结论。
7. 生成代码、Schema、依赖锁、测试、总体设计、VPS 运行手册和详细准备计划，并把最终非代码证据放入 `artifacts/phase-2.1/$RELEASE_ID/`。
8. 任务开始后先阅读仓库中的 `AGENTS.md`、`README`、现有 Phase 2 文档、测试和相关脚本；根据仓库实际内容确定 build 与 lint 命令，并在最终报告中记录完整命令及退出码。

## 不得完成

- 不得修改、删除、覆盖或重新生成 `artifacts/phase-2/` 下的任何历史正式结果、预注册、review 或 acceptance 制品。
- 不得修改 Phase 1 冻结输入及其哈希身份。
- 不得修改阶段 3 的模型、特征、号码排名或投注实现。
- 不得在 G0/G1 未通过前生成正式 audit 或 power 结果；失败尝试的证据必须保留，不得覆盖或删除。
- 不得直接修改或强推 `main`，不得合并 PR；只在当前任务分支提交和推送。
- 不得使用 `sudo`、root、生产凭据或未授权外部服务；所有依赖安装在普通用户的隔离环境中完成。
- 公网仅用于准备阶段；正式运行必须使用冻结输入和本地 wheelhouse/依赖，不得在正式运行中下载网络依赖。
- 不得把 Token、私钥、密码、API key 或完整环境凭据写入代码、日志、任务报告、提交或 PR。
- 每一次运行必须使用唯一 release ID 和独立目录；禁止复用或覆盖已有 release 证据。

## 验收标准

- `P2.1-R00` 状态为 `READY`，且正式历史结果数量为 0。
- VPS 为 Linux `x86_64`，至少 4 vCPU、16 GiB RAM、60 GiB 可用磁盘；资源证据必须保存到最终 bundle。
- G0-G6 全部通过，10/10 E2E 达到预期终态。
- 证据哈希闭包、结果覆盖率和独立重放一致率均为 100%。
- blocking findings 为 0。
- 科学结论与交付状态分离；`indeterminate` 不得解释为“证明随机”，必须按合同给出有限、准确的科学分类。
- 最终验收必须从底层证据重新计算指标，递归验证 bundle 哈希，验证 release identity、输入快照、Schema、运行环境和命令版本一致。
- 所有上述标准必须在同一个最终 bundle 上有可定位证据；不能用另一轮、另一 release 或未冻结输入的结果替代。

## 必须执行的验收命令

先检查仓库并确定实际 build/lint 命令；最终报告必须记录确定依据和完整输出。以下命令为固定验收命令，必须在远端 worktree 中逐条执行：

1. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v`
2. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v`
3. `python3 scripts/phase2_1/validate_phase2_1_readiness.py`
4. 仓库检查后确定的 build 命令。
5. 仓库检查后确定的 lint 命令。
6. G0-G6、10/10 E2E、qualification、audit、power、replay、最终验收以及独立方法/重放复核命令；每条命令必须记录退出码、release ID 和证据路径。

## 交付物

- 交付类型：`MIXED`。
- 代码/文档：Phase 2.1 脚本、Schema、依赖锁、测试、总体设计、VPS 运行手册和详细准备计划。
- 非代码交付物：`artifacts/phase-2.1/$RELEASE_ID/` 下通过最终验收的完整 bundle；包含合同、预注册、不可变 release identity、资源预检、wheelhouse/依赖清单、benchmark、qualification、audit、power、replay、E2E、最终验收、独立方法复核、独立重放复核、递归哈希闭包和运行日志摘要。
- `$RELEASE_ID` 必须按目标分支基线 SHA 前 12 位确定，并在合同、所有证据清单、验收记录和最终报告中保持完全一致；运行标签必须明确为 `P2.1-R00`。
- 最终报告必须列出所有交付文件相对 worktree 的路径；非代码 bundle 的每一项路径必须在验收记录中逐项列出。

## 执行约束

- 先阅读仓库规范和历史 Phase 2/Phase 1 资料，再设计和修改；不得凭猜测改动协议。
- 只在当前任务 worktree 和当前任务分支工作；不得访问或改写其他任务的 worktree、任务目录或证据。
- 先完成资源预检和合同/readiness 门禁；G0/G1 未通过时停止正式 audit/power 生成，只保留诊断证据并在报告中说明阻塞。
- 保留每次失败尝试的完整证据；不得覆盖 `artifacts/phase-2.1/` 中已有 release。
- 正式运行只使用冻结输入和本地依赖；网络准备行为必须与正式运行证据区分记录。
- 每次修改后运行与改动相关的测试；完成后重新运行全部固定验收命令和全套 G0-G6。
- 代码改动完成后提交并推送当前任务分支；不得合并 PR，不得修改目标分支。
- 缺少需求、凭据、输入或权限时停止修改，使用 `NEEDS_INPUT:` 报告具体问题；不得降低验收标准或自行扩大范围。

## 最终报告协议

最终回复第一行只能是以下三种之一：

- `COMPLETED: ` 后接一句话结果：代码/文档已完成，当前任务分支已提交并推送；同一最终 bundle 上 G0-G6、10/10 E2E、固定命令、独立方法复核、独立重放复核均通过，blocking findings 为 0，且所有交付物已列出。
- `NEEDS_INPUT: ` 后接必须由任务发起人决定的问题：存在必须由任务发起人决定的范围、权限、凭据、资源或科学结论问题，不能安全继续。
- `FAILED: ` 后接失败原因：环境、命令、实现或验收失败，未满足交付条件；不得把未完成工作报告为完成。

随后必须列出：

- 运行标签、不可变 `$RELEASE_ID`、基线 SHA、任务分支和 PR（不得声称已合并）。
- 修改文件、提交 SHA、推送分支和最终 bundle 路径。
- VPS 资源、隔离环境、wheelhouse 和正式运行是否无公网依赖的证据路径。
- build、lint、固定 test/regression/readiness、G0-G6、10/10 E2E、qualification、audit、power、replay、最终验收及独立复核的完整命令、退出码和结果摘要。
- 证据哈希闭包、结果覆盖率、独立重放一致率、blocking findings 和科学分类。
- 未完成项、已知限制和风险；不得将 `indeterminate` 解释成证明随机。
