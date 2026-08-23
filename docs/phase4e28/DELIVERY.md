# Phase4E28：40 项特征可用性/可采集性研究

## 结论

本轮逐项复核 `artifacts/phase4e27/features.json`，并严格区分“数学上能定义”与“所需观测数据真实存在”。40 项中，29 项可从当前冻结数据计算，3 项有可执行的新采集路径，8 项在当前公开与获准渠道下不可行。

这里的“可计算”只表示输入数据存在，不表示特征能预测彩票；多数号码统计更适合描述、随机性审计或漂移监测。所有滚动量仍须 point-in-time 构造并接受严格样本外检验。

| 档位 | 数量 | 特征清单 |
|---|---:|---|
| `computable_from_current` | 29 | `FREQ_ROLLING_RATE`, `FREQ_WAITING_TIME`, `FREQ_UNIFORMITY_RESIDUAL`, `REL_PAIR_RATE`, `REL_TRIPLE_RATE`, `REL_PREVIOUS_OVERLAP`, `STRUCT_ODD_COUNT`, `STRUCT_SUM`, `STRUCT_RANGE`, `STRUCT_ADJACENT_PAIRS`, `STRUCT_GAP_VECTOR`, `STRUCT_BAND_COUNTS`, `TIME_WEEKDAY`, `TIME_DAYS_SINCE_DRAW`, `CTX_SALES_AMOUNT`, `CTX_JACKPOT_BALANCE`, `BEHAV_BIRTHDAY_COUNT`, `BEHAV_ARITHMETIC_PATTERN`, `BEHAV_RECENT_WIN_OVERLAP`, `BEHAV_WINNER_COUNT_RESIDUAL`, `STAT_LAG_AUTOCORRELATION`, `STAT_MARKOV_TRANSITION`, `STAT_SHANNON_ENTROPY`, `STAT_RENYI_ENTROPY`, `STAT_PERMUTATION_ENTROPY`, `STAT_SAMPLE_ENTROPY`, `STAT_HYPERGEOMETRIC_MAHALANOBIS`, `STAT_RUN_LENGTH`, `STAT_CHANGE_POINT_SCORE` |
| `collectable_feasible` | 3 | `TIME_RULE_REGIME`, `ENV_BALL_SET_ID`, `ENV_DRAW_POSITION` |
| `not_feasible` | 8 | `ENV_MACHINE_ID`, `ENV_PRETEST_STATUS`, `ENV_DRAW_ANOMALY`, `ENV_BALL_MASS`, `ENV_BALL_DIMENSION`, `BEHAV_FORM_CENTER_DISTANCE`, `BEHAV_FORM_LINE_PATTERN`, `BEHAV_NUMBER_POPULARITY_MAXENT` |

机器可读的逐项判定、数据需求、来源、采集方法和理由见 [`feature-feasibility.json`](../../artifacts/phase4e28/feature-feasibility.json)。

## 当前数据边界

`artifacts/phase-1/baseline-v1/draws.jsonl` 含 SSQ 和 DLT 各 200 期，字段包括 `game`、`issue_id`、`front_numbers`、`back_numbers` 和 `draw_date_local`。因此频数、遗漏、共现、组合结构、日期、熵、自相关、马尔可夫、游程和变点类均可直接计算。

`artifacts/phase4e25_b1_dlt_pool_data/dlt-draws.jsonl` 另含 DLT 26051–26070 共 20 期的全国销售额、下期滚存奖池和逐奖级中奖注数。因此以下三项虽然归入“当前可算”，实际覆盖严格限定为 DLT 20 期：

- `CTX_SALES_AMOUNT`
- `CTX_JACKPOT_BALANCE`
- `BEHAV_WINNER_COUNT_RESIDUAL`

它们不能被误写成 SSQ 可用，也不能用开奖后才发布的当期公告字段预测同一期；作为预测输入时只能使用截点前已发布的滞后值。`BEHAV_WINNER_COUNT_RESIDUAL` 还受“销售额除以 2 只是基础票等价代理”的限制，追加投注没有被精确还原。

## 只读来源核验

研究日为 2026-08-22，本轮只做了 3 个页面探针，没有批量采集：

1. 广东体彩 DLT 第 26070 期静态公告 `https://www.gdlottery.cn/f_html/kjgg/P085_26070.html` 成功访问。正文实际含“本期使用第1套摇奖球”、前区出球顺序 `15 04 05 32 21`、后区出球顺序 `11 02`、销售额 312,722,588 元、逐奖级中奖注数和滚入下期奖池 814,894,461.66 元。这直接支持 DLT 的球套与真实出球顺序低速采集。
2. 中国体彩网 DLT 规则 URL `https://m.lottery.gov.cn/ksjz/m/yxgz_dlt/` 本次请求超时。该官方规则资源已知存在，但采集设计必须包含重试、缓存、内容哈希和人工核对；一次超时不应伪报为已成功抓取。
3. 中国福利彩票 SSQ 公告 `https://www.cwl.gov.cn/c/2025/09/09/627575.shtml` 返回 HTTP 403。结合历史任务中省级页只有约 200 期核心号码、无销售额/奖池字段的事实，本轮没有发现新的获准 SSQ 扩展字段来源。

可采集项的治理要求是：串行、至少 3 秒限速、缓存响应、保留 URL/UTC 抓取时间/原文哈希；404、超时、字段缺失均显式记 `missing`，不从排序开奖号码反推真实出球顺序，也不把未观测值填成正常状态。

## SSQ 与 DLT 的差异

| 数据族 | DLT | SSQ |
|---|---|---|
| 核心号码、期号、日期 | 当前 200 期可用 | 当前 200 期可用 |
| 销售额、奖池、中奖注数 | 当前已有 20 期；广东静态公告也证明字段可公开访问 | 当前没有；国家主源 403，获准省级页不含这些字段 |
| 球套编号 | 广东 DLT 公告已实证可采 | 无获准逐期来源 |
| 实际出球顺序 | 广东 DLT 公告已实证可采 | 无获准逐期来源 |
| 规则版本 | 官方 DLT 规则可立项做低频版本归档，本次探针超时 | 当前无获准可达规则档案，必须保持缺失 |
| 机器、试机、逐球质检、完整异常状态 | 无公开逐期台账 | 无公开逐期台账 |

因此，`TIME_RULE_REGIME`、`ENV_BALL_SET_ID` 和 `ENV_DRAW_POSITION` 的 `collectable_feasible` 判定都是“仅 DLT 可行”，不是对两个游戏的笼统承诺。若产品要求单一特征必须同时覆盖 SSQ 与 DLT，应在实施合同中拆成按游戏的数据覆盖状态，而不是把 SSQ 缺失伪装成可采。

## 不可行项的硬理由

- `ENV_MACHINE_ID`：流程公开不等于逐期机器唯一编号公开；已验证公告没有编号，也无获准内部设备日志。
- `ENV_PRETEST_STATUS`：官方材料可描述标准试机流程，但没有逐期次数、结果和异常类别台账。把制度默认值填进每期不是观测。
- `ENV_DRAW_ANOMALY`：个别异常事件公告只能给部分阳性，没有穷尽的逐期阴性清单。将“没搜到公告”编码成无异常会产生选择偏差。
- `ENV_BALL_MASS`、`ENV_BALL_DIMENSION`：官方可声明做质量和尺寸检测，但不公开 SSQ/DLT 每套、每号、每次校准的测量值；这些物理量不能从开奖号反演。
- `BEHAV_FORM_CENTER_DISTANCE`、`BEHAV_FORM_LINE_PATTERN`：当前无带渠道、版本和生效期的获准官方选号表坐标档案。自行假定网格会伪造票面特征；不同省份、终端和线上渠道也未证明共用版式。
- `BEHAV_NUMBER_POPULARITY_MAXENT`：销售总额和奖级中奖注数是高度聚合数据，不能识别每个号码或完整组合被购买的注数。最大熵只是额外模型假设，不会把不可识别的号码级投注分布变成真实观测。

这些结论针对当前公开与获准渠道。未来只有获得运营方正式数据授权、可审计的官方历史档案，才可重新评估；普通搜索结果、流程新闻或从开奖号反推都不是替代来源。

## 交付建议

- 直接进入基线候选：29 项 `computable_from_current`，但销售/奖池/中奖残差必须标记 DLT 20 期覆盖，其余按 SSQ/DLT 和前后区的结构适用性处理。先做简单、稳定、可解释的号码/时间统计；熵、变点等参数必须预注册。
- 单独立项采集：仅 DLT 的规则版本、球套编号和实际出球顺序。先做小样本覆盖率审计，再决定是否值得扩展；不承诺 SSQ。
- 当前放弃：8 项 `not_feasible`。在没有新授权或正式公开档案之前，不用猜测值、默认正常值或代理变量冒充真实观测。

诚实的产品结论是：这 40 项里大多数“能算”只提高了可研究的特征广度，并没有建立预测有效性；三项可采集字段也应先验证覆盖率、时点和增量价值。不可行项应明确放弃，而不是为了凑齐特征矩阵降低证据标准。
