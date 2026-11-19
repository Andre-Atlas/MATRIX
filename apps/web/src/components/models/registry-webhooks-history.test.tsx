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
