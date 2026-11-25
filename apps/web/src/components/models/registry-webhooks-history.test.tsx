// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type {
  ModelStageHistoryItem,
  ModelVersion,
  ModelWebhook,
  RegisteredModelDetail,
  WebhookDelivery,
} from "@/lib/types";

const proxyMock = vi.fn();
let mockIsAdmin = true;

vi.mock("@/lib/proxy-client", async (orig) => ({
  ...(await orig<typeof import("@/lib/proxy-client")>()),
  proxy: (...args: unknown[]) => proxyMock(...args),
}));
vi.mock("@/components/auth/role-context", () => ({
  useCan: (minRole: string) => (minRole === "admin" ? mockIsAdmin : true),
}));

import { ProxyError } from "@/lib/proxy-client";
import { LineageDagTab } from "./lineage-dag-tab";
import { ModelsRegistryClient } from "./models-registry-client";
import { StageHistoryTab } from "./stage-history-tab";
import { WebhooksTab } from "./webhooks-tab";
import { PromoteModal } from "./promote-modal";

const SAMPLE_HISTORY: ModelStageHistoryItem[] = [
  {
    id: "sh-1",
    project_id: "proj-default",
    model_id: "model-lora-01",
    version: 3,
    from_stage: "staging",
    to_stage: "production",
    actor_kind: "user",
    actor_id: "admin@matrix.local",
    comment: "Passed safety audit",
    ts: "2026-11-12T12:30:00Z",
  },
];

const SAMPLE_WEBHOOK: ModelWebhook = {
  id: "wh-1",
  project_id: "proj-default",
  model_id: "model-lora-01",
  url: "https://ci.acme.internal/deploy",
  events: ["stage.changed"],
  active: true,
  created_by: "admin@matrix.local",
  created_at: "2026-11-10T10:00:00Z",
};

const SAMPLE_DELIVERIES: WebhookDelivery[] = [
  {
    id: "del-1",
    project_id: "proj-default",
    webhook_id: "wh-1",
    model_id: "model-lora-01",
    event: "stage.changed",
    status: "delivered",
    attempts: 1,
    last_status_code: 200,
    last_error: null,
    created_at: "2026-11-12T12:30:01Z",
    last_attempt_at: "2026-11-12T12:30:02Z",
    delivered_at: "2026-11-12T12:30:02Z",
  },
];

const SAMPLE_VERSION: ModelVersion = {
  id: "mv-2",
  project_id: "proj-default",
  model_id: "model-lora-01",
  version: 2,
  artifact_id: "art-1",
  run_id: "run-1",
  eval_experiment_id: null,
  safety_scan_id: null,
  stage: "staging",
  lora_config: null,
  signature: null,
  metrics_snapshot: {},
  model_card: null,
  created_by: "admin@matrix.local",
  created_at: "2026-11-11T10:00:00Z",
};

const SAMPLE_MODEL: RegisteredModelDetail = {
  id: "model-lora-01",
  project_id: "proj-default",
  name: "support-copilot-lora",
  description: null,
  model_type: "lora",
  base_model: "meta-llama/Llama-3.1-8B-Instruct",
  tags: [],
  created_at: "2026-11-08T10:00:00Z",
  updated_at: "2026-11-12T10:00:00Z",
  latest_version: 2,
  aliases: { champion: 1 },
  stages: { staging: [2] },
  latest_metrics: {},
  versions: [SAMPLE_VERSION],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockIsAdmin = true;
});

describe("StageHistoryTab", () => {
  it("renders stage transition timeline entries", () => {
    render(<StageHistoryTab history={SAMPLE_HISTORY} />);

    expect(screen.getByText("Passed safety audit")).toBeTruthy();
    expect(screen.getByText("v3")).toBeTruthy();
  });

  it("shows empty state when no stage transitions exist", () => {
    render(<StageHistoryTab history={[]} />);

    expect(screen.getByText(/No stage transitions recorded/i)).toBeTruthy();
  });
});

describe("WebhooksTab", () => {
  it("creates a webhook, reveals one-time secret with warning, and loads deliveries", async () => {
    proxyMock
      .mockResolvedValueOnce({ items: [SAMPLE_WEBHOOK] }) // initial GET webhooks
      .mockResolvedValueOnce({
        id: "wh-2",
        project_id: "proj-default",
        model_id: "model-lora-01",
        url: "https://hooks.example.com/model",
        events: ["stage.changed", "alias.moved"],
        active: true,
        created_by: "admin@matrix.local",
        created_at: "2026-11-13T10:00:00Z",
        secret: "whsec_one_time_secret_123",
      }) // POST webhook
      .mockResolvedValueOnce({ items: SAMPLE_DELIVERIES }); // GET deliveries

    render(<WebhooksTab modelId="model-lora-01" />);

    await waitFor(() => {
      expect(screen.getByText("https://ci.acme.internal/deploy")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText(/Endpoint URL/i), {
      target: { value: "https://hooks.example.com/model" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add Webhook|Create Webhook/i }));

    const secretEl = await screen.findByText(/whsec_one_time_secret_123/);
    expect(secretEl).toBeTruthy();

    const delivBtns = screen.getAllByRole("button", { name: /Deliveries/i });
    fireEvent.click(delivBtns[delivBtns.length - 1]);
    await waitFor(() => {
      expect(proxyMock).toHaveBeenCalledWith("GET", "models/model-lora-01/webhooks/wh-1/deliveries");
      expect(screen.getByText("200")).toBeTruthy();
    });
  });

  it("gates webhook management behind admin role", () => {
    mockIsAdmin = false;

    render(<WebhooksTab modelId="model-lora-01" />);

    expect(screen.getAllByText(/admin/i).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Add Webhook|Create Webhook/i })).toBeNull();
  });
});

describe("PromoteModal", () => {
  it("posts canonical stage transition with optional comment", async () => {
    proxyMock.mockResolvedValueOnce({ ...SAMPLE_VERSION, stage: "production" });
    const onSuccess = vi.fn();
    const onClose = vi.fn();

    render(
      <PromoteModal
        open
        mode="stage"
        model={SAMPLE_MODEL}
        version={SAMPLE_VERSION}
        onClose={onClose}
        onSuccess={onSuccess}
      />,
    );

    fireEvent.click(screen.getByDisplayValue("production"));
    fireEvent.change(screen.getByLabelText(/Transition Note/i), {
      target: { value: "Ready for prod" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Set Stage to Production/i }));

    await waitFor(() => {
      expect(proxyMock).toHaveBeenCalledWith("POST", "models/model-lora-01/versions/2/stage", {
        stage: "production",
        comment: "Ready for prod",
      });
    });
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });
});

describe("ModelsRegistryClient & LineageDagTab error handling", () => {
  it("LineageDagTab renders error alert instead of empty DAG when error is set", () => {
    const onRetry = vi.fn();
    render(
      <LineageDagTab
        lineage={{ nodes: [], edges: [] }}
        error="Internal server error loading sweep lineage."
        onRetry={onRetry}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Internal server error loading sweep lineage.");
    expect(screen.queryByText(/No lineage data recorded/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("ModelsRegistryClient surfaces HTTP 500 lineage failure instead of swallowing it as an empty DAG", async () => {
    proxyMock.mockImplementation((_method: string, path: string) => {
      if (path === "mlops/models/model-lora-01") {
        return Promise.resolve(SAMPLE_MODEL);
      }
      if (path === "models/model-lora-01/history") {
        return Promise.resolve({ items: SAMPLE_HISTORY });
      }
      if (path.startsWith("lineage?")) {
        return Promise.reject(
          new ProxyError(500, "'Sweep' object has no attribute 'method'"),
        );
      }
      return Promise.resolve({ nodes: [], edges: [] });
    });

    render(
      <ModelsRegistryClient
        initialModels={[SAMPLE_MODEL]}
        initialDetail={SAMPLE_MODEL}
        initialLineage={null}
        initialHistory={SAMPLE_HISTORY}
        initialArtifacts={[]}
        initialSelectedId="model-lora-01"
        initialTab="lineage"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /support-copilot-lora/i }));

    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert.textContent).toContain("'Sweep' object has no attribute 'method'");
    });
    expect(screen.queryByText(/No lineage data recorded/i)).toBeNull();
  });

  it("ModelsRegistryClient surfaces network failure on model fallback lineage endpoint", async () => {
    const noVersionsModel: RegisteredModelDetail = {
      ...SAMPLE_MODEL,
      id: "model-empty-ver",
      name: "empty-version-model",
      versions: [],
    };
    proxyMock.mockImplementation((_method: string, path: string) => {
      if (path === "mlops/models/model-empty-ver") {
        return Promise.resolve(noVersionsModel);
      }
      if (path === "models/model-empty-ver/history") {
        return Promise.resolve({ items: [] });
      }
      if (path === "mlops/models/model-empty-ver/lineage") {
        return Promise.reject(
          new ProxyError(0, "Network error: the MATRIX web server did not answer."),
        );
      }
      return Promise.resolve({ nodes: [], edges: [] });
    });

    render(
      <ModelsRegistryClient
        initialModels={[SAMPLE_MODEL, noVersionsModel]}
        initialDetail={SAMPLE_MODEL}
        initialLineage={null}
        initialHistory={SAMPLE_HISTORY}
        initialArtifacts={[]}
        initialSelectedId="model-lora-01"
        initialTab="lineage"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /empty-version-model/i }));

    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert.textContent).toContain("Network error");
    });
    expect(screen.queryByText(/No lineage data recorded/i)).toBeNull();
  });
});
