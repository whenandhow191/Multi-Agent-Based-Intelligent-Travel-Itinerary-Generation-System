# Harness

`packages/harness` 是自建 Agent 运行时，后续实现状态机、任务 DAG、调度、上下文、策略、检查点和恢复。

边界：Harness 执行确定性的生命周期与权限规则，不把核心编排委托给第三方托管 Agent 服务。

## 当前模块

- `model_gateway.py`：供应商中立的消息、工具 Schema、生成策略、Usage、请求/响应与异步 Gateway 协议。
- `tool_gateway.py`：显式注册工具、生成 Schema、执行 allowlist，以及输入/输出双向校验。
- `agent.py`：不可变上下文、Agent 规格、有限模型/工具循环、结构化输出和稳定错误分类。
- `scheduler.py`：任务 DAG 校验、依赖就绪判断、有限并行、协作取消、超时和有限重试。

真实模型 SDK 只能在后续 Provider Adapter 中出现；Agent 只依赖 `ModelGateway`，离线测试使用 `ScriptedModelGateway`。
