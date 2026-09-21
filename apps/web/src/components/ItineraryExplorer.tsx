import { useMemo, useState } from "react";

import type { CostEstimate, TripRunResult } from "../types";

function formatCost(cost: CostEstimate) {
  if (cost.kind === "unknown") return `待确认 ${cost.currency}`;
  if (cost.lower === cost.upper) return `${cost.lower} ${cost.currency}`;
  return `${cost.lower}–${cost.upper} ${cost.currency}`;
}

function time(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function ItineraryExplorer({ result }: { result: TripRunResult }) {
  const [planId, setPlanId] = useState(result.bundle.plans[0]?.plan_id ?? "");
  const [activePlace, setActivePlace] = useState<string | null>(null);
  const plan =
    result.bundle.plans.find((item) => item.plan_id === planId) ??
    result.bundle.plans[0];
  const comparison = result.bundle.comparison.entries.find(
    (item) => item.plan_id === plan?.plan_id,
  );
  const bounds = useMemo(() => {
    const longitudes = result.map_points.map((point) => point.longitude);
    const latitudes = result.map_points.map((point) => point.latitude);
    return {
      minX: Math.min(...longitudes),
      maxX: Math.max(...longitudes),
      minY: Math.min(...latitudes),
      maxY: Math.max(...latitudes),
    };
  }, [result.map_points]);

  if (!plan || !comparison) return null;
  const mapPosition = (longitude: number, latitude: number) => ({
    x:
      14 +
      ((longitude - bounds.minX) / Math.max(bounds.maxX - bounds.minX, 0.001)) *
        72,
    y:
      84 -
      ((latitude - bounds.minY) / Math.max(bounds.maxY - bounds.minY, 0.001)) *
        68,
  });

  return (
    <section className="itinerary-card" aria-labelledby="itinerary-title">
      <div className="section-heading">
        <div>
          <p className="kicker">04 / VERIFIED ITINERARY</p>
          <h2 id="itinerary-title">行程、地图与证据同步</h2>
        </div>
        <span className="fixture-badge">版本 v{result.version}</span>
      </div>

      <div className="plan-tabs" role="tablist" aria-label="候选方案">
        {result.bundle.plans.map((item) => (
          <button
            type="button"
            role="tab"
            aria-selected={item.plan_id === plan.plan_id}
            className={
              item.plan_id === plan.plan_id
                ? "plan-tab plan-tab--active"
                : "plan-tab"
            }
            onClick={() => setPlanId(item.plan_id)}
            key={item.plan_id}
          >
            <span>{item.title}</span>
            <small>{formatCost(item.total_cost)}</small>
          </button>
        ))}
      </div>

      <div className="metric-row">
        <div>
          <span>活动</span>
          <strong>{comparison.activity_count}</strong>
        </div>
        <div>
          <span>通勤</span>
          <strong>{comparison.commute_minutes} min</strong>
        </div>
        <div>
          <span>空闲</span>
          <strong>{comparison.free_minutes} min</strong>
        </div>
        <div>
          <span>证据覆盖</span>
          <strong>{Math.round(comparison.evidence_coverage * 100)}%</strong>
        </div>
        <div>
          <span>风险</span>
          <strong>{comparison.risk_count}</strong>
        </div>
      </div>

      <div className="itinerary-layout">
        <div className="day-list">
          {plan.days.map((day, dayIndex) => (
            <article key={day.date}>
              <header>
                <span>DAY {dayIndex + 1}</span>
                <h3>{day.date}</h3>
              </header>
              {day.items.map((item) => (
                <button
                  type="button"
                  className={
                    activePlace === item.place_id
                      ? "schedule-item schedule-item--active"
                      : "schedule-item"
                  }
                  onMouseEnter={() => setActivePlace(item.place_id)}
                  onFocus={() => setActivePlace(item.place_id)}
                  onClick={() => setActivePlace(item.place_id)}
                  key={item.plan_item_id}
                >
                  <time>{time(item.start_at)}</time>
                  <span>
                    <strong>{item.activity}</strong>
                    <small>
                      {formatCost(item.estimated_cost)} ·{" "}
                      {item.evidence_ids.length} 条证据
                    </small>
                  </span>
                </button>
              ))}
            </article>
          ))}
        </div>

        <div className="map-panel">
          <div className="map-topography" aria-label="行程点位地图">
            <svg
              viewBox="0 0 100 100"
              role="img"
              aria-label="点位与日程对应示意图"
            >
              <path
                d="M8 72 C24 18, 56 88, 92 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.3"
                strokeDasharray="3 3"
              />
              {result.map_points.map((point, index) => {
                const position = mapPosition(point.longitude, point.latitude);
                const selected = activePlace === point.place_id;
                return (
                  <g
                    key={point.place_id}
                    transform={`translate(${position.x} ${position.y})`}
                  >
                    <circle
                      r={selected ? 7 : 5}
                      className={
                        selected ? "map-dot map-dot--active" : "map-dot"
                      }
                    />
                    <text y="-9" textAnchor="middle">
                      {index + 1}. {point.name}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
          <p>点选左侧日程可定位对应地点。地图采用 GCJ-02 合成坐标示意。</p>
        </div>
      </div>

      <div className="comparison-table">
        <h3>方案横向比较</h3>
        <table>
          <thead>
            <tr>
              <th>方案</th>
              <th>预算</th>
              <th>通勤</th>
              <th>空闲</th>
              <th>偏好</th>
              <th>风险</th>
            </tr>
          </thead>
          <tbody>
            {result.bundle.comparison.entries.map((entry) => (
              <tr key={entry.plan_id}>
                <td>
                  {
                    result.bundle.plans.find(
                      (item) => item.plan_id === entry.plan_id,
                    )?.title
                  }
                </td>
                <td>{formatCost(entry.total_cost)}</td>
                <td>{entry.commute_minutes} min</td>
                <td>{entry.free_minutes} min</td>
                <td>{Math.round(entry.preference_coverage * 100)}%</td>
                <td>{entry.risk_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <details className="evidence-drawer">
        <summary>
          查看 {result.bundle.evidence.length} 条证据与数据新鲜度
        </summary>
        <ul>
          {result.bundle.evidence.map((item) => (
            <li key={item.evidence_id}>
              <strong>{item.source_name}</strong>
              <span>
                {item.freshness} · {item.source_url_or_provider_id}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
