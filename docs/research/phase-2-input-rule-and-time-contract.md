# 阶段 2 输入、规则与时间合同

状态：已冻结（P2-00B）  
冻结时间：2026-08-05T00:08:00Z  
适用范围：仅用于大乐透与双色球的回顾性随机性审计、校准和功效研究。

## 1. 统计单位

每条 `DrawRecord` 是一个独立开奖期次；`SourceObservation` 只证明来源，不增加样本量。正式样本共 400 期：大乐透 200 期（2025034–2026083），双色球 200 期（2025037–2026085）。

## 2. 号码空间与公开流程

| 游戏 | 号码空间 | 固定抽取 | 公开流程版本 | 样本覆盖 |
|---|---|---|---|---|
| 大乐透 | 前区 1–35；后区 1–12 | 前区 5 个；后区 2 个，区内不放回 | `dlt-process-documented-random-device-35c5-12c2-v1` | 2025034–2026083 |
| 双色球 | 红区 1–33；蓝区 1–16 | 红区 6 个；蓝区 1 个，区内不放回 | `ssq-process-documented-draw-machine-33c6-16c1-v1` | 2025037–2026085 |

公开规则只支持“固定号码空间、固定抽取数量、分区抽取”的统计零假设。现有数据不能识别实际摇奖机、球组、维护或更换记录，因此两个游戏均标记为 `unknown_machine_and_ball_set_identity`。这不妨碍号码集合层面的审计，但禁止把异常归因到具体物理设备。

2026 年第 014 期起的奖金规则变化，以及大乐透 2026 年第 050–066 期促销，未发现改变号码生成机制的证据，故均为 `generation_split=false`，不得据此拆分生成过程。

## 3. 联合零假设与日历

在游戏、号码空间版本和公开流程版本条件下，各期相互独立；每个分区是号码空间内固定数量、均匀、无放回的集合抽样。模拟保留真实期号、日期、缺期和流程分段，只重新生成号码，不补造未观测期次。

数据没有物理出球顺序，因此任何依赖出球先后次序的检验都不在阶段 2 范围内。

## 4. 时间与信息使用

400 条记录均为 `retrospective_current_view`，且 `available_at_utc=null`。允许把它们作为历史开奖标签和回顾性随机性审计输入；禁止把来源链接、修订信息或任何结果字段当作开奖前已知的预测变量，也禁止据此声称具备逐期回测的 point-in-time 特征。

禁止进入统计矩阵的字段：`evidence_links`、`revision_id`、`supersedes_revision_id`、`core_fact_sha256`、`source_observation_count`、`future_draw_numbers`。

## 5. 可机器检查的断言

- `ASSERT-P2-IN-01`：Phase 1 合同、最终验收、draws、observations、manifest 与 schema-freeze 的路径和 SHA-256 必须全部匹配。
- `ASSERT-P2-IN-02`：统计单位总数必须为 400，两个游戏各 200；Observation 对样本量的贡献必须为 0。
- `ASSERT-P2-IN-04`：每期开奖必须且只能连接到一个号码空间段、一个公开流程段和一个奖金规则段；奖金或促销生成分段数必须为 0。
- `ASSERT-P2-IN-05`：禁止字段进入统计矩阵的数量必须为 0；全部数据仅按回顾性标签使用。
- `ASSERT-P2-MECH-01`：未知摇奖机和球组身份的显式标记率必须为 100%；具体设备归因数量必须为 0。

机器真值以 `artifacts/phase-2/contracts/input-manifest.json` 为准。合同与该清单、Phase 1 冻结证据及 `preregistration.json` 的任一身份不一致时，命令必须 fail closed。

## 6. 证据

- 财政部大乐透规则批准（2018）：https://www.mof.gov.cn/gp/xxgkml/zhs/201812/t20181229_3111394.htm
- 国家体彩中心大乐透现行规则：https://m.lottery.gov.cn/ksjz/m/yxgz_dlt/
- 财政部双色球规则（2014）：https://zhs.mof.gov.cn/zhengcefabu/201404/t20140421_1069579.htm
- 福彩公开双色球规则转载：https://www.scflcp.com.cn/yxgz/823145252.jhtml

## 7. 边界

本合同不授权预测模型、特征搜索、Top-1000 号码、投注建议或“证明随机/可预测”的结论。阶段 2 只能报告检验流程是否有效、当前样本发现了什么、以及能检测到多大的偏离。
