# Model benchmark dataset

`model_cases.jsonl` 包含 25 条纯合成用例，每个 Agent 五条。数据不含真实用户信息、API
响应或密钥。评测必须按 Agent 和候选模型分别输出 Schema、硬约束、引用、延迟、成功率和
每个成功结果成本；不能只用一个总平均掩盖某个 Agent 的失败。
