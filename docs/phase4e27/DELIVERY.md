# Phase4E27：彩票预测可观测特征互联网研究

## 结论先行

本研究整理出 40 项可落到数据的可观测特征，覆盖 7 类。它们只是对历史结果、开奖过程、制度上下文或群体选号行为的**可观测代理**。文献中的使用场景包括描述、随机性审计、检测选号偏好和构造统计模型；“被提出或使用”不等于“预测有效”。**没有任何一项被证实能够预测下一期开奖，本报告也不作收益、lift 或中奖保证。**

尤其要区分两个对象：历史开奖号特征描述开奖输出；conscious-selection 特征描述彩民可能如何选号、从而影响中奖注数/分奖拥挤度。后者不是开奖生成机制。球重、尺寸、机器等字段也仅是审计资料明确测量的物理量；本报告不推理未知影响因素，更不从开奖号倒推物理状态。

机器可读的完整主表是 [`features.json`](../../artifacts/phase4e27/features.json)。下表与其逐项对应；JSON 保留完整 URL，表中用来源编号避免一行塞入多个长链接。

## 检索范围与方法

- 检索时间：2026-08-22；使用可用的互联网搜索/页面打开工具，实际返回并核验页面标题、摘要或正文。
- 中文检索覆盖“彩票预测特征、双色球选号特征、大乐透冷热遗漏、彩票开奖球套/机器/出球顺序/销售额、随机性检验、马尔可夫/熵”等组合；英文覆盖 “lottery prediction features / number selection / conscious selection / ball bias / machine audit / frequency gap / autocorrelation / entropy”。
- 优先级：同行评议论文及 DOI、作者/大学存档、政府和彩票机构规则/流程、标准和法规；搜索聚合页、新闻和论坛没有作为核心统计证据。
- 纳入标准：必须能说明输入数据和确定的计算；购买策略、倍投/轮盘/合买、不可观测心理状态均排除。资料只要明确提出、测量或使用该量即可列为 `documented`，不把它升级为有效性结论。
- 适配方式：一般 k/N 方法分别用于 SSQ 红/蓝区与 DLT 前/后区；单球区仍可算频数和上下文，但组合内部结构在单球区没有意义，因此部分条目标为 `front`。
- 证据档：`documented`=检索到资料明确提出/使用；`heuristic`=常见工程做法但本轮无严格出处；`hypothetical`=理论上可算但本轮未查到彩票实际使用。无出处项统一写“模型内部知识，无出处”。

## 来源清单

| 编号 | 来源与核验要点 |
|---|---|
| S01 | Joe, *Tests of uniformity for sets of lotto numbers*, DOI [10.1016/0167-7152(93)90141-5](https://doi.org/10.1016/0167-7152(93)90141-5)：k-tuples 的单号、号码对及三元组边际均匀性检验，并提醒普通独立格卡方公式不适用。 |
| S02 | Johnson & Klotz, *Estimating Hot Numbers and Testing Uniformity for the Lottery*, DOI [10.1080/01621459.1993.10476320](https://doi.org/10.1080/01621459.1993.10476320)：顺序无放回开奖下估计各号码概率及似然比检验。 |
| S03 | Haigh, *The Statistics of the National Lottery*, DOI [10.1111/1467-985X.00056](https://doi.org/10.1111/1467-985X.00056)：检验单号频率、连续出现等待时间，并讨论非均匀的玩家选号与模型局限。 |
| S04 | Coronel-Brizio et al., *Statistical auditing and randomness test of lotto k/N-type games*, DOI [10.1016/j.physa.2008.07.017](https://doi.org/10.1016/j.physa.2008.07.017)，[arXiv:0806.4595](https://arxiv.org/abs/0806.4595)：推导开奖号变量的理论均值/协方差用于审计。 |
| S05 | Boland & Pawitan, *Trying To Be Random in Selecting Numbers for Lotto*, DOI [10.1080/10691898.1999.12131278](https://doi.org/10.1080/10691898.1999.12131278)：用描述统计和拟合优度比较人工、真实开奖和模拟组合。 |
| S06 | Wang et al., *Number preferences in lotteries*, DOI [10.1017/S1930297500003089](https://doi.org/10.1017/S1930297500003089)，[SSRN 页面](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2657776)：约 3300 万选择号码，记录中心偏好、日期/个人意义号码、空间/数值图案及对近期开奖号的追逐/规避。 |
| S07 | Langer et al., *Is There a Gender Gap in the Birthday-Number Effect?*, DOI [10.1007/s10899-024-10288-5](https://doi.org/10.1007/s10899-024-10288-5)：彩票选号的 birthday-number effect。本文不使用性别这种 SSQ/DLT 开奖数据不可得属性。 |
| S08 | 韩国 6/45 时间序列统计论文，[KCI 条目及 DOI 10.47116/apjcri.2025.02.43](https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=ART003176560)：摘要列举连续号、奇偶、颜色/分段与和值等统计量。 |
| S09 | Wu, *Testing of the Randomness of the Lottery Winning Numbers and the Signed Lottery*, [大学论文 PDF](https://www.stat.nuk.edu.tw/huangwj/student-paper/94-01.pdf)：Pearson、KS、maximum entropy，并从中奖人数讨论玩家选号习惯。 |
| S10 | *A Randomness Check of The Lottery Data*, [Stanford 托管 PDF](https://ai.stanford.edu/~nlambert/papers/judgment_sep2018.pdf)：对真实彩票序列明确计算 lag autocorrelation。 |
| S11 | *Shannon Entropy and Beyond*, [Entropy 期刊页面](https://www.mdpi.com/1099-4300/28/6/695)：将 Shannon、Rényi、permutation、sample entropy 用于 2,538 期罗马尼亚 6/49 数据；也明确单一熵不足以证明随机。 |
| S12 | *Comprehensive Method for Measuring Randomness in Pseudorandom Generators*, [期刊正文](https://www.scielo.org.mx/scielo.php?pid=S1405-55462024000301155&script=sci_arttext)：列出 frequency、gap、run、serial、autocorrelation 等可计算随机性统计。这里仅将游程用于彩票输出的审计型特征。 |
| S13 | 中国福利彩票 [双色球第 2025104 期开奖公告](https://www.cwl.gov.cn/c/2025/09/09/627575.shtml)：实际公告含当期销售金额。 |
| S14 | 中国体育彩票 [超级大乐透游戏规则](https://m.lottery.gov.cn/ksjz/m/yxgz_dlt/)：号码域、开奖日、专用设备，以及公开销售总额、开奖号码、奖级中奖情况、奖池余额的要求。 |
| S15 | 国家体育总局体彩中心 [“超级大乐透”开奖流程](https://www.sport.gov.cn/cpzx/n5649/c663435/content.html)：抽取正式球套、固定装球顺序和逐个实际出球流程。 |
| S16 | 国家体育总局 [体彩开奖过程](https://www.sport.gov.cn/n20001280/n20745751/n20767297/c21153065/content.html)：设备保管/使用、球套抽取和双系统计奖核验。 |
| S17 | 国家体育总局 [“维纳斯”摇奖机](https://www.sport.gov.cn/n20001280/n20745751/n20767297/c21164292/content.html)：吹气式机器原理及每次开奖前两次试运行。 |
| S18 | 国家体育总局体彩中心 [第 22098 期设备异常说明](https://www.sport.gov.cn/cpzx/n5657/c24643964/content.html)：公开了中断位置、取球、试机及继续开奖过程。 |
| S19 | 青海福彩 [双色球摇奖用球选择](https://www.qhflcp.cn/news/NewsDetail.aspx?newsId=2734)：四套球及正选/备选球套抽取。 |
| S20 | 国家体育总局 [体彩品牌开放日](https://www.sport.gov.cn/n20001280/n20067608/n20067637/c27201503/content.html)：球材质、重量/尺寸检测、三套正式球。 |
| S21 | 爱尔兰国家标准局 [NSAI & National Lottery](https://www.nsai.ie/about/news/nsai-national-lottery-ensure-all-is-left-to-chance/)：逐球重量、尺寸测量及机器重复性/抽样测试。 |
| S22 | Florida draw procedures，[Cornell LII 法规镜像](https://www.law.cornell.edu/regulations/florida/Fla-Admin-Code-Ann-R-53ER21-67)：机器/球套选择、检查、球套称重和容差处置。 |

说明：S11 的页面日期处于本研究日附近；其价值只在“资料确实把四类熵算在彩票历史数据上”，不引用其分类准确率作为开奖预测证据。S12 是通用 PRNG 方法，不据此宣称彩票预测用途；它只支持游程等统计量可用于随机性审计。所有 URL 均来自本轮实际检索结果。

## 特征总表

字段约定：区域 `front/back/both`；彩种 `ssq/dlt`；可观测性为“可算”或“需额外采集”。“可算”表示项目既有官方历史/公告链通常足够，不代表任何时点都已入库。计算详情与边界条件以 JSON 同名字段为准。

| feature_id | 中文名 / English | 定义与计算方法 | 区域 | 彩种 | 可观测性 | evidence | 出处 |
|---|---|---|---|---|---|---|---|
| FREQ_ROLLING_RATE | 滚动出现率 / rolling occurrence rate | 最近固定 W 期 `出现次数/W`，按区分算。 | both | ssq,dlt | 可算 | documented | S02 |
| FREQ_WAITING_TIME | 距上次出现等待期数 / waiting time since last appearance | 从截点回溯最近出现，计中间完整未出现期；截尾另标。 | both | ssq,dlt | 可算 | documented | S03 |
| FREQ_UNIFORMITY_RESIDUAL | 边际均匀性标准化残差 / marginal uniformity standardized residual | k/N 理论均值协方差下 `(观测-期望)/理论标准差`。 | both | ssq,dlt | 可算 | documented | S01 |
| REL_PAIR_RATE | 号码对共现率 / pair co-occurrence rate | 固定窗 unordered pair 共现次数/期数，可减超几何期望。 | both | ssq,dlt | 可算 | documented | S01 |
| REL_TRIPLE_RATE | 三元组共现率 / triple co-occurrence rate | 固定窗 unordered triple 共现次数/期数。 | both | ssq,dlt | 可算 | documented | S01 |
| REL_PREVIOUS_OVERLAP | 与上期重号数 / overlap with previous draw | 候选集合与上期同区集合交集大小。 | both | ssq,dlt | 可算 | heuristic | 模型内部知识，无出处 |
| STRUCT_ODD_COUNT | 奇数个数 / odd-number count | 对组合中 `n mod 2` 求和。 | both | ssq,dlt | 可算 | documented | S08 |
| STRUCT_SUM | 号码和值 / number sum | 同区号码直接求和。 | both | ssq,dlt | 可算 | documented | S08 |
| STRUCT_RANGE | 跨度 / range | `max(C)-min(C)`。 | both | ssq,dlt | 可算 | documented | S05 |
| STRUCT_ADJACENT_PAIRS | 相邻连号对数 / adjacent consecutive pair count | 排序后统计相邻差为 1 的对数。 | both | ssq,dlt | 可算 | documented | S08 |
| STRUCT_GAP_VECTOR | 有序间距向量 / ordered spacing vector | 排序后逐项差 `(x2-x1,…,xk-x[k-1])`。 | both | ssq,dlt | 可算 | documented | S05 |
| STRUCT_BAND_COUNTS | 分段区间计数 / number-band counts | 按预注册数值区间或票面行分段计数。 | both | ssq,dlt | 可算 | documented | S06 |
| TIME_WEEKDAY | 开奖星期 / draw weekday | 官方日期按统一时区作 weekday one-hot/周期编码。 | both | ssq,dlt | 可算 | heuristic | 模型内部知识，无出处 |
| TIME_DAYS_SINCE_DRAW | 距上期开奖天数 / days since previous draw | 当前与上一期开奖日期的日历天数差。 | both | ssq,dlt | 可算 | hypothetical | 模型内部知识，无出处 |
| TIME_RULE_REGIME | 规则制度段 / game-rule regime | 以规则生效日建版本表并作 point-in-time join。 | both | ssq,dlt | 需额外采集 | documented | S14 |
| ENV_BALL_SET_ID | 摇奖球套标识 / ball-set identifier | 从日志/视频逐期转录实际球套；未知不能反推。 | both | ssq,dlt | 需额外采集 | documented | S15,S19 |
| ENV_MACHINE_ID | 摇奖机标识 / drawing-machine identifier | 记录设备编号或主/备机类别，缺失记 unknown。 | both | ssq,dlt | 需额外采集 | documented | S16 |
| ENV_DRAW_POSITION | 实际出球序位 / physical draw position | 从视频转录 number→position；不能用排序公告代替。 | both | ssq,dlt | 需额外采集 | documented | S15,S18 |
| ENV_PRETEST_STATUS | 试机状态 / pre-draw machine-test status | 采集试机次数及正常/异常类别。 | both | ssq,dlt | 需额外采集 | documented | S17 |
| ENV_DRAW_ANOMALY | 开奖设备异常标志 / draw-equipment anomaly flag | 编码中断/重启/备用及发生出球位。 | both | ssq,dlt | 需额外采集 | documented | S18 |
| ENV_BALL_MASS | 单球质量偏差 / individual ball mass deviation | 检验台账中 `mass_i-套均值` 或套内 z-score，不能从开奖号推测。 | both | ssq,dlt | 需额外采集 | documented | S21,S22 |
| ENV_BALL_DIMENSION | 单球尺寸偏差 / individual ball dimension deviation | 检验台账中直径相对套均值及圆度指标。 | both | ssq,dlt | 需额外采集 | documented | S20,S21 |
| CTX_SALES_AMOUNT | 当期销售额 / draw sales amount | 官方金额作 `log1p`/滚动标准化；截点未公开则只许滞后。 | both | ssq,dlt | 可算 | documented | S13,S14 |
| CTX_JACKPOT_BALANCE | 奖池余额 / jackpot pool balance | 取截点前最近公布余额，算 `log1p`/变化率。 | both | ssq,dlt | 可算 | documented | S14 |
| BEHAV_BIRTHDAY_COUNT | 生日范围号码数 / birthday-range number count | `sum(n<=31)`；月份范围可另计且须固定口径。 | front | ssq,dlt | 可算 | documented | S06,S07 |
| BEHAV_FORM_CENTER_DISTANCE | 票面中心距离 / choice-form center distance | 由票面号码坐标求到网格中心平均/最小距离。 | both | ssq,dlt | 需额外采集 | documented | S06 |
| BEHAV_FORM_LINE_PATTERN | 票面直线/几何图案分数 / choice-form line or geometric-pattern score | 票面坐标的最大共线点、邻接边或预注册模板匹配数。 | both | ssq,dlt | 需额外采集 | documented | S06 |
| BEHAV_ARITHMETIC_PATTERN | 等差/倍数图案分数 / arithmetic-sequence pattern score | 长度≥3 等差子集数/最大长度及预注册小整数整除计数。 | both | ssq,dlt | 可算 | documented | S06 |
| BEHAV_RECENT_WIN_OVERLAP | 近期开奖号追逐/规避重合度 / recent-winner chase/avoid overlap | `sum_l decay(l)*|C∩D[t-l]|`，窗和衰减预先固定。 | both | ssq,dlt | 可算 | documented | S06 |
| BEHAV_NUMBER_POPULARITY_MAXENT | 最大熵单号投注热度 / maximum-entropy number-choice popularity | 以历史中奖注数等约束拟合最大熵选号分布，输出单号边际；只用过去。 | both | ssq,dlt | 需额外采集 | documented | S03,S09 |
| BEHAV_WINNER_COUNT_RESIDUAL | 中奖注数超额残差 / winner-count excess residual | 销售注数与理论命中率给期望，算标准化残差；开奖后值只许滞后。 | both | ssq,dlt | 可算 | documented | S03,S14 |
| STAT_LAG_AUTOCORRELATION | 滞后自相关 / lagged autocorrelation | 对单号指示或固定结构统计量算预注册 lag-k ACF。 | both | ssq,dlt | 可算 | documented | S10 |
| STAT_MARKOV_TRANSITION | 号码状态转移概率 / number-state Markov transition probability | 每号 0/1 出现序列的平滑 `P(X_t=b|X[t-1]=a)`。 | both | ssq,dlt | 可算 | hypothetical | 模型内部知识，无出处 |
| STAT_SHANNON_ENTROPY | 窗口香农熵 / windowed Shannon entropy | `-sum p_i log2 p_i`，可除 `log2(N)`。 | both | ssq,dlt | 可算 | documented | S11 |
| STAT_RENYI_ENTROPY | 窗口 Rényi 熵 / windowed Renyi entropy | 固定 q，算 `log(sum p_i^q)/(1-q)` 并归一化。 | both | ssq,dlt | 可算 | documented | S11 |
| STAT_PERMUTATION_ENTROPY | 排列熵 / permutation entropy | 对不受公告排序污染的逐期标量，统计固定 m 序模式熵。 | both | ssq,dlt | 可算 | documented | S11 |
| STAT_SAMPLE_ENTROPY | 样本熵 / sample entropy | 固定 m、容差 r，算相似模板延伸匹配率的 `-log(A/B)`。 | both | ssq,dlt | 可算 | documented | S11 |
| STAT_HYPERGEOMETRIC_MAHALANOBIS | 超几何均值协方差审计距离 / hypergeometric mean-covariance audit distance | 经验与 k/N 理论均值差的协方差广义逆二次型。 | both | ssq,dlt | 可算 | documented | S04 |
| STAT_RUN_LENGTH | 二元出现序列游程长度 / binary occurrence run length | 每号 0/1 序列末端游程、窗内游程数及基线残差。 | both | ssq,dlt | 可算 | documented | S12 |
| STAT_CHANGE_POINT_SCORE | 分布变点分数 / distribution change-point score | 相邻历史窗平滑频率的 Jensen–Shannon divergence，过去数据校准阈值。 | both | ssq,dlt | 可算 | hypothetical | 模型内部知识，无出处 |

## 使用边界与不可观测项

1. 这些统计量首先适合描述、审计、漂移监测或受控的事后研究。若进入模型，必须 point-in-time 生成、滚动训练并与严格样本外基线比较；即便如此也不能称作已证实能预测开奖。
2. 公告中的号码常按大小排序。任何“出球顺序”、序列自相关或排列熵必须使用真实视频顺序或不会被排序机械制造结构的逐期标量。
3. 当期销售额、当期奖池、中奖注数和设备异常若在预测锁定时点之后才知道，是标签后信息；只能使用上一期或其他截点前已经公开的值。
4. “彩民心理”、个人生日、性别、幸运信念本身不是开奖数据可观测量，未收入特征。生日范围、票面图案等只是组合层代理。若没有真实逐票投注数据，不能声称测得个体偏好。
5. 球重/尺寸、机器 ID、球套 ID 在制度上被测量或记录，但公开逐期台账通常不可得，所以标为“需额外采集”。缺失时应保留 unknown/missing，不得以开奖号估算。
6. 多重检验、窗口/lag/熵参数的事后挑选会制造偶然模式。所有超参数应预注册，并用理论 k/N 模拟校准；审计拒绝或不拒绝随机性都不是下一期预测保证。
