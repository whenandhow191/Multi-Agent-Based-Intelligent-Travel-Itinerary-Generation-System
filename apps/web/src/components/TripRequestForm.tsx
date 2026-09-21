import { useState, type FormEvent } from "react";

import type { TripRequestInput } from "../types";

interface Props {
  disabled?: boolean;
  onSubmit: (input: TripRequestInput) => Promise<void>;
}

export function TripRequestForm({ disabled = false, onSubmit }: Props) {
  const [origin, setOrigin] = useState("上海");
  const [destination, setDestination] = useState("北京");
  const [date, setDate] = useState("2026-10-01");
  const [budget, setBudget] = useState("1000");
  const [pace, setPace] =
    useState<TripRequestInput["soft_preferences"]["pace"]>("balanced");
  const [notes, setNotes] = useState("希望参观故宫并安排公园，优先公共交通。");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit({
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
    });
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
        本章使用项目合成数据，演示完整产品流程，不代表实时价格与库存。
      </p>
    </form>
  );
}
