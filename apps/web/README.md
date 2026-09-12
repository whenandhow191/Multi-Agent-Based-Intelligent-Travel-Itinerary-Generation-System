# Web 应用

`apps/web` 是 React + TypeScript + Vite 单页应用，当前提供可访问的 C03 占位页，并探测 FastAPI `/health`。

边界：前端只消费后端验证后的结构化结果，不自行决定路线顺序或重新计算行程事实。

开发命令：

```shell
pnpm --filter @travel/web dev
```

