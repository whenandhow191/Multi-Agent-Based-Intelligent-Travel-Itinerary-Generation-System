import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("App", () => {
  it("shows the product identity and a successful API check", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    render(<App />);

    expect(
      screen.getByRole("heading", { name: /让每一段旅程/ }),
    ).toBeInTheDocument();
    expect(await screen.findByText("API 已连接")).toBeInTheDocument();
  });

  it("submits a fixture request and renders run status", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          provider_id: "penguin",
          configured: true,
          connection: "verified",
          models: [
            {
              id: "claude-sonnet-5",
              name: "claude-sonnet-5",
              family: "claude",
              available: true,
            },
          ],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          access_token: "token_fixture",
          run: {
            run_id: "run_fixture",
            state: "succeeded",
            fixture_mode: true,
            request: { destination: "北京" },
            created_at: "2026-09-21T00:00:00Z",
            updated_at: "2026-09-21T00:00:00Z",
            result_available: true,
            current_version: 1,
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () =>
          'id: 1\nevent: run.completed\ndata: {"sequence":1,"event_type":"run.completed","task_id":null,"agent_id":"coordinator","state":"succeeded","message":"完成","occurred_at":"2026-09-21T00:00:00Z","artifact_ids":[],"tool_calls":[],"estimated_cost_microunits":20}\n\n',
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          run_id: "run_fixture",
          version: 1,
          bundle: {
            plans: [
              {
                plan_id: "plan_fixture",
                title: "均衡方案",
                strategy: "balanced",
                days: [],
                total_cost: {
                  kind: "known",
                  currency: "CNY",
                  lower: "140",
                  upper: "140",
                  basis: "fixture",
                },
                score_breakdown: {},
                is_executable: true,
                unresolved_risks: [],
              },
            ],
            comparison: {
              entries: [
                {
                  plan_id: "plan_fixture",
                  total_cost: {
                    kind: "known",
                    currency: "CNY",
                    lower: "140",
                    upper: "140",
                    basis: "fixture",
                  },
                  activity_count: 2,
                  commute_minutes: 30,
                  free_minutes: 240,
                  preference_coverage: 1,
                  risk_count: 0,
                  evidence_coverage: 1,
                },
              ],
            },
            evidence: [],
            assumptions: [],
            collaboration_summary: { revision_rounds: 0 },
          },
          map_points: [],
          markdown: "# fixture",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ versions: [] }),
      });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "idempotency-fixture" });

    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "生成旅行方案" }),
    );

    expect(
      await screen.findByRole("heading", { name: "方案已生成" }),
    ).toBeInTheDocument();
    expect(screen.getByText("运行编号 run_fixture")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "行程、地图与证据同步" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "不推倒重来，只修改必要部分" }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });
});
