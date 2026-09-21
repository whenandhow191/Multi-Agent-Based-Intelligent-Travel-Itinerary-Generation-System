import type {
  ModelCatalog,
  PenguinModelId,
  RunProgressEvent,
  RunSession,
  RunVersionDiff,
  RunVersionSummary,
  TripRequestInput,
  TripRun,
  TripRunResult,
} from "./types";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface CreatedRun {
  run: TripRun;
  access_token: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as {
        detail?: string | { message?: string };
      };
      if (typeof body.detail === "string") message = body.detail;
      if (typeof body.detail === "object" && body.detail?.message) {
        message = body.detail.message;
      }
    } catch {
      // Keep the stable status-based fallback when the server is unreachable.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function checkHealth(signal?: AbortSignal): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/health`, { signal });
  if (!response.ok) throw new Error("API health check failed");
}

export async function listModels(): Promise<ModelCatalog> {
  return request<ModelCatalog>("/api/v1/models");
}

export async function probeModel(modelId: PenguinModelId): Promise<void> {
  await request("/api/v1/models/probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
}

export async function createRun(
  input: TripRequestInput,
  modelId: PenguinModelId,
): Promise<RunSession> {
  const created = await request<CreatedRun>("/api/v1/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({ request: input, model_id: modelId }),
  });
  return { run: created.run, accessToken: created.access_token };
}

export async function readRun(session: RunSession): Promise<TripRun> {
  return request<TripRun>(`/api/v1/runs/${session.run.run_id}`, {
    headers: { "X-Run-Token": session.accessToken },
  });
}

export async function readProgress(
  session: RunSession,
): Promise<RunProgressEvent[]> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/runs/${session.run.run_id}/events`,
    {
      headers: { "X-Run-Token": session.accessToken },
    },
  );
  if (!response.ok) throw new Error(`无法读取协作进度（${response.status}）`);
  const payload = await response.text();
  return payload
    .split("\n\n")
    .map((block) => block.split("\n").find((line) => line.startsWith("data: ")))
    .filter((line): line is string => Boolean(line))
    .map((line) => JSON.parse(line.slice(6)) as RunProgressEvent);
}

export async function readResult(session: RunSession): Promise<TripRunResult> {
  return request<TripRunResult>(`/api/v1/runs/${session.run.run_id}/result`, {
    headers: { "X-Run-Token": session.accessToken },
  });
}

export async function replanRun(
  session: RunSession,
  instruction: string,
  baseVersion: number,
): Promise<TripRunResult> {
  return request<TripRunResult>(`/api/v1/runs/${session.run.run_id}/replan`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Run-Token": session.accessToken,
    },
    body: JSON.stringify({ instruction, base_version: baseVersion }),
  });
}

export async function readVersions(
  session: RunSession,
): Promise<RunVersionSummary[]> {
  const response = await request<{ versions: RunVersionSummary[] }>(
    `/api/v1/runs/${session.run.run_id}/versions`,
    { headers: { "X-Run-Token": session.accessToken } },
  );
  return response.versions;
}

export async function readVersionDiff(
  session: RunSession,
  fromVersion: number,
  toVersion: number,
): Promise<RunVersionDiff> {
  return request<RunVersionDiff>(
    `/api/v1/runs/${session.run.run_id}/versions/diff?from=${fromVersion}&to=${toVersion}`,
    { headers: { "X-Run-Token": session.accessToken } },
  );
}

export async function restoreVersion(
  session: RunSession,
  version: number,
): Promise<TripRunResult> {
  return request<TripRunResult>(
    `/api/v1/runs/${session.run.run_id}/versions/${version}/restore`,
    { method: "POST", headers: { "X-Run-Token": session.accessToken } },
  );
}

export async function downloadRun(
  session: RunSession,
  format: "markdown" | "json",
): Promise<void> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/runs/${session.run.run_id}/export?format=${format}`,
    { headers: { "X-Run-Token": session.accessToken } },
  );
  if (!response.ok) throw new Error(`导出失败（${response.status}）`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${session.run.run_id}.${format === "markdown" ? "md" : "json"}`;
  anchor.click();
  URL.revokeObjectURL(url);
}
