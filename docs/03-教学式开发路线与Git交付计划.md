# 基于多 Agent 的智能旅游攻略生成系统——教学式开发路线与 Git 交付计划

> 版本：v1.0  
> 更新日期：2026-09-10  
> 目标：以学习为主，把项目拆成可理解、可运行、可测试、可提交、可回滚的最小检查点。

## 1. 对原七步思路的判断

原七步方向正确，但如果按七次大开发直接完成，每一步会同时出现太多新概念，不利于学习和排错。建议调整为 **9 个学习章节、50 个代码检查点**。

主要调整如下：

1. “整体框架”拆成 Git、目录骨架、开发环境、配置、领域契约、Fixture 和数据库。
2. 在写真实 Agent 前，先完成可测试的 `ModelGateway`、`ToolGateway`、Agent 基类和 Harness 状态机。
3. Brain 和四个业务 Agent 分五次独立实现，每次只验证一个 Agent 的输入、能力边界、工具权限和输出 Schema。
4. 共享黑板先定义接口和内存实现，再实现 PostgreSQL 持久化与并发控制，避免所有 Agent 直接操作数据库。
5. 真实 API 不是第一个数据来源；先用合成 Fixture 跑通全链路，再逐个接入高德、天气和可获得授权的旅行数据。
6. CLI、MCP 和 REST/API 分开实现，但都挂在同一个 `ToolGateway` 下。业务 Agent 不感知传输方式。
7. 模型接口应提前建立；具体模型选型放在 Agent 测试集完成之后，用质量、延迟和“每个成功方案成本”决定。
8. 后端分“运行平台”和“安全/进度流”两步，前端分“输入”“协作过程”“最终方案”“修改导出”四步。
9. 测试不是最后一步补做。每个检查点必须自带测试，末尾只做系统级加固和发布验收。

## 2. 每个检查点的统一完成标准

每个检查点只有满足以下条件才算完成：

- 只引入一个主要学习主题。
- 有清楚的文件用途说明，新增目录包含 `README.md`，公开模块包含 docstring。
- 更新 `docs/04-代码地图.md`，说明新增或调整的每个源码文件负责什么、依赖谁、被谁调用。
- 提供可执行命令或最小演示。
- 新增功能有对应测试，原有测试保持通过。
- 不提交 `.env`、API Key、Token、真实用户行程和未经许可的供应商原始数据。
- 提交信息能够说明本步目的；提交后立即推送到 GitHub。
- 代码、测试、文档在同一个 commit 中保持一致。

## 3. 50 个教学式代码检查点

### 第一章：仓库与工程基础

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C00 | 初始化 Git；加入现有需求、技术方案和本路线；建立 `.gitignore`、License、贡献说明、Issue/PR 模板 | GitHub 可以查看项目设计基线，CI 占位任务成功 | `chore: initialize repository and documentation baseline` |
| C01 | 建立 monorepo：`apps/api`、`apps/web`、`packages/domain`、`packages/harness`、`packages/agents`、`packages/tools`、`packages/evals`、`tests` | 每个目录有 README 解释用途；空包可被导入 | `chore: scaffold monorepo structure` |
| C02 | 配置 Python、Node、包管理、格式化、Lint、类型检查、pre-commit 和 GitHub Actions | 一条命令运行格式、类型和空测试；CI 与本地一致 | `build: configure development toolchain and ci` |
| C03 | 建立 Docker Compose：PostgreSQL；加入 FastAPI `/health` 和前端占位页 | `docker compose up` 后 API、数据库、Web 均可访问 | `build: add local development runtime` |
| C04 | 配置与密钥系统：`.env.example`、Pydantic Settings、配置分层、启动检查 | `config doctor` 只显示 present/missing，不回显密钥 | `feat: add typed configuration and secret checks` |
| C05 | 建立 `docs/04-代码地图.md` 和 Architecture Decision Record 模板 | 用户能按文档找到每个文件和关键设计原因 | `docs: add code map and adr workflow` |

### 第二章：领域契约与确定性基础

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C06 | 定义 `TripRequest`、硬约束、软偏好、澄清问题 | 示例用户输入可转换成合法 JSON | `feat(domain): define trip request contracts` |
| C07 | 定义 `Task`、`Event`、`ArtifactRef`、`Claim`、`Evidence`、状态枚举 | 非法状态和缺失字段会被 Schema 拒绝 | `feat(domain): define harness message contracts` |
| C08 | 定义 POI、路线、天气、住宿、交通和坐标系内部模型 | 不同供应商数据可以映射到同一内部结构 | `feat(domain): define canonical travel data models` |
| C09 | 定义 `PlanCandidatesArtifact`、`ReviewArtifact`、`FinalPlanBundle` | 能生成并校验一份完整示例攻略 JSON | `feat(domain): define itinerary output contracts` |
| C10 | 建立纯合成 Fixture 工厂和 `ScriptedModel` | 无网络、无 Key 也能稳定得到固定测试响应 | `test: add synthetic fixtures and scripted model` |
| C11 | 实现预算、时间窗、活动重叠、开放时间、坐标和引用完整性校验器 | 故意构造的超预算、闭馆、重叠方案会失败 | `feat(domain): add deterministic validators` |

### 第三章：自建 Harness 内核

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C12 | 定义 provider-neutral `ModelGateway` 与 Usage 结构，不接真实厂商 | 虚拟模型通过统一接口返回结构化结果 | `feat(harness): add model gateway contract` |
| C13 | 定义 `ToolGateway`、工具注册、Schema 校验、allowlist 和 Fixture Tool | 能列出工具并拒绝越权/非法参数 | `feat(harness): add tool gateway and registry` |
| C14 | 实现 Agent 基类、上下文、步骤上限、结构化输出和错误分类 | 一个 Echo Agent 能完成、失败和超时 | `feat(harness): implement base agent lifecycle` |
| C15 | 实现任务 DAG、依赖判断、并行调度、取消、超时与有限重试 | 独立任务并发、依赖任务按序执行 | `feat(harness): add dag scheduler and retries` |
| C16 | 建立 PostgreSQL migration、Repository、原子任务租约和幂等事件 | 两个 worker 不会重复领取同一任务 | `feat(harness): persist runs tasks and events` |
| C17 | 实现 heartbeat、checkpoint、进程重启恢复和 Outbox | 运行中断后能从检查点继续，不重复发布事件 | `feat(harness): add checkpoints and recovery` |

### 第四章：1+4 Agent 分别实现

每个 Agent 都先使用 `ScriptedModel + Fixture Tool` 独立运行。这样可以判断问题究竟来自 Agent 逻辑、模型还是外部数据。

| 编号 | Agent | 独立实现范围 | 独立验收 | 建议 commit |
|---|---|---|---|---|
| C18 | A0 Brain / Coordinator | 澄清需求、生成固定允许范围内的 DAG、分发任务、预算控制、冲突裁决；调度规则仍由 Harness 代码掌控 | 能从 `TripRequest` 生成任务图；缺关键条件进入 `WAITING_USER`；不直接编造旅行事实 | `feat(agent): implement coordinator brain` |
| C19 | A1 目的地情报 Agent | 调用 POI/内容工具，归一化候选点，输出来源、查询时间、未知项和游玩时长估计 | 单独输入成都需求，输出通过 `DestinationIntelArtifact` Schema；无来源的事实被拒绝 | `feat(agent): implement destination intelligence agent` |
| C20 | A2 交通与住宿 Agent | 获取城际交通、住宿区域与候选、入住退房约束和衔接风险；不负责构造全量路线矩阵 | 单独输出 `MobilityLodgingArtifact`；测试价格未知、供应商失败和人工确认降级 | `feat(agent): implement mobility and lodging agent` |
| C21 | A3 行程规划 Agent | 读取 A1/A2 Artifact，使用确定性路线矩阵、预算和 OR-Tools 生成 2～3 个不同侧重方案 | 不联网也能生成无重叠的候选方案；硬约束不满足时明确失败 | `feat(agent): implement itinerary planning agent` |
| C22 | A4 审校与风险 Agent | 规则检查结果之上审查证据缺口、语义矛盾、节奏和风险，输出结构化 patch request | 能发现预置的闭馆、超预算、折返、缺证据和未知硬约束 | `feat(agent): implement critic and risk agent` |

### 第五章：共享黑板与 1+4 协作

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C23 | 实现 Blackboard 接口、内存实现、PostgreSQL 实现、Artifact 版本和 lineage | CLI 能按 `run_id/task_id/type/version` 读写 Artifact；并发写入有版本冲突保护 | `feat(harness): implement shared artifact blackboard` |
| C24 | 实现 1+4 fan-out/fan-in：A1/A2 并行，路线矩阵后处理，A3 规划，A4 审校 | trace 能证明真实并行和结构化 Artifact 交接 | `feat(workflow): orchestrate one plus four agents` |
| C25 | 实现局部返工、最多两轮修订、依赖失效传播和人工澄清恢复 | 修改酒店只重算受影响路线/计划/审校，不重跑全部数据 | `feat(workflow): add targeted repair and invalidation` |
| C26 | 实现 FinalAggregator、确定性 Markdown/JSON 渲染、引用存在性检查和多方案比较 | 即使关闭最终润色模型，也能输出完整可用方案 | `feat(workflow): add verified final aggregation` |

### 第六章：真实工具与数据源逐个接入

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C27 | 通用 HTTP Provider 基类：超时、指数退避、限流、熔断、缓存、调用审计、字段级存储策略 | 用本地假服务模拟 200/429/500/超时并验证行为 | `feat(tools): add resilient provider client` |
| C28 | 高德地理编码与 POI Adapter | 真实开发 Key 下可检索点位；无 Key 自动切 Fixture | `feat(tools): integrate amap geocoding and poi` |
| C29 | 高德路线、路线批处理和前端地图 payload；处理 GCJ-02/WGS84 | 给定 Top-K 候选可生成受控规模路线矩阵和 polyline | `feat(tools): integrate routes and coordinate handling` |
| C30 | 天气 Adapter：QWeather 或 Open-Meteo，含预报范围、更新时间和降级 | 过远日期不伪装成精确预报；失败时使用 Fixture/气候提示 | `feat(tools): integrate weather provider` |
| C31 | 航班、铁路、酒店接口骨架分别实现；默认 Fixture、人工输入或官方链接 | 没有商业权限也能演示；测试数据不会标成实时库存 | `feat(tools): add transport and lodging provider stubs` |
| C32 | CLI Adapter 与自建 `travelctl`：doctor、tools list/call、run inspect、blackboard get | CLI 调用仍经过 ToolGateway，参数不用 `shell=True` 拼接 | `feat(cli): add travelctl and cli adapter` |
| C33 | MCP Adapter：stdio 与 Streamable HTTP；工具发现、名称映射、超时和权限 | 同一内部工具可以在 REST/Fixture/MCP profile 间切换，业务 Agent 无改动 | `feat(tools): add optional mcp transport` |

### 第七章：模型配置、成本与评测

具体 model ID 只是可替换的 2026-09 默认值，不应成为架构依赖。

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C34 | OpenAI Responses/OpenAI-compatible/Ollama Provider Adapter | 同一个 Agent 可通过配置切换云模型和本地模型 | `feat(models): add provider adapters` |
| C35 | `providers.yaml` 与 `agents.yaml`：每个 Agent 配 provider/model/reasoning/max tokens/timeout | 修改配置即可换模型，无需修改 Agent 代码 | `feat(models): add per-agent model profiles` |
| C36 | 成本账本：输入、缓存、输出、推理 Token、工具费、run/agent/day 预算 | 每次运行显示成本估算；超过硬上限返回 partial | `feat(models): add usage ledger and budget gates` |
| C37 | 失败与升级路由：429、超时、余额、Schema 失败分别处理；Brain 降级必须显式标记 | 模拟主模型失败后按策略重试/切换，不能无限循环 | `feat(models): add fallback and escalation policies` |
| C38 | 建立 20～50 条模型基准集，按 Agent 分开评测 | 输出 Schema 通过率、硬约束、引用率、延迟和每个成功方案成本 | `test(models): benchmark agent model candidates` |

初始能力分配建议：

| Agent | 所需档位 | 省钱原则 | 2026-09 OpenAI 示例 |
|---|---|---|---|
| A0 Brain | 高质量推理 | 只做拆解、冲突裁决和修订决策，不抓每个 POI、不重写全部长文 | 默认 `gpt-5.6-sol`；确需最高能力时升级 `gpt-6-astra` |
| A1 目的地情报 | 低成本、稳定工具调用 | 事实来自工具，模型只抽取、分类、去重 | `gpt-5.6-luna` 或本地模型 |
| A2 交通住宿 | 低至中档 | 价格和时间由 API/代码计算，模型只解释风险 | `gpt-5.6-luna`，复杂衔接时升级 Terra |
| A3 行程规划 | 中档推理 | 候选排序交模型，可行性和路线交 OR-Tools/规则 | `gpt-5.6-terra` |
| A4 审校风险 | 中档、强指令遵循 | 硬规则先运行，模型只检查语义矛盾和风险 | `gpt-5.6-terra`，疑难冲突可升级 Sol |

OpenAI 官方当前列出的每百万输入/输出 Token 价格为：Astra 10/50 美元、Sol 4/20 美元、Terra 2/12 美元、Luna 0.20/1.20 美元。实际可用性与价格应在 C38 重新核对，最终通过本项目基准集决定，而不是只看 Token 单价。[OpenAI Models](https://developers.openai.com/api/docs/models)

同一 Provider 通常只需一组服务端项目 Key，不需要每个 Agent 各有一把 Key。不同 Agent 的逻辑隔离由配置、预算和 trace 完成。

```dotenv
# .env.example：只提交变量名，不提交值
OPENAI_API_KEY=
AMAP_WEB_SERVICE_KEY=
AMAP_JS_KEY=
QWEATHER_API_KEY=
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/travel_agent
```

```yaml
# config/agents.yaml 示例
agents:
  brain:
    provider: openai
    model: gpt-5.6-sol
    reasoning: medium
    max_output_tokens: 5000
  destination:
    provider: ollama
    model: <由 C38 评测确定>
    max_output_tokens: 1800
  mobility_lodging:
    provider: openai
    model: gpt-5.6-luna
    reasoning: low
  planner:
    provider: openai
    model: gpt-5.6-terra
    reasoning: medium
  critic:
    provider: openai
    model: gpt-5.6-terra
    reasoning: low
```

API Key 只能由 FastAPI 服务端从环境变量或 Secret Manager 读取，不进入 React、浏览器日志、Git 或 Agent 输出。前端高德 JS Key 必须与服务端 Web Service Key 分开，使用独立权限和域名限制。OpenAI 官方也明确要求 API Key 不得暴露在浏览器或客户端代码中。[OpenAI API Reference](https://developers.openai.com/api/reference/overview)

### 第八章：后端与前端产品化

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C39 | FastAPI Trip Run API：创建、读取、取消、澄清、结果和多方案比较 | OpenAPI 页面可完整操作 Fixture 运行 | `feat(api): expose trip run endpoints` |
| C40 | SSE 进度、匿名运行令牌、删除、错误模型和请求幂等 | 浏览器可看到实时节点状态；无权用户不能查看/删除他人运行 | `feat(api): add progress security and lifecycle` |
| C41 | React 基础：需求表单、API Client、状态管理、错误与降级页面 | 用户可提交请求并看到运行状态 | `feat(web): add trip request and run status pages` |
| C42 | 协作调试页：1+4 时间线、Task DAG、工具调用摘要、Artifact lineage、成本 | 能直观看到五个 Agent 是否真的协作 | `feat(web): visualize agent collaboration` |
| C43 | 攻略页：逐日时间轴、地图、预算、证据、风险和方案对比 | 地图点位和日程一一对应，可切换方案 | `feat(web): render itinerary map and comparison` |
| C44 | 自然语言修改、局部重规划、版本差异、Markdown/JSON 导出 | “第二天轻松一点”只重算受影响节点，可恢复上一版 | `feat(web): add replanning versions and exports` |

### 第九章：系统加固与发布

| 编号 | 学习目标与实现内容 | 本步可见结果 | 建议 commit |
|---|---|---|---|
| C45 | OpenTelemetry、结构化日志、模型/工具延迟和成本指标 | 可从最终 Claim 追溯到 Agent、Artifact 和工具调用 | `feat(obs): add end-to-end telemetry` |
| C46 | 集成、E2E、契约、Prompt Injection、SSRF、密钥脱敏和数据删除测试 | 安全样例不能改变系统规则或调用未授权工具 | `test: add integration and security suites` |
| C47 | 并发、限流、超时、崩溃恢复、Provider 故障和性能测试 | 单 Provider 故障仍返回明确降级或部分结果 | `test: add resilience and performance suites` |
| C48 | 单 Agent 与 1+4 Agent 对照评测，修复发现的问题 | 有质量、成本、延迟对照报告；关键验收阈值通过 | `test: evaluate single versus multi agent workflow` |
| C49 | 全新环境安装、部署说明、演示数据、最终验收和 `v1.0.0` | 新机器按 README 能启动并完成演示旅行计划 | `release: prepare version 1.0.0` |

最终路线实际为 **9 个章节、C00～C49 共 50 个检查点**。编号多是刻意的：每一步都保持可理解、可验证、可回滚。

## 4. Git 与 GitHub 操作策略

### 4.1 当前状态

截至 2026-09-10，当前项目目录还不是 Git 仓库，也没有 remote。应先在 GitHub 创建一个空仓库。

创建仓库时建议：

- 可以选择 Private 或 Public。
- 不勾选自动生成 README、`.gitignore` 或 License，避免与本地文件产生两套初始历史。
- 创建后提供 HTTPS 或 SSH 仓库地址。
- 在确认 remote 地址后，再执行 `git init -b main`、首次提交和推送。

### 4.2 分支、提交和标签

不建议为 50 个小检查点创建 50 个长期分支。推荐：

- 每个学习章节一个分支，例如 `chapter/04-agents`。
- 每个 C 编号一个独立 commit，并在完成后立即 push；GitHub 上可以逐步检查和回滚。
- 每章完成后创建 PR 合并到 `main`。
- PR 必须通过 CI，采用 squash 或 merge commit 均可；如果希望保留每个教学 commit，优先普通 merge。
- 每章合并后打 annotated tag，例如 `chapter-04-agents-complete`。
- 重要版本点：C17=`v0.1.0`，C26=`v0.2.0`，C38=`v0.3.0`，C44=`v0.4.0`，C49=`v1.0.0`。

若某个检查点风险较高，例如数据库迁移、MCP、模型降级或删除功能，可以单独开短分支和 PR。

### 4.3 回滚方式

- 回到某一步学习：检出对应 commit 或 tag，新建练习分支。
- 撤销已经推送的功能：使用 `git revert` 产生反向 commit，不改写公开历史。
- 未提交的个人实验：放在临时分支，不混进章节分支。
- 数据库 migration 只新增，不修改已经合并和运行过的旧 migration。

### 4.4 密钥安全

以下内容永远不得提交：

- `.env`、私钥、Provider API Key、数据库生产密码。
- 真实用户行程、精确住址、身份证件或联系方式。
- 不允许持久化的地图/酒店/票务原始响应。
- 本地模型权重、数据库 volume、日志、trace 原文和构建产物。

GitHub 中需要运行真实集成测试时，把 Key 放在 GitHub Actions Secrets 中；普通 PR 默认只运行 Fixture 测试，真实 Provider 测试手动触发并设置费用上限。

## 5. 推荐的实际开发顺序

按照 C00 → C49 顺序执行。四个关键里程碑是：

1. C10：项目第一次能在完全离线条件下重放数据和模型结果。
2. C17：自建 Harness 内核可以恢复长运行任务。
3. C26：1+4 Agent 可以在 Fixture 模式端到端协作并输出方案。
4. C44：前后端完整可交互。
5. C49：评测、故障恢复、文档和部署达到发布标准。

真实 API Key 最早在 C28 才需要；真实模型 Key 最早在 C34 才需要。在此之前不应因为没有 Key 阻塞学习。

## 6. 用户需要先完成的事项

开始 C00 前只需要：

1. 在 GitHub 创建一个空仓库，不初始化 README、`.gitignore` 或 License。
2. 把仓库 HTTPS 或 SSH URL 发回。
3. 说明仓库要设为 Public 还是 Private；这不影响代码结构。

模型和地图 Key 暂时都不需要申请。到 C28/C34 时，项目会先提供 `doctor` 命令和 `.env.example`，再逐项告诉用户需要申请什么、填在哪里以及如何验证。
