// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Dataset, DatasetBranch, DatasetCommit, DatasetItem, Span } from "@/lib/types";
import { DatasetStudioClient } from "./dataset-studio-client";
import { SpanPanel } from "../trace/span-panel";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/datasets/ds-sota-multimodal",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// Mock global fetch for proxy calls
const mockBranches: DatasetBranch[] = [
  {
    id: "br-main",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    name: "main",
    head_commit_id: "c-main-02",
    is_default: true,
    created_by: "system",
    created_at: "2026-11-13T08:00:00Z",
    updated_at: "2026-11-13T09:30:00Z",
  },
  {
    id: "br-feat",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    name: "feat/multimodal-v2",
    head_commit_id: "c-feat-01",
    is_default: false,
    created_by: "researcher@matrix.local",
    created_at: "2026-11-13T09:00:00Z",
    updated_at: "2026-11-13T10:15:00Z",
  },
];

const mockCommits: DatasetCommit[] = [
  {
    id: "c-main-02",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    parent_commit_id: "c-main-01",
    second_parent_commit_id: null,
    tree_hash: "a1b2c3d4e5f60002",
    message: "feat: add verified OCR and visual bounding boxes",
    author: "curator@matrix.local",
    item_count: 4,
    stats: { added: 0, removed: 0, modified: 2 },
    created_at: "2026-11-13T09:30:00Z",
  },
  {
    id: "c-main-01",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    parent_commit_id: null,
    second_parent_commit_id: null,
    tree_hash: "a1b2c3d4e5f60001",
    message: "Initial multimodal dataset commit",
    author: "curator@matrix.local",
    item_count: 4,
    stats: { added: 4, removed: 0, modified: 0 },
    created_at: "2026-11-13T08:00:00Z",
  },
];

const mockDataset: Dataset = {
  id: "ds-sota-multimodal",
  name: "sota-multimodal",
  description: "Zero-copy LakeFS branched multimodal dataset with images, audio, PDF and diffs",
  item_count: 4,
  created_at: "2026-11-13T08:00:00Z",
};

const mockItems: DatasetItem[] = [
  {
    id: "dci-01",
    dataset_id: "ds-sota-multimodal",
    input: { question: "What is the total revenue in Q3 shown in the table?" },
    expected_output: "The total revenue in Q3 is $42.5M.",
    context: ["Page 4, Table 2.1: Financial Summary"],
    metadata: { domain: "finance", modality: "image" },
    source_trace_id: null,
    source_span_id: null,
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "image",
        url: "https://assets.matrix.local/images/quarterly-financial-table.png",
        mime: "image/png",
        width: 1024,
        height: 768,
        boxes: [
          { x: 120, y: 180, w: 240, h: 60, label: "Q3 Revenue Header", score: 0.99 },
          { x: 380, y: 180, w: 120, h: 60, label: "$42.5M Value", score: 0.98 },
        ],
      },
    ],
  },
  {
    id: "dci-02",
    dataset_id: "ds-sota-multimodal",
    input: { prompt: "Transcribe the customer service audio recording accurately." },
    expected_output: "Hello, I would like to confirm my subscription renewal date.",
    context: ["Call Center Recording #49281"],
    metadata: { domain: "customer_support", modality: "audio" },
    source_trace_id: null,
    source_span_id: null,
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "audio",
        url: "https://assets.matrix.local/audio/customer-call-12.wav",
        mime: "audio/wav",
        duration_ms: 14200,
      },
    ],
  },
  {
    id: "dci-03",
    dataset_id: "ds-sota-multimodal",
    input: { query: "Extract section 4.2 compliance clause from the security audit report." },
    expected_output: "Section 4.2 specifies that all encryption keys must be rotated every 90 days.",
    context: ["Security Audit Report v3.1, Page 4"],
    metadata: { domain: "compliance", modality: "pdf" },
    source_trace_id: null,
    source_span_id: null,
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "pdf",
        url: "https://assets.matrix.local/docs/security-audit-report.pdf",
        mime: "application/pdf",
        page: 4,
        width: 1200,
        height: 1600,
      },
    ],
  },
  {
    id: "dci-04",
    dataset_id: "ds-sota-multimodal",
    input: { task: "Summarize code refactoring changes between releases." },
    expected_output: "Updated authentication middleware to use JWT bearer tokens instead of session cookies.",
    context: ["PR #1042: Migrate session auth to JWT"],
    metadata: { domain: "code", modality: "text_diff" },
    source_trace_id: null,
    source_span_id: null,
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "text_diff",
        url: "https://assets.matrix.local/diffs/auth-middleware.diff",
        mime: "text/x-diff",
        diff: {
          before: "function auth(req) {\n  return req.cookies.session_id;\n}",
          after: "function auth(req) {\n  return req.headers.authorization?.replace(/^Bearer /, '');\n}",
          hunks: [
            {
              old_start: 1,
              old_lines: 3,
              new_start: 1,
              new_lines: 3,
              lines: [
                " function auth(req) {",
                "-  return req.cookies.session_id;",
                "+  return req.headers.authorization?.replace(/^Bearer /, '');",
                " }",
              ],
            },
          ],
        },
      },
    ],
  },
];

describe("DatasetStudioClient (LakeFS Branching & Multimodal Inspector)", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders LakeFS branch selector, active commit hash, and multimodal table cells", () => {
    render(
      <DatasetStudioClient
        dataset={mockDataset}
        initialItems={mockItems}
        initialBranches={mockBranches}
        initialCommits={mockCommits}
      />
    );

    // Branch Selector
    expect(screen.getByRole("combobox", { name: /select active branch/i })).toBeTruthy();
    expect(screen.getByText("c-main-02")).toBeTruthy();

    // Multimodal table cells
    expect(screen.getByText(/2 boxes/i)).toBeTruthy();
    expect(screen.getAllByText(/Audio/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/PDF p.4/i)).toBeTruthy();
    expect(screen.getAllByText(/Diff/i).length).toBeGreaterThan(0);
  });

  it("opens '+ New Branch' modal dialog with accessible attributes and focus trap", () => {
    render(
      <DatasetStudioClient
        dataset={mockDataset}
        initialItems={mockItems}
        initialBranches={mockBranches}
        initialCommits={mockCommits}
      />
    );

    const newBranchBtn = screen.getByRole("button", { name: /\+ New Branch/i });
    fireEvent.click(newBranchBtn);

    const dialog = screen.getByRole("dialog", { name: /Create Zero-Copy Branch/i });
    expect(dialog).toBeTruthy();
    expect(dialog.getAttribute("aria-modal")).toBe("true");

    const input = screen.getByLabelText(/Branch Name/i);
    expect(input).toBeTruthy();

    // Close on Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /Create Zero-Copy Branch/i })).toBeNull();
  });

  it("opens 'Diff Branches' drawer with base/compare selectors and summary counters", async () => {
    // Mock diff fetch
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        dataset_id: "ds-sota-multimodal",
        base_commit_id: "c-main-02",
        head_commit_id: "c-feat-01",
        added: ["item-new-01"],
        removed: [],
        modified: ["item-table-q3"],
        stats: { added: 1, removed: 0, modified: 1 },
      }),
    } as Response);

    render(
      <DatasetStudioClient
        dataset={mockDataset}
        initialItems={mockItems}
        initialBranches={mockBranches}
        initialCommits={mockCommits}
      />
    );

    const diffBtn = screen.getByRole("button", { name: /Diff Branches/i });
    fireEvent.click(diffBtn);

    const baseSel = document.getElementById("base-branch-sel");
    const compareSel = document.getElementById("compare-branch-sel");
    expect(baseSel).toBeTruthy();
    expect(compareSel).toBeTruthy();

    // Close on Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /Diff & Merge Branches/i })).toBeNull();
  });

  it("toggles Commit History timeline and opens accessible Revert confirm dialog", () => {
    render(
      <DatasetStudioClient
        dataset={mockDataset}
        initialItems={mockItems}
        initialBranches={mockBranches}
        initialCommits={mockCommits}
      />
    );

    const historyBtn = screen.getByRole("button", { name: /Commit History/i });
    fireEvent.click(historyBtn);

    expect(screen.getByText(/Commit History Timeline/i)).toBeTruthy();
    expect(screen.getByText(/Initial multimodal dataset commit/i)).toBeTruthy();

    // Non-head commit should have a Revert button
    const revertBtn = screen.getByRole("button", { name: /Revert/i });
    fireEvent.click(revertBtn);

    const revertDialog = screen.getByRole("dialog", { name: /Confirm Branch Revert/i });
    expect(revertDialog).toBeTruthy();
    expect(screen.getByText(/Explicit impact:/i)).toBeTruthy();

    // Close on Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /Confirm Branch Revert/i })).toBeNull();
  });

  it("opens Row Inspector Drawer with SVG BBoxes, Audio Waveform, PDF attention, and Text Diff", () => {
    render(
      <DatasetStudioClient
        dataset={mockDataset}
        initialItems={mockItems}
        initialBranches={mockBranches}
        initialCommits={mockCommits}
      />
    );

    // Inspect the first item (image + bounding boxes)
    const inspectBtns = screen.getAllByRole("button", { name: /Inspect/i });
    fireEvent.click(inspectBtns[0]);

    const inspector = screen.getByRole("dialog", { name: /dci-01/i });
    expect(inspector).toBeTruthy();

    // Bounding Box controls & detected objects list
    expect(screen.getByText(/Show Bounding Boxes/i)).toBeTruthy();
    expect(screen.getByRole("list", { name: /Detected bounding boxes list/i })).toBeTruthy();
    expect(screen.getAllByText(/Q3 Revenue Header/i).length).toBeGreaterThan(0);

    // Navigate to next item (Audio)
    const nextBtn = screen.getByRole("button", { name: /Next row/i });
    fireEvent.click(nextBtn);

    expect(screen.getByText(/Audio Spectrogram & Waveform/i)).toBeTruthy();

    // Navigate to next item (PDF)
    fireEvent.click(nextBtn);
    expect(screen.getByText(/ColPali Patch Attention/i)).toBeTruthy();
    expect(screen.getByText(/Extracted Text/i)).toBeTruthy();

    // Navigate to next item (Text Diff)
    fireEvent.click(nextBtn);
    expect(screen.getAllByText(/Text Diff/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Candidate \/ After/i)).toBeTruthy();

    // Close on Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /dci-/i })).toBeNull();
  });
});

describe("SpanPanel (Multimodal Span Preview & Lightbox)", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const mockSpan: Span = {
    span_id: "span-01",
    trace_id: "trace-01",
    parent_span_id: null,
    name: "retrieve_and_inspect",
    kind: "AGENT",
    start_time: "2026-11-13T10:00:00Z",
    end_time: "2026-11-13T10:00:01Z",
    duration_ms: 1000,
    status: "ok",
    status_message: null,
    input: "Analyze multimodal input",
    output: "Verified 2 bounding boxes",
    input_tokens: 100,
    output_tokens: 50,
    reasoning_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_usd: 0.001,
    ttft_ms: 120,
    model: "claude-3-7-sonnet",
    provider: "anthropic",
    invocation_parameters: {},
    attributes: {},
    events: [],
    input_messages: [],
    output_messages: [],
    documents: [],
    tool: null,
    media: [
      {
        kind: "image",
        url: "https://assets.matrix.local/images/table.png",
        mime: "image/png",
        width: 800,
        height: 600,
        boxes: [{ x: 50, y: 50, w: 100, h: 50, label: "Table Cell" }],
      },
      {
        kind: "audio",
        url: "https://assets.matrix.local/audio/note.wav",
        mime: "audio/wav",
        duration_ms: 5000,
      },
      {
        kind: "pdf",
        url: "https://assets.matrix.local/docs/paper.pdf",
        mime: "application/pdf",
        page: 2,
      },
      {
        kind: "text_diff",
        url: "https://assets.matrix.local/diff.patch",
        mime: "text/x-diff",
        diff: {
          before: "const x = 1;",
          after: "const x = 2;",
        },
      },
    ],
  };

  it("renders rich multimodal media chips in span panel and opens Lightbox modal on click", () => {
    render(<SpanPanel span={mockSpan} traceStart={Date.parse(mockSpan.start_time)} />);

    // Media section exists
    expect(screen.getByText(/1 boxes/i)).toBeTruthy();
    expect(screen.getByText(/♫ Audio Stream/i)).toBeTruthy();
    expect(screen.getByText(/PDF Document · Page 2/i)).toBeTruthy();
    expect(screen.getByText(/~ Text Diff Payload/i)).toBeTruthy();

    // Click on image thumbnail to open Lightbox
    const imageBtn = screen.getByRole("button", { name: /Image 1 attached to retrieve_and_inspect/i });
    fireEvent.click(imageBtn);

    const modal = screen.getByRole("dialog", { name: /retrieve_and_inspect — Multimodal Media/i });
    expect(modal).toBeTruthy();
    expect(modal.getAttribute("aria-modal")).toBe("true");

    // Close on Escape
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
