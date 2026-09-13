# Models

`packages/models` 保存模型 Provider Adapter、Agent profile、成本账本、失败路由和基准评测。

边界：Agent 只依赖 `ModelGateway`；API Key 只在适配器发请求时解包，不进入请求快照、日志或输出。
