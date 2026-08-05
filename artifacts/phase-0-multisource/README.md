# Phase 0 多源数据可行性证据

当前有效快照由 `active-snapshot.json` 指定。最终验收快照为 `20260802T025000Z`。

核心结果：

- 双色球 200/200，缺失 0，冲突 0；
- 大乐透 200/200，缺失 0，冲突 0；
- 30/30 个采集请求成功，每个请求均有开始和成功终态；
- 400 条共识记录均可回到两份原始发布证据；
- 离线重建无差异，阶段 0 判定为 `ACHIEVED / GO`。

关键文件：

- `snapshots/20260802T025000Z/phase0-report.json`：阶段 0 机器报告；
- `snapshots/20260802T025000Z/offline-verification.json`：离线重放结果；
- `snapshots/20260802T025000Z/consensus/canonical-records.jsonl`：400 条可供后续研究使用的标准记录；
- `snapshots/20260802T025000Z/consensus/reconciliation.jsonl`：逐期对账状态；
- `snapshots/20260802T025000Z/request-events.jsonl`：请求事件审计；
- `source-catalog.json`：使用、排除和限制说明。

这里的“多源”是不同运营主体对同一官方开奖事实的交叉发布核对，不是两个独立摇奖过程。快照仅批准用于项目内部阶段 0 研究；生产采集或分发前需要重新审查来源条款。
