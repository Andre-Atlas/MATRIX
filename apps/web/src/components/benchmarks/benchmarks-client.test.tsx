// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MOCK_BENCHMARK_CATALOG, MOCK_BENCHMARK_RUNS } from "@/lib/sota-mocks";
import { BenchmarksClient } from "./benchmarks-client";

vi.mock("@/components/ui", async () => {
  const actual = await vi.importActual<typeof import("@/components/ui")>("@/components/ui");
  return {
    ...actual,
    PageHeader: ({
      title,
      subtitle,
      actions,
    }: {
      title: React.ReactNode;
      subtitle?: React.ReactNode;
      actions?: React.ReactNode;
    }) => (
      <header>
        <h1>{title}</h1>
        <div>{subtitle}</div>
        <div>{actions}</div>
      </header>
    ),
  };
});

afterEach(() => {
  cleanup();
});

describe("BenchmarksClient", () => {
  it("renders 4 KPIs with labels in text-zinc-400 and mono scores", () => {
    render(
      <BenchmarksClient
        initialRuns={MOCK_BENCHMARK_RUNS}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    // 4 KPIs
    const kpi1 = screen.getByText("Benchmark Runs");
    const kpi2 = screen.getByText("Avg MMLU-Pro");
    const kpi3 = screen.getByText("Avg Multimodal (MMMU/DocVQA)");
    const kpi4 = screen.getByText("Active GPU Runners");

    expect(kpi1).toBeTruthy();
    expect(kpi2).toBeTruthy();
    expect(kpi3).toBeTruthy();
    expect(kpi4).toBeTruthy();

    expect(kpi1.className).toContain("text-zinc-400");
    expect(kpi2.className).toContain("text-zinc-400");
    expect(kpi3.className).toContain("text-zinc-400");
    expect(kpi4.className).toContain("text-zinc-400");

    // Total runs count in the first KPI card
    const runsKpi = kpi1.parentElement!;
    expect(within(runsKpi).getByText("3")).toBeTruthy();
    // GPU runners
    expect(screen.getByText(/GPUs/)).toBeTruthy();
  });

  it("supports WAI-ARIA ArrowRight/ArrowLeft tab navigation between LM-Eval and VLMEvalKit", () => {
    render(
      <BenchmarksClient
        initialRuns={MOCK_BENCHMARK_RUNS}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    const tablist = screen.getByRole("tablist", { name: "Benchmark evaluation suites" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs).toHaveLength(2);

    // Initial tab: LM-Eval
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(screen.getAllByText("MMLU-Pro").length).toBeGreaterThan(0);

    // ArrowRight moves to VLMEvalKit
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(tabs[0].getAttribute("aria-selected")).toBe("false");
    expect(screen.getAllByText("MMMU-Pro").length).toBeGreaterThan(0);

    // ArrowLeft moves back to LM-Eval
    fireEvent.keyDown(tabs[1], { key: "ArrowLeft" });
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
  });

  it("renders pure-SVG Radar and Grouped Bar charts with accessible titles, descriptions, and Data Table toggle", () => {
    render(
      <BenchmarksClient
        initialRuns={MOCK_BENCHMARK_RUNS}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    // Radar SVG should be rendered initially
    const svgChart = screen.getByRole("img");
    expect(svgChart).toBeTruthy();
    expect(svgChart.querySelector("title")).toBeTruthy();
    expect(svgChart.querySelector("desc")).toBeTruthy();

    // Toggle to Grouped Bar
    const barBtn = screen.getByRole("button", { name: "Grouped Bar" });
    fireEvent.click(barBtn);
    expect(barBtn.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Baseline: 70%")).toBeTruthy();

    // Toggle to Data Table (<table role="grid">)
    const tableBtn = screen.getByRole("button", { name: "Data Table" });
    fireEvent.click(tableBtn);
    expect(tableBtn.getAttribute("aria-pressed")).toBe("true");

    const gridTable = screen.getByRole("grid", { name: "Benchmark Comparison Table" });
    expect(gridTable).toBeTruthy();
    expect(within(gridTable).getAllByRole("columnheader").length).toBeGreaterThan(0);
    expect(within(gridTable).getAllByRole("rowheader").length).toBeGreaterThan(0);
  });

  it("filters runs via the filter bar (search, runner, status)", () => {
    render(
      <BenchmarksClient
        initialRuns={MOCK_BENCHMARK_RUNS}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    const searchInput = screen.getByPlaceholderText("Search model, run, or task...");
    fireEvent.change(searchInput, { target: { value: "Mistral" } });

    // Should only show Mistral run
    expect(screen.getByText("mistralai/Mistral-7B-Instruct-v0.3")).toBeTruthy();
    expect(screen.queryByText("meta-llama/Llama-3.1-8B-Instruct")).toBeNull();

    // Reset button appears
    const resetBtn = screen.getByRole("button", { name: "Reset" });
    fireEvent.click(resetBtn);

    expect(screen.getByText("meta-llama/Llama-3.1-8B-Instruct")).toBeTruthy();
  });

  it("opens Breakdown Drawer on clicking a task score cell, showing Log-likelihood vs Gen comparison & SymPy verification", () => {
    render(
      <BenchmarksClient
        initialRuns={MOCK_BENCHMARK_RUNS}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    // Find a score button e.g. for MMLU-Pro (68.4%)
    const scoreBtns = screen.getAllByTitle("Inspect MMLU-Pro breakdown");
    expect(scoreBtns.length).toBeGreaterThan(0);
    fireEvent.click(scoreBtns[0]);

    // Breakdown drawer dialog should now be open
    const drawer = screen.getByRole("dialog", { name: "Benchmark Task Breakdown" });
    expect(drawer).toBeTruthy();
    expect(within(drawer).getByText(/mmlu pro breakdown/i)).toBeTruthy();
    expect(within(drawer).getByText("Log-likelihood (LL)")).toBeTruthy();
    expect(within(drawer).getByText("Generation (GEN)")).toBeTruthy();
    expect(within(drawer).getByText("SymPy Symbolic Equivalence")).toBeTruthy();
    expect(within(drawer).getByText("Format Compliance & Parse Error Rate")).toBeTruthy();

    // Pressing Escape closes the drawer
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Benchmark Task Breakdown" })).toBeNull();
  });

  it("opens + Launch Benchmark modal dialog and supports Escape key close", () => {
    render(
      <BenchmarksClient
        initialRuns={MOCK_BENCHMARK_RUNS}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    const launchBtn = screen.getByRole("button", { name: "+ Launch Benchmark" });
    fireEvent.click(launchBtn);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeTruthy();
    expect(within(dialog).getByText("Launch Benchmark Run")).toBeTruthy();
    expect(within(dialog).getByText("LM-Eval Harness")).toBeTruthy();
    expect(within(dialog).getByText("VLMEvalKit")).toBeTruthy();

    // Close via Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders empty state with CTA when no runs match filters", () => {
    render(
      <BenchmarksClient
        initialRuns={[]}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
      />,
    );

    expect(
      screen.getByText("No benchmark runs found matching the current filters"),
    ).toBeTruthy();
    const cta = screen.getAllByRole("button", { name: "+ Launch Benchmark" });
    expect(cta.length).toBeGreaterThan(0);
  });

  it("renders error banner with Retry button when initialError is present", () => {
    render(
      <BenchmarksClient
        initialRuns={[]}
        initialCatalog={MOCK_BENCHMARK_CATALOG}
        initialError="Database timeout occurred."
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    expect(within(alert).getByText(/Database timeout occurred/)).toBeTruthy();
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
