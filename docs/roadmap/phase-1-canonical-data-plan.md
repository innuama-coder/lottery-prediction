# 阶段 1：规范数据与采集 CLI 交付计划

## 1. 阶段目标和边界

阶段 1 只交付三项成果：

1. 一份可执行的数据规格；
2. 一份符合该规格、可追溯到阶段 0 原始证据的 400 条基础数据；
3. 一个用单条处理链路完成 bootstrap、增量采集、离线重放、验证和原子发布的非交互式 CLI。

阶段 1 不做模型、特征、预测、全历史回填、WebUI、调度平台或数据库服务。`GO` 只表示数据层能够可信、重复地交付规范开奖记录，不表示彩票存在可预测规律。

## 2. 已冻结的真实输入

基础数据唯一输入为阶段 0 活动快照 `20260802T025000Z`：

- canonical 记录 400 条，双色球和大乐透各 200 条；
- `(game, issue_id)` 唯一数为 400；
- 每条 canonical 记录恰好包含两个不同发布主体和两个 raw 证据引用；
- 双色球范围为 `2025037` 至 `2026085`；
- 大乐透范围为 `2025034` 至 `2026083`；
- 大乐透 `2026026`、`2026027` 在东方财富普通历史页缺失，由一定牛与广东体彩静态公告一致补证；
- 阶段 0 报告、采集 manifest、raw、parsed、reconciliation 和离线验证均已保存。

“两个发布主体一致”只用于发现发布或解析错误，不代表两个独立开奖过程。阶段 0 数据属于回溯整理后的当前事实视图，统一标记为 `retrospective_current_view`。

## 3. 三个工作和交付目录

| 工作 | 人话目标 | 核心交付物 | 完成门 |
| --- | --- | --- | --- |
| W1 数据规格 | 把字段、证据、状态、哈希和修订规则写成机器能检查的合同 | 规格文档、6 个 Schema、正反例、哈希测试向量 | G1 |
| W2 基础数据 | 用正式 CLI 将阶段 0 证据转换成 400 条规范数据 | 400 条 DrawRecord、800 条 SourceObservation、manifest、质量报告、哈希 | G2 |
| W3 采集工作流 | 用一个 CLI 完成采集、核对、发布、失败留证和重放 | Python 包、配置、打包入口、测试、run/release/验收报告 | G3 |

最终目录：

```text
docs/data/
  lottery-data-spec-v1.md

schemas/phase1/
  source-observation.schema.json
  draw-record.schema.json
  dataset-release.schema.json
  run-manifest.schema.json
  run-event.schema.json
  run-result.schema.json

pyproject.toml
requirements.lock

src/lottery_data/
  __main__.py
  cli.py
  models.py
  serialization.py
  workflow.py
  acceptance.py
  steps/
    preflight.py
    fetch.py
    snapshot.py
    parse.py
    validate.py
    reconcile.py
    normalize.py
    quality_gate.py
    publish.py
    report.py

config/phase1/
  source-catalog.json
  collection-policy.json
  live-source-policy.json

tests/phase1/
  fixtures/spec/valid/
  fixtures/spec/invalid/
  fixtures/spec/hash-vectors.json
  fixtures/real/
  test_specification.py
  test_baseline.py
  test_workflow_unit.py
  test_workflow_e2e.py
  run_acceptance.py

artifacts/phase-1/
  baseline-v1/
  runs/<run_id>/
  releases/<release_id>/
  acceptance/phase1-acceptance.json
  reviews/data-review.json
  reviews/workflow-review.json
  current-release.json
```

JSONL 是阶段 1 的规范事实载体。数据库、Parquet 或 DuckDB 可以在后续作为派生格式，但不得成为阶段 1 唯一事实源。

## 4. W1：可执行数据规格

### 4.1 六个对象必须全部定义和验收

#### A. `SourceObservation`

表示某个来源页面中实际解析到的一期开奖事实。

必需字段：

- `observation_schema_version`
- `observation_id`
- `source_id`
- `publisher_id`
- `game`
- `raw_issue_id`
- `issue_id`
- `draw_date_local`
- `front_numbers`
- `back_numbers`
- `source_url`
- `captured_at_utc`
- `raw_ref`
- `raw_sha256`
- `parser_id`
- `parser_version`
- `core_fact_profile`
- `core_fact_sha256`
- `parse_status`

`observation_id` 由 `source_id + game + issue_id + raw_sha256 + parser_version` 的规范投影计算，不能使用随机 UUID。

#### B. `DrawRecord`

表示通过多来源核对后可发布的规范开奖记录。

必需字段：

- `record_schema_version`
- `game`
- `issue_id`
- `draw_date_local`
- `front_numbers`
- `back_numbers`
- `status`，发布数据只能为 `verified`
- `core_fact_profile`
- `core_fact_sha256`
- `evidence_links`
- `revision_id`
- `supersedes_revision_id`，首次发布为空
- `knowledge_class`
- `available_at_utc`，无可靠时间证据时为空

`evidence_links` 是对象数组，每项同时包含 `source_id`、`publisher_id`、`observation_id`、`raw_ref` 和 `raw_sha256`。不得用两个互不约束的平行数组表达来源与证据关系。

#### C. `DatasetRelease`

表示已经成功发布的不可变数据快照。

必需字段：

- `release_schema_version`
- `release_id`
- `created_at_utc`
- `previous_release_id`
- `input_run_id`
- `record_count_by_game`
- `observation_count`
- `input_manifest_sha256`
- `schema_bundle_sha256`
- `pipeline_bundle_sha256`
- `records_sha256`
- `observations_sha256`
- `quality_report_ref`
- `status`，唯一允许值为 `published`

失败或拒绝只属于 `RunResult`，不得创建 `DatasetRelease`。

#### D. `RunManifest`

冻结一次运行的输入和执行环境，至少包含：

- `run_schema_version`、`run_id`、`mode`、`source_mode`；
- `started_at_utc`、`artifacts_root`、`previous_release_id`；
- games、请求计划、配置引用及哈希；
- Schema bundle、pipeline bundle、Python 版本；
- bootstrap 快照或 incremental watermark；
- publish policy 和 replay_of_run_id。

manifest 只保存 preflight 时已经确定的静态输入并一次写入；运行中的动态统计只进入 events 和 RunResult，不得回写 manifest。

#### E. `RunEvent`

表示 run 和 request 的持久化状态变化。至少包含：

- `event_schema_version`、`event_id`、`run_id`、`sequence`；
- `event_type`、`occurred_at_utc`、`request_id`（可空）、`attempt`（请求事件必需）；
- `source_id`、`game`、`error_code`、`error_detail_ref`（按事件需要）；

`events.jsonl` 只能追加，并由 `hashes.json` 记录最终文件哈希。每个网络 attempt 必须恰好有一个 `request_started`，该事件必须在网络调用前写入并刷新到文件；随后恰好有一个同 request/attempt 的 `request_succeeded` 或 `request_failed`。base/snapshot v1 与历史 live v1.1/v1.2 的 attempt 固定为 1；current live v1.3 允许连续的 attempt 1..2，attempt 2 只能跟在批准的 retryable attempt-1 failure 后。未开始的逻辑请求由 run 终止事件解释，不能事后伪造 request 事件。

#### F. `RunResult`

表示一次运行的最终判定，至少包含：

- `result_schema_version`、`run_id`、`mode`；
- `status`: `published | no_change | rejected | interrupted`；
- 开始和结束时间、请求/观察/候选统计；
- added、revised、unchanged、conflict、invalid 统计；
- `exit_code`、`release_id`（未发布为空）；
- manifest、events、quality report 和错误证据引用；
- deterministic artifact hashes。

### 4.2 彩票硬约束

| 游戏 | 前区 | 后区 | 单式组合总数 |
| --- | --- | --- | ---: |
| 双色球 | 1–33 选 6 | 1–16 选 1 | 17,721,088 |
| 大乐透 | 1–35 选 5 | 1–12 选 2 | 21,425,712 |

号码必须是整数、数量固定、区间合法、区内唯一且严格升序。`issue_id` 为七位字符串 `YYYYNNN`；同一游戏内唯一。补零只属于显示层。

### 4.3 规范序列化和哈希合同

所有哈希使用 SHA-256。规范 JSON 字节规则固定为：

1. UTF-8、无 BOM；
2. `ensure_ascii=false`；
3. 对象键按 Unicode 码点升序；
4. 分隔符为 `,` 和 `:`，不含多余空格；
5. 每个 JSON/JSONL 对象末尾恰好一个 `LF`；
6. JSONL 按规格定义的稳定键排序，不依赖文件系统或抓取完成顺序。

`phase0-core-fact-v1` 投影必须与阶段 0 实际算法完全兼容：

```json
{
  "game": "<game>",
  "issue_id": "<issue_id>",
  "draw_date": "<draw_date_local>",
  "front_numbers": [],
  "back_numbers": []
}
```

对上述对象按规范 JSON 字节规则序列化后计算 SHA-256。`draw_date_local` 只在投影时映射为阶段 0 字段名 `draw_date`。`hash-vectors.json` 必须包含阶段 0 的真实 SSQ、普通 DLT、补证 DLT 三类向量。

JSONL 排序规则：

- observations：`game, issue_id, publisher_id, source_id, observation_id` 升序；
- reconciliation：`game, issue_id` 升序；
- draws：`game, issue_id, revision_id` 升序。

`records_sha256` 和 `observations_sha256` 是最终规范 JSONL 文件的字节哈希。`schema_bundle_sha256` 和 `pipeline_bundle_sha256` 先生成“相对路径 + 文件 SHA-256”的有序 manifest，再对该 manifest 的规范字节计算。

replay 比较分两类：

- 必须字节一致：observations、reconciliation、candidate draws、quality report 中的 deterministic 区域；
- 不要求字节一致：run_id、replay run 的开始/结束时间、事件时间和 replay 自身 manifest；这些字段通过语义断言比较。

禁止再使用“整个 run bundle 字节一致”作为验收语句。

### 4.4 状态与修订规则

run 合法状态为：

```text
planned -> running -> published
planned -> running -> no_change
planned -> running -> rejected
planned -> running -> interrupted
```

禁止从终态再次转换。每次终态必须恰好对应一个 `RunResult`。

修订规则：

- 已发布记录不得原地覆盖；
- 两个不同发布主体对新事实一致后才能生成新 revision；
- 新 revision 引用 `supersedes_revision_id`；
- 旧 revision 保留在历史 release 中；
- 当前 release 的 `draws.jsonl` 是当前有效视图，manifest 保留完整前序 release 链；
- 单源变化或来源异议进入 rejected run，不改变 current release。

### 4.5 W1 验收与 G1

```text
python tests/phase1/run_acceptance.py \
  --contract docs/roadmap/phase-1-acceptance-contract.json \
  --gate G1 \
  --output artifacts/phase-1/acceptance/g1.json
```

G1 通过条件：

- 六个 Schema 全部通过 meta-schema 和实例测试；
- 每个对象至少一个真实合法样例；
- 缺字段、类型错误、号码越界/重复、非法状态转换、事件终态重复、证据链断裂和错误哈希均有反例；
- 三类真实 hash vector 与阶段 0 完全一致；
- 文档、六个 Schema 和 hash vectors 的哈希被冻结。

G1 未通过不得生成 `baseline-v1`。

## 5. W2：400 条基础数据

### 5.1 唯一构建路径

W2 不允许一次性转换脚本，必须调用 W3 的 bootstrap 核心链路：

```text
python -m lottery_data run \
  --mode bootstrap \
  --source-mode snapshot \
  --phase0-snapshot 20260802T025000Z \
  --run-id p1-baseline-v1 \
  --release-id baseline-v1 \
  --artifacts-root artifacts/phase-1

python -m lottery_data verify \
  --release-id baseline-v1 \
  --artifacts-root artifacts/phase-1
```

### 5.2 800 条观察的选择规则

对阶段 0 每条 canonical 记录：

1. Phase 1 parser 重新解析阶段 0 保存的真实 raw，不能把阶段 0 parsed 文件直接改名作为交付；
2. 阶段 0 parsed observation 只作为 oracle，用来检查 Phase 1 parser 是否得到相同 issue、日期、号码和 raw 引用；
3. 按 canonical 中的两个 `source_ids` 和两个 `evidence_refs` 定位 Phase 1 observation；
4. 对应 `(game, issue_id, source_id, raw_ref)` 必须唯一；
5. `publisher_id` 从冻结的 source catalog 映射；`raw_issue_id` 保存 parser 看到的原字符串；`parser_id` 和 `parser_version` 指向 Phase 1 实际 parser；
6. 两个 observation 的 `publisher_id` 必须不同；
7. 两个 observation 的 `core_fact_sha256` 必须等于 canonical hash；
8. 缺少、重复、oracle 不一致或映射不唯一均拒绝整个 bootstrap。

run 目录保存 Phase 1 parser 从请求页面解析出的全部 observation；release 的 `observations.jsonl` 只保存被选为 400 条 DrawRecord 发布证据的 800 条 observation。它不是阶段 0 所有 parsed 行的复制。

### 5.3 W2 交付物

```text
artifacts/phase-1/baseline-v1/
  draws.jsonl
  observations.jsonl
  manifest.json
  quality-report.json
  hashes.json
```

### 5.4 W2 验收与 G2

G2 必须同时满足：

- DrawRecord 恰好 400，SSQ/DLT 各 200；
- SourceObservation 恰好 800；
- 400 个 `(game, issue_id)` 唯一；
- 每条 DrawRecord 有两个不同 publisher 的结构化 evidence link；
- core fact、raw ref、raw hash 与阶段 0 逐条一致；
- invalid、missing、duplicate、conflict、manual_core_edit 均为 0；
- 在第二个空 artifacts root 中用相同输入独立 bootstrap，draws/observations 规范文件哈希不变；
- 在首个临时 root 上用相同 snapshot 执行 incremental，结果为 `no_change` 且不创建 release；
- verify 能从 DrawRecord 逐级回到 observation、raw 和 capture manifest。

统一验收由机器合同驱动：

```text
python tests/phase1/run_acceptance.py \
  --contract docs/roadmap/phase-1-acceptance-contract.json \
  --gate G2 \
  --output artifacts/phase-1/acceptance/g2.json
```

## 6. W3：采集工作流

### 6.1 交付形式和运行环境

交付形式为 Python 3.12 非交互式 CLI。以下两个入口必须调用同一函数：

```text
python -m lottery_data ...
lottery-data ...
```

`pyproject.toml` 必须声明 Python 版本、console script、运行依赖和测试依赖；实际解析得到的依赖版本写入 `requirements.lock`。缺少打包或锁定证据时 G3 不通过。

验收环境先按 `requirements.lock` 安装锁定依赖，再以 editable 模式安装本项目；安装步骤由机器合同声明，不能依赖调用者手工设置 `PYTHONPATH`。

公开命令：

```text
lottery-data run --mode bootstrap|incremental ...
lottery-data replay --run-id <run_id> --offline ...
lottery-data verify --release-id <release_id> ...
```

`run` 公共参数：

- `--mode bootstrap|incremental`
- `--source-mode live|snapshot`
- `--games ssq,dlt`
- `--run-id`
- `--release-id`
- `--phase0-snapshot`，bootstrap 必需
- `--snapshot-root`，snapshot 模式可显式覆盖输入目录
- `--artifacts-root`，所有运行和指针只能写入该根目录
- `--config-root`，默认 `config/phase1`

snapshot 模式禁止网络；live 模式禁止用旧缓存冒充本次成功。未知参数、缺少参数或非法组合退出 4，且不得创建 run/release。

### 6.2 单条处理链路

```text
preflight
-> request plan
-> fetch or load snapshot
-> persist raw and request events
-> parse SourceObservation
-> validate Schema and game rules
-> reconcile publishers
-> normalize new/revised DrawRecord
-> quality gate
-> acquire publish lock and compare previous release
-> publish atomically or reject
-> write RunResult
```

bootstrap 和 incremental 只能在“请求计划/输入选择”上不同；parse 之后必须使用相同实现。replay 从保存的 raw 和 manifest 重新进入 parse，禁止另建第二条处理管线。

### 6.3 incremental 的确定规则

`collection-policy.json` 必须至少冻结以下字段：

- `recheck_published_issues: 20`；
- 普通历史来源每个游戏 `max_pages_per_run: 3`；
- snapshot/base `collection-policy.json` 冻结 `request_timeout_seconds: 30`、`max_attempts_per_request: 1`、`inter_request_delay_seconds: 0.75`；current live v1.3 独立冻结单 attempt timeout 30 秒、最多 2 个 attempts、固定 retry backoff 2 秒；
- issue-specific fallback allowlist；
- 普通 issue 的有序 source pair 为 `ydniu + eastmoney`；已批准缺期的 source pair 为 `ydniu + gdlottery`，且两者的 `publisher_id` 必须不同；
- source policy review date和适用范围；
- `on_source_failure: reject_run`；
- `on_unresolved_issue: reject_run`。

增量算法：

1. 从 current release 读取每个游戏最近 20 个已发布 issue，形成 recheck window；
2. 各来源从最新普通页面开始顺序请求，直到覆盖该 window 或达到配置页数上限；
3. 候选集合为“current release 中不存在的 observed issue”加“recheck window 中事实发生变化的 issue”；
4. 不根据开奖日历猜测尚未被任何来源观察到的 issue；
5. 如果一个较新 issue 已被观察，而同年编号区间出现内部缺口，则把缺口记录为 unresolved，不得静默跳过；
6. 新 issue 或修订只有在两个不同 publisher 的 core fact 完全一致时通过；
7. release 每条 DrawRecord 恰好选择两个 evidence link：按 collection policy 中的有序 source pair 选择，并验证 publisher 不同；其他一致观察保留在 run/reconciliation，任何额外来源异议仍阻断发布；
8. 普通来源缺失时只能使用 source catalog 中预先批准的显式 fallback；
9. `2026026`、`2026027` 的广东体彩公告是已批准的 issue-specific fallback；这不自动授权推导未来公告 URL；
10. 未来缺口没有已批准 fallback 时 run 为 rejected/HOLD，先更新并审查 source catalog，不能临时抓取未知来源；
11. 所有计划请求成功且无新事实时为 `no_change`；
12. snapshot/base 网络失败直接退出 3；current live v1.3 只有 attempt 1 的 `dns_timeout_tls_or_required_source_unavailable` 可在固定等待 2 秒后进入唯一 attempt 2，attempt 2 仍失败才退出 3；HTTP、认证、解析、数据或冲突等非 retryable failure 不进入 attempt 2，current release 始终不变。

20 期回看是阶段 1 的明确运行窗口，用于检查近期更正。阶段 0 真实快照显示一定牛普通页每页 30 条、东方财富普通页每页 50 条，因此 20 期在两个普通来源中都能由一页覆盖；最多三页为页面容量变化或跨页边界保留余量。更早修订不保证被自动发现，属于已记录限制，而不是虚假承诺。base/snapshot 与历史 live v1.1/v1.2 不自动重试；current live v1.3 只对唯一批准的暂态网络类别做一次固定 2 秒重试，不支持通用 retry、指数退避、HTTP retry 或跨 run 隐式续试。

阶段 0 的来源批准不得自动继承。`source-catalog.json` 必须重新记录“阶段 1 内部研究采集”的审查日期、页面范围、robots/条款证据和批准结论；任何生产服务或数据再分发仍需另行审查，不属于本阶段。

### 6.3.1 Snapshot 与 live 来源政策分离（合同 3.6.0）

阶段 1 保留两套用途不同且不得互相覆盖的来源政策：

- `source-catalog.json` 与 `collection-policy.json` 继续只服务于冻结的 Phase 0 snapshot、`baseline-v1`、G1 和 G2。普通来源组合仍为 `ydniu + eastmoney`，已有 fallback、baseline ID、400/800 数据及其哈希语义不变。
- `live-source-policy.json` 只服务于 `mode=incremental` 且 `source-mode=live`。`bootstrap + live` 是非法组合；live 配置不能重解释或替换 snapshot 证据。
- live SSQ 使用两个不同 publisher：`ydniu + swlc`；live DLT 使用 `ydniu + gdlottery`。任一必需来源缺失、解析失败或核心事实不一致，整次 run 拒绝且不得发布。

current live 请求计划只能来自政策中冻结的四个精确静态地址；发现链接、动态 child、开奖日历推断、预期期号和未来 URL 均不得进入计划：

- ydniu 仅请求两个第一页地址：`https://www.ydniu.com/open/ssq-500/1.html` 与 `https://www.ydniu.com/open/dlt-500/1.html`；
- swlc 仅请求 `https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30`，参数、顺序和 `limit` 均不得改变；
- gdlottery 仅请求无 query 的精确 JSON 地址 `https://www.gdlottery.cn/f_html/kjgg/gameNumber.json`，固定 `request_kind=history`、`parser_id=phase1-gdlottery-history-parser`、`parser_version=1.0.0`；时间戳 cache-buster 和任意 query 均禁止。公告页/PDF 仍可作为人工来源审查材料，但不进入 current 请求计划，也不能计为第二 publisher。

所有 live 来源仅批准用于低频内部研究 raw 留证：生产采集与再分发均为 `false`。全局网络约束为 HTTPS/GET、禁止认证和 cookie、跨进程同 host 最小间隔 2 秒、单 attempt 超时 30 秒、每逻辑请求最多 2 个 attempts、固定 retry backoff 2 秒、最多 3 次且仅同源跳转。host throttle、retry backoff 和 request timeout 是三个独立约束。默认响应上限为 1 MiB；只有上述精确 GD JSON 端点因已观测真实响应大小而使用 2 MiB，上限不得按 source 或 media type 扩散到其他请求。raw 必须以内容寻址路径先落盘并完成 SHA-256 闭包，之后才能进入 parser；旧缓存不能冒充本次 live 成功。

本轮人工审查有效期截至 `2026-08-16`。该日期是项目主动设置的再审门，不是来源稳定性声明；超过有效期时，产品 CLI/preflight 以底层 exit 4 结束，并且不创建 request、run 或 release。E2E-05 与 G3 必须在报告中保留 `underlying_exit_code=4`，将结论映射为 `HOLD`，验收 runner 返回 exit 20；禁止自动延长日期。配置文件的合同 SHA-256 必须在请求计划生成前验证，并写入验收报告；只有已创建 run 的运行期检查才写入 run manifest。

失败处理必须按发生阶段区分。策略过期、配置哈希或端点配置违规属于 preflight 失败，不得伪造 request/run 事件。每个 runtime failure 必须为既有 `request_started` 追加且仅追加一个同 attempt 的 `request_failed`。只有 attempt 1 的 DNS/timeout/TLS/必需来源暂时不可用可在固定 2 秒后进入 attempt 2；同源跳转违规、认证挑战、HTTP/响应过大、解析失败、期号异常、publisher 冲突等非 retryable failure，以及 attempt 2 的失败，都立即拒绝 run，不创建 release，并保持 current pointer 不变。不得用一个统一 effect 同时覆盖 preflight 与 runtime。

上述限制由 G3 与 E2E-05 验证，不改变已经通过的 G1/G2 baseline 语义、ID 或数据哈希。

### 6.3.2 Current live 执行档案 v1.3：四个静态 history 请求

live incremental 的 manifest 是一次写入、不可回写的执行输入，只列出四个启动前确定的 GET history：易得牛双色球、上海福彩双色球、易得牛大乐透和广东体彩 `gameNumber.json` 大乐透历史。每项冻结完整 request identity、`request_kind`、`parser_id`、`parser_version` 与响应 profile；GET 不伪造 `input_ref`。current manifest schema 为 `run-manifest-v1.3.schema.json`，SHA-256 为 `55919a70ab5f9870419dccf5e1ba8da838414b1dc2a307c66c46a8a018c9c4f2`；event schema 为 `run-event-v1.3.schema.json`，SHA-256 为 `e2224d3d803e1d055b2a4d0e54b078515c752e973936921157a531ded885751e`。

v1.3 不表示 `request_discovered`、`child_authorization`、parent/discovery identity 或 announcement child，任何第五逻辑请求都必须拒绝。每个 attempt 先写并刷盘 `request_started`，再由同 request/attempt 的唯一成功或失败事件关闭。网络成功后将响应持久化为 `raw/<source>/<game>/sha256/<sha256>.raw`；文件 SHA-256 必须等于路径摘要和 parser provenance，完成这些检查后才能调用 parser。

有效请求计划在前缀和终态中恒为四项。每个逻辑请求允许 1–2 个连续 attempts；attempt 2 只能跟在可重试 attempt-1 failure 后。`run_published` 或 `run_no_change` 只在 `planned=started=succeeded=4` 且 `failed=not_started=0` 时成立，这些统计按逻辑请求而不是 attempt 计数。`run_rejected` 或 `run_interrupted` 可以在部分请求未开始时结束，但 `planned` 仍为 4，且所有 started attempts 必须各有唯一 succeeded/failed 终态。run 终态与 `RunResult` 一一对应：published 对应 `published/0/非空 release_id`，no-change 对应 `no_change/0/null`，rejected 对应 `rejected/非零/null`，interrupted recovery 可对应 `interrupted/null/null`；run_id、mode 和请求统计必须与事件流完全一致。

verify、recovery 和 replay 只根据显式 manifest schema version 与匹配的 frozen policy SHA 分派。current v1.3、historical v1.2 与 legacy v1.1 三档互斥，禁止根据请求字段形状猜 profile。historical v1.2 固定 policy SHA `23b7fc1bd1d5d7518b345ee92dd8fd7a3172305b7478fe82175d7af38aa80a1b`、manifest Schema SHA `2da08e2445e8e495da891127d3a172a074610000335257185ef97d5592d8fb11`、event Schema SHA `11f510b158fa7c67267ee15d51af9060347179d8af1045180d408abc05cea14d`，attempt 固定为 1。legacy v1.1 保留 policy SHA `442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1`、manifest Schema SHA `b5cc83086b4c3dbbce35ebd20373302fac36f7b3e3800c4759d04610baebd673` 和 event Schema SHA `cff590d5b0dec8a4bc28ad3eb6c2b86bc56ca903978b362d56524ce2b7353c73`，attempt 同样固定为 1。两者只用于历史兼容，不授权 current live，也不得升级解释为 v1.3。

manifest 的磁盘实现必须调用 `write_once_json`：目标已存在时失败，不得覆盖或回写。对象语义由本档案测试验收，真实磁盘 write-once 行为留在 workflow 集成测试验收。

### 6.3.3 Live latest-20 复查的可交付边界

latest-20 仍按游戏分别计算，但窗口取 current issue 与本轮 observed issue 的并集后再取最新 20 个。任何新 observed issue 都必须处理；因此当大乐透出现 1 个新期时，窗口是“新期 + 19 个旧期”，而不是旧 release 的 20 期再额外加新期。

新期必须取得策略规定的两个不同 publisher 观察，否则 `unresolved` 并拒绝发布。旧期取得完整 pair 时，照常判定 unchanged、revised 或 conflict；完整 pair 之外出现 dissent 仍阻断。旧期 pair 不完整时分两种情况：如果本轮所有可用观察的 core 都等于 current DrawRecord，保留旧 draw 和旧 evidence，reconciliation 写 `RECHECK_DEFERRED_MISSING_PARTNER`，只增加 `recheck_deferred`，不增加 unresolved、不阻断，但严禁称该期复查 complete；如果任一可用观察与 current core 不同，则写 `RECHECK_UNCONFIRMED_CHANGE`、增加 unresolved 并拒绝发布，绝不凭单边观察修订 draw。质量报告同时给出 `recheck_attempted`、`recheck_complete`、`recheck_deferred`。原有 observed bounded same-year gap 规则不变。

冻结的真实 GD JSON fixture 为 `tests/phase1/fixtures/real/gd-game-number-history-20260803.json`：SHA-256 `dae5c9e0f33cfc09e8b245e9f093bfeaf115ed9383c673dd78ef08f34c98b5ac`、`1828447` bytes、顶层 `14091` 条、其中严格 `085_` identity `1276` 条。确定性 parser 输出最新 20 期 `2026067..2026086`，并闭合 `26084→2026084`、`26085→2026085`、`26086→2026086`。所有顶层 identity key 严格校验；所有 085 记录（包括 latest20 之外的旧记录）完整严格校验；非 085 record body 不按大乐透合同解释。

合同升级为 3.6.0 后，G1/G2 的 normalized data、baseline ID 与制品字节不变，但旧合同签名不能自动代表 3.6.0；进入 G3/final acceptance 前必须对 G1/G2 按本合同重新签署。原 base v1 数据规格与 G1 freeze 成员保持原字节；current live 由规范索引和 v1.3 扩展规格约束。只有按合同顺序重新签署并生成合同要求的最终报告后，才能宣称阶段 1 通过。

preflight 在 run 创建前失败时只输出非持久 preflight 结果 envelope，不创建 run 目录，也不写或伪造 `manifest_ref/events_ref/quality_report_ref`。这不是持久 `RunResult v1`。replay 保持 `read_only_without_lock`：开始前冻结并哈希 manifest/raw inventory，结束前重新枚举和哈希；期间禁止网络和发布，任何并发新增、删除或字节变化都以 exit 5 失败且不发布。

### 6.4 run、release 和失败证据

每次 run 都交付：

```text
artifacts/phase-1/runs/<run_id>/
  run-manifest.json
  events.jsonl
  raw/<source>/<game>/...
  observations/<source>-<game>.jsonl
  reconciliation.jsonl
  candidate-draws.jsonl
  quality-report.json
  run-result.json
  hashes.json
```

只有 published run 才创建：

```text
artifacts/phase-1/releases/<release_id>/
  draws.jsonl
  observations.jsonl
  manifest.json
  quality-report.json
  hashes.json
```

rejected/interrupted run 保留 manifest、events、已取得的 raw、错误详情和 RunResult，但不创建 release。

stdout 只输出一个 RunResult JSON；诊断写 stderr 和 `events.jsonl`。

退出码：

| 码 | 含义 | 发布 |
| ---: | --- | --- |
| 0 | published 或 no_change，由 RunResult.status 区分 | published 时是；no_change 保持当前 |
| 2 | 解析、Schema、数据质量或来源冲突 | 否 |
| 3 | 网络或必需来源不可用 | 否 |
| 4 | 参数、配置、Schema 定义或来源策略错误 | 否 |
| 5 | raw、文件哈希或 replay 不一致 | 否 |
| 6 | 发布锁冲突或 current release 已被其他 run 更新 | 否 |
| 10 | 未分类程序错误 | 否 |

### 6.5 并发、崩溃和原子发布

- 写模式 run 使用 `artifacts/phase-1/.publish.lock` 排他锁；verify/replay 可只读并行；
- preflight 记录 `previous_release_id`，发布前再次比较；变化时退出 6；
- release 先写同一文件系统中的临时目录，关闭文件、重算哈希并 verify 后再改名为最终目录；
- current pointer 先写临时文件，再使用原子替换；
- 指针替换前崩溃时 current release 不变；
- 启动时将失去锁且未终结的旧 run 标记为 interrupted；对已 started 但没有同 request/attempt 终态的 attempt 追加 recovery `request_failed` 事件，恢复不得补造 attempt 2，并把临时 release 移入该 run 的 recovery 证据目录；
- 已存在的 run_id 不得覆盖；已存在的 release_id 只有在内容已发布且请求是 verify 时可读取，写模式复用均退出 4；
- 发布锁竞争或 compare-and-swap 失败必须保留 run 证据，但不能重试覆盖新 release。

## 7. 确定性真实端到端验收

除 live smoke 外，所有 E2E 使用临时 `--artifacts-root`，不得读取或修改正式 `artifacts/phase-1/current-release.json`。测试输入只使用真实保存的 raw；合成修改只用于故障副本。

### E2E-01：真实 400 条 bootstrap

使用阶段 0 快照，在临时 artifacts root 生成 baseline。断言 exit 0、400/800、200+200、所有证据和 hash 一致，并成功发布。

### E2E-02：固定输入 no-change

先在第二个空临时 root 中对相同输入独立 bootstrap，断言两个 root 的 draws/observations 规范文件哈希一致；再在 E2E-01 的首个临时 root 上，以 `source-mode=snapshot` 执行 incremental。断言 exit 0、status=no_change、added/revised/duplicate/conflict 均为 0、current release 和数据哈希不变。禁止把 live 页面用于本用例。

### E2E-03：真实历史期模拟新期

测试准备程序从 baseline 副本中移除一条真实 issue，但不修改 raw。incremental 使用包含该 issue 的两份真实 raw。断言只新增该一条、旧 release 不变、第二次运行 no_change。测试 seed、目标 issue 和预期 hash 固定在 `tests/phase1/fixtures/real/`。

### E2E-04：真实缺期补证

用冻结 raw 验证 DLT `2026026`、`2026027`：东方财富为 missing，一定牛与广东体彩公告一致。断言两期均被发布，reconciliation 记录 missing、fallback rule id、两个 URL 和选择原因。

### E2E-05：真实在线访问冒烟

测试 runner 复制 baseline 到独立临时 root，再以 live incremental 执行 current v1.3 的四个静态 history 请求。E2E-05 机械验证 policy SHA、四请求身份、无 discovery/child、raw-before-parse、latest-20 规则、完整成功统计与 formal 前后一致。完整成功只能是 published/no_change、exit 0、`planned=started=succeeded=4` 且 `failed=not_started=0`；网络或策略环境失败保留底层 exit 3/4，映射为 `HOLD`，验收 runner 返回 20。公网 smoke 不保证实际触发 retry，因此暂态失败后成功、两次耗尽及非 retryable 不重试由独立确定性 v1.3 workflow/event 测试验收。不能伪报 PASS，也不能影响已通过的确定性测试；阶段 1 最终 GO 前仍必须有一次当次验收会话中的 PASS。

### E2E-06：真实 raw 离线 replay

对 E2E-01 的 run 执行 `replay --offline`。测试进程禁止网络，断言 observations、reconciliation、candidate 的规范文件哈希一致，质量判定一致，允许 replay 自身的时间和 run_id 不同。

### E2E-07：故障与并发拒绝发布

在真实 raw 的临时副本中分别注入一个号码差异、截断 HTML、错误编码和错误 raw hash；另注入网络失败、非法配置、锁竞争、CAS 失败和模拟崩溃。重试专项确定性测试拆分为：attempt 1 暂态失败后 attempt 2 成功、两次暂态失败耗尽、非 retryable failure 后不存在 attempt 2，并断言固定退避、attempt 级事件闭包与逻辑 request_stats。逐项断言退出码、错误证据、无错误 release、current pointer 不变，并验证下一次启动能识别 interrupted run。

所有 E2E 由以下命令统一执行并输出结构化报告：

```text
python tests/phase1/run_acceptance.py \
  --contract docs/roadmap/phase-1-acceptance-contract.json \
  --gate G3 \
  --output artifacts/phase-1/acceptance/g3.json
```

## 8. 可执行验收合同

`phase-1-acceptance-contract.json` 是机器执行入口，不只是自然语言说明。每个 gate 必须声明：

- 固定 inputs；
- verification command；
- expected exit code；
- 字段和文件 assertions；
- required evidence files；
- pass/fail/HOLD 规则。

`run_acceptance.py` 必须读取合同执行对应检查，不能在脚本中维护另一套隐藏标准。最终报告至少包含：合同哈希、输入哈希、每个 assertion 的 PASS/FAIL/HOLD、命令退出码、证据引用和总判定。

验收 runner 自身退出码固定为：0=`PASS`，1=`FAIL`，20=`HOLD`。live smoke 的外部临时不可用返回 20；最终阶段 GO 只接受 0。

两个只读复核分别检查：

- data review：Schema、400/800、证据链、哈希和修订；
- workflow review：状态机、成功/失败 run、原子发布、并发和 replay。

复核结果保存为结构化 JSON。复核者不修改被审查交付物。

## 9. 实施顺序和完成门

```mermaid
flowchart LR
    W1["W1 六对象规格与哈希合同"] --> G1["G1 规格冻结"]
    G1 --> W3A["W3 bootstrap核心链路"]
    W3A --> W2["W2 生成400/800基础数据"]
    W2 --> G2["G2 基础数据验收"]
    G2 --> W3B["W3 incremental/replay/verify/并发"]
    W3B --> G3["G3 E2E与独立复核"]
    G3 --> GO["阶段1 GO"]
```

顺序说明：

1. 完成 W1，冻结六对象、序列化、哈希、状态机和 collection policy 合同；
2. 实现 W3 的唯一入口和 bootstrap 最小闭环；
3. 用该闭环生成 W2，不得使用一次性转换脚本；
4. G2 通过后补齐同一管线的 live incremental、replay、verify、并发和恢复；
5. 执行 G3 的确定性 E2E、live smoke 和两项只读复核；
6. 机器合同汇总所有证据后给出 GO/HOLD。

### G1：规格冻结

六个 Schema、正反例、hash vectors 和 collection policy 合同全部通过；否则 HOLD。

### G2：基础数据签发

400/800、证据链、阶段 0 哈希兼容、幂等 bootstrap 和 verify 全部通过；否则 HOLD。

### G3：工作流签发

CLI 入口、打包、增量策略、失败留证、revision、原子发布、并发保护、离线 replay、E2E-01 至 E2E-07、当次 live smoke 和两项复核全部通过；否则 HOLD。

## 10. 阶段 1 最终完成定义

阶段 1 只有在以下事实同时成立时才能标记 `ACHIEVED / GO`：

- W1、W2、W3 的全部文件存在；
- G1、G2、G3 的机器报告均为 PASS；
- 正式 `baseline-v1` 恰好包含 400 条 DrawRecord 和 800 条 SourceObservation；
- 成功 run、no-change run、rejected run、interrupted run 和 replay 均有可验证证据；
- current release 只能由完整通过质量门且获得发布权的 run 更新；
- 两份只读复核报告均无阻断项；
- `phase1-acceptance.json` 根据合同汇总为 PASS。

任何 required evidence 缺失、断言失败、live smoke 未通过或复核存在阻断项，结论均为 `HOLD`，不得进入依赖可信数据层的后续研究阶段。
