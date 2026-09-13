# 基于多 Agent 的智能旅游攻略生成系统

本项目采用自建 Agent Harness，以“1 个总控 Coordinator + 4 个核心业务 Agent + 可选扩展 Agent”的方式完成旅行信息调研、交通住宿分析、行程优化、独立审校和最终汇总。

## 设计文档

- [需求规格说明书](./docs/01-需求规格说明书.md)
- [技术实现方案](./docs/02-技术实现方案.md)
- [教学式开发路线与 Git 交付计划](./docs/03-教学式开发路线与Git交付计划.md)

三份文档基于 2026-09-10 的公开资料整理，已经修正早期图片方案中关于 Agent 划分、MCP/CLI、Amadeus、12306、小红书爬虫、LangGraph/Celery/Redis 和地图坐标/许可等不准确或不够可落地的部分。

## 当前进度

- 第一章“仓库与工程基础”：已完成（C00～C05）。
- 第二章“领域契约与确定性基础”：已完成（C06～C11）。
- 第三章“自建 Harness 内核”：已完成（C12～C17，里程碑 `v0.1.0`）。
- 第四章“1+4 Agent 分别实现”：已完成（C18～C22）。
- 后续将按 `docs/03-教学式开发路线与Git交付计划.md` 逐个检查点交付。

逐文件导航见 [代码地图](./docs/04-代码地图.md)。分章复现过程见 [第一章实现说明](./docs/05-第一章实现说明.md)、[第二章实现说明](./docs/06-第二章实现说明.md)、[第三章实现说明](./docs/07-第三章实现说明.md) 和 [第四章实现说明](./docs/08-第四章实现说明.md)。

## 本地质量检查

项目要求 Python 3.12、Node.js 20～24、pnpm 10～11 和 `uv`。安装依赖后运行：

```shell
pnpm check
```

该命令依次执行格式检查、Lint、类型检查和测试，与 GitHub Actions 保持一致。

## 本地启动

```powershell
conda env create --prefix .\.conda\env --file environment.yml
conda activate .\.conda\env
uv sync --all-groups
pnpm install
Copy-Item .env.example .env
python -m apps.api.config doctor
docker compose up --build
```

项目专用 Conda 环境、uv 虚拟环境、依赖缓存和 `.env` 均已被 Git 忽略。默认 Mock 模式不要求任何外部 Provider Key。

## 参与贡献

请先阅读 [贡献指南](./CONTRIBUTING.md)。本项目采用 MIT License。
