import type {
  RunProgressEvent,
  RunSession,
  TripRequestInput,
  TripRun,
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

export async function createRun(input: TripRequestInput): Promise<RunSession> {
  const created = await request<CreatedRun>("/api/v1/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({ request: input }),
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
