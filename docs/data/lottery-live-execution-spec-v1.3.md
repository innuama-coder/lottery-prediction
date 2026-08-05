# 彩票 live 执行规格 v1.3

本规格只适用于 `mode=incremental` 且 `source_mode=live`。它扩展基础数据规格的 RunManifest/RunEvent 执行语义，不改变 DrawRecord、SourceObservation、DatasetRelease、400/800 baseline 或 snapshot v1。

## 1. 版本与分派

- current manifest Schema：`schemas/phase1/run-manifest-v1.3.schema.json`；
- current event Schema：`schemas/phase1/run-event-v1.3.schema.json`；
- current policy：`config/phase1/live-source-policy.json`；
- verify、recovery 和 replay 必须同时匹配 manifest version 与 policy SHA；
- v1.1/v1.2 是只读历史兼容档案，不得根据字段形状升级为 v1.3；
- `bootstrap:live` 非法。

## 2. 逻辑请求计划

manifest 一次写入且不可回写。计划恰好包含四个有序静态 GET history 请求：

1. `live-ydniu-ssq-history`；
2. `live-swlc-ssq-history`；
3. `live-ydniu-dlt-history`；
4. `live-gdlottery-dlt-history`。

禁止 discovery、child、announcement、`request_discovered`、猜测期号 URL、cache-buster、第五逻辑请求和 GET `input_ref`。

## 3. Attempt 状态机

每个逻辑请求允许一个或两个连续、可审计的 attempt：

- attempt 必须从 1 开始且不能跳号；
- 每个 attempt 在网络调用前持久化一个 `request_started`；
- 同一 request/attempt 随后必须恰有一个 `request_succeeded` 或 `request_failed`；
- 成功后禁止继续 attempt；
- attempt 3 永远非法；
- attempt 2 只能紧跟在 attempt 1 的可重试失败之后；
- attempt 1 失败与 attempt 2 开始之间固定等待 2 秒。

唯一可重试错误类别是 `dns_timeout_tls_or_required_source_unavailable`。它覆盖 DNS、连接/读取超时、TLS/底层网络错误和必需来源暂时不可用。HTTP 非成功、响应过大、认证/cookie/challenge、非法跳转、配置或 policy 错误、媒体类型/编码/解析错误、号码/期号错误和 publisher 冲突均不得重试。

非重试错误在 attempt 1 后立即拒绝 run；可重试错误在 attempt 2 仍失败时耗尽并拒绝 run。失败不得创建 release 或修改 current pointer。

## 4. 证据与错误文件

- 每个真实 attempt 都必须在事件流中可见；
- 错误证据按 `errors/<request-id>/attempt-<n>.json` 唯一保存，后续 attempt 不得覆盖先前失败；
- 网络成功后 raw 先以内容寻址路径持久化并完成 SHA-256 闭包，之后才能进入 parser；
- `request_succeeded.artifact_ref`、路径摘要、文件 SHA 和 parser provenance 必须一致；
- 旧缓存不能冒充本次请求成功。

## 5. 逻辑计数与 run 终态

RunResult 的 `planned/started/succeeded/failed/not_started` 统计四个逻辑请求，不统计网络 attempt 行数：

- retry 后成功的请求计为一个 succeeded，不同时计入 failed；
- published/no_change 要求四个逻辑请求全部成功：`planned=started=succeeded=4` 且 `failed=not_started=0`；
- rejected/interrupted 可以留下未开始的逻辑请求，但每个已经开始的 attempt 必须闭合；
- run 只能有一个终态，终态后不得追加业务事件。

## 6. Recovery

recovery 只关闭当前已经 `request_started`、但没有同 request/attempt 终态的 attempt。它不得补造 attempt 2。若 attempt 1 已持久化为可重试失败、但进程在 attempt 2 开始前终止，恢复结果是 interrupted，而不是自动续试。

## 7. 验收要求

确定性测试至少覆盖：

- attempt 1 暂态失败、固定退避后 attempt 2 成功；
- 两次暂态失败后耗尽；
- 非重试错误只发生一次 attempt；
- 跳号、attempt 3、成功后重试、双终态均拒绝；
- 逻辑 request_stats 不因 attempt 增加而重复计数；
- 历史 v1.1/v1.2 verify/recovery 仍按 attempt 1 解释。

真实 E2E-05 证明 current live 四请求链路可运行；由于公网响应不保证恰好触发瞬态故障，重试状态机由确定性集成测试验收，不能伪称由每次公网 smoke 直接证明。

