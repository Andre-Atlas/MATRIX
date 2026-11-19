// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { NotEvaluatedBadge, SimulatedBadge } from "@/components/ui";
import { SafetyGauge } from "@/components/safety/safety-gauge";
import { GatewayLogsTable } from "@/components/gateway/gateway-logs-table";
import { summarizeScan } from "@/lib/safety";
import { MOCK_SAFETY_CATEGORIES } from "@/lib/mock-safety";
import type { GatewayLogEntry, SafetyScan } from "@/lib/types";

afterEach(() => {
  cleanup();
});

describe("SimulatedBadge & NotEvaluatedBadge (WCAG 2.2 AA)", () => {
  it("renders SimulatedBadge with non-color-only icon and text", () => {
    render(<SimulatedBadge />);
    const badge = screen.getByRole("status");
    expect(badge.textContent).toContain("Simulated");
    expect(badge.getAttribute("aria-label")).toMatch(/Simulated/i);
  });

  it("renders NotEvaluatedBadge with accessible text", () => {
    render(<NotEvaluatedBadge />);
    const badge = screen.getByRole("status");
    expect(badge.textContent).toContain("Not evaluated");
  });
});

describe("Safety not_evaluated handling", () => {
  it("SafetyGauge shows Not evaluated and never 100 when score is null or notEvaluated is true", () => {
    render(
      <SafetyGauge
        score={null}
        riskLevel="not_evaluated"
        notEvaluated
        simulated
        message="No target outputs supplied for red-team evaluation."
        summary={{
          total_probes: 0,
          probes_passed: 0,
          probes_failed: 0,
          critical_findings: 0,
          high_findings: 0,
          medium_findings: 0,
          low_findings: 0,
        }}
      />,
    );

    const meter = screen.getByRole("meter");
    expect(meter.getAttribute("aria-valuetext")).toBe("Not evaluated");
    expect(screen.getAllByText("Not evaluated").length).toBeGreaterThan(0);
    expect(screen.queryByText("100")).toBeNull();
    expect(screen.queryByText("100%")).toBeNull();
    expect(screen.getByText(/Simulated/i)).toBeTruthy();
  });

  it("summarizeScan preserves not_evaluated status with null safety_score (never defaults to 100)", () => {
    const scan: SafetyScan = {
      id: "scan-ne-1",
      name: "Unconfigured scan",
      target_model: "gpt-4o",
      status: "not_evaluated",
      categories_tested: ["LLM01"],
      total_probes: 0,
      probes_passed: 0,
      probes_failed: 0,
      safety_score: null,
      risk_level: "not_evaluated",
      findings_count: 0,
      simulated: false,
      not_evaluated: true,
      message: "Target model endpoint was not evaluated.",
      created_at: "2026-11-12T10:00:00Z",
    };

    const report = summarizeScan(scan, [], MOCK_SAFETY_CATEGORIES);
    expect(report.scan.safety_score).toBeNull();
    expect(report.scan.risk_level).toBe("not_evaluated");
    expect(report.category_breakdown[0].compliance_rate).toBeNull();
  });
});

describe("GatewayLogsTable simulated badge", () => {
  it("renders Simulated badge when log.simulated is true or provider is mock", () => {
    const logs: GatewayLogEntry[] = [
      {
        id: "gw-sim-1",
        timestamp: "2026-11-12T10:00:00Z",
        request_id: "req-sim-1",
        route_name: "Mock Route",
        requested_model: "mock-gpt",
        served_model: "mock-gpt",
        provider: "mock",
        simulated: true,
        status: "DIRECT",
        cache_hit: false,
        fallback_triggered: false,
        status_code: 200,
        latency_ms: 12,
        prompt_tokens: 100,
        completion_tokens: 50,
        total_tokens: 150,
        cost_usd: 0,
        saved_cost_usd: 0,
      },
    ];

    render(<GatewayLogsTable logs={logs} />);
    expect(screen.getByText("Simulated")).toBeTruthy();
  });
});
