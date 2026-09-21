# Architecture Decision Records

ADR 用于记录影响多个模块、难以回滚或需要明确取舍的技术决策。它回答“为什么这样做”，代码地图回答“文件在哪里”。

## 工作流

1. 复制 `0000-模板.md`，使用四位递增编号和简短标题。
2. 在实现前填写背景、约束、候选方案和决策。
3. PR 评审后把状态改为 `Accepted`；尚在讨论时使用 `Proposed`。
4. 决策变化时新增 ADR，并在旧 ADR 中标记 `Superseded by ADR-xxxx`，不要重写历史。
5. 在相关代码、代码地图或 PR 中链接 ADR。

允许的状态：`Proposed`、`Accepted`、`Deprecated`、`Superseded`、`Rejected`。

## 索引

| 编号 | 标题 | 状态 |
|---|---|---|
| ADR-0001 | 工程基础与本地运行时 | Accepted |
| ADR-0002 | 领域契约与确定性校验 | Accepted |
| ADR-0003 | Harness 内核与持久恢复 | Accepted |
| ADR-0004 | 1+4 Agent 职责与确定性边界 | Accepted |
| ADR-0005 | 共享黑板与可验证工作流 | Accepted |
| ADR-0006 | 可观测、安全与发布闸门 | Accepted |
