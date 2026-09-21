import { useEffect, useState, type FormEvent } from "react";

import { probeModel } from "../api";
import type { ModelCatalog, PenguinModelId, TripRequestInput } from "../types";

interface Props {
  disabled?: boolean;
  modelCatalog: ModelCatalog | null;
  onSubmit: (input: TripRequestInput, modelId: PenguinModelId) => Promise<void>;
}

export function TripRequestForm({
  disabled = false,
  modelCatalog,
  onSubmit,
}: Props) {
  const [origin, setOrigin] = useState("上海");
  const [destination, setDestination] = useState("北京");
  const [date, setDate] = useState("2026-10-01");
  const [budget, setBudget] = useState("1000");
  const [pace, setPace] =
    useState<TripRequestInput["soft_preferences"]["pace"]>("balanced");
  const [notes, setNotes] = useState("希望参观故宫并安排公园，优先公共交通。");
  const [modelId, setModelId] = useState<PenguinModelId>("claude-sonnet-5");
  const [probeState, setProbeState] = useState<
    "idle" | "checking" | "ok" | "failed"
  >("idle");

  useEffect(() => {
    const firstAvailable = modelCatalog?.models.find(
      (model) => model.available,
    );
    if (firstAvailable) setModelId(firstAvailable.id);
  }, [modelCatalog]);

  async function testConnection() {
    setProbeState("checking");
    try {
      await probeModel(modelId);
      setProbeState("ok");
    } catch {
      setProbeState("failed");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit(
      {
        request_id: `web_${Date.now()}`,
        origin,
        destination,
        start_date: date,
        end_date: date,
        days: 1,
        timezone: "Asia/Shanghai",
        party: { adults: 2, children: 0, seniors: 0 },
        budget: {
          total: budget,
          currency: "CNY",
          policy: "hard",
          flexibility_percent: 0,
        },
        hard_constraints: { must_visit: ["故宫博物院"], excluded_places: [] },
        soft_preferences: {
          interests: ["历史", "公园"],
          pace,
          transport_modes: ["public_transit", "walking"],
        },
        natural_language_notes: notes,
      },
      modelId,
    );
  }

  return (
    <form className="request-card" onSubmit={submit} aria-label="旅行需求表单">
      <div className="section-heading">
        <div>
          <p className="kicker">01 / TRIP BRIEF</p>
          <h2>从一份清晰的需求开始</h2>
        </div>
        <span className="fixture-badge">Fixture 演示</span>
      </div>
      <div className="form-grid">
        <label>
          出发地
          <input
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            required
          />
        </label>
        <label>
          目的地
          <input
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            required
          />
        </label>
        <label>
          出行日期
          <input
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            required
          />
        </label>
        <label>
          总预算（CNY）
          <input
            type="number"
            min="1"
            value={budget}
            onChange={(event) => setBudget(event.target.value)}
            required
          />
        </label>
        <label>
          行程节奏
          <select
            value={pace}
            onChange={(event) => setPace(event.target.value as typeof pace)}
          >
            <option value="relaxed">轻松</option>
            <option value="balanced">均衡</option>
            <option value="intensive">充实</option>
          </select>
        </label>
        <label className="model-field">
          运行模型
          <span className="model-control">
            <select
              value={modelId}
              onChange={(event) => {
                setModelId(event.target.value as PenguinModelId);
                setProbeState("idle");
              }}
            >
              {(
                modelCatalog?.models ?? [
                  { id: "claude-sonnet-5", name: "claude-sonnet-5" },
                  { id: "claude-opus-5", name: "claude-opus-5" },
                  { id: "gpt-5.6-luna", name: "gpt-5.6-luna" },
                  { id: "gpt-5.6-sol", name: "gpt-5.6-sol" },
                  { id: "gpt-5.6-terra", name: "gpt-5.6-terra" },
                ]
              ).map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                  {"available" in model && !model.available ? "（未验证）" : ""}
                </option>
              ))}
            </select>
            <button
              className="probe-action"
              type="button"
              disabled={disabled || probeState === "checking"}
              onClick={testConnection}
            >
              {probeState === "checking" ? "检测中…" : "测试连接"}
            </button>
          </span>
          <small className={`probe-state probe-state--${probeState}`}>
            {probeState === "ok"
              ? "连接成功，密钥不会发送到浏览器。"
              : probeState === "failed"
                ? "连接失败，请检查 .env.local、模型权限和余额。"
                : modelCatalog?.connection === "verified"
                  ? "已读取 PenguinAPI 模型列表。"
                  : "需在 .env.local 配置 PENGUIN_API_KEY。"}
          </small>
        </label>
        <label className="field-wide">
          补充说明
          <textarea
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={3}
          />
        </label>
      </div>
      <button className="primary-action" type="submit" disabled={disabled}>
        {disabled ? "正在生成协作任务…" : "生成旅行方案"}
      </button>
      <p className="form-note">
        模型连接由后端代理；当前产品行程仍使用合成数据，不代表实时价格与库存。
      </p>
    </form>
  );
}
