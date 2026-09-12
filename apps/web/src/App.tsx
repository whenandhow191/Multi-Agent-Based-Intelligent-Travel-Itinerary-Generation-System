import { useEffect, useState } from "react";

type ApiState = "checking" | "online" | "offline";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export default function App() {
  const [apiState, setApiState] = useState<ApiState>("checking");

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${apiBaseUrl}/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error("API health check failed");
        }
        setApiState("online");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setApiState("offline");
      });

    return () => controller.abort();
  }, []);

  const stateLabel = {
    checking: "正在检查 API",
    online: "API 已连接",
    offline: "API 暂不可用",
  }[apiState];

  return (
    <main className="page-shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">CHAPTER 01 · ENGINEERING FOUNDATION</p>
        <h1 id="page-title">多 Agent 智能旅游攻略</h1>
        <p className="intro">
          自建 Harness 将协调目的地情报、交通住宿、行程规划与审校风险
          Agent，生成可执行、可解释的旅行方案。
        </p>
        <div className={`status status--${apiState}`} role="status">
          <span aria-hidden="true" />
          {stateLabel}
        </div>
        <p className="next-step">工程运行时已就绪，领域契约将在第二章实现。</p>
      </section>
    </main>
  );
}
