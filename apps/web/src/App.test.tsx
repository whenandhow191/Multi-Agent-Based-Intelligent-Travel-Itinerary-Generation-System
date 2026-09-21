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
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
