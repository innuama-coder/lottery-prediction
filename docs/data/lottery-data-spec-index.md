# 阶段 1 数据规格索引

本文件是阶段 1 当前唯一的规范入口。消费者必须先按 `RunManifest.run_schema_version`、来源模式和冻结的 policy SHA 选择执行档案，不能把历史规格中的规则直接套用到 current live。

## 规范优先级

发生歧义时按以下顺序解释：

1. `docs/roadmap/phase-1-acceptance-contract.json` 的当前结构化约束；
2. `docs/data/lottery-live-execution-spec-v1.3.md` 对 current `incremental:live` 的解释；
3. `docs/data/lottery-data-spec-v1.md` 对 base/snapshot v1 对象和数据发布的解释；
4. 历史 v1.1/v1.2 policy、Schema 和 fixture 只用于验证、恢复和 replay 兼容，不授权 current live。

验收合同负责机器可执行的边界；两份规格负责解释对应版本的对象语义。低优先级文件中的无版本限定语句不得覆盖更高优先级的显式版本规则。

## 执行档案矩阵

| 档案 | 适用范围 | Manifest/Event | 每逻辑请求尝试次数 | 状态 |
|---|---|---|---:|---|
| base/snapshot v1 | bootstrap:snapshot、incremental:snapshot、基础数据发布 | 1.0.0 / 1.0.0 | 1 | 当前基础数据档案，字节冻结 |
| legacy live v1.1 | 旧 run 的 verify/recovery/replay | 1.1 / 1.1 | 1 | 只读历史兼容 |
| historical live v1.2 | 旧 run 的 verify/recovery/replay | 1.2 / 1.2 | 1 | 只读历史兼容 |
| current live v1.3 | incremental:live | 1.3 / 1.3 | 1–2 | 当前唯一 live 写入档案 |

`docs/data/lottery-data-spec-v1.md` 中“阶段 1 `max_attempts_per_request=1`、attempt 固定为 1”的语句只适用于 base/snapshot v1；该文件保持原字节是为了让已经发布的 `baseline-v1`、Schema bundle 和 Phase 0 证据链继续可复验。current live 的 attempt 语义由 v1.3 扩展规格定义。

## 不变边界

- 400 条 DrawRecord、800 条发布 SourceObservation 及其字节哈希不因 live v1.3 改变；
- 六个基础 v1 Schema 和 `spec-bundle-freeze.json` 保持原字节；
- v1.1/v1.2 policy、Schema 和 fixture 保持原字节；
- current live 不得根据请求字段形状猜版本，只能由 manifest version 与对应 policy SHA 联合分派；
- 任一未在本索引登记的新版档案必须先更新合同、规格、测试和签名，不能静默升级解释。

## 当前交付入口

- 基础对象与发布：`docs/data/lottery-data-spec-v1.md`
- current live：`docs/data/lottery-live-execution-spec-v1.3.md`
- 总体交付与 E2E：`docs/roadmap/phase-1-canonical-data-plan.md`
- 机器验收：`docs/roadmap/phase-1-acceptance-contract.json`

