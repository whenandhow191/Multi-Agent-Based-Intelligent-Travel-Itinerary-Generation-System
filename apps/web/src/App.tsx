import { useEffect, useState } from "react";

import { checkHealth, createRun, readProgress } from "./api";
import { CollaborationPanel } from "./components/CollaborationPanel";
import { RunStatus } from "./components/RunStatus";
import { TripRequestForm } from "./components/TripRequestForm";
import type {
  ApiProblem,
  RunProgressEvent,
  RunSession,
  TripRequestInput,
} from "./types";

type ApiState = "checking" | "online" | "offline";

export default function App() {
  const [apiState, setApiState] = useState<ApiState>("checking");
  const [session, setSession] = useState<RunSession | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [events, setEvents] = useState<RunProgressEvent[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    checkHealth(controller.signal)
      .then(() => setApiState("online"))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError")
          return;
        setApiState("offline");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!session) return;
    readProgress(session)
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [session]);

  async function submit(input: TripRequestInput) {
    setSubmitting(true);
    setProblem(null);
    try {
      const created = await createRun(input);
      setSession(created);
      setEvents([]);
      sessionStorage.setItem(
        `travel-run:${created.run.run_id}`,
        created.accessToken,
      );
    } catch (error) {
      setProblem({
        message: error instanceof Error ? error.message : "无法创建旅行方案",
      });
    } finally {
      setSubmitting(false);
    }
  }

  const stateLabel = {
    checking: "正在检查 API",
    online: "API 已连接",
    offline: "API 暂不可用",
  }[apiState];

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="行迹首页">
          <span>行迹</span>
          <small>Agentic Travel Studio</small>
        </a>
        <div className={`api-pill api-pill--${apiState}`} role="status">
          <i aria-hidden="true" />
          {stateLabel}
        </div>
      </header>

      <section className="hero" id="top" aria-labelledby="page-title">
        <div>
          <p className="eyebrow">ONE BRAIN · FOUR SPECIALISTS</p>
          <h1 id="page-title">
            让每一段旅程
            <br />
            <em>有据可循。</em>
          </h1>
        </div>
        <p className="intro">
          一个总控协调四个专业
          Agent，把目的地事实、交通住宿、路线规划与风险审校汇成可比较、可追溯的旅行方案。
        </p>
      </section>

      <div className="workspace">
        <TripRequestForm
          disabled={submitting || apiState === "offline"}
          onSubmit={submit}
        />
        {problem ? (
          <section className="error-card" role="alert">
            <strong>暂时无法继续</strong>
            <p>{problem.message}</p>
            <button type="button" onClick={() => setProblem(null)}>
              返回修改需求
            </button>
          </section>
        ) : null}
        {session ? <RunStatus run={session.run} /> : null}
        {events.length ? <CollaborationPanel events={events} /> : null}
      </div>
    </main>
  );
}
