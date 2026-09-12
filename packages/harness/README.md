# Harness

`packages/harness` 是自建 Agent 运行时，后续实现状态机、任务 DAG、调度、上下文、策略、检查点和恢复。

边界：Harness 执行确定性的生命周期与权限规则，不把核心编排委托给第三方托管 Agent 服务。

