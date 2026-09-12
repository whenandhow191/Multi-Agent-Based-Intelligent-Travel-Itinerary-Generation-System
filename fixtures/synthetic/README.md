# Synthetic fixtures

这里的 JSON 完全由项目构造，用于证明在无网络、无 API Key 的环境中也能获得固定测试响应。

`scripted_turns.json` 按数组顺序被 `ScriptedModel` 消费：第一轮要求调用地点工具，第二轮返回固定结构化结果。
