# Phase 4 当前工作进展与交接说明

更新时间：2026-08-22  
交接基线：`d7065152ae0e22ecb505f9b391fea79f57d3dcc8`  
远程分支：`origin/codex/phase4-real-model-implementation-20260815`

## 1. 当前目标

本阶段已将双色球（SSQ）和超级大乐透（DLT）的常规奖金计算硬化并冻结，作为预测模型回测、分组奖金和验收指标的唯一依据。奖金计算与模型训练/调参是两个隔离层次；本交接不代表模型晋升或预测效果达标。

## 2. 已交付内容

### 2.1 唯一奖金规则实现

- `src/lottery_system/phase4/bonus.py`
  - SSQ 唯一规则：`SSQ_PRIZE_FIXED_6TIER`
  - DLT 唯一规则：`DLT_PRIZE_FIXED_7TIER`
  - 规则表和金额表为不可变注册表。
  - 期号只校验 `YYYYNNN` 格式（年份 2000–2099、期号 001–999），不参与奖金规则选择。
  - 未中奖状态返回 0 元；越界命中数、未知规则、非法类型失败闭合。
  - 契约指纹：`0c57745377d7821a26fb3b8bd954d0010c1c4410c51643e6a5650e53aabad2d1`。

- `src/lottery_system/phase4/prizes.py`
  - 仅作为兼容 API 外壳，实际判定委托 `bonus.py`。
  - 不再存在大乐透新/旧规则切换 API。

### 2.2 奖金说明文档

- `docs/phase4/prize-calculation-contract.md`
  - 记录 SSQ/DLT 完整命中状态、固定金额、失败闭合规则、分组平均奖金口径和验收值。
  - 明确声明：超级大乐透只有一套固定逻辑，不区分新旧。

### 2.3 验收与审计

- `tests/phase4/test_phase4e21_bonus_hardening.py`
- `tests/phase4_legacy/test_prize_oracle.py`
- `tests/phase4_legacy/test_prizes.py`
- `scripts/phase4e21_bonus_hardening/run_bonus_hardening.py`
- `artifacts/phase4e21_bonus_hardening/state-space-audit.json`
- `artifacts/phase4e21_bonus_hardening/.bonus-contract-fingerprint`

## 3. 冻结的奖金口径

### 双色球

| 奖级 | 命中状态 | 奖金（元） |
| --- | --- | ---: |
| 一 | 6+1 | 5,000,000 |
| 二 | 6+0 | 100,000 |
| 三 | 5+1 | 3,000 |
| 四 | 5+0、4+1 | 200 |
| 五 | 4+0、3+1 | 10 |
| 六 | 2+1、1+1、0+1 | 5 |

### 超级大乐透

| 奖级 | 命中状态 | 奖金（元） |
| --- | --- | ---: |
| 一 | 5+2 | 5,000,000 |
| 二 | 5+1 | 100,000 |
| 三 | 5+0、4+2 | 5,000 |
| 四 | 4+1 | 300 |
| 五 | 4+0、3+2 | 150 |
| 六 | 3+1、2+2 | 15 |
| 七 | 3+0、2+1、1+2、0+2 | 5 |

不考虑奖池余额、浮动奖金、特殊派奖、营销活动及期次特例。一、二等奖固定为 5,000,000 / 100,000 元。

## 4. 验收结果

已通过的针对性验收：

- SSQ/DLT 全部合法命中状态 oracle：通过。
- 一等奖、二等奖固定金额：通过。
- 特殊元数据不改变奖金：通过。
- 规则隔离、非法规则和非法命中数失败闭合：通过。
- 任意合法 DLT 期号返回同一 `DLT_PRIZE_FIXED_7TIER`：通过。
- 独立全空间 oracle：通过。
  - SSQ：总注数 17,721,088；中奖注数 1,188,988；固定奖金总额 15,117,950 元。
  - DLT：总注数 21,425,712；中奖注数 1,429,197；固定奖金总额 18,890,405 元。
- `compileall`：通过。

未宣称完整历史测试套件全部通过：本地完整套件依赖部分历史 artifacts，且部分测试受 macOS 运行时资源差异影响；接手者应以本文件列出的定向验收和远程完整环境复核为准。

## 5. Git 与工作区状态

已提交并推送：

```text
branch:  codex/phase4-real-model-implementation-20260815
commit:  d7065152ae0e22ecb505f9b391fea79f57d3dcc8
remote:  origin/codex/phase4-real-model-implementation-20260815
```

工作区仍有其他历史未提交/未跟踪文件，它们不属于本次奖金冻结提交。接手者不要执行破坏性清理或全量 `git add .`；如需继续开发，应只选择明确相关文件。

## 6. 历史产物与“新/旧”命名说明

Phase 0/早期回测产物中可能仍保留历史数据 lineage 名称，例如 `DLT_PRIZE_2019_9TIER`、`DLT_PRIZE_2026_7TIER`。这些名称只代表历史输入或旧审计产物，不是当前 Phase 4 的可用奖金策略。当前实现、文档和新的 `state-space-audit.json` 只认可一套 DLT 固定规则。

## 7. 接手后的建议步骤

1. 拉取并检出上述远程分支，确认提交哈希一致。
2. 运行定向验收：

   ```bash
   PYTHONPATH=src python3 tests/phase4_legacy/test_prize_oracle.py
   PYTHONPATH=src python3 tests/phase4_legacy/test_prizes.py
   PYTHONPATH=src python3 -m compileall -q src scripts/phase4e21_bonus_hardening tests/phase4 tests/phase4_legacy
   ```

3. 若需要运行 `tests/phase4/test_phase4e21_bonus_hardening.py`，确保 `scripts/phase4e17/run_per_number_feature_model.py` 在 `PYTHONPATH` 中；该测试依赖 `ticket_prize` wrapper。
4. 若继续做模型或特征工程，必须保持 SSQ 与 DLT 模型隔离，并调用冻结奖金入口计算分组奖金。
5. 任何奖金规则变更必须新建版本化变更、更新契约指纹、oracle、说明文档和审计产物，不能直接修改冻结表。

## 8. 明确未完成事项

- 本交接不包含新的特征工程、模型调参或重新生成 1000/5000/10000 等号码组合。
- 本交接不证明预测中奖概率或奖金均值已经达到业务目标。
- 远程完整工作树的最终复核应由接手者在可连接的 VPS 环境执行；本地已完成的定向验收结果不能替代远程环境复核。
