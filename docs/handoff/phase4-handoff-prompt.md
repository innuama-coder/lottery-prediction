# Phase 4 接手提示词

你现在接手 `lottery-prediction` 项目的 Phase 4 工作。请先阅读：

- `docs/handoff/phase4-current-status.md`
- `docs/phase4/prize-calculation-contract.md`

## 当前代码基线

- 分支：`codex/phase4-real-model-implementation-20260815`
- 最新提交：`60749bb070bcc95124765e3e9b73e897bd7e1af8`
- 上一个奖金冻结提交：`d7065152ae0e22ecb505f9b391fea79f57d3dcc8`
- 远程分支：`origin/codex/phase4-real-model-implementation-20260815`

先确认当前分支、提交哈希和工作区状态。工作区中存在其他历史未提交/未跟踪文件，不要执行 `git reset --hard`、`git clean -fd` 或全量 `git add .`，也不要覆盖与本任务无关的用户文件。

## 已完成且已冻结的内容

奖金计算已经硬化并冻结，是模型回测和验收指标的唯一事实源：

- SSQ 唯一规则：`SSQ_PRIZE_FIXED_6TIER`
- DLT 唯一规则：`DLT_PRIZE_FIXED_7TIER`
- 大乐透不存在“新规则/旧规则”两套 Phase 4 计算逻辑。
- 期号只校验 `YYYYNNN` 格式，不参与奖金表选择。
- 一等奖固定 5,000,000 元，二等奖固定 100,000 元。
- 不考虑奖池余额、浮动奖金、非常规派奖、营销活动和期次特例。
- 未中奖状态奖金为 0 元；非法输入和未知规则必须失败闭合。
- 奖金契约指纹：`0c57745377d7821a26fb3b8bd954d0010c1c4410c51643e6a5650e53aabad2d1`

不得重新引入或设计大乐透新/旧规则切换，也不得绕过 `src/lottery_system/phase4/bonus.py` 自行复制奖级逻辑。

## 当前验收基线

以下定向验收必须保持通过：

```bash
PYTHONPATH=src python3 tests/phase4_legacy/test_prize_oracle.py
PYTHONPATH=src python3 tests/phase4_legacy/test_prizes.py
PYTHONPATH=src python3 -m compileall -q src scripts/phase4e21_bonus_hardening tests/phase4 tests/phase4_legacy
```

奖金全空间 oracle 固定值：

- SSQ：总注数 `17,721,088`，中奖注数 `1,188,988`，固定奖金总额 `15,117,950` 元。
- DLT：总注数 `21,425,712`，中奖注数 `1,429,197`，固定奖金总额 `18,890,405` 元。

## 接手后的工作纪律

1. 先做只读检查和定向测试，再决定是否修改代码。
2. SSQ 与 DLT 的预测模型、特征工程和评估必须保持隔离。
3. 分组指标使用：
   - 分组奖金总额 = 所有完整注单固定奖金之和；
   - 分组平均奖金 = 分组奖金总额 / 完整注数；
   - 中奖率 = 获得任一固定奖级的完整注数 / 完整注数。
4. 历史产物中的 `DLT_PRIZE_2019_9TIER`、`DLT_PRIZE_2026_7TIER` 等名称只属于历史 lineage，不得当作当前 Phase 4 的可选策略。
5. 任何奖金规则修改都必须同时更新实现、oracle、测试、说明文档、契约指纹和审计产物，并重新提交验收。
6. 不要把奖金规则冻结误报为模型晋升，也不要在没有真实指标证据时宣称预测效果达标。

## 如果继续做模型/特征工程

请先明确：目标彩种、训练/验证窗口、特征变更、模型参数变更、评估指标和交付物。所有回测都必须使用冻结奖金入口计算奖金，不得使用旧报表中的奖金字段直接替代重算结果。完成后提供：变更文件、运行命令、测试结果、关键指标、失败项和是否达到验收标准。

## 交付要求

完成任何后续变更后：

- 运行与变更相关的定向测试；
- 检查 `git diff --check`；
- 只提交本次任务相关文件；
- 在提交信息中说明变更目的；
- 推送到当前远程分支；
- 在交接报告中记录提交哈希、测试命令、测试结果和剩余风险。
