import { beforeEach, describe, expect, it, vi } from "vitest";
import { redirect } from "next/navigation";
import { ApiError } from "@/lib/api-error";

const apiGetMock = vi.fn();

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  apiGet: (...args: unknown[]) => apiGetMock(...args),
}));

vi.mock("@/components/models/models-registry-client", () => ({
  ModelsRegistryClient: (props: Record<string, unknown>) => ({
    type: "ModelsRegistryClient",
    props,
  }),
}));

vi.mock("@/components/training/training-studio-client", () => ({
  TrainingStudioClient: (props: Record<string, unknown>) => ({
    type: "TrainingStudioClient",
    props,
  }),
}));

vi.mock("@/components/sweeps/sweeps-list-client", () => ({
  SweepsListClient: (props: Record<string, unknown>) => ({
    type: "SweepsListClient",
    props,
  }),
}));

vi.mock("@/components/sweeps/sweep-detail-client", () => ({
  SweepDetailClient: (props: Record<string, unknown>) => ({
    type: "SweepDetailClient",
    props,
  }),
}));

vi.mock("@/components/lineage/lineage-explorer-client", () => ({
  LineageExplorerClient: (props: Record<string, unknown>) => ({
    type: "LineageExplorerClient",
    props,
  }),
}));

vi.mock("@/components/benchmarks/benchmarks-client", () => ({
  BenchmarksClient: (props: Record<string, unknown>) => ({
    type: "BenchmarksClient",
    props,
  }),
}));

vi.mock("@/components/sandboxes/sandboxes-client", () => ({
  SandboxesClient: (props: Record<string, unknown>) => ({
    type: "SandboxesClient",
    props,
  }),
}));

vi.mock("@/components/safety/safety-dashboard", () => ({
  SafetyDashboard: (props: Record<string, unknown>) => ({
    type: "SafetyDashboard",
    props,
  }),
}));

vi.mock("@/components/datasets/dataset-studio-client", () => ({
  DatasetStudioClient: (props: Record<string, unknown>) => ({
    type: "DatasetStudioClient",
    props,
  }),
}));

import ModelsPage from "./models/page";
import TrainingPage from "./training/page";
import SweepsPage from "./sweeps/page";
import SweepDetailPage from "./sweeps/[sweepId]/page";
import LineagePage from "./lineage/page";
import BenchmarksPage from "./benchmarks/page";
import SandboxesPage from "./sandboxes/page";
import SafetyPage from "./safety/page";
import DatasetPage from "./datasets/[datasetId]/page";

function throwRedirect(): never {
  redirect("/api/auth/expired?next=%2F");
}

function findChildProps(node: any, targetName: string): Record<string, any> | null {
  if (!node || typeof node !== "object") return null;
  if (node.type?.name === targetName || String(node.type).includes(targetName)) {
    return node.props ?? null;
  }
  const props = node.props;
  if (!props) return null;
  if (Array.isArray(props.children)) {
    for (const child of props.children) {
      const hit = findChildProps(child, targetName);
      if (hit) return hit;
    }
  } else if (props.children) {
    return findChildProps(props.children, targetName);
  }
  return null;
}

describe("(app) Server Component unstable_rethrow & error surfacing", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
  });

  it("ModelsPage rethrows NEXT_REDIRECT from primary, secondary, and lineage catches", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(ModelsPage({})).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/mlops/models") return Promise.resolve({ items: [{ id: "m1" }] });
      if (path === "/api/v1/artifacts") return Promise.reject(new ApiError(500, "boom"));
      if (path === "/api/v1/mlops/models/m1") {
        return Promise.resolve({ id: "m1", versions: [{ id: "mv1" }] });
      }
      if (path === "/api/v1/models/m1/history") return Promise.resolve({ items: [] });
      if (path === "/api/v1/lineage") return throwRedirect();
      return Promise.resolve({ nodes: [], edges: [] });
    });
    await expect(ModelsPage({})).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/mlops/models") return Promise.resolve({ items: [{ id: "m1" }] });
      if (path === "/api/v1/artifacts") return throwRedirect();
      return Promise.resolve({ items: [] });
    });
    await expect(ModelsPage({})).rejects.toThrow("NEXT_REDIRECT");
  });

  it("ModelsPage surfaces initialLineageError when lineage returns HTTP 500", async () => {
    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/mlops/models") return Promise.resolve({ items: [{ id: "m1" }] });
      if (path === "/api/v1/artifacts") return Promise.resolve({ items: [] });
      if (path === "/api/v1/mlops/models/m1") {
        return Promise.resolve({ id: "m1", versions: [{ id: "mv1" }] });
      }
      if (path === "/api/v1/models/m1/history") return Promise.resolve({ items: [] });
      if (path === "/api/v1/lineage") {
        return Promise.reject(new ApiError(500, "Sweep has no attribute method"));
      }
      return Promise.resolve({ nodes: [], edges: [] });
    });

    const el = (await ModelsPage({})) as any;
    expect(el.props.initialLineageError).toContain("API 500: Sweep has no attribute method");
  });

  it("TrainingPage rethrows NEXT_REDIRECT from main catch, allSettled, and sweep detail catch", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(TrainingPage({ searchParams: Promise.resolve({}) })).rejects.toThrow(
      "NEXT_REDIRECT",
    );

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/runs") return Promise.resolve({ items: [{ id: "r1", summary_metrics: {} }] });
      if (path === "/api/v1/sweeps") return Promise.resolve({ items: [] });
      if (path === "/api/v1/runs/r1/reports") return throwRedirect();
      return Promise.resolve({ items: [] });
    });
    await expect(TrainingPage({ searchParams: Promise.resolve({}) })).rejects.toThrow(
      "NEXT_REDIRECT",
    );

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/runs") return Promise.resolve({ items: [] });
      if (path === "/api/v1/sweeps") {
        return Promise.resolve({ items: [{ id: "sw1", config: { method: "random", metric: { key: "loss", goal: "minimize" }, parameters: {} } }] });
      }
      if (path === "/api/v1/sweeps/sw1") return throwRedirect();
      return Promise.resolve({});
    });
    await expect(TrainingPage({ searchParams: Promise.resolve({}) })).rejects.toThrow(
      "NEXT_REDIRECT",
    );
  });

  it("SweepsPage and SweepDetailPage rethrow NEXT_REDIRECT", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(SweepsPage()).rejects.toThrow("NEXT_REDIRECT");
    await expect(
      SweepDetailPage({ params: Promise.resolve({ sweepId: "sw-1" }) }),
    ).rejects.toThrow("NEXT_REDIRECT");
  });

  it("LineagePage rethrows NEXT_REDIRECT", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(
      LineagePage({ searchParams: Promise.resolve({ node: "run:r1" }) }),
    ).rejects.toThrow("NEXT_REDIRECT");
  });

  it("BenchmarksPage rethrows NEXT_REDIRECT from runs and catalog", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(BenchmarksPage()).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/benchmarks/runs") return Promise.resolve({ runs: [] });
      if (path === "/api/v1/benchmarks/catalog") return throwRedirect();
      return Promise.resolve({});
    });
    await expect(BenchmarksPage()).rejects.toThrow("NEXT_REDIRECT");
  });

  it("SandboxesPage rethrows NEXT_REDIRECT from runs and transcripts catches", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(SandboxesPage({})).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/sandboxes/runs") return Promise.resolve([{ id: "sb-1" }]);
      if (path === "/api/v1/sandboxes/runs/sb-1/transcripts") return throwRedirect();
      return Promise.resolve({});
    });
    await expect(SandboxesPage({})).rejects.toThrow("NEXT_REDIRECT");
  });

  it("SafetyPage rethrows NEXT_REDIRECT and surfaces scan detail fetch errors via initialError", async () => {
    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/safety/categories") return Promise.resolve({ categories: [] });
      if (path === "/api/v1/safety/scans") return Promise.resolve({ scans: [{ id: "sc-1" }] });
      if (path === "/api/v1/safety/scans/sc-1") return throwRedirect();
      return Promise.resolve({});
    });
    await expect(SafetyPage()).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/safety/categories") return Promise.resolve({ categories: [] });
      if (path === "/api/v1/safety/scans") {
        return Promise.resolve({ scans: [{ id: "sc-1", safety_score: 90, status: "completed" }] });
      }
      if (path === "/api/v1/safety/scans/sc-1") {
        return Promise.reject(new ApiError(500, "scan detail down"));
      }
      return Promise.resolve({});
    });
    const el = await SafetyPage();
    const dashProps = findChildProps(el, "SafetyDashboard");
    expect(dashProps?.initialError).toContain("API 500: scan detail down");
  });

  it("DatasetPage rethrows NEXT_REDIRECT from dataset, branches, and commits catches", async () => {
    apiGetMock.mockImplementation(() => throwRedirect());
    await expect(
      DatasetPage({ params: Promise.resolve({ datasetId: "ds-1" }) }),
    ).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/datasets/ds-1") {
        return Promise.resolve({ dataset: { id: "ds-1", name: "ds" }, items: [] });
      }
      if (path === "/api/v1/datasets/ds-1/branches") return throwRedirect();
      return Promise.resolve({ items: [] });
    });
    await expect(
      DatasetPage({ params: Promise.resolve({ datasetId: "ds-1" }) }),
    ).rejects.toThrow("NEXT_REDIRECT");

    apiGetMock.mockImplementation((path: string) => {
      if (path === "/api/v1/datasets/ds-1") {
        return Promise.resolve({ dataset: { id: "ds-1", name: "ds" }, items: [] });
      }
      if (path === "/api/v1/datasets/ds-1/branches") return Promise.resolve({ items: [] });
      if (path === "/api/v1/datasets/ds-1/commits") return throwRedirect();
      return Promise.resolve({ items: [] });
    });
    await expect(
      DatasetPage({ params: Promise.resolve({ datasetId: "ds-1" }) }),
    ).rejects.toThrow("NEXT_REDIRECT");
  });
});
