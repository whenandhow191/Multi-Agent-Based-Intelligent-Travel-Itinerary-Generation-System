# Tests

`tests` 保存跨应用与包的自动化验证。随着项目发展，将拆分为 `unit`、`integration`、`contract`、`e2e`、`chaos` 和 `evals`。

当前测试覆盖工程导入与健康检查、类型化配置、C06～C11 领域与离线基础、C12～C17 Harness 内核、C18～C22 四个业务 Agent，以及 C23～C26 Blackboard、1+4 工作流、局部返工和最终聚合。默认测试无网络、无外部 API Key；`TEST_DATABASE_URL` 存在时额外运行真实 PostgreSQL 租约与 Blackboard 并发测试，GitHub Actions 会自动提供该数据库。
