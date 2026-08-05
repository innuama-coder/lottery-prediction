# 阶段 2 随机性审计与功效包络报告

## 结论摘要

阶段 2 的交付状态与科学结论是两件事。本报告所依据的正式历史审计、功效计算和独立重放均已通过各自质量门槛，D2-10 已由独立重放复核 Agent 签署。

截至 G5，体彩大乐透（DLT）和福彩双色球（SSQ）的十个预注册主检验均未形成经 Holm 多重校正后的候选信号。但是，两个游戏在五类实质意义边界处的功效 95% 同时下界均低于 0.80。因此，两个游戏、十个分类单元目前都必须标记为 `indeterminate`，阶段汇总科学状态也是 `indeterminate`。

这表示：在当前各 200 期样本中没有达到预注册候选标准的证据，同时现有样本量不足以可靠排除实质意义边界附近的偏差。该结果既不能证明开奖机制绝对随机，也不能证明不存在未观测到的机制偏差。

## 研究问题与边界

本阶段只回答两个问题：冻结的 400 期历史号码是否呈现预注册的统计偏离；以及当前样本量对不同大小偏离的实际检出能力有多强。

统计单位是 400 条 `DrawRecord`：DLT 200 期、SSQ 200 期。800 条 `SourceObservation` 只用于来源追溯，未被当作额外独立样本。两个游戏分别建模和报告，不做跨游戏合并。研究使用冻结时可见的回溯性当前标签，不读取冻结后的数据。

本阶段不评价具体物理设备、球组或操作人员，因为数据中没有这些身份和顺序元数据；也不把历史频数分析外推为未来号码选择结论。

## 冻结方法

联合零假设是在每个已记录开奖规则段内，从合法号码空间均匀抽取合法票，并在期与期之间独立。所有模拟生成器都直接生成合法票，保证号码范围、基数和不重复约束。

每个游戏预注册五个主检验，共十个主决策：

| 偏差族 | 统计目标 | 实质意义边界 |
|---|---|---:|
| marginal_inclusion | 最大单号入选概率绝对偏差 | 0.01 |
| set_structure | 号码和值均值绝对偏移 | 1.0 |
| pair_dependence | 最大号码对共同入选概率绝对偏差 | 0.005 |
| temporal_instability | 前后半样本单号入选概率最大绝对差 | 0.02 |
| cross_zone_dependence | 分区和值离散化后的 Cramér's V | 0.10 |

历史审计使用 9,999 次独立参考零假设模拟，经验 p 值按 `(b+1)/(B+1)` 计算，并对十个主决策统一执行 Holm 校正。效应置信集合通过冻结效应网格上的 Neyman 反演构造，使用每场景 20,000 次独立 evaluation 模拟。敏感性分析按冻结日历规则裁去 10% 边界期，并检查方向一致性。

功效包络使用每个网格点 4,000 次合法票 Monte Carlo 模拟，共 240 个游戏×偏差族×效应×样本量点。每个点使用 Clopper–Pearson 区间，并对完整冻结网格做 Bonferroni 同时覆盖。`delta-star` 只能从实际样本量处同时 95% 功效下界达到 0.80 的已模拟效应点中选择；`required-n` 只能从冻结样本量网格中选择，不插值。

## 校准与执行质量

| 指标 | 正式结果 | 门槛 | 判定 |
|---|---:|---:|---|
| CAL-01：FWER 单侧 95% 上界 | 0.03932 | ≤ 0.06 | PASS |
| CAL-02：关键 FWER 区间半宽 | 0.00264 | ≤ 0.005 | PASS |
| CAL-03：名义 95% 区间覆盖单侧下界 | 0.94366 | ≥ 0.93 | PASS |
| CAL-04：负对照晋升为候选信号数 | 0 | = 0 | PASS |
| qualification 非法生成组合 | 0 | = 0 | PASS |
| QUAL-01：强偏差恢复率及方向匹配率 | 1.00 | = 1.00 | PASS |
| POW-03：关键同时功效区间最大半宽 | 0.02933 | ≤ 0.03 | PASS |
| 网格覆盖 | 240/240 | 100% | PASS |
| Δ* / required-n 覆盖 | 10/10；40/40 | 100% | PASS |
| 检查点恢复 | 复用 1，缺失 0，重复 0 | 无缺失或重复 | PASS |

正式功效结果的规范化哈希为 `79a35a3eee0c06427db29f3e23e851cec92a0e6ae3c661c667d0747a3b3c5e54`。

## DLT 历史审计与功效解释

| 偏差族 | Holm p | n=200、实质边界处功效 | 同时 95% 下界 | Δ*（n=200） | 边界所需 n | 分类 |
|---|---:|---:|---:|---|---|---|
| marginal_inclusion | 1.0000 | 0.0060 | 0.00248 | 网格内未识别 | 网格内未识别 | indeterminate |
| set_structure | 1.0000 | 0.0135 | 0.00774 | 网格内未识别 | 网格内未识别 | indeterminate |
| pair_dependence | 1.0000 | 0.0010 | 0.00006 | 网格内未识别 | 网格内未识别 | indeterminate |
| temporal_instability | 1.0000 | 0.0028 | 0.00066 | 网格内未识别 | 网格内未识别 | indeterminate |
| cross_zone_dependence | 1.0000 | 0.1040 | 0.08693 | 0.30 | 1000 | indeterminate |

DLT 五项主检验均无候选证据。只有跨区依赖在当前网格内识别出 `delta-star=0.30`，说明 n=200 时系统只能对远大于实质边界 0.10 的偏差达到预注册检出要求；在边界 0.10 处，冻结样本量网格给出的最小合格样本量是 1000。其余四类在冻结效应网格或样本量网格内均未达到 0.80 同时下界，不能声称已具备排除实质偏差的能力。

## SSQ 历史审计与功效解释

| 偏差族 | Holm p | n=200、实质边界处功效 | 同时 95% 下界 | Δ*（n=200） | 边界所需 n | 分类 |
|---|---:|---:|---:|---|---|---|
| marginal_inclusion | 1.0000 | 0.0045 | 0.00158 | 网格内未识别 | 网格内未识别 | indeterminate |
| set_structure | 0.7000 | 0.0188 | 0.01180 | 网格内未识别 | 网格内未识别 | indeterminate |
| pair_dependence | 0.3160 | 0.0038 | 0.00116 | 网格内未识别 | 网格内未识别 | indeterminate |
| temporal_instability | 1.0000 | 0.0020 | 0.00035 | 网格内未识别 | 网格内未识别 | indeterminate |
| cross_zone_dependence | 0.3204 | 0.1135 | 0.09571 | 0.30 | 1000 | indeterminate |

SSQ 五项主检验也均无候选证据。未经校正的较小 p 值没有通过十项 Holm 家族校正，不能选择性报告为主结论。跨区依赖同样只在 `delta-star=0.30` 达到 n=200 的检出要求，在实质边界 0.10 处需要冻结网格中的 n=1000；其余四类均未在冻结范围内识别出合格 Δ* 或边界 required-n。

## 信号分类规则与汇总结论

单元只有同时满足以下条件才可标记为 `candidate_signal`：候选资格有效；Holm 校正后显著；效应置信集合完全越过实质零区间；敏感性方向一致；功效与精度门槛通过。只有没有候选信号且实质意义边界处的功效 95% 下界至少为 0.80，单元才可标记为 `no_detectable_signal`。其余情况必须标记为 `indeterminate`。

本次十个单元都没有候选信号，但十个实质边界处的功效下界都明显低于 0.80，因此没有任何单元满足 `no_detectable_signal`。DLT 汇总为 `indeterminate`，SSQ 汇总为 `indeterminate`，阶段科学状态汇总为 `indeterminate`。

阶段交付是否达到 GO 由 G0–G6 的证据质量、复现性和验收闭环决定，不由是否发现候选信号决定。

## 独立重放

独立重放 run `p2-replay-refreeze-20260805t0654z` 以 `exit 0 / PASS` 结束。结果满足全部冻结门槛：

- REP-01：同种子规范化产物匹配率 1.00，源结果与重放哈希均为 `79a35a3eee0c06427db29f3e23e851cec92a0e6ae3c661c667d0747a3b3c5e54`；
- REP-02：独立参考实现的关键确定性统计量 4/4 精确匹配；
- REP-03：保留种子的 240/240 个功效网格点同时区间兼容；
- REP-04：10 个 Δ* 状态和 40 个 required-n 状态共 50/50 兼容；
- REP-05：检查点缺失批次 0、重复批次 0，恢复与不中断规范化哈希一致，复用批次 640；
- COV-05：未解决阻断项 0。

正式 `replay-run.json` 的 SHA-256 为 `61b5fe5207e6eb69805041163612fc223e878764d54c43107a0c5322ec591334`；D2-10 独立签署的 SHA-256 为 `fd7debc2357f70854d0413d3ff9158f7e072378b510cfc0a7c8dc80f974acc8d`。

独立重放首先暴露并阻断了 replay/accept 对相对输出路径与绝对项目根目录混用的问题。修复只涉及路径归一化、项目边界和对应测试；六个统计核心文件、方法、阈值、种子及效应/样本量网格均未改变。原 D2-05 的预结果签署保持不变，另由同一独立 reviewer 签署了明确标注为 post-result、非盲态的非统计补丁复核附录。随后 audit、power 和 replay 均在新环境身份下用新 run-id 重新发布，功效与重放 checkpoint 在复用前逐配置和逐批次校验。E2E-10 的恢复/不中断进程在补丁前已启动，回执保留其真实旧环境身份；因其覆盖的 `research_engine`/`formal_workflows` 身份经附录证明未变而不重复计算，受补丁影响的 replay/accept 路径则由新环境下的 E2E-09 和 E2E-01 覆盖。

## 局限性

- 数据没有每期抽取顺序，无法检验顺序位置效应。
- 数据没有物理机器、球组、操作人员和维护事件身份，无法把统计变化归因到具体机制。
- 历史记录是冻结时的回溯性当前视图，不是逐期保存的原始发布快照。
- 每个游戏只有 200 个独立开奖单位；功效包络表明这对实质边界附近的多数偏差远远不足。
- 统计未拒绝不能证明机制绝对随机；高功效模拟也只覆盖已预注册的偏差族和效应网格。
- 独立重放仍是程序化 Agent 独立，不等同于外部组织审计。

## 证据索引

- 输入与方法合同：`docs/roadmap/phase-2-acceptance-contract.json`、`docs/research/phase-2-input-rule-and-time-contract.md`
- 独立方法复核：`artifacts/phase-2/reviews/method-review.json`
- 非统计补丁复核：`artifacts/phase-2/reviews/nonstatistical-patch-review.json`
- G2 qualification：`artifacts/phase-2/qualification/harness-qualification.json`
- G3 历史审计：`artifacts/phase-2/results/historical-audit.json`；run `p2-audit-refreeze-20260805t0652z`
- G4 功效包络：`artifacts/phase-2/results/power-envelope.json`；run `p2-power-refreeze-20260805t0652z`
- G5 独立重放：`artifacts/phase-2/replay/replay-run.json`、`artifacts/phase-2/reviews/replay-review.json`；run `p2-replay-refreeze-20260805t0654z`
