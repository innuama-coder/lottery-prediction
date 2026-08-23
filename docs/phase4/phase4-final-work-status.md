# Phase 4 完整工作状态说明（结论版）

更新时间：2026-08-23
分支：`codex/phase4-real-model-implementation-20260815`（HEAD `5eba96aba`）

本文件是"提升预测模型收益率（分组内每注平均奖金 > 2 元）"这一目标在 Phase 4 的完整工作状态与结论归档。**每条结论均标注证据（提交哈希 / 产物路径 / 具体数值）。**

---

## 1. 目标

业务目标：双色球（SSQ）/ 超级大乐透（DLT）预测模型输出分组号码，使**分组内每注平均奖金 > 2 元**（2 元 = 每注票价回本线）。分组大小 K 可调（1000/5000/10000/…/100000）。

## 2. 最终结论（一句话）

**在现有数据与全部已尝试的手段下，该目标不可达成；1430 期 DLT 数据 + 32 项特征 + 穷举模型搜索，样本外均无预测信号（`no_confirmed_lift`）。**

分解为四条结论：

| # | 结论 | 证据 |
| --- | --- | --- |
| C1 | 冻结固定奖金契约下，每注期望奖金恒为 SSQ 0.853105 / DLT 0.881670 元，任何选号/分组/覆盖都无法改变 | `prize_metrics.py`（提交 `2203b4518`）`full_space_oracle` + 对称性证明 |
| C2 | 真实 parimutuel 口径下，DLT 每注 EV 实测 0.616–1.738 元，从未达 2 元；冷门规避可抬到 ~1.95 元，但仍依赖未校准的热度假设 | `artifacts/phase4e25_b1_dlt_pool_data/`（`19c7e8c7c`）、`artifacts/phase4e26_b2_popularity/`（`069a92145`） |
| C3 | 1430 期 DLT + 32 特征，模型样本外与均匀基线统计上无差异（log loss 差 ≈ 0，p=0.569） | `artifacts/phase4e31_baseline/summary.json`（`cff0f528e`） |
| C4 | 穷举 48 个模型配置后最优候选仍不优于基线；样本量 1430 期对"2 元所需信号量级"仍差 ~2.3 倍 | `artifacts/phase4e32_model_search/summary.json`（`5eba96aba`） |

---

## 3. 工作历程（提交链证据）

| 提交 | 内容 | 结论 |
| --- | --- | --- |
| `d7065152a` | 冻结单一固定奖金规则（e21） | 基线 |
| `2203b4518` | 冻结分组奖金入口 `prize_metrics.py` | 全空间 oracle：SSQ 15,117,950/17,721,088=0.853105；DLT 18,890,405/21,425,712=0.881670 |
| `4c86aa0d` | C+B 路线总体+详细计划 | 确立"先 C 后 B" |
| `68f402f04` | e23 预注册分组奖金评估器 | **路径 C 证伪**：3 策略×K∈{1000,5000,10000} 实现均值 0.33–1.17 元，95% 区间全部 <2 元，`no_confirmed_lift` |
| `0fe137d25` | B 路径 parimutuel 计划 | — |
| `6463f0fed` | e24 parimutuel 奖金+EV 模型 | 一二等奖 parimutuel 抽象；低奖级复用冻结值 |
| `19c7e8c7c` | e25 采集 DLT 真实奖池/销量/中奖注数 | **真实 EV 0.615920–1.737901 元**，距 2 元 0.26–1.38 元 |
| `069a92145` | e26 号码热度代理（生日效应） | 冷门票 EV ~1.945524 vs 均匀 0.964800（+0.98），但 `birthday_bias=0.2` 为**假设非实测** |
| `f4045f019` | e27 深度研究 40 项可观测特征 | 7 类、35 documented/2 heuristic/3 hypothetical，零"proven" |
| `e5041c144` | e28 特征可行性 | 29 可算 + 3 可采(DLT) + 8 不可行 |
| `70c0e59d9` | e29 实现 32 项特征 + 序列安全快照 | 5 测试含前缀不变性 |
| `e51330c67` | e30 扩展 DLT 训练数据 | **1430 期（2017–2026）**，provenance 完整 |
| `cff0f528e` | e31 基线模型 + walk-forward | 12 逐号码特征，120 期样本外 p=0.569，`no_confirmed_lift` |
| `5eba96aba` | e32 模型选择+参数搜索 | 48 配置穷举，最优 log loss 差 2.4e-06，`no_confirmed_lift` |

---

## 4. 四条结论的详细证据

### C1：固定奖金下期望不变（数学定理，非判断）

- 冻结契约：`docs/phase4/prize-calculation-contract.md`、`src/lottery_system/phase4/bonus.py`。
- 全空间 oracle（`prize_metrics.full_space_oracle`）：任一固定开奖号，全包总奖金恒为 SSQ 15,117,950 元、DLT 18,890,405 元，与具体开奖号无关。
- 由超几何分布对称性：任意一注的命中数分布与"选哪一注"无关，故每注期望恒为 0.853105（SSQ）/ 0.881670（DLT）。
- 因此**分组平均奖金 = 组内每注期望的平均 = 常数**，与 K、与选号策略无关。要达 2 元需 2.34×/2.27× 的真实命中优势。
- 实证：e23（`68f402f04`）在冻结 120 期上，覆盖型策略（蓝球锁+红球全包）期望精确等于 0.853105；观察到的 1.17 元是 120 期重尾噪声（95% CI [0.65,1.80] 含 0.85）。

### C2：parimutuel 真实数据下仍不达 2 元

- e25（`19c7e8c7c`）采集 DLT 20 期真实开奖公告：每注 EV = 应派奖金/(销售额/2)，区间 **0.615920–1.737901 元**；一等奖单注实值 264.5 万–1000 万（冻结假设 500 万），二等奖 5.6 万–32 万。
- e26（`069a92145`）生日效应代理下，冷门票（前区 31,32,33,34,35，热度权重 0.24）EV 均值 1.945524 元，距 2 元仍差 ~0.05；要稳定达 2 元需一等奖池 4521 万–7282 万元（为实际池 2.65–23.89 倍）。
- 两个硬前提使其"有条件"而非"可实现"：(a) `birthday_bias=0.2` 是**假设**（文献支持方向、不支持精确幅度，官方不公布号码级投注分布）；(b) EV>2 是**期望**，一等奖 1/2143 万概率使"稳定实现 >2 元"不可能。

### C3：模型无样本外信号

- e31（`cff0f528e`）：1430 期 DLT + 12 逐号码特征，逐号码 L2 逻辑回归，120 期 walk-forward 严格样本外（λ 只用前缀选、泄漏测试通过）。
- 结果：前区 log loss 模型 0.410061 vs 均匀 0.410116；后区 0.450572 vs 0.450561；配对检验 **p=0.569**。

### C4：穷举搜索后仍无提升，且样本量不足

- e32（`5eba96aba`）：穷举 48 个配置（3 模型族 × 特征子集 × λ∈{1e-4,1e-3,1e-2} × W∈{30,60}），最优 A/statistical-only(9)/λ=0.01/W=30。
- 最优 log loss 差 vs 均匀 = **2.37e-06**（≈0）；Top-K 命中 2.183 vs 基线 2.095（+0.088，前区 +0.11 / 后区 −0.02，噪声级），且文档标注 best-of-N 上偏。
- 样本量：1430 期最小可检单号偏置 ≈0.07（前区相对 50%），而 2 元目标所需 ≈0.03，**仍差 ~2.3 倍**；要检出 0.03 需约 **7800 期**（未定向/Bonferroni，按 `n ∝ 1/δ²` 外推），远超该源可给的 1430 期、也超过 DLT 2007 年以来的总期数 ~2800 期。

---

## 5. 阻塞与未完成事项

1. **SSQ 数据扩展未完成**：国家主源 403、广东页仅 200 期；上海福彩历史页（HTTP 200、含销量/奖池）是候选，但"公开可查看 ≠ 获准批量采集"，需授权（`docs/phase4e30/DELIVERY.md`）。
2. **号码级投注分布不可得**：官方只公布销售总额+各奖级中奖注数，无号码级热度（e28 判 8 项特征不可行的主因）。
3. **2007–2016 DLT 历史不可得**：该源 2016 及更早返回 301→404。
4. **物理层数据非公开**：球质量/尺寸台账、机器编号、逐期预检/异常记录均非公开（e28 判不可行）。

## 6. 建议下一步（按可行性）

1. **接受结论、收尾**：预测路径在现有数据+手段下无信号，`平均奖金>2元` 不可达成。
2. **扩 SSQ 做第二彩种独立验证**（需授权上海福彩批量采集）：价值是交叉验证 DLT 结论；但 DLT 已 7 倍数据无信号，SSQ 大概率同果。
3. **扩数据源**：DLT 2007–2016 历史、或可观测物理/环境字段——均需新数据源授权，且 e28 已判大部分非公开。

## 7. 证据索引

- 冻结奖金契约：`docs/phase4/prize-calculation-contract.md`、`src/lottery_system/phase4/bonus.py`
- 分组奖金入口：`src/lottery_system/phase4/prize_metrics.py`
- 路径 C 评估：`artifacts/phase4e23_group_prize_eval/summary.json`
- parimutuel 模型：`src/lottery_system/phase4/parimutuel.py`
- 真实 DLT 数据：`artifacts/phase4e25_b1_dlt_pool_data/dlt-draws.jsonl`、`artifacts/phase4e30_data_expansion/dlt-draws-full.jsonl`
- 热度代理：`src/lottery_system/phase4/popularity.py`
- 特征研究/可行性/实现：`artifacts/phase4e27/features.json`、`artifacts/phase4e28/feature-feasibility.json`、`src/lottery_system/phase4/features/`
- 基线模型 + 评估：`artifacts/phase4e31_baseline/summary.json`、`src/lottery_system/phase4/baseline_model.py`
- 模型搜索：`artifacts/phase4e32_model_search/summary.json`、`src/lottery_system/phase4/model_selection.py`
- 随机性审计（路径 A 依据）：`docs/research/phase-2-randomness-audit-power-envelope.md`
- 计划文档：`docs/phase4/phase4-c-b-route-plan.md`、`docs/phase4/phase4-b-parimutuel-plan.md`
