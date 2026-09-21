# API 应用

`apps/api` 是系统的 FastAPI 入口，负责 REST API、SSE 进度流、配置装配、依赖注入和无 Key 发布演示。

边界：这里只处理传输层与应用装配，不放置 Agent 业务规则；领域契约来自 `packages/domain`，运行编排来自 `packages/harness`。

主要入口：

- `main.py`：版本化 FastAPI 应用与 `/health`。
- `run_routes.py` / `run_service.py`：Trip Run API 与 Fixture 组合根。
- `travelctl.py`：工具诊断 CLI。
- `demo.py`：`pnpm demo` 使用的无网络 v1.0.0 冒烟演示。
