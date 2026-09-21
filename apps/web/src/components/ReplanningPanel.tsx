import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  downloadRun,
  readVersionDiff,
  readVersions,
  replanRun,
  restoreVersion,
} from "../api";
import type {
  RunSession,
  RunVersionDiff,
  RunVersionSummary,
  TripRunResult,
} from "../types";

interface Props {
  session: RunSession;
  result: TripRunResult;
  onResult: (result: TripRunResult) => void;
}

export function ReplanningPanel({ session, result, onResult }: Props) {
  const [instruction, setInstruction] = useState("第二天轻松一点");
  const [versions, setVersions] = useState<RunVersionSummary[]>([]);
  const [difference, setDifference] = useState<RunVersionDiff | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(
    () => readVersions(session).then(setVersions),
    [session],
  );
  useEffect(() => {
    refresh().catch(() => setVersions([]));
  }, [refresh]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const previous = result.version;
      const next = await replanRun(session, instruction, previous);
      onResult(next);
      setDifference(await readVersionDiff(session, previous, next.version));
      await refresh();
      setMessage(
        "仅重新运行了行程规划、审校与最终聚合节点。目的地和交通事实已复用。",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "局部重规划失败");
    } finally {
      setBusy(false);
    }
  }

  async function restore(version: number) {
    setBusy(true);
    setMessage(null);
    try {
      onResult(await restoreVersion(session, version));
      await refresh();
      setDifference(null);
      setMessage(`已恢复版本 v${version}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="replan-card" aria-labelledby="replan-title">
      <div className="section-heading">
        <div>
          <p className="kicker">05 / REFINE & EXPORT</p>
          <h2 id="replan-title">不推倒重来，只修改必要部分</h2>
        </div>
        <div className="export-actions">
          <button
            type="button"
            onClick={() => void downloadRun(session, "markdown")}
          >
            导出 Markdown
          </button>
          <button
            type="button"
            onClick={() => void downloadRun(session, "json")}
          >
            导出 JSON
          </button>
        </div>
      </div>

      <form className="replan-form" onSubmit={submit}>
        <label>
          用自然语言修改方案
          <textarea
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            rows={2}
            required
          />
        </label>
        <button className="primary-action" type="submit" disabled={busy}>
          {busy ? "正在局部重规划…" : "提交修改"}
        </button>
      </form>
      {message ? (
        <p className="replan-message" role="status">
          {message}
        </p>
      ) : null}

      <div className="version-layout">
        <div>
          <h3>版本历史</h3>
          <ol className="version-list">
            {versions.map((version) => (
              <li
                className={
                  version.current
                    ? "version-item version-item--current"
                    : "version-item"
                }
                key={version.version}
              >
                <div>
                  <strong>v{version.version}</strong>
                  <span>{version.instruction}</span>
                </div>
                <small>{version.changed_task_ids.join(" → ")}</small>
                {!version.current ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void restore(version.version)}
                  >
                    恢复此版本
                  </button>
                ) : (
                  <em>当前</em>
                )}
              </li>
            ))}
          </ol>
        </div>
        <div>
          <h3>结构化差异</h3>
          {difference ? (
            <div className="diff-panel">
              <strong>{difference.summary}</strong>
              <ul>
                {difference.changed_paths.slice(0, 8).map((path) => (
                  <li key={path}>
                    <code>{path}</code>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="empty-diff">
              完成一次修改后，这里会显示具体字段差异。
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
