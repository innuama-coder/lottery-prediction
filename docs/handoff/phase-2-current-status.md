# Phase 2 当前状态与交接说明

更新时间：2026-08-07

## 权威状态

阶段 2 当前状态为：

> `COMPLETE / GO`

Phase 2.1 是对 Phase 2 统计方法和验收闭包缺陷的版本化修复 release，
不是仍需 Phase 2.2 的普通子阶段。最终 release
`P2.1-R00-61a99a2c3732-i07-r02` 已完成正式执行和验收；其结果为
`status=PASS`、`delivery_status=GO`、阻断问题为 0，G0～G6 全部 `PASS`。
因此 Phase 2 已完成，可以进入 Phase 3 规划或交接。

当前科学分类为 `indeterminate`。这表示注册检验在当前样本和功效边界内
没有形成可晋级候选信号，也不能把“未发现”解释为“证明彩票随机”；它是
Phase 2 允许的有效科学结论，不是交付失败或未完成状态。

## 权威验收制品

当前完成结论以以下不可变 Phase 2.1 bundle 为准：

- 最终验收：`artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json`
- 递归清单：`artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/manifest.json`
- 总体设计：`docs/research/phase-2.1-overall-design.md`
- 验收合同：`docs/roadmap/phase-2.1-acceptance-contract.json`
- VPS 复算手册：`docs/runbooks/phase-2.1-vps-runbook.md`

`artifacts/phase-2/acceptance/phase2-acceptance.json` 是深度复核前生成的历史
Phase 2 制品。它继续保留用于审计，但不再是当前完成状态的权威来源，也不得
覆盖或替代上面的 Phase 2.1 最终 bundle。

## 已验收成果

- DLT、SSQ 分开执行注册的历史审计、敏感性分析和负对照。
- G2 对实际生成器支持和真正缓慢漂移备择完成 known-answer 验证。
- historical audit 从冻结 draws 和 reference-null corpus 完整重算，没有复制旧结果行。
- power 与不同 seed 的 independent replay 覆盖五个注册 family 和全部 240 个格点。
- 核心结果使用专用 Schema；release identity 绑定合同、代码、输入、环境、任务输入和下游证据。
- 终局 validator 重新计算注册指标、递归检查证据身份并自动推导科学分类。
- 十个 E2E、负向验证、独立方法复核和独立 replay 复核均进入最终闭包。
- Phase 2.1 测试 39/39 PASS，Phase 2 回归 31/31 PASS。
- readiness、离线 build、`compileall` 和 `git diff --check` 外部交付检查全部 PASS。

## 已关闭的历史阻断

2026-08-05 交接复核列出的七类阻断——顶层聚合代替底层重算、核心结果缺少
专用 Schema、阶跃变化冒充缓慢漂移、G2 未验证实际生成器、内部身份过期、
功效区间命名失真以及偏差族覆盖不足——均已纳入 Phase 2.1 的合同、实现、
正式复算和最终验证。最终验收记录 `blocking_findings=0`，这些历史问题不再
阻断 Phase 2 完成。

## 非阻断科学限制

最终验收保留以下限制，它们约束结论强度，但不降低交付状态：

- `indeterminate` 不是随机性证明。
- 缺少物理出球设备和球组身份，不能对设备层机制作出结论。
- 功效结论只覆盖已注册的检验 family、效应尺度和样本量网格。
- 独立复核体现流程与实现独立性，不等同于外部机构审计。

## 接手入口

按顺序阅读：

1. `README.md`
2. 最终 `acceptance/acceptance.json` 与 `acceptance/manifest.json`
3. `docs/research/phase-2.1-overall-design.md`
4. `docs/roadmap/phase-2.1-acceptance-contract.json`
5. `docs/runbooks/phase-2.1-vps-runbook.md`
6. `docs/roadmap/phase-2-randomness-audit-plan.md`
7. `src/lottery_research/phase2_1/` 与 `tests/phase2_1/`
8. 历史 `src/lottery_research/phase2/` 与 `tests/phase2/`

## 阶段边界

- Phase 2 已完成，不存在已规划或待执行的 Phase 2.2。
- 下一步如继续项目，应单独规划 Phase 3 的模型、特征和前瞻验证工作。
- Phase 3 必须继续以均匀模型 M0 为默认 champion；Phase 2 的历史异常不能直接授予 challenger 晋级。
- 不把 `indeterminate` 宣称为“证明随机”，也不据此生成投注承诺或收益承诺。
- Phase 2 历史制品与失败候选 release 保持只读，不覆盖、不删除、不伪装成最终成功 release。
