# Agents

`packages/agents` 保存 Coordinator 与四个核心业务 Agent：目的地情报、交通住宿、行程规划、审校风险。

边界：Agent 只通过 Harness 接收任务、发布 Artifact，并通过 Tool Gateway 申请工具调用；Agent 之间不直接互调。

当前实现：

- `coordinator.py`：把合法 `TripRequest` 转换为固定白名单 DAG；存在阻塞问题时返回 `WAITING_USER`。
- `destination_intelligence.py`：A1 通过地点、天气、白名单网页与实体消歧工具生成 `DestinationIntelArtifact`，并在 Schema 层封闭 Place、Weather、Claim 到 Evidence 的引用。
- `mobility_lodging.py`：A2 归一化城际交通、住宿、定向路线和衔接风险；未知价格、非实时库存或供应商失败必须显式降级为人工确认。
- `itinerary_planning.py`：A3 只使用确定性工具；内置 CP-SAT 候选选择、真实 Route 引用、开放时间排程和最终领域校验，输出均衡/经济/轻松三种候选。
- `critic_risk.py`：A4 在确定性校验报告之上检查证据缺口、矛盾与折返风险，按问题归属向 A1、A2 或 A3 发出结构化 PatchRequest，不直接篡改事实。
- Coordinator 只声明任务、依赖和预算，不携带可执行回调，也不生成 POI、路线、价格等旅行事实。
- `merge_candidates` 与 `build_route_matrix` 明确归 Harness 所有，A2 不得越权生成全量矩阵。
