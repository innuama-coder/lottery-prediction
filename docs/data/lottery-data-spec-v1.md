# 彩票规范数据规格 v1

## 1. 目的与适用边界

本规格是阶段 1 的数据层机器合同，定义 `SourceObservation`、`DrawRecord`、`DatasetRelease`、`RunManifest`、`RunEvent` 和 `RunResult` 六个对象，以及发布指针、核对结果、质量报告和哈希清单的最小合同。规范事实载体为 UTF-8 JSON/JSONL。

本规格只证明开奖记录可以被确定地解析、核对、追溯、修订和发布。它不证明开奖过程可预测，也不授权数据再分发。阶段 0 基线是回溯整理后的当前事实视图，必须标记为 `retrospective_current_view`。

版本规则：六个对象的 Schema 版本均为 `1.0.0`。任何破坏兼容性的字段、语义、投影或状态变化都必须发布新 major 版本；不得在原版本下静默改变哈希算法。

## 2. 共同类型和时间语义

### 2.1 游戏与号码

| `game` | 前区 | 后区 | 单式组合总数 |
| --- | --- | --- | ---: |
| `ssq` | 1–33 选 6 | 1–16 选 1 | 17,721,088 |
| `dlt` | 1–35 选 5 | 1–12 选 2 | 21,425,712 |

号码必须为 JSON 整数，区内唯一并严格升序。数组长度和数值范围由 Schema 检查；严格升序由语义验证器检查。`issue_id` 是七位字符串 `YYYYNNN`，不允许数字类型；`raw_issue_id` 保留页面原文，显示补零不得反写原值。

### 2.2 `Asia/Shanghai` 日期语义

`draw_date_local` 是开奖发布主体所表达的中国民用开奖日期，固定按 IANA 时区 `Asia/Shanghai` 解释，格式为 `YYYY-MM-DD`。它不是 UTC 日期，也不得从 `captured_at_utc` 推导。

- 来源只给出日期时，直接把该日解释为 `Asia/Shanghai` 日历日。
- 来源给出带时区时间时，先转换到 `Asia/Shanghai`，再取日历日。
- 来源给出无时区的本地开奖时间时，只有来源合同明确其为中国当地时间才可按 `Asia/Shanghai` 解释；否则该观察不能进入发布证据。
- `captured_at_utc`、`started_at_utc`、`occurred_at_utc`、`completed_at_utc` 和 `created_at_utc` 必须是带 `Z` 的 RFC 3339 UTC 时间。
- `available_at_utc` 表示该事实最早可靠可知的 UTC 时间。没有可靠证据时必须为 `null`，不能用抓取时间或开奖日期午夜代填。阶段 0 回溯基线统一为 `null`。

## 3. 规范序列化与哈希 Profile

所有摘要使用 SHA-256，并输出 64 位小写十六进制。`canonical-json-v1` 的字节规则为：

1. UTF-8，无 BOM，`ensure_ascii=false`；
2. 对象键按 Unicode 码点升序；
3. 数组保留规格规定的语义顺序；
4. 分隔符严格为 `,` 和 `:`，无额外空白；
5. 每个对象末尾恰好一个字节 `LF`；
6. 不允许 `NaN`、`Infinity`、浮点时间、重复对象键或平台相关路径分隔符；
7. JSONL 不含空行，文件最后一条同样以一个 `LF` 结束。

规范文件排序键：

- `observations.jsonl`：`game, issue_id, publisher_id, source_id, observation_id` 升序；
- `reconciliation.jsonl`：`game, issue_id` 升序；
- `draws.jsonl`：`game, issue_id, revision_id` 升序；
- `events.jsonl`：`sequence` 严格递增；它是追加日志，不允许事后重排。

### 3.1 核心事实

`phase0-core-fact-v1` 必须与阶段 0 算法字节兼容。投影对象为：

```json
{"game":"<game>","issue_id":"<issue_id>","draw_date":"<draw_date_local>","front_numbers":[],"back_numbers":[]}
```

字段名 `draw_date_local` 只在投影时映射成 `draw_date`。按 `canonical-json-v1`（包括末尾 LF）序列化后计算 `core_fact_sha256`。

### 3.2 确定性 ID

ID 不使用随机 UUID，也不包含抓取完成顺序或墙钟时间。

- `observation_id`：对 `{"source_id", "game", "issue_id", "raw_sha256", "parser_version"}` 的完整对象按 `canonical-json-v1` 计算 SHA-256，再加前缀 `obs-v1:`。重新解析同一 raw 且 parser 版本不变时 ID 必须相同；parser 版本或 raw 改变时 ID 必须改变。
- `revision_id`：对 `{"game", "issue_id", "core_fact_sha256", "supersedes_revision_id"}` 按同一规则计算 SHA-256，再加前缀 `rev-v1:`。首次修订的 `supersedes_revision_id` 为 JSON `null`。这样事实回退也会因前序修订不同而产生新 ID。
- `event_id`：对 `{"run_id", "sequence", "event_type", "request_id", "attempt"}` 计算 SHA-256，再加前缀 `evt-v1:`。空值必须编码为 JSON `null`。

投影必须包含上述全部且仅包含上述字段；对象键最终仍由规范序列化器排序。实现必须用冻结的真实向量验证 SSQ、普通 DLT 和广东体彩补证 DLT 三种核心事实。

### 3.3 文件与 Bundle 哈希

`records_sha256` 和 `observations_sha256` 是最终规范 JSONL 文件的原始字节哈希。Schema/pipeline bundle 先为每个纳入文件生成 `{"path","sha256"}`，按 POSIX 相对路径升序组成数组，再把该数组按 `canonical-json-v1` 序列化并取哈希。路径相对仓库根目录，使用 `/`，不得包含绝对路径或 `..`。

离线 replay 必须逐字节复现 observations、reconciliation、candidate draws 和 quality report 的 `deterministic` 区域；run ID、运行时间、事件时间和 replay 自身 manifest 只做语义比较。

## 4. 六个规范对象

Schema 位于 `schemas/phase1/`。所有 Schema 使用 JSON Schema Draft 2020-12、顶层 `additionalProperties: false`。Schema 验证之后仍必须执行本节列出的跨字段、跨记录语义验证。

### 4.1 `SourceObservation`

一条观察表示一个来源页面实际解析出的单期事实。失败、截断或无法形成完整号码的解析只写 `RunEvent` 和错误证据，不得伪造成 SourceObservation；因此 v1 的 `parse_status` 唯一合法值是 `parsed`。

必须字段以 `source-observation.schema.json` 为准，包含来源/发布主体、原始和规范期号、日期与号码、URL、抓取时间、raw 引用及摘要、parser 身份与版本、核心事实摘要。`source_id` 表示采集适配器，`publisher_id` 表示内容发布主体，两者不得混用。

语义验证器必须确认：号码严格升序；`raw_ref` 存在且字节哈希等于 `raw_sha256`；parser 可从该 raw 重放出相同字段；`observation_id` 和 `core_fact_sha256` 按本规格重算一致。

### 4.2 `DrawRecord`

DrawRecord 是已核对并可发布的规范开奖记录。v1 发布记录的 `status` 只能是 `verified`。每条记录恰有两个结构化 `evidence_links`，每项绑定 `source_id`、`publisher_id`、`observation_id`、`raw_ref`、`raw_sha256`；禁止使用无法保持对应关系的平行数组。

语义验证器必须确认：

- 两个 evidence link 的 `publisher_id` 不同，且 `(publisher_id, observation_id)` 不重复；
- 两个 observation 均存在，游戏、期号、日期、号码和核心事实摘要与 DrawRecord 一致；
- evidence 中的 raw 引用和摘要与 observation 一致；
- 号码严格升序，`revision_id` 可重算；
- 首次记录的 `supersedes_revision_id=null`；后续修订必须指向同一 `(game, issue_id)` 的直属前序 revision。

### 4.3 `DatasetRelease`

DatasetRelease 是成功发布后不可变的数据快照 manifest。其 `status` 唯一允许值为 `published`；`rejected` 或 `interrupted` 只属于 RunResult。`record_count_by_game` 固定包含 `ssq` 和 `dlt`，计数之和必须等于 draws 行数；`observation_count` 必须等于 observations 行数。

`previous_release_id` 在首个 release 为 `null`，否则必须指向存在且不可变的直接前序 release。`input_manifest_sha256`、bundle/file 摘要和 `quality_report_ref` 必须能在 release 的 `hashes.json` 与实际文件中复核。release 内容一经发布不得原地改变。

### 4.4 `RunManifest`

RunManifest 是 preflight 一次写入的静态输入。它冻结 run/mode/source mode、执行根目录、前序 release、games、请求计划、配置及哈希、Schema/pipeline bundle、Python 3.12、bootstrap 快照或 incremental watermark、发布策略和 replay 来源。运行统计、响应结果和终态不得回写 manifest。

- bootstrap 必须提供 `bootstrap_snapshot`，且 `incremental_watermark=null`；
- incremental 必须提供 `incremental_watermark`，且 `bootstrap_snapshot=null`；
- snapshot 模式禁止网络，计划项的 `input_ref` 必须可解析；
- `request_plan.sequence` 和 `request_id` 各自唯一；计划顺序一经写入不可改变；
- `replay_of_run_id` 非空时只允许使用原 run 保存的 raw/manifest，禁止网络。

### 4.5 `RunEvent`

`events.jsonl` 是只追加的状态日志。`sequence` 从 1 开始严格递增且无重复；event ID 必须可重算。run 合法状态机为：

```text
planned -> running -> published
planned -> running -> no_change
planned -> running -> rejected
planned -> running -> interrupted
```

每个 run 只能出现一个终态事件，终态后不得再追加业务事件。每个计划请求必须恰有一个 `request_started`；该行必须在网络调用前写入、刷新并完成持久化屏障，随后恰有一个同 request/attempt 的 `request_succeeded` 或 `request_failed`。阶段 1 `max_attempts_per_request=1`，所以 attempt 固定为 1。未开始的计划请求只能由 run 终态和 RunResult 的 `not_started` 解释，不得补造请求事件。

崩溃恢复可以为已经 started 但无终态的请求追加 `request_failed`，再追加 `run_interrupted`；必须引用恢复错误详情。Schema 检查单事件形状，以上序列和恰好一次约束由状态机验证器检查。

### 4.6 `RunResult`

每个达到终态的 run 恰有一个 RunResult。合法 status 为 `published | no_change | rejected | interrupted`。published/no_change 的 `exit_code=0`；只有 published 的 `release_id` 非空。rejected 使用合同定义的 2/3/4/5/6/10；非正常进程终止后恢复生成的 interrupted 结果允许 `exit_code=null`，正常捕获的未分类中断为 10。

计数守恒必须由语义验证器检查：`started=succeeded+failed`，`planned=started+not_started`；发布时 failed、invalid、missing、duplicate、conflict、manual_core_edit 均为 0。`completed_at_utc` 不得早于 started。结果必须引用 manifest、events、quality report、错误证据及确定性制品摘要。

## 5. 修订、核对与发布

已发布 DrawRecord 不得原地覆盖。只有两个不同 publisher 对新的核心事实完全一致时才能创建 revision；新 revision 指向直属前序 revision，旧 revision 保留在历史 release。单源变化、任何额外来源异议、证据断裂或无批准 fallback 均拒绝 run，current release 不变。

当前 release 的 `draws.jsonl` 是当前有效视图，不是完整修订日志；完整历史通过 DatasetRelease 的 `previous_release_id` 链恢复。每期发布证据固定选择 collection policy 的有序 source pair；其他一致观察保留在 run reconciliation，任何不一致观察仍阻断发布。

## 6. 四个伴随制品的最小合同

这些对象在 v1 没有独立 Schema 文件，但字段名和语义在此冻结，W3 实现和测试不得另造不兼容结构。

### 6.1 `current-release.json`

顶层对象必须且只能包含：

```json
{
  "pointer_schema_version": "1.0.0",
  "release_id": "<release_id>",
  "manifest_ref": "releases/<release_id>/manifest.json",
  "manifest_sha256": "<sha256>",
  "updated_at_utc": "<RFC3339 UTC Z>",
  "updated_by_run_id": "<run_id>"
}
```

指针只能在 release 临时目录完成关闭、重算哈希和 verify，并原子改名成功后，通过同文件系统临时文件原子替换。CAS 前必须确认当前 release 等于 RunManifest 冻结的 `previous_release_id`。no_change、rejected、interrupted 或锁/CAS 失败不得改变指针。

### 6.2 `reconciliation.jsonl`

每行必须且只能包含：`reconciliation_schema_version`、`game`、`issue_id`、`decision`、`core_fact_sha256`、`selected_observation_ids`、`agreeing_observation_ids`、`missing_source_ids`、`dissenting_observation_ids`、`fallback_rule_id`、`reason_codes`。decision 为 `verified | missing | conflict | invalid | unresolved`；ID 数组去重并升序，`fallback_rule_id` 无适用规则时为 `null`。

`verified` 行必须有恰好两个 selected observation、不同 publisher、相同核心事实；agreeing 可包含额外观察。missing/conflict/invalid/unresolved 不得生成可发布 candidate。DLT 2026026/2026027 使用广东体彩时必须记录批准的 issue-specific fallback rule ID、普通来源 missing 和选择原因。

### 6.3 `quality-report.json`

顶层必须且只能包含 `quality_schema_version`、`run_id`、`decision`、`deterministic`、`generated_at_utc`。decision 为 `PASS | FAIL | HOLD`。`deterministic` 至少包含：

- `counts`：draw/observation/game、invalid、missing、duplicate、conflict、manual_core_edit；
- `checks`：按 `check_id` 升序的对象数组，每项含 `check_id`、`status`、`expected`、`actual`、`evidence_refs`；
- `input_hashes` 与 `output_hashes`；
- `blocking_reason_codes`。

同一输入 replay 时 `deterministic` 必须字节一致；`generated_at_utc` 明确排除在该比较之外。只有 deterministic checks 全部 PASS 且阻断原因为空时 quality decision 才能 PASS。

### 6.4 `hashes.json`

顶层必须且只能包含 `hash_manifest_schema_version`、`hash_profile`、`generated_at_utc`、`entries`。`hash_profile` 固定为 `sha256-file-manifest-v1`。每个 entry 必须且只能包含 `path`、`sha256`、`size_bytes`、`role`；按 POSIX 相对 `path` 升序且路径唯一。role 为 `input | raw | observation | reconciliation | candidate | draw | manifest | quality | event | result | schema | pipeline`。

摘要针对文件实际字节；`hashes.json` 不得包含自身，避免递归摘要。run hash manifest 必须覆盖 manifest、events 和所有已持久化输入/输出；release hash manifest 必须覆盖 draws、observations、manifest 和 quality report。验证时发现缺文件、额外受管文件、大小差异或摘要差异均失败。

## 7. Schema 与语义验证职责

JSON Schema 负责字段存在性、基本类型、封闭对象、枚举、字符串格式、数组长度、号码范围及单对象条件。以下约束必须由确定性语义验证器完成：严格升序、不同 publisher、文件存在与摘要、确定性 ID/核心事实重算、计数守恒、排序键、请求/运行状态机、修订链、release 链、current pointer CAS 和跨文件证据闭包。

验证顺序固定为：JSON 解析 → Schema → 单对象语义 → 跨记录唯一性/排序 → 证据和哈希闭包 → 状态机/修订链 → 质量门。任何一步失败都不得继续发布。

## 8. W1 完成门

G1 只有在六个 Schema 均通过 Draft 2020-12 meta-schema、正反实例覆盖六对象、状态机负例与三类真实 phase 0 hash vector 全部通过，并冻结规格/Schema/hash-vector bundle 后才可 PASS。只完成本文件和六个 Schema 是 W1-A 开发出口，不等同于 G1 或阶段 1 验收完成。
