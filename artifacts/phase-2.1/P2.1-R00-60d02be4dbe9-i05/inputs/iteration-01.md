# Iteration 01 for lottery-phase-2.1-20260805

先阅读 `/home/royzuo/codex-tasks/lottery-phase-2.1-20260805/prompt.md`、本文件，以及当前 worktree 的 Git diff、提交和测试状态。继续使用当前任务分支；不得重置、删除或覆盖 `runs/00` 的已有证据，也不得丢弃任何已有正确改动。

## 本轮问题

原任务合同将 Linux `x86_64`、至少 4 vCPU、至少 16 GiB RAM 和至少 60 GiB 可用磁盘设为通用 Phase 2.1 readiness 硬门槛。任务发起人现明确变更该项要求：VPS 的容量适配性由任务发起人自行保障，Phase 2.1 不得再以通用 CPU、内存、磁盘或架构数值门槛拒绝 `P2.1-R00` 或阻止后续工作。

## 复现步骤

1. 原轮 `runs/00/result.md` 报告当前 VPS 的 RAM 为 15.57 GiB，低于原 16 GiB 门槛。
2. 原轮因此在实现开始前返回 `NEEDS_INPUT`，而当前 VPS 具备本任务所需的远端执行环境。

## 当前结果

原资源门禁把环境快照误当成通用验收条件，导致在没有实际基准或真实资源耗尽证据时中止任务。

## 期望结果

1. 保留 VPS 资源预检，但它只能收集并固化系统、架构、CPU、总内存、可用磁盘、解释器和依赖等事实，供可重复性、benchmark 与证据回传使用。
2. 移除 Phase 2.1 合同、Schema、readiness、预检实现、测试、总体设计、VPS 运行手册和详细准备计划中的通用最低 CPU、内存、磁盘或架构阈值，以及仅因这些数值触发的 `HOLD`、`ENVIRONMENT_FAILURE` 或 `NEEDS_INPUT`。
3. 在合同和运行手册中明确：任务发起人负责为批准的工作负载保障 VPS 容量；执行者记录实际资源和 benchmark。只有实际命令、benchmark、wheelhouse、测试或正式运行发生可复现的资源耗尽/环境错误时，才按真实错误处理，不得虚构预检阈值。
4. `P2.1-R00` 的 `READY` 必须继续严格验证源代码身份、冻结输入、隔离工作区、wheelhouse、benchmark、证据回传、不可变 release identity 和正式历史结果数量为 0；不得因本项修订削弱这些门禁。

## 必须重新执行

1. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v`
2. `PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v`
3. `python3 scripts/phase2_1/validate_phase2_1_readiness.py`
4. 仓库实际确定的 build 与 lint 命令。
5. 完整的 P2.1-R00、G0-G6、10/10 E2E、qualification、audit、power、replay、最终验收、独立方法复核和独立重放复核命令；在 G0/G1 未通过前，仍不得生成正式 audit 或 power 结果。

## 本轮通过条件

- 资源事实和 benchmark 证据完整，但不存在未由任务说明明确给出的通用 VPS 配置硬门槛。
- 所有对应测试覆盖“资源事实被记录而非按固定阈值拒绝”的行为，并通过完整 Phase 2.1 与 Phase 2 回归测试。
- 在同一个最终 bundle 上完成原任务全部验收：`P2.1-R00=READY`、G0-G6 通过、10/10 E2E 达到预期终态、证据哈希闭包/结果覆盖率/独立重放一致率均为 100%、blocking findings 为 0。
- 未修改 `artifacts/phase-2/` 的历史正式制品、Phase 1 冻结输入及哈希身份，或阶段 3 的模型、特征、号码排名和投注实现。

## 范围

- 本文件仅替代原任务中“VPS 为 Linux x86_64、至少 4 vCPU、16 GiB RAM、60 GiB 可用磁盘”的通用配置验收条件及其派生实现要求。
- 其他目标、非目标、正式运行无公网依赖、唯一 release ID/独立目录、证据保留、验收和交付要求继续有效。
- 不得修改原始 `prompt.md`、`runs/00` 或历史正式制品；将本次变更作为新的输入快照和新的运行证据保留。

## 最终报告协议

最终回复第一行必须是 `COMPLETED:`、`NEEDS_INPUT:` 或 `FAILED:`；随后列出修改、提交、推送、测试、最终 bundle 路径和剩余风险。不得把 `indeterminate` 解释为证明随机。
