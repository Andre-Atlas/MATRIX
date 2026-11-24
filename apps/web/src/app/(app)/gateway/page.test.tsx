// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { redirect } from "next/navigation";
import { ApiError } from "@/lib/api-error";

const apiGetMock = vi.fn();

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

import GatewayPage from "./page";

const REAL_STATS = {
  total_requests: 12,
  cache_hits: 3,
  cache_hit_rate: 0.25,
  saved_cost_usd: 0.42,
  total_cost_usd: 1.58,
  avg_latency_ms: 120,
  latency_p50_ms: 100,
  latency_p99_ms: 350,
  active_providers: 1,
  healthy_providers: 1,
  fallback_events_24h: 0,
};

const REAL_PROVIDERS = {
  providers: [
    {
      name: "openai",
      provider: "openai",
      circuit_state: "closed",
      failure_count: 0,
      failure_threshold: 3,
      avg_latency_ms: 120,
      total_requests: 12,
      models: ["gpt-4o"],
    },
  ],
  total: 1,
};

const REAL_LOGS = {
  logs: [
    {
      id: "log-real-1",
      timestamp: "2026-11-24T12:00:00Z",
      provider: "openai",
      requested_model: "gpt-4o",
      served_model: "gpt-4o",
      status: "DIRECT",
      latency_ms: 120,
      simulated: false,
    },
  ],
  total: 1,
  next_cursor: null,
};

const REAL_RULES = {
  rules: [],
  total: 0,
};

describe("GatewayPage", () => {
  const prevMockEnv = process.env.MATRIX_MOCK;

  beforeEach(() => {
    delete process.env.MATRIX_MOCK;
    apiGetMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    if (prevMockEnv === undefined) {
      delete process.env.MATRIX_MOCK;
    } else {
      process.env.MATRIX_MOCK = prevMockEnv;
    }
  });

  it("propagates API 500 errors to the error boundary instead of falling back to MOCK_GATEWAY_*", async () => {
    apiGetMock.mockRejectedValue(new ApiError(500, "gateway unavailable"));

    await expect(GatewayPage()).rejects.toThrow("API 500: gateway unavailable");
  });

  it("propagates errors on /api/v1/gateway/rules instead of silently returning MOCK_GATEWAY_ROUTING_RULES", async () => {
    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/gateway/stats") return Promise.resolve(REAL_STATS);
      if (path === "/api/v1/gateway/providers") return Promise.resolve(REAL_PROVIDERS);
      if (path === "/api/v1/gateway/logs") return Promise.resolve(REAL_LOGS);
      if (path === "/api/v1/gateway/rules") {
        return Promise.reject(new ApiError(503, "rules unavailable"));
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    await expect(GatewayPage()).rejects.toThrow("API 503: rules unavailable");
  });

  it("propagates NEXT_REDIRECT errors cleanly instead of swallowing them", async () => {
    apiGetMock.mockImplementation(() => {
      redirect("/login");
    });

    await expect(GatewayPage()).rejects.toThrow("NEXT_REDIRECT");
  });

  it("renders real gateway data without Simulated banner when MATRIX_MOCK !== '1'", async () => {
    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/gateway/stats") return Promise.resolve(REAL_STATS);
      if (path === "/api/v1/gateway/providers") return Promise.resolve(REAL_PROVIDERS);
      if (path === "/api/v1/gateway/logs") return Promise.resolve(REAL_LOGS);
      if (path === "/api/v1/gateway/rules") return Promise.resolve(REAL_RULES);
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const el = await GatewayPage();
    render(el);

    expect(screen.getByText("AI Gateway & Inference Proxy")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.queryByText(/displaying simulated\/mock data/i)).toBeNull();
  });

  it("passes isSimulated=true when MATRIX_MOCK === '1'", async () => {
    process.env.MATRIX_MOCK = "1";
    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/gateway/stats") return Promise.resolve(REAL_STATS);
      if (path === "/api/v1/gateway/providers") return Promise.resolve(REAL_PROVIDERS);
      if (path === "/api/v1/gateway/logs") return Promise.resolve(REAL_LOGS);
      if (path === "/api/v1/gateway/rules") return Promise.resolve(REAL_RULES);
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const el = await GatewayPage();
    render(el);

    expect(screen.getByText(/displaying simulated\/mock data/i)).toBeTruthy();
  });
});
