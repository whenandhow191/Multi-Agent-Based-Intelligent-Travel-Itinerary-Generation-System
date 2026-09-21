import type { RunProgressEvent } from "../types";

const agents = [
  ["A0", "总控协调", "coordinator"],
  ["A1", "目的地情报", "destination_intelligence"],
  ["A2", "交通与住宿", "mobility_lodging"],
  ["A3", "行程规划", "itinerary_planner"],
  ["A4", "审校与风险", "critic"],
] as const;

export function CollaborationPanel({ events }: { events: RunProgressEvent[] }) {
  const cost = events.reduce(
    (total, event) => total + event.estimated_cost_microunits,
    0,
  );
  const artifacts = events.flatMap((event) => event.artifact_ids);
  const tools = [...new Set(events.flatMap((event) => event.tool_calls))];

  return (
    <section
      className="collaboration-card"
      aria-labelledby="collaboration-title"
    >
      <div className="section-heading">
        <div>
          <p className="kicker">03 / COLLABORATION TRACE</p>
          <h2 id="collaboration-title">五个 Agent，真实交接</h2>
        </div>
        <span className="cost-badge">成本估算 {cost} μUSD</span>
      </div>

      <div className="agent-strip">
        {agents.map(([code, name, id]) => {
          const complete = events.some(
            (event) => event.agent_id === id && event.state === "succeeded",
          );
          return (
            <article
              className={
                complete ? "agent-chip agent-chip--done" : "agent-chip"
              }
              key={id}
            >
              <strong>{code}</strong>
              <span>{name}</span>
              <i aria-label={complete ? "已完成" : "等待中"} />
            </article>
          );
        })}
      </div>

      <div className="trace-grid">
        <div>
          <h3>任务时间线</h3>
          <ol className="timeline">
            {events.map((event) => (
              <li key={event.sequence}>
                <span>{String(event.sequence).padStart(2, "0")}</span>
                <div>
                  <strong>{event.task_id ?? event.event_type}</strong>
                  <p>{event.message}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <div className="trace-details">
          <h3>Task DAG</h3>
          <div className="dag" aria-label="任务依赖图">
            <span>A0</span>
            <b>→</b>
            <span>A1 + A2</span>
            <b>→</b>
            <span>路线矩阵</span>
            <b>→</b>
            <span>A3</span>
            <b>→</b>
            <span>A4</span>
          </div>
          <h3>工具调用摘要</h3>
          <div className="tag-list">
            {tools.map((tool) => (
              <code key={tool}>{tool}</code>
            ))}
          </div>
          <h3>Artifact lineage</h3>
          <ul className="artifact-list">
            {artifacts.map((artifact, index) => (
              <li key={artifact}>
                <span>{index + 1}</span>
                {artifact}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
