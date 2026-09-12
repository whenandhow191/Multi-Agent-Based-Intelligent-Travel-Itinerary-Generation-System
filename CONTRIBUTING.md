# 贡献指南

感谢参与“基于多 Agent 的智能旅游攻略生成系统”。本项目采用教学式、小步提交的开发方式。

## 开始之前

1. 阅读 `docs/01-需求规格说明书.md`、`docs/02-技术实现方案.md` 和 `docs/03-教学式开发路线与Git交付计划.md`。
2. 从最新主分支创建短期分支，例如 `chapter/02-domain-contracts`。
3. 不提交 `.env`、密钥、真实用户行程或未经许可保存的供应商响应。

## 提交要求

- 一个提交只处理一个检查点或一个清晰问题。
- 使用 Conventional Commits，例如 `feat(domain): define trip request contracts`。
- 新增源码时同步增加测试和文件用途说明。
- 提交前运行项目统一检查命令：`pnpm check`。
- 架构决策使用 `docs/adr/` 中的模板记录。

## Pull Request

PR 应说明关联检查点、变更范围、验证命令和结果，并确认没有加入敏感数据。合并前必须通过 CI。

