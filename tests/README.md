# Tests

`tests` 保存跨应用与包的自动化验证。随着项目发展，将拆分为 `unit`、`integration`、`contract`、`e2e`、`chaos` 和 `evals`。

当前测试覆盖工程导入与健康检查、类型化配置、C06～C09 领域契约、C10 纯合成 Fixture/ScriptedModel、C11 确定性校验器，以及 C12～C16 的 Gateway、Agent 生命周期、DAG 调度和 Repository。默认测试无网络、无外部 API Key；PostgreSQL 语句与 migration 可离线生成，容器用于运行时复验。
