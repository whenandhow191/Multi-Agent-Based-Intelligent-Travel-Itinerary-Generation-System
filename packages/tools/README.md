# Tools

`packages/tools` 保存统一 Tool Gateway、工具注册表与 REST/CLI/MCP/Fixture 适配器。

边界：供应商私有响应在此归一化；上层只依赖 `places.search`、`routes.compute` 等稳定内部工具名。

