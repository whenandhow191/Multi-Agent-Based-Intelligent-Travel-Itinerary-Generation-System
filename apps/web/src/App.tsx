import { useEffect, useState } from "react";

import {
  checkHealth,
  createRun,
  listModels,
  readProgress,
  readResult,
} from "./api";
import { CollaborationPanel } from "./components/CollaborationPanel";
import { ItineraryExplorer } from "./components/ItineraryExplorer";
import { ReplanningPanel } from "./components/ReplanningPanel";
import { RunStatus } from "./components/RunStatus";
import { TripRequestForm } from "./components/TripRequestForm";
import type {
  ApiProblem,
  ModelCatalog,
  PenguinModelId,
  RunProgressEvent,
  RunSession,
  TripRequestInput,
  TripRunResult,
} from "./types";

type ApiState = "checking" | "online" | "offline";

export default function App() {
  const [apiState, setApiState] = useState<ApiState>("checking");
  const [session, setSession] = useState<RunSession | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [events, setEvents] = useState<RunProgressEvent[]>([]);
  const [result, setResult] = useState<TripRunResult | null>(null);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    checkHealth(controller.signal)
      .then(async () => {
        setApiState("online");
        try {
          setModelCatalog(await listModels());
        } catch {
          setModelCatalog(null);
        }
      })
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

  useEffect(() => {
    if (!session?.run.result_available) return;
    readResult(session)
      .then(setResult)
      .catch(() => setResult(null));
  }, [session]);

  async function submit(input: TripRequestInput, modelId: PenguinModelId) {
    setSubmitting(true);
    setProblem(null);
    try {
      const created = await createRun(input, modelId);
      setSession(created);
      setEvents([]);
      setResult(null);
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

  function acceptResult(next: TripRunResult) {
    setResult(next);
    setSession((current) =>
      current
        ? { ...current, run: { ...current.run, current_version: next.version } }
        : current,
    );
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
          modelCatalog={modelCatalog}
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
        {result ? <ItineraryExplorer result={result} /> : null}
        {result && session ? (
          <ReplanningPanel
            session={session}
            result={result}
            onResult={acceptResult}
          />
        ) : null}
      </div>
    </main>
  );
}
