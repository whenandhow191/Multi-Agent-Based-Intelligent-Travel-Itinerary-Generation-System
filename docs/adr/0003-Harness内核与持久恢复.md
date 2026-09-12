# ADR-0003：Harness 内核与持久恢复

- 状态：Accepted
- 日期：2026-09-12
- 决策者：项目维护者
- 关联检查点：C12～C17

## 背景

系统需要让多个 Agent 并行处理受依赖约束的长任务，同时能限制模型与工具权限、响应取消、从进程崩溃恢复并保留审计事件。第三方 Agent Runner 会隐藏部分状态迁移和持久化语义，而一开始引入 Celery/Redis 又会形成第二套状态来源。

## 决策驱动因素

- 模型与工具供应商可替换，Agent 不接触其私有 SDK。
- 步骤、工具调用、超时、重试和并发都必须有硬上限。
- Agent 不能直接调用工具 Handler，也不能自行决定任务状态。
- PostgreSQL 是任务、事件、租约与恢复信息的唯一权威来源。
- Worker 崩溃后任务可重新领取，并从已提交检查点继续。
- 业务状态与待发布事件必须原子提交。

## 考虑过的方案

### 托管 Agent 编排

工具调用方便，但核心生命周期、DAG、检查点和事件模型受外部平台控制，不满足本项目可解释、自托管与可替换目标。

### Celery + Redis + PostgreSQL

适合成熟的通用任务队列，但首版仍需自行实现 Agent 状态机和 Artifact 协议，同时增加 Redis 与任务结果的一致性问题。

### 自建 asyncio 内核 + PostgreSQL 租约队列（采用）

进程内由 asyncio 有限并发，跨 Worker 由 PostgreSQL `FOR UPDATE SKIP LOCKED` 原子租约；所有业务状态、检查点和 Outbox 保持在同一数据库。

## 决策

1. `ModelGateway` 只接受供应商中立请求并返回结构化 `ModelTurn` 和 `ModelUsage`。
2. `ToolGateway` 是唯一工具执行入口；注册表、JSON Schema 和 Agent allowlist 在 Handler 前校验。
3. `BaseAgent` 使用有限模型/工具循环，强制结构化输出，并把失败归类为模型、工具、输出、上限或超时。
4. `TaskDAG` 在执行前拒绝缺失依赖、自依赖和环；Scheduler 只并发执行 ready 节点，并实现协作取消、单任务超时与有限重试。
5. PostgreSQL 使用 Alembic 管理 `runs`、`tasks`、`task_dependencies`、`events`、`checkpoints` 和 `outbox_events`。
6. Worker 通过 CTE + `FOR UPDATE SKIP LOCKED` + `UPDATE ... RETURNING` 原子领取任务；heartbeat 不能复活已经失去的租约。
7. 过期任务在未耗尽尝试时进入 `waiting_retry`，否则进入 `failed`；恢复时读取最大 step 的检查点。
8. 事件与 Outbox 行同事务提交。Outbox 是 at-least-once，publisher 使用行租约避免并发重复，消费者以稳定 `event_id` 幂等。
9. 检查点只保存恢复所需的结构化上下文，不保存模型私有思维链或未获许可的 Provider 原始响应。

## 后果

正面后果：内核行为可完全离线测试，状态与事件一致，模型/工具可替换，崩溃恢复路径清晰；CI 可在真实 PostgreSQL 16 上验证租约。

代价：需要维护自有 Scheduler 与恢复代码；Transactional Outbox 无法单独提供跨外部系统的 exactly-once，消费者必须幂等；进程规模扩大后可能需要独立 Worker 服务和更丰富的 reaper/告警。

## 验证

```powershell
pnpm db:sql
uv run pytest tests/test_model_gateway.py tests/test_tool_gateway.py
uv run pytest tests/test_agent_lifecycle.py tests/test_scheduler.py
uv run pytest tests/test_repository.py tests/test_recovery.py
pnpm check
```

GitHub Actions 设置 `TEST_DATABASE_URL`，应用全部 migration 后运行 `tests/test_postgres_repository.py`。

## 复审条件

- 单个 PostgreSQL 队列无法满足吞吐或跨区域容灾目标。
- 运行需要暂停数小时或数天，并由外部信号恢复。
- 引入 Temporal、Celery 或其他执行后端。
- 外部消息系统要求更严格的投递确认与死信治理。
