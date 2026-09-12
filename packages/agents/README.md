# Agents

`packages/agents` 保存 Coordinator 与四个核心业务 Agent：目的地情报、交通住宿、行程规划、审校风险。

边界：Agent 只通过 Harness 接收任务、发布 Artifact，并通过 Tool Gateway 申请工具调用；Agent 之间不直接互调。

