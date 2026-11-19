import { beforeEach, describe, expect, it } from "vitest";
import { MOCK_DATASET_ID, MOCK_EXPERIMENT_ID, evalExportJsonl, evalGet, evalSend, resetMockEvals } from "./mock-evals";
import type { DatasetDetail, ExperimentDetail, ListResponse, ExperimentListItem } from "./types";

beforeEach(() => resetMockEvals());

describe("mock evals", () => {
  it("seeds a finished sweep with regressions and faithfulness statements", () => {
    const d = evalGet(`/api/v1/experiments/${MOCK_EXPERIMENT_ID}`) as ExperimentDetail;
    expect(d.experiment.status).toBe("done");
    expect(d.results).toHaveLength(24);
    expect(d.regressions.length).toBeGreaterThan(0);
    expect(d.regressions.some((r) => r.metric === "latency")).toBe(true);
    const st = d.results.find((r) => r.scores.faithfulness?.details?.statements)!;
    expect(Array.isArray(st.scores.faithfulness.details!.statements)).toBe(true);
    const list = evalGet("/api/v1/experiments") as ListResponse<ExperimentListItem>;
    expect(new Set(list.items.map((e) => e.status))).toEqual(new Set(["done", "running", "failed"]));
  });

  it("exposes _meta without treating it as a variant, and an errored regression", () => {
    const d = evalGet(`/api/v1/experiments/${MOCK_EXPERIMENT_ID}`) as ExperimentDetail;
    const meta = d.summary._meta as { cells_total: number; cells_failed: number; partial: boolean; judge: string };
    expect(meta).toMatchObject({ cells_total: 24, cells_failed: 1, partial: true, judge: "heuristic" });
    expect(Object.keys(d.summary).filter((k) => k !== "_meta")).toEqual(d.experiment.variants.map((v) => v.label));
    expect(d.regressions.some((r) => r.errored)).toBe(true);
    expect(d.regressions.some((r) => r.errored === false)).toBe(true);
  });

  it("creates datasets and items, deletes and exports them", () => {
    const ds = evalSend("POST", "/api/v1/datasets", { name: "x" }) as { id: string };
    const item = evalSend("POST", `/api/v1/datasets/${ds.id}/items`, { input: { q: 1 }, context: ["c"] }) as { id: string };
    expect(evalExportJsonl(`/api/v1/datasets/${ds.id}/export.jsonl`)).toContain('"q":1');
    evalSend("DELETE", `/api/v1/datasets/${ds.id}/items/${item.id}`, undefined);
    expect((evalGet(`/api/v1/datasets/${ds.id}`) as DatasetDetail).items).toHaveLength(0);
    expect(() => evalSend("POST", `/api/v1/datasets/${ds.id}/items`, { input: [] })).toThrow();
    expect((evalGet(`/api/v1/datasets/${MOCK_DATASET_ID}`) as DatasetDetail).items).toHaveLength(6);
  });

  it("queues new experiments", () => {
    const res = evalSend("POST", "/api/v1/experiments", {
      name: "n",
      dataset_id: MOCK_DATASET_ID,
      variants: [{ label: "a", prompt: { template: "{{question}}", type: "text" }, model: { provider: "fake", name: "echo" } }],
      metrics: [{ type: "contains", params: { value: "the" } }],
    }) as { id: string; status: string };
    expect(res.status).toBe("queued");
    expect((evalGet(`/api/v1/experiments/${res.id}`) as ExperimentDetail).experiment.status).toBe("queued");
  });
});
