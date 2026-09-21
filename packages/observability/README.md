# Observability

本目录实现供应商中立的 OpenTelemetry 手工埋点、结构化日志脱敏和 Claim lineage 索引。

- `TelemetryRecorder` 只记录运行、任务、Agent、模型/工具、延迟、Token 和成本等低基数字段。
- `InstrumentedModelGateway` 与 `InstrumentedToolGateway` 包装既有 Gateway，不改变业务契约。
- `LineageIndex` 将最终 Claim 追溯到生成它的 Agent、Artifact 和工具调用。
- `SafeJsonFormatter` 在输出 JSON 前按字段名递归脱敏。

Prompt、模型原始响应、API Key、匿名运行令牌和 chain-of-thought 不得进入遥测。
