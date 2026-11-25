// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { LineageGraph } from "@/lib/types";

const proxyMock = vi.fn();

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

vi.mock("@/lib/proxy-client", async (orig) => ({
  ...(await orig<typeof import("@/lib/proxy-client")>()),
  proxy: (...args: unknown[]) => proxyMock(...args),
}));

import { LineageExplorerClient } from "./lineage-explorer-client";

const TRUNCATED_GRAPH: LineageGraph = {
  root: "model_version:mv-lora-v3",
  direction: "both",
  depth: 3,
  truncated: true,
  nodes: [
    {
      id: "dataset:ds-support-sft",
      node_type: "dataset",
      entity_id: "ds-support-sft",
      label: "ds-support-sft",
      metadata: {},
    },
    {
      id: "model_version:mv-lora-v3",
      node_type: "model_version",
      entity_id: "mv-lora-v3",
      label: "support-copilot-lora v3",
      metadata: { stage: "production" },
    },
  ],
  edges: [
    {
      id: "e-1",
      source: "dataset:ds-support-sft",
      target: "model_version:mv-lora-v3",
      source_type: "dataset",
      source_id: "ds-support-sft",
      target_type: "model_version",
      target_id: "mv-lora-v3",
      relation: "trained_on",
      metadata: {},
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LineageExplorerClient", () => {
  it("renders accessible warning banner when truncated is true", () => {
    render(
      <LineageExplorerClient
        initialGraph={TRUNCATED_GRAPH}
        initialNode="model_version:mv-lora-v3"
        initialDirection="both"
        initialDepth={3}
      />,
    );

    const banner = screen.getByTestId("lineage-truncated-banner");
    expect(banner.getAttribute("role")).toBe("alert");
    expect(banner.textContent).toMatch(/truncated/i);
  });

  it("hides truncated warning banner when truncated is false", () => {
    render(
      <LineageExplorerClient
        initialGraph={{ ...TRUNCATED_GRAPH, truncated: false }}
        initialNode="model_version:mv-lora-v3"
        initialDirection="both"
        initialDepth={3}
      />,
    );

    expect(screen.queryByTestId("lineage-truncated-banner")).toBeNull();
  });

  it("queries canonical GET lineage endpoint when submitting form", async () => {
    proxyMock.mockResolvedValueOnce({ ...TRUNCATED_GRAPH, truncated: false });

    render(
      <LineageExplorerClient
        initialGraph={TRUNCATED_GRAPH}
        initialNode="model_version:mv-lora-v3"
        initialDirection="both"
        initialDepth={3}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Root Node/i), {
      target: { value: "run:run-lora-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Explore|Query Lineage/i }));

    await waitFor(() => {
      expect(proxyMock).toHaveBeenCalledWith(
        "GET",
        expect.stringContaining("lineage?node=run%3Arun-lora-01"),
      );
    });
  });
});
