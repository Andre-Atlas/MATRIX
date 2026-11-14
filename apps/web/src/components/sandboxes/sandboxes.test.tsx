// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  MOCK_SANDBOX_RUNS,
  MOCK_SANDBOX_TRANSCRIPTS,
} from "@/lib/sota-mocks";
import { SandboxesClient } from "./sandboxes-client";
import { InspectView } from "./inspect-view";
import { NewSandboxModal } from "./new-sandbox-modal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SandboxesClient", () => {
  it("renders 4 KPIs with accessible labels and Populated state table", () => {
    const runs = [...MOCK_SANDBOX_RUNS];
    const initialTranscripts = MOCK_SANDBOX_TRANSCRIPTS["sb-run-01"] || [];

    render(
      <SandboxesClient
        initialRuns={runs}
        initialTranscripts={initialTranscripts}
        initialSelectedRunId="sb-run-01"
      />,
    );

    // 4 KPIs
    const kpiSection = screen.getByLabelText("Agent Sandboxes Summary Metrics");
    expect(within(kpiSection).getByText("Sandbox Runs")).toBeTruthy();
    expect(within(kpiSection).getByText("Pass Rate")).toBeTruthy();
    expect(within(kpiSection).getByText("Avg Steps / Task")).toBeTruthy();
    expect(within(kpiSection).getByText("Active Containers")).toBeTruthy();

    // Table Headers
    const table = screen.getByRole("grid", { name: "Sandbox Runs" });
    expect(within(table).getByText("Run ID")).toBeTruthy();
    expect(within(table).getByText("Dataset / Task")).toBeTruthy();
    expect(within(table).getByText("Solver")).toBeTruthy();
    expect(within(table).getByText("Scorer")).toBeTruthy();
    expect(within(table).getByText("Runtime")).toBeTruthy();
    expect(within(table).getByText("Steps")).toBeTruthy();
    expect(within(table).getByText("Tokens")).toBeTruthy();
    expect(within(table).getByText("Duration")).toBeTruthy();
    expect(within(table).getByText("Verdict")).toBeTruthy();
    expect(within(table).getByText("Started (UTC)")).toBeTruthy();

    // Rows
    expect(within(table).getByText("sb-run-01")).toBeTruthy();
    expect(within(table).getByText("Agent Code Refactor")).toBeTruthy();
    expect(within(table).getByText("sb-run-02")).toBeTruthy();
    expect(within(table).getByText("Web Scraper Fix")).toBeTruthy();
  });

  it("filters runs by search term, status, and runtime", () => {
    const runs = [...MOCK_SANDBOX_RUNS];
    render(<SandboxesClient initialRuns={runs} initialSelectedRunId={null} />);

    const table = screen.getByRole("grid", { name: "Sandbox Runs" });

    // Search filter
    const searchInput = screen.getByPlaceholderText(/Filter by Run ID/i);
    fireEvent.change(searchInput, { target: { value: "scraper" } });

    expect(within(table).getByText("Web Scraper Fix")).toBeTruthy();
    expect(within(table).queryByText("Agent Code Refactor")).toBeNull();

    // Reset search
    fireEvent.change(searchInput, { target: { value: "" } });
    expect(within(table).getByText("Agent Code Refactor")).toBeTruthy();

    // Status filter
    const statusSelect = screen.getByLabelText("Filter by status");
    fireEvent.change(statusSelect, { target: { value: "failed" } });
    expect(within(table).getByText("SQL Migration Agent")).toBeTruthy();
    expect(within(table).queryByText("Agent Code Refactor")).toBeNull();
  });

  it("renders Empty state with CTA when runs list is empty", () => {
    render(<SandboxesClient initialRuns={[]} initialSelectedRunId={null} />);

    expect(screen.getByText("No Sandbox Runs Found")).toBeTruthy();
    expect(screen.getByText(/MATRIX Agent Sandboxes evaluate autonomous agents/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "+ New Sandbox Run" })).toHaveLength(2);
  });

  it("renders Error state with Retry button when initialError is present", () => {
    render(
      <SandboxesClient
        initialRuns={[]}
        initialSelectedRunId={null}
        initialError="Container cluster unreachable"
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Container cluster unreachable");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("opens New Sandbox Run modal on + New Sandbox Run button click and closes with Escape", () => {
    render(<SandboxesClient initialRuns={MOCK_SANDBOX_RUNS} />);

    const openBtn = screen.getByRole("button", { name: "+ New Sandbox Run" });
    fireEvent.click(openBtn);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeTruthy();
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(within(dialog).getByText("New Sandbox Run")).toBeTruthy();

    // 3 modular pipeline sections
    expect(within(dialog).getByText("1. Dataset & Container Policy")).toBeTruthy();
    expect(within(dialog).getByText("2. Solver Strategy")).toBeTruthy();
    expect(within(dialog).getByText("3. Scorer & Verification")).toBeTruthy();

    // Close on Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("InspectView", () => {
  it("renders Split Panel with left timeline and right step detail tabs", () => {
    const run = MOCK_SANDBOX_RUNS[0];
    const transcripts = MOCK_SANDBOX_TRANSCRIPTS["sb-run-01"];
    const onClose = vi.fn();

    render(<InspectView run={run} transcripts={transcripts} onClose={onClose} />);

    // Left column: Steps Timeline
    const timelineRegion = screen.getByRole("region", {
      name: "Steps Timeline navigation",
    });
    expect(timelineRegion).toBeTruthy();

    // Steps badges and exit codes
    expect(within(timelineRegion).getByText("#01")).toBeTruthy();
    expect(within(timelineRegion).getByText("Bash")).toBeTruthy();
    expect(within(timelineRegion).getByText(/exit 1/)).toBeTruthy(); // step 1 failed in refactor

    expect(within(timelineRegion).getByText("#02")).toBeTruthy();
    expect(within(timelineRegion).getByText("Python")).toBeTruthy();

    expect(within(timelineRegion).getByText("#03")).toBeTruthy();
    expect(within(timelineRegion).getByText("File Edit")).toBeTruthy();

    expect(within(timelineRegion).getByText("#04")).toBeTruthy();
    expect(within(timelineRegion).getByText("Scorer")).toBeTruthy();

    // Right column: Step detail tabs
    const tablist = screen.getByRole("tablist", { name: "Step Detail Sections" });
    expect(tablist).toBeTruthy();
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0].textContent).toContain("stdout / stderr");
    expect(tabs[1].textContent).toContain("Modified Files Diff");
    expect(tabs[2].textContent).toContain("Scorer Verdict");
  });

  it("supports keyboard navigation with ArrowDown/ArrowUp across steps", () => {
    const run = MOCK_SANDBOX_RUNS[0];
    const transcripts = MOCK_SANDBOX_TRANSCRIPTS["sb-run-01"];
    const onClose = vi.fn();

    render(<InspectView run={run} transcripts={transcripts} onClose={onClose} />);

    const timelineRegion = screen.getByRole("region", {
      name: "Steps Timeline navigation",
    });

    // Initial step is #01
    expect(screen.getByText("Step #01")).toBeTruthy();

    // ArrowDown moves to step #02
    fireEvent.keyDown(timelineRegion, { key: "ArrowDown" });
    expect(screen.getByText("Step #02")).toBeTruthy();

    // ArrowDown moves to step #03
    fireEvent.keyDown(timelineRegion, { key: "ArrowDown" });
    expect(screen.getByText("Step #03")).toBeTruthy();

    // ArrowUp moves back to step #02
    fireEvent.keyDown(timelineRegion, { key: "ArrowUp" });
    expect(screen.getByText("Step #02")).toBeTruthy();
  });

  it("switches detail tabs with ArrowRight/ArrowLeft and displays diff lines with prefix + / -", () => {
    const run = MOCK_SANDBOX_RUNS[0];
    const transcripts = MOCK_SANDBOX_TRANSCRIPTS["sb-run-01"];
    const onClose = vi.fn();

    render(<InspectView run={run} transcripts={transcripts} onClose={onClose} />);

    // Select step #03 (which has diff)
    const step3Btn = screen.getByLabelText(/Step 3: File Edit/);
    fireEvent.click(step3Btn);

    const tablist = screen.getByRole("tablist", { name: "Step Detail Sections" });
    const tabs = within(tablist).getAllByRole("tab");

    // Press ArrowRight to move from stdout/stderr to Modified Files Diff
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");

    // In Modified Files Diff panel: check unified diff with prefix + and -
    const diffPanel = screen.getByRole("tabpanel");
    expect(diffPanel.textContent).toContain("+");
    expect(diffPanel.textContent).toContain("-");
    expect(diffPanel.textContent).toContain("validate_payload");

    // Press ArrowRight to move to Scorer Verdict
    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");

    const verdictPanel = screen.getByRole("tabpanel");
    expect(verdictPanel.textContent).toContain("Task PASSED");
    expect(verdictPanel.textContent).toContain("Score: 1");
    expect(verdictPanel.textContent).toContain("unit_test");
  });
});

describe("NewSandboxModal", () => {
  it("traps focus and validates required run name", () => {
    const onClose = vi.fn();
    const onCreated = vi.fn();

    render(<NewSandboxModal open={true} onClose={onClose} onCreated={onCreated} />);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeTruthy();

    // Form submission without name triggers validation error alert
    const form = dialog.querySelector("form")!;
    fireEvent.submit(form);

    expect(within(dialog).getByRole("alert")).toBeTruthy();
    expect(within(dialog).getByRole("alert").textContent).toContain(
      "Please specify a sandbox run name",
    );
  });
});
