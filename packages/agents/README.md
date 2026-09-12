# Agents

`packages/agents` 保存 Coordinator 与四个核心业务 Agent：目的地情报、交通住宿、行程规划、审校风险。

边界：Agent 只通过 Harness 接收任务、发布 Artifact，并通过 Tool Gateway 申请工具调用；Agent 之间不直接互调。

当前实现：

- `coordinator.py`：把合法 `TripRequest` 转换为固定白名单 DAG；存在阻塞问题时返回 `WAITING_USER`。
- `destination_intelligence.py`：A1 通过地点、天气、白名单网页与实体消歧工具生成 `DestinationIntelArtifact`，并在 Schema 层封闭 Place、Weather、Claim 到 Evidence 的引用。
- Coordinator 只声明任务、依赖和预算，不携带可执行回调，也不生成 POI、路线、价格等旅行事实。
- `merge_candidates` 与 `build_route_matrix` 明确归 Harness 所有，A2 不得越权生成全量矩阵。
