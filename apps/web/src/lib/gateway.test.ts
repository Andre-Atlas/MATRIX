import { describe, expect, it } from "vitest";
import {
  computeGatewayStatsFromLogs,
  filterGatewayLogs,
  formatLatencyMs,
  formatTokenCount,
  formatUSD,
  getCircuitStateConfig,
  getLogStatusConfig,
  getProviderHealthConfig,
  normalizeCircuitState,
} from "./gateway";
import { mockGet } from "./mock-api";
import {
  MOCK_GATEWAY_LOGS,
  MOCK_GATEWAY_PROVIDERS,
  MOCK_GATEWAY_ROUTING_RULES,
  MOCK_GATEWAY_STATS,
} from "./mock-data";
import type {
  GatewayLogsResponse,
  GatewayProvidersResponse,
  GatewayRulesResponse,
  GatewayStatsResponse,
} from "./types";

describe("AI Gateway — Circuit Breaker & Health States", () => {
  it("normalizes circuit breaker state variants", () => {
    expect(normalizeCircuitState("closed")).toBe("closed");
    expect(normalizeCircuitState("CLOSED")).toBe("closed");
    expect(normalizeCircuitState("half_open")).toBe("half_open");
    expect(normalizeCircuitState("HALF_OPEN")).toBe("half_open");
    // @ts-expect-error test dash variant
    expect(normalizeCircuitState("half-open")).toBe("half_open");
    expect(normalizeCircuitState("open")).toBe("open");
    expect(normalizeCircuitState("OPEN")).toBe("open");
  });

  it("returns green styling and normal label for CLOSED state", () => {
    const config = getCircuitStateConfig("closed");
    expect(config.key).toBe("closed");
    expect(config.label).toContain("Closed");
    expect(config.badgeClass).toContain("emerald");
    expect(config.dotClass).toContain("emerald");
  });

  it("returns amber styling and testing label for HALF_OPEN state", () => {
    const config = getCircuitStateConfig("half_open");
    expect(config.key).toBe("half_open");
    expect(config.label).toContain("Half-Open");
    expect(config.badgeClass).toContain("amber");
    expect(config.dotClass).toContain("amber");
  });

  it("returns red styling and tripped label for OPEN state", () => {
    const config = getCircuitStateConfig("open");
    expect(config.key).toBe("open");
    expect(config.label).toContain("Open");
    expect(config.badgeClass).toContain("rose");
    expect(config.dotClass).toContain("rose");
  });

  it("maps provider health status correctly", () => {
    expect(getProviderHealthConfig("healthy").label).toBe("Operational");
    expect(getProviderHealthConfig("healthy").badgeClass).toContain("emerald");

    expect(getProviderHealthConfig("degraded").label).toBe("Degraded");
    expect(getProviderHealthConfig("degraded").badgeClass).toContain("amber");

    expect(getProviderHealthConfig("outage").label).toBe("Outage");
    expect(getProviderHealthConfig("outage").badgeClass).toContain("rose");
  });

  it("maps log status badges correctly", () => {
    expect(getLogStatusConfig("CACHE_HIT").badgeClass).toContain("emerald");
    expect(getLogStatusConfig("DIRECT").badgeClass).toContain("sky");
    expect(getLogStatusConfig("FALLBACK").badgeClass).toContain("amber");
    expect(getLogStatusConfig("ERROR").badgeClass).toContain("rose");
  });
});

describe("AI Gateway — Formatters & Metric Computations", () => {
  it("formats USD currency values accurately", () => {
    expect(formatUSD(1248.65)).toBe("$1,248.65");
    expect(formatUSD(0)).toBe("$0.00");
  });

  it("formats latencies in ms and seconds", () => {
    expect(formatLatencyMs(45)).toBe("45ms");
    expect(formatLatencyMs(950)).toBe("950ms");
    expect(formatLatencyMs(1250)).toBe("1.25s");
    expect(formatLatencyMs(3500)).toBe("3.50s");
  });

  it("formats token counts compactly", () => {
    expect(formatTokenCount(512)).toBe("512");
    expect(formatTokenCount(2400)).toBe("2.4k");
    expect(formatTokenCount(1500000)).toBe("1.50M");
  });

  it("computes gateway metrics from log slices", () => {
    const stats = computeGatewayStatsFromLogs(MOCK_GATEWAY_LOGS);
    expect(stats.total_requests).toBe(MOCK_GATEWAY_LOGS.length);
    expect(stats.cache_hits).toBe(
      MOCK_GATEWAY_LOGS.filter((l) => l.status === "CACHE_HIT").length,
    );
    expect(stats.cache_hit_rate).toBeGreaterThan(0);
    expect(stats.saved_cost_usd).toBeGreaterThan(0);
    expect(stats.latency_p50_ms).toBeGreaterThan(0);
    expect(stats.latency_p99_ms).toBeGreaterThanOrEqual(stats.latency_p50_ms ?? 0);
  });

  it("handles empty log array when computing metrics", () => {
    const stats = computeGatewayStatsFromLogs([]);
    expect(stats.total_requests).toBe(0);
    expect(stats.cache_hits).toBe(0);
    expect(stats.cache_hit_rate).toBe(0);
  });
});

describe("AI Gateway — Log Filtering", () => {
  it("filters logs by status", () => {
    const cacheHits = filterGatewayLogs(MOCK_GATEWAY_LOGS, { status: "CACHE_HIT" });
    expect(cacheHits.length).toBeGreaterThan(0);
    expect(cacheHits.every((l) => l.status === "CACHE_HIT")).toBe(true);

    const fallbacks = filterGatewayLogs(MOCK_GATEWAY_LOGS, { status: "FALLBACK" });
    expect(fallbacks.length).toBeGreaterThan(0);
    expect(fallbacks.every((l) => l.status === "FALLBACK")).toBe(true);
  });

  it("filters logs by provider", () => {
    const anthropicLogs = filterGatewayLogs(MOCK_GATEWAY_LOGS, { provider: "anthropic" });
    expect(anthropicLogs.length).toBeGreaterThan(0);
    expect(anthropicLogs.every((l) => l.provider === "anthropic")).toBe(true);
  });

  it("filters logs by search query (model or route or request id)", () => {
    const queryResult = filterGatewayLogs(MOCK_GATEWAY_LOGS, { search: "claude-3-5" });
    expect(queryResult.length).toBeGreaterThan(0);
    expect(
      queryResult.every(
        (l) =>
          (l.served_model ?? "").includes("claude-3-5") ||
          (l.requested_model ?? "").includes("claude-3-5") ||
          (l.route_name ?? "").includes("claude-3-5"),
      ),
    ).toBe(true);
  });
});

describe("AI Gateway — Mock API Routes", () => {
  it("GET /api/v1/gateway/stats returns comprehensive gateway KPIs", async () => {
    const stats = await mockGet<GatewayStatsResponse>("/api/v1/gateway/stats");
    expect(stats).toBeDefined();
    expect(stats.total_requests).toBe(MOCK_GATEWAY_STATS.total_requests);
    expect(stats.cache_hit_rate).toBe(MOCK_GATEWAY_STATS.cache_hit_rate);
    expect(stats.saved_cost_usd).toBe(MOCK_GATEWAY_STATS.saved_cost_usd);
    expect(stats.latency_p50_ms).toBe(MOCK_GATEWAY_STATS.latency_p50_ms);
    expect(stats.latency_p99_ms).toBe(MOCK_GATEWAY_STATS.latency_p99_ms);
  });

  it("GET /api/v1/gateway/providers returns provider statuses and circuit breakers", async () => {
    const resp = await mockGet<GatewayProvidersResponse>("/api/v1/gateway/providers");
    expect(resp.providers).toBeDefined();
    expect(resp.providers.length).toBe(MOCK_GATEWAY_PROVIDERS.length);

    // Verify presence of closed, half-open and open states
    const states = resp.providers.map((p) => p.circuit_state);
    expect(states).toContain("closed");
    expect(states).toContain("half_open");
    expect(states).toContain("open");
  });

  it("GET /api/v1/gateway/logs returns request logs with filters and pagination", async () => {
    const resp = await mockGet<GatewayLogsResponse>("/api/v1/gateway/logs", {
      status: "CACHE_HIT",
    });
    expect(resp.logs).toBeDefined();
    expect(resp.logs.length).toBeGreaterThan(0);
    expect(resp.logs.every((l) => l.status === "CACHE_HIT")).toBe(true);
  });

  it("GET /api/v1/gateway/rules returns routing rules and fallback chains", async () => {
    const resp = await mockGet<GatewayRulesResponse>("/api/v1/gateway/rules");
    expect(resp.rules).toBeDefined();
    expect(resp.rules.length).toBe(MOCK_GATEWAY_ROUTING_RULES.length);

    const codeRule = resp.rules.find((r) => r.id === "rule-code-agent");
    expect(codeRule).toBeDefined();
    expect(codeRule?.primary_provider).toBe("anthropic");
    expect(codeRule?.fallback_chain.length).toBeGreaterThan(0);
    expect(codeRule?.fallback_chain[0].provider).toBe("openai");
  });
});
