// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { mockGet } from "@/lib/mock-api";
import type {
  ListResponse,
  MLOpsSweep,
  SweepDetail,
  TrainingMetricSeries,
  TrainingRun,
} from "@/lib/types";
import { TrainingStudioClient } from "./training-studio-client";
import { SweepsTab } from "./sweeps-tab";
import { ClassicMlTab } from "./classic-ml-tab";

vi.mock("@/components/ui", () => ({
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
      {subtitle}
      {actions}
    </header>
  ),
}));

afterEach(() => {
  cleanup();
});

async function loadFixtures() {
  const runsResp = await mockGet<ListResponse<TrainingRun>>("/api/v1/mlops/runs");
  const sweepsResp = await mockGet<ListResponse<MLOpsSweep>>("/api/v1/mlops/sweeps");
  const runs = runsResp.items;
  const sweeps = sweepsResp.items;

  const seriesByRun: Record<string, TrainingMetricSeries["series"]> = {};
  for (const r of runs.slice(0, 3)) {
    const s = await mockGet<TrainingMetricSeries>(`/api/v1/mlops/runs/${r.id}/metrics`);
    seriesByRun[r.id] = s.series;
  }
  const sweepDetail = await mockGet<SweepDetail>(`/api/v1/mlops/sweeps/${sweeps[0].id}`);
  return { runs, sweeps, seriesByRun, sweepDetail };
}

describe("TrainingStudioClient", () => {
  it("renders 4 KPIs and supports WAI-ARIA ArrowRight/ArrowLeft tab navigation", async () => {
    const { runs, sweeps, seriesByRun, sweepDetail } = await loadFixtures();
    render(
      <TrainingStudioClient
        initialRuns={runs}
        initialSweeps={sweeps}
        initialSeriesByRun={seriesByRun}
        initialSweepDetail={sweepDetail}
      />,
    );

    expect(screen.getByText("Active Runs")).toBeTruthy();
    expect(screen.getByText("Completed (7d)")).toBeTruthy();
    expect(screen.getByText("Best Eval Loss")).toBeTruthy();
    expect(screen.getByText("Avg GPU VRAM")).toBeTruthy();

    const tablist = screen.getByRole("tablist");
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Parallel Coordinates")).toBeTruthy();

    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("ROC-AUC Curve")).toBeTruthy();

    fireEvent.keyDown(tabs[2], { key: "ArrowLeft" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
  });

  it("renders empty state when no runs exist", () => {
    render(
      <TrainingStudioClient
        initialRuns={[]}
        initialSweeps={[]}
        initialSeriesByRun={{}}
        initialSweepDetail={null}
      />,
    );
    expect(screen.getByText("No training runs recorded yet")).toBeTruthy();
  });

  it("renders error alert with Retry button when initialError is present", () => {
    render(
      <TrainingStudioClient
        initialRuns={[]}
        initialSweeps={[]}
        initialSeriesByRun={{}}
        initialSweepDetail={null}
        initialError="Backend unavailable"
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Backend unavailable");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("toggles Chart/Data Table and opens/closes CompareDrawer with Escape", async () => {
    const { runs, sweeps, seriesByRun, sweepDetail } = await loadFixtures();
    render(
      <TrainingStudioClient
        initialRuns={runs}
        initialSweeps={sweeps}
        initialSeriesByRun={seriesByRun}
        initialSweepDetail={sweepDetail}
      />,
    );

    const curvesSection = screen.getByLabelText("Training Metric Curves");
    const toggleBtn = within(curvesSection).getByRole("button", { name: "Data Table" });
    fireEvent.click(toggleBtn);
    expect(within(curvesSection).getByRole("table")).toBeTruthy();
    expect(within(curvesSection).getByRole("button", { name: "Chart View" })).toBeTruthy();

    const compareBtn = screen.getByRole("button", { name: /Compare Selected/i });
    fireEvent.click(compareBtn);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(within(dialog).getByText(/Compare Training Runs/i)).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("SweepsTab", () => {
  it("filters trials in real time using keyboard min/max axis inputs", async () => {
    const { sweeps, sweepDetail } = await loadFixtures();
    render(
      <SweepsTab
        sweeps={sweeps}
        selectedSweepId={sweeps[0].id}
        sweepDetail={sweepDetail}
        loadingSweep={false}
        onSelectSweep={vi.fn()}
      />,
    );

    const initialCount = sweepDetail.analysis.parallel_coordinates.trials.length;
    expect(screen.getByText(`Filtered Trials (${initialCount})`)).toBeTruthy();

    const lossMaxInput = screen.getByLabelText("eval/loss maximum") as HTMLInputElement;
    fireEvent.change(lossMaxInput, { target: { value: "0.5" } });

    const filteredHeading = screen.getByText(/Filtered Trials \(/);
    const match = filteredHeading.textContent?.match(/\((\d+)\)/);
    const filteredCount = match ? Number(match[1]) : -1;
    expect(filteredCount).toBeGreaterThan(0);
    expect(filteredCount).toBeLessThan(initialCount);
  });
});

describe("ClassicMlTab", () => {
  it("renders ROC-AUC, Confusion Matrix Counts/Row % toggle, and empty state for non-classic run", async () => {
    const { runs } = await loadFixtures();
    const classicRun = runs.find((r) => Boolean(r.classic_eval))!;
    const loraRun = runs.find((r) => !r.classic_eval)!;

    const { rerender } = render(
      <ClassicMlTab runs={runs} selectedRunId={classicRun.id} onSelectRun={vi.fn()} />,
    );

    const grid = screen.getByRole("grid");
    expect(grid.textContent).toContain("412");

    fireEvent.click(screen.getByRole("button", { name: "Row %" }));
    expect(grid.textContent).toContain("%");

    rerender(<ClassicMlTab runs={runs} selectedRunId={loraRun.id} onSelectRun={vi.fn()} />);
    expect(screen.getByText(/No classic evaluation metrics/i)).toBeTruthy();
  });
});
