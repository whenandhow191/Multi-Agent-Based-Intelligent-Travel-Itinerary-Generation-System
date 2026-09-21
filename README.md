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
- 第五章“共享黑板与 1+4 协作”：已完成（C23～C26，里程碑 `v0.2.0`）。
- 第六章“真实工具与数据源逐个接入”：已完成（C27～C33）。
- 第七章“模型配置、成本与评测”：已完成（C34～C38，里程碑 `v0.3.0`）。
- 第八章“后端与前端产品化”：已完成（C39～C44，里程碑 `v0.4.0`）。
- 第九章“系统加固与发布”：已完成（C45～C49，正式版 `v1.0.0`）。

逐文件导航见 [代码地图](./docs/04-代码地图.md)。分章复现过程见
[第一章实现说明](./docs/05-第一章实现说明.md)、
[第二章实现说明](./docs/06-第二章实现说明.md)、
[第三章实现说明](./docs/07-第三章实现说明.md)、
[第四章实现说明](./docs/08-第四章实现说明.md)、
[第五章实现说明](./docs/09-第五章实现说明.md) 和
[第六章实现说明](./docs/10-第六章实现说明.md) 和
[第七章实现说明](./docs/11-第七章实现说明.md) 和
[第八章实现说明](./docs/12-第八章实现说明.md) 和
[第九章实现说明](./docs/13-第九章实现说明.md)。部署、升级、密钥和回滚见
[部署与发布说明](./docs/14-部署与发布说明.md)，最终证据见
[最终验收报告](./docs/15-最终验收报告.md)。

## 本地质量检查

项目要求 Python 3.12、Node.js 20～24、pnpm 10～11、`uv`，或直接使用 Docker
Desktop。安装依赖后运行：

```shell
pnpm check
```

该命令依次执行格式检查、Lint、类型检查和测试，与 GitHub Actions 保持一致。

## 新机器快速开始（Fixture，无需 API Key）

```powershell
conda env create --prefix .\.conda\env --file environment.yml
conda activate .\.conda\env
uv sync --active --all-groups --frozen
pnpm install --frozen-lockfile
Copy-Item .env.example .env
python -m apps.api.config doctor
pnpm demo
```

`pnpm demo` 会生成一份北京一日、三个候选方案的完整合成结果摘要，用于确认 Python、领域契约、Run Service、Artifact 和最终聚合均可工作。它不访问网络，也不读取真实库存。

启动完整 Web 产品：

```powershell
docker compose up --build
```

等待容器健康后访问：

- 产品页：`http://localhost:5173`
- OpenAPI：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

停止服务使用 `docker compose down`。只有明确需要清空本地 PostgreSQL 数据时才使用
`docker compose down --volumes`。

项目专用 Conda 环境、uv 虚拟环境、依赖缓存和 `.env` 均被 Git 忽略。默认
`MOCK_MODE=true`，不要求任何 Provider Key；真实 Key 只写本机 `.env` 或部署平台的 Secret。

## 发布验收

```powershell
pnpm check
pnpm demo
docker compose config --quiet
```

推送形如 `v1.0.0` 的 Tag 后，Release 工作流会重新执行迁移、质量闸门、演示和镜像构建，全部成功后创建不可变 GitHub Release。

## 参与贡献

请先阅读 [贡献指南](./CONTRIBUTING.md)。本项目采用 MIT License。
