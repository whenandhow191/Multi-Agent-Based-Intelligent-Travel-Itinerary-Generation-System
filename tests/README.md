# Tests

`tests` 保存跨应用与包的自动化验证。随着项目发展，将拆分为 `unit`、`integration`、`contract`、`e2e`、`chaos` 和 `evals`。

当前测试覆盖工程导入与健康检查、类型化配置、C06～C11 领域与离线基础，以及 C12～C17 的 Gateway、Agent 生命周期、DAG 调度、Repository、heartbeat、checkpoint、恢复和 Outbox。默认测试无网络、无外部 API Key；`TEST_DATABASE_URL` 存在时额外运行真实 PostgreSQL 原子租约测试，GitHub Actions 会自动提供该数据库。
