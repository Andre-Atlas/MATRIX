// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  estimateGridSize,
  normalizeImportance,
  validateSweepDraft,
} from "@/lib/sweeps";
import type { CanonicalSweep, CanonicalSweepDetail } from "@/lib/types";

const proxyMock = vi.fn();
let mockCan = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));
vi.mock("@/lib/proxy-client", async (orig) => ({
  ...(await orig<typeof import("@/lib/proxy-client")>()),
  proxy: (...args: unknown[]) => proxyMock(...args),
}));
vi.mock("@/components/auth/role-context", () => ({
  useCan: () => mockCan,
}));

import { SweepsListClient } from "./sweeps-list-client";
import { SweepDetailClient } from "./sweep-detail-client";

const SAMPLE_SWEEP: CanonicalSweep = {
  id: "swp-tpe-01",
  name: "llama3-lora-tpe",
  description: "TPE tuning with ASHA pruning",
  status: "running",
  config: {
    method: "tpe",
    metric: { key: "eval/loss", goal: "minimize" },
    parameters: {
      learning_rate: { type: "float", min: 1e-5, max: 5e-4, log: true },
      r: { type: "choice", values: [8, 16, 32] },
    },
    early_stop: { type: "asha", min_iter: 2, eta: 3 },
    max_runs: 12,
  },
  created_at: "2026-11-12T08:00:00Z",
  created_by: "admin@matrix.local",
  run_count: 2,
  best_run_id: "run-best-01",
  best_value: 0.468,
};

const SAMPLE_DETAIL: CanonicalSweepDetail = {
  sweep: SAMPLE_SWEEP,
  runs: [
    {
      run_id: "run-best-01",
      status: "finished",
      params: { learning_rate: 0.0002, r: 16 },
      last_value: 0.468,
      best_value: 0.468,
      pruned: false,
      step: 1200,
    },
    {
      run_id: "run-pruned-02",
      status: "killed",
      params: { learning_rate: 0.0005, r: 8 },
      last_value: 0.712,
      best_value: 0.712,
      pruned: true,
      step: 300,
    },
  ],
  importance: [{ learning_rate: 0.68 }, { r: 0.32 }],
  importance_note: null,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockCan = true;
});

describe("sweeps lib helpers", () => {
  it("estimates grid size and rejects float params in grid mode", () => {
    const ok = estimateGridSize({
      r: { type: "choice", values: [8, 16, 32] },
      lr: { type: "choice", values: [1e-4, 2e-4] },
    });
    expect(ok.size).toBe(6);
    expect(ok.errors).toEqual([]);

    const bad = estimateGridSize({
      lr: { type: "float", min: 0.001, max: 0.01 },
    });
    expect(bad.size).toBeNull();
    expect(bad.errors[0]).toMatch(/float/i);
  });

  it("validates sweep draft bounds and grid constraints", () => {
    const badGrid = validateSweepDraft("my-grid", {
      method: "grid",
      metric: { key: "eval/loss", goal: "minimize" },
      parameters: {
        lr: { type: "float", min: 0.001, max: 0.01 },
      },
    });
    expect(badGrid.valid).toBe(false);

    const badLog = validateSweepDraft("my-tpe", {
      method: "tpe",
      metric: { key: "eval/loss", goal: "minimize" },
      parameters: {
        lr: { type: "float", min: 0, max: 0.01, log: true },
      },
    });
    expect(badLog.valid).toBe(false);
    expect(badLog.errors.join(" ")).toMatch(/min > 0/i);

    const ok = validateSweepDraft("my-tpe", {
      method: "tpe",
      metric: { key: "eval/loss", goal: "minimize" },
      parameters: {
        lr: { type: "float", min: 1e-5, max: 1e-3, log: true },
      },
    });
    expect(ok.valid).toBe(true);
  });

  it("normalizes importance scores in descending order", () => {
    const out = normalizeImportance([{ r: 0.25 }, { lr: 0.75 }]);
    expect(out.map((x) => x.param)).toEqual(["lr", "r"]);
    expect(out[0].score).toBe(0.75);
  });
});

describe("SweepsListClient", () => {
  it("renders sweeps list and pauses a running sweep via canonical PATCH /sweeps/:id", async () => {
    proxyMock.mockResolvedValueOnce({ ...SAMPLE_SWEEP, status: "paused" });
    render(<SweepsListClient initialSweeps={[SAMPLE_SWEEP]} />);

    expect(screen.getByText("llama3-lora-tpe")).toBeTruthy();
    expect(screen.getByText(/0\.468/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => {
      expect(proxyMock).toHaveBeenCalledWith("PATCH", "sweeps/swp-tpe-01", { status: "paused" });
    });
  });

  it("opens create modal and posts canonical POST /sweeps", async () => {
    proxyMock.mockResolvedValueOnce({
      ...SAMPLE_SWEEP,
      id: "swp-new-01",
      name: "new-sweep-test",
    });

    render(<SweepsListClient initialSweeps={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /New Sweep/i }));

    const nameInput = screen.getByLabelText(/Sweep Name/i);
    fireEvent.change(nameInput, { target: { value: "new-sweep-test" } });

    fireEvent.click(screen.getByRole("button", { name: /Create Sweep/i }));
    await waitFor(() => {
      expect(proxyMock).toHaveBeenCalledWith(
        "POST",
        "sweeps",
        expect.objectContaining({ name: "new-sweep-test" }),
      );
    });
  });
});

describe("SweepDetailClient", () => {
  it("renders best run highlight, pruned badge, and importance bar chart", () => {
    render(<SweepDetailClient sweepId="swp-tpe-01" initialDetail={SAMPLE_DETAIL} />);

    expect(screen.getAllByText(/run-best-01/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Pruned/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/68\.0%/)).toBeTruthy();
    expect(screen.getByText(/32\.0%/)).toBeTruthy();
  });

  it("renders accessible fallback notice when importance is null", () => {
    render(
      <SweepDetailClient
        sweepId="swp-tpe-01"
        initialDetail={{
          ...SAMPLE_DETAIL,
          importance: null,
          importance_note: "At least 5 completed runs are required for importance.",
        }}
      />,
    );

    const note = screen.getByRole("status");
    expect(note.textContent).toContain("At least 5 completed runs are required");
  });
});
