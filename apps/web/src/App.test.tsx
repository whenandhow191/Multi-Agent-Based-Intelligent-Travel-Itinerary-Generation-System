import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows the project identity and a successful API check", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    render(<App />);

    expect(
      screen.getByRole("heading", { name: "多 Agent 智能旅游攻略" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("API 已连接")).toBeInTheDocument();
  });
});
