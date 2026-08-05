# Phase 2 当前状态与交接说明

更新时间：2026-08-05

## 权威状态

阶段 2 当前状态为：

> `HOLD / partially achieved`

核心历史审计、功效计算和 `indeterminate` 科学结论基本成立，但阶段 2 尚未达到完整目标和可信终局验收标准。现有 `artifacts/phase-2/acceptance/phase2-acceptance.json` 是深度复核前的历史制品，不得继续作为“阶段 2 完全完成”的依据。

## 已确认成立的成果

- DLT、SSQ 各使用 200 期 DrawRecord；800 条 SourceObservation 没有被错误放大为独立样本。
- 10 项主检验、10 项敏感性分析及负对照已执行。
- 240/240 功效网格、10 个 delta-star 状态和 40 个 required-n 状态完整。
- 当前十个单元均应分类为 `indeterminate`。
- 最近一次本地回归为 58/58 PASS：phase2 31、CLI contract 15、E2E runner 7、readiness 5。

仓库级交接验证另发现：Phase 0 全量测试当前有 19 个历史证据重放/冻结环境错误，Phase 1 全量测试在 10 分钟内未完成。它们不属于 Phase 2 修复范围，但接手方必须以 `README.md` 的验证矩阵为准，不得把历史验收报告等同于当前仓库可完全重放。

## 阻断完全验收的问题

1. `accept` 主要聚合外层证据和顶层 PASS，没有从底层结果重新计算全部量化指标；`signal_status` 直接来自最终证据清单，而不是由历史审计和功效结果唯一推导。
2. qualification、historical-audit、power-envelope 缺少各自的专用结果 Schema。
3. Roadmap 要求研究缓慢漂移，但当前 temporal 生成器实际是前后半两段阶跃。
4. G2 小世界验证只检查理论组合恒等式，没有把实际生成器输出与独立精确分布比较。
5. 正式 qualification 和 method review 中存在已经过期的内部输入身份；最终验收只闭合了外层哈希。
6. 当前网格反演结果被命名为 `effect_interval_95`，但其语义只是冻结格点上的兼容集合。
7. 部分功效结果只覆盖前区、正向、单一典型分量，不能自动解释为整个偏差族的最坏情况功效。

## 已决定的修复方向

不得原地改写旧预注册或手工修补旧 SHA。后续应创建版本化的 Phase 2.1 修复版本，至少包括：

- 增加真正的缓慢漂移备择，并保留、重命名现有阶跃备择；
- 为核心结果增加严格 Schema；
- 让终局验收重新计算指标、递归验证证据身份并自动推导科学分类；
- 重做实际生成器的 known-answer 验证；
- 使用不可变 release identity 绑定合同、代码、环境、输入和全部下游证据；
- 在同一最终 bundle 上重跑 qualification、audit、power、replay、E2E01～E2E10、D2-12 和 D2-13。

Phase 2.1 的详细实现计划尚未冻结。接手团队不得在没有版本化补充预注册和独立方法复核的情况下直接修改现有正式统计结果。

## 接手入口

按顺序阅读：

1. `README.md`
2. `docs/roadmap/phase-2-randomness-audit-plan.md`
3. `docs/roadmap/phase-2-acceptance-contract.json`
4. `artifacts/phase-2/contracts/preregistration.json`
5. `docs/research/phase-2-randomness-audit-power-envelope.md`
6. `src/lottery_research/phase2/workflows.py`
7. `src/lottery_research/phase2/formal_workflows.py`
8. `src/lottery_research/phase2/vectorized.py`
9. `tests/phase2/`、`tests/phase2_cli_contract/`、`tests/phase2_e2e/`

## 交接边界

- 不进入阶段 3 的模型、特征、Top-1000 号码或投注实现。
- 不把当前 `indeterminate` 解释为“证明彩票随机”。
- 不因为现有 58 项测试通过而宣布 Phase 2 完全完成。
- `artifacts/phase-2/superseded/`、Monte Carlo 检查点和递归 E2E workspace 不进入首个远程提交；冻结输入、正式摘要、运行清单、qualification corpora、E2E registry 与十份 compact receipt 保留。批量中间状态应从冻结种子重新生成，不能把 Git 仓库当作运行时缓存。
