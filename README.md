# lottery-prediction

中国体彩大乐透与福彩双色球 autoresearch 项目。当前仓库包含阶段 0 的数据可行性验证、阶段 1 的规范化数据层，以及阶段 2 的随机性审计、功效分析和证据制品。

## 当前状态

- 阶段 0：已完成。
- 阶段 1：已完成。
- 阶段 2：`HOLD / partially achieved`。
- 当前科学结论：`indeterminate`，既没有形成候选信号，也没有足够功效支持“未发现可检测偏差”。

仓库中现有 `artifacts/phase-2/acceptance/phase2-acceptance.json` 是深度复核前生成的历史正式验收制品，其中的 `GO` 不能代表当前项目状态。接手前必须阅读 [阶段 2 当前状态与交接说明](docs/handoff/phase-2-current-status.md)。

## 环境

- Python `>=3.12,<3.13`
- Git LFS（用于阶段 2 二进制模拟证据）

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements\phase2.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

## 阶段 2 快速验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2 -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_cli_contract -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_e2e -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests\phase2_readiness -p "test_*.py" -v
```

阶段 2 CLI 与正式运行顺序见 [阶段 2 Roadmap](docs/roadmap/phase-2-randomness-audit-plan.md)。测试通过只表示现有实现没有回归，不代表阶段 2 已完成最终可信验收。

## 首次远程交接前验证（2026-08-05）

| 范围 | 结果 | 说明 |
| --- | --- | --- |
| Phase 0 amendment | 10/10 PASS | 当前环境可重复执行。 |
| Phase 0 multisource | 8/8 PASS | 当前环境可重复执行。 |
| Phase 0 全量 | 141/160 未报错，19 errors | 已使用冻结记录指定的 Python 3.12.13 解释器；错误集中在 `p0-06-dlt-2026087` 证据重放与 manifest 不一致，以及一项冻结环境标志缺失。历史冻结报告记录的是 160/160 PASS，但当前工作树不能据此宣称全量可重放。 |
| Phase 1 全量 | 10 分钟超时 | 套件在上限内未返回失败明细；正式真实采集证据仍保留在 `artifacts/live-validation-20260803-01/`，但接手方应拆分慢测试并建立有时限的快速门禁。 |
| Phase 2 | 58/58 PASS | phase2 31、CLI contract 15、E2E runner 7、readiness 5。科学/验收状态仍为 `HOLD`。 |

以上是“当前代码和证据能否由接手方重新验证”的真实状态，不替代各阶段历史验收制品，也不把历史制品中的 PASS 自动提升为当前状态。

## 目录

- `docs/`：路线图、数据规格和研究报告。
- `src/`：数据工作流与阶段 2 研究实现。
- `schemas/`：机器合同 Schema。
- `tests/`：单元、合同、readiness 和 E2E 测试。
- `artifacts/`：冻结输入、正式结果和证据制品。
- `requirements/phase2.lock`：阶段 2 Python 3.12 依赖锁。

本地虚拟环境、缓存、构建产物、运行锁、集成测试临时根、Phase 2 Monte Carlo 检查点、递归 E2E workspace 和 `artifacts/phase-2/superseded/` 历史重复快照不会提交到远程仓库。仓库保留冻结输入、正式摘要、运行清单、当前 qualification corpora 和 E2E registry 选定的十份 compact receipt；被排除的批量中间状态可从冻结种子和代码重新生成。
