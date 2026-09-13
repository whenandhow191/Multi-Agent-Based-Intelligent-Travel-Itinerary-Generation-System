# Workflow

`packages/workflow` 把 Coordinator 的声明式任务图连接到 Blackboard 和四个业务 Agent。

- `orchestrator.py`：A1/A2 真实并发，候选合并后的 Harness Route Matrix，随后 A3 与 A4 串行 fan-in；每步只通过版本化 ArtifactRef 交接。
- `repair.py`：按 PatchRequest 所有者计算依赖失效闭包、最多两轮版本化局部返工，并支持 `WAITING_USER` 澄清后恢复。
- `final_aggregator.py`：只接受通过 A4 和最终确定性复验的方案，计算多方案比较，并从同一 `FinalPlanBundle` 渲染稳定 JSON/Markdown。

边界：Workflow 决定确定性依赖顺序，不绕过 Agent/Tool Schema，不保存模型私有思维链，不让 Agent 直接互调。
