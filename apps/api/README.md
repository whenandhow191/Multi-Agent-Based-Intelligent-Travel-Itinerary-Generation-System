# API 应用

`apps/api` 是系统的 FastAPI 入口，后续负责 REST API、SSE 进度流、配置装配和依赖注入。

边界：这里只处理传输层与应用装配，不放置 Agent 业务规则；领域契约来自 `packages/domain`，运行编排来自 `packages/harness`。

