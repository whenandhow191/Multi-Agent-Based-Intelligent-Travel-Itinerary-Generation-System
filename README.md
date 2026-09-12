# 基于多 Agent 的智能旅游攻略生成系统

本项目采用自建 Agent Harness，以“1 个总控 Coordinator + 4 个核心业务 Agent + 可选扩展 Agent”的方式完成旅行信息调研、交通住宿分析、行程优化、独立审校和最终汇总。

## 设计文档

- [需求规格说明书](./docs/01-需求规格说明书.md)
- [技术实现方案](./docs/02-技术实现方案.md)
- [教学式开发路线与 Git 交付计划](./docs/03-教学式开发路线与Git交付计划.md)

三份文档基于 2026-09-10 的公开资料整理，已经修正早期图片方案中关于 Agent 划分、MCP/CLI、Amadeus、12306、小红书爬虫、LangGraph/Celery/Redis 和地图坐标/许可等不准确或不够可落地的部分。

## 当前进度

- 第一章“仓库与工程基础”：进行中（C00～C05）。
- 后续将按 `docs/03-教学式开发路线与Git交付计划.md` 逐个检查点交付。

## 本地质量检查

项目要求 Python 3.12、Node.js 20～24、pnpm 10～11 和 `uv`。安装依赖后运行：

```shell
pnpm check
```

该命令依次执行格式检查、Lint、类型检查和测试，与 GitHub Actions 保持一致。

## 参与贡献

请先阅读 [贡献指南](./CONTRIBUTING.md)。本项目采用 MIT License。
