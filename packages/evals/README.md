# Evals

`packages/evals` 保存版本化评测集、评测运行器和质量/成本/延迟指标定义。

边界：评测数据使用合成、自有或明确获准保存的数据，并保留单 Agent 基线以验证多 Agent 拆分的真实价值。

当前离线测试基础设施：

- `fixtures.py` 以固定时间、固定 ID 和 `Decimal` 金额生成完整的合成旅行场景；
- `scripted_model.py` 按顺序返回预先校验的模型响应，可测试工具调用、结构化输出和脚本耗尽；
- `../../fixtures/synthetic/` 保存可版本化的 JSON 脚本，不读取网络、API Key 或真实用户数据。
