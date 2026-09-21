# Web 应用

`apps/web` 是 React + TypeScript + Vite 单页产品，提供需求提交、运行状态、1+4 协作 trace、方案/地图比较、局部重规划、版本恢复和 Markdown/JSON 导出。

边界：前端只消费后端验证后的结构化结果，不自行决定路线顺序或重新计算行程事实。

开发命令：

```shell
pnpm --filter @travel/web dev
```
