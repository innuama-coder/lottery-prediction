# lottery-prediction

中国体彩大乐透与福彩双色球 autoresearch 项目。当前仓库包含阶段 0 的数据可行性验证、阶段 1 的规范化数据层，以及阶段 2 的随机性审计、功效分析和证据制品。

## 当前状态

- 阶段 0：已完成。
- 阶段 1：已完成。
- 阶段 2：已完成，最终交付状态为 `GO`。
- 当前科学结论：`indeterminate`，既没有形成候选信号，也没有足够功效支持“未发现可检测偏差”。

阶段 2 的当前权威验收制品是 [`artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json`](artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json)：`status=PASS`、`delivery_status=GO`、G0～G6 全部 `PASS`、阻断问题为 0。`artifacts/phase-2/acceptance/phase2-acceptance.json` 是深度复核前生成的历史制品，不再代表当前状态。详细解释见 [阶段 2 当前状态与交接说明](docs/handoff/phase-2-current-status.md)。

Phase 2.1 是完成阶段 2 的版本化修复 release，不是仍需 Phase 2.2 的普通子阶段。其设计与复算入口见 [总体设计](docs/research/phase-2.1-overall-design.md) 与 [VPS 运行手册](docs/runbooks/phase-2.1-vps-runbook.md)。Phase 2.1 使用独立 release 目录，没有改写历史 Phase 2 制品。

## 环境

- Python `>=3.12,<3.13`
- Git LFS（用于阶段 2 二进制模拟证据）

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements\phase2_1.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

## 阶段 2 快速回归验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_1 -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2 -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_cli_contract -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_e2e -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_readiness -p "test_*.py" -v
```

阶段 2 CLI 与正式运行顺序见 [阶段 2 Roadmap](docs/roadmap/phase-2-randomness-audit-plan.md)。这些命令用于当前代码回归；阶段完成结论来自上面的不可变 Phase 2.1 正式验收 bundle，而不是由任意一次本地测试单独推导。

## 阶段 2 最终验收（2026-08-06）

| 范围 | 结果 | 说明 |
| --- | --- | --- |
| Phase 2.1 | 39/39 PASS | 修复版合同、Schema、readiness、统计工作流、证据闭包与负向路径。 |
| Phase 2 回归 | 31/31 PASS | 原 Phase 2 研究引擎未发生回归。 |
| 外部交付检查 | PASS | readiness、离线 build、`compileall` 与 `git diff --check` 均通过。 |
| 正式验收 | `PASS / GO` | G0～G6 全部通过，阻断问题为 0；科学分类为 `indeterminate`。 |

## 首次远程交接前验证（2026-08-05 历史快照）

| 范围 | 结果 | 说明 |
| --- | --- | --- |
| Phase 0 amendment | 10/10 PASS | 当前环境可重复执行。 |
| Phase 0 multisource | 8/8 PASS | 当前环境可重复执行。 |
| Phase 0 全量 | 141/160 未报错，19 errors | 已使用冻结记录指定的 Python 3.12.13 解释器；错误集中在 `p0-06-dlt-2026087` 证据重放与 manifest 不一致，以及一项冻结环境标志缺失。历史冻结报告记录的是 160/160 PASS，但当前工作树不能据此宣称全量可重放。 |
| Phase 1 全量 | 10 分钟超时 | 套件在上限内未返回失败明细；正式真实采集证据仍保留在 `artifacts/live-validation-20260803-01/`，但接手方应拆分慢测试并建立有时限的快速门禁。 |
| Phase 2 | 58/58 PASS | phase2 31、CLI contract 15、E2E runner 7、readiness 5；该时点仍为 `HOLD`，随后已被 2026-08-06 的 Phase 2.1 最终验收取代。 |

以上仅记录首次远程交接前的历史状态，不替代 2026-08-06 的 Phase 2.1 最终验收制品。

## 目录

- `docs/`：路线图、数据规格和研究报告。
- `src/`：数据工作流与阶段 2 研究实现。
- `schemas/`：机器合同 Schema。
- `tests/`：单元、合同、readiness 和 E2E 测试。
- `artifacts/`：冻结输入、正式结果和证据制品。
- `requirements/phase2_1.lock`：最终 Phase 2.1 release 的 Python 3.12 依赖锁；`requirements/phase2.lock` 保留为历史 Phase 2 依赖锁。

本地虚拟环境、缓存、构建产物、运行锁、集成测试临时根、Phase 2 Monte Carlo 检查点、递归 E2E workspace 和 `artifacts/phase-2/superseded/` 历史重复快照不会提交到远程仓库。仓库保留冻结输入、正式摘要、运行清单、当前 qualification corpora 和 E2E registry 选定的十份 compact receipt；被排除的批量中间状态可从冻结种子和代码重新生成。
