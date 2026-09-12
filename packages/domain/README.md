# Domain

`packages/domain` 保存与模型、供应商和传输协议无关的领域契约，包括旅行请求、地点、路线、证据、任务消息和最终行程结构。

依赖方向：它位于依赖图底层，不依赖 API、Harness、Agent 或具体工具适配器。

## 当前模块

- `common.py`：不可变、禁止多余字段的领域模型基类与公共标量。
- `trip_request.py`：`TripRequest`、同行人、预算、硬约束、软偏好和澄清问题。
- `messaging.py`：`Task`、`Event`、`ArtifactRef`、`Claim`、`Evidence` 与状态枚举。
- `travel.py`：POI、路线、天气、住宿、城际交通、费用区间与坐标系内部模型。
- `itinerary.py`：候选方案、审校、修复请求、对比和 `FinalPlanBundle`。

验证示例：

```powershell
uv run pytest tests/test_trip_request.py
uv run pytest tests/test_messaging.py
uv run pytest tests/test_travel_models.py
uv run pytest tests/test_itinerary_contracts.py
```
