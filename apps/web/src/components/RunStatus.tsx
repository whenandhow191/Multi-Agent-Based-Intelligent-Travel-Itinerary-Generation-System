import type { TripRun } from "../types";

const labels: Record<TripRun["state"], string> = {
  running: "协作进行中",
  waiting_user: "等待你的确认",
  succeeded: "方案已生成",
  cancelled: "运行已取消",
  failed: "生成失败",
};

export function RunStatus({ run }: { run: TripRun }) {
  return (
    <section className="run-status" aria-live="polite">
      <div>
        <p className="kicker">02 / RUN STATUS</p>
        <h2>{labels[run.state]}</h2>
        <p>运行编号 {run.run_id}</p>
      </div>
      <div className={`state-orb state-orb--${run.state}`} aria-hidden="true" />
      <dl>
        <div>
          <dt>目的地</dt>
          <dd>{run.request.destination}</dd>
        </div>
        <div>
          <dt>运行模式</dt>
          <dd>{run.fixture_mode ? "Fixture" : "Live"}</dd>
        </div>
        <div>
          <dt>结果</dt>
          <dd>{run.result_available ? "可查看" : "生成中"}</dd>
        </div>
      </dl>
    </section>
  );
}
