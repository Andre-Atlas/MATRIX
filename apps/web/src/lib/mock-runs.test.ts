import { beforeEach, describe, expect, it } from "vitest";
import { resetMockRuns, runArtifactDownload, runsGet, runsSend } from "./mock-runs";
import type { CompareResponse, MetricKeyStat, Report, Run, RunPage, SeriesResponse } from "./runs/types";

beforeEach(() => resetMockRuns());

describe("mock runs", () => {
  it("lists 12 runs with every status, filters and pages by cursor", () => {
    const all = runsGet("/api/v1/runs", { limit: 50 }) as RunPage;
    expect(all.items).toHaveLength(12);
    expect(new Set(all.items.map((r) => r.status))).toEqual(new Set(["running", "finished", "failed", "killed"]));
    const failed = runsGet("/api/v1/runs", { status: "failed" }) as RunPage;
    expect(failed.items.every((r) => r.status === "failed")).toBe(true);
    expect((runsGet("/api/v1/runs", { q: "resnet" }) as RunPage).items).toHaveLength(4);
    const p1 = runsGet("/api/v1/runs", { limit: 5 }) as RunPage;
    const p2 = runsGet("/api/v1/runs", { limit: 5, cursor: p1.next_cursor! }) as RunPage;
    expect(p2.items[0].id).not.toBe(p1.items[0].id);
  });

  it("reduces 20 000-point series with M4 under max_points and returns NaN as null", () => {
    const keys = (runsGet("/api/v1/runs/run_llama3_sft/metric-keys", {}) as { keys: MetricKeyStat[] }).keys;
    expect(keys.find((k) => k.key === "train/loss")!.count).toBeGreaterThan(19_000);
    expect(keys.some((k) => k.key === "sys/gpu.1.power_w")).toBe(true);
    const res = runsGet("/api/v1/runs/run_llama3_sft/metrics", { keys: "train/loss,train/grad_norm", max_points: 1000 }) as SeriesResponse;
    expect(res.downsampled).toBe(true);
    expect(res.series["train/loss"].steps.length).toBeLessThanOrEqual(1000);
    const div = runsGet("/api/v1/runs/run_resnet_3e3/metrics", { keys: "train/loss", max_points: 5000 }) as SeriesResponse;
    expect(div.series["train/loss"].values.at(-1)).toBeNull();
  });

  it("compare, reports and artifacts", () => {
    const cmp = runsGet("/api/v1/runs/compare", { run_ids: "run_resnet_1e3,run_resnet_3e4,nope", keys: "eval/acc" }) as CompareResponse;
    expect(Object.keys(cmp.runs)).toEqual(["run_resnet_1e3", "run_resnet_3e4"]);
    const reps = (runsGet("/api/v1/runs/run_churn_gbm/reports", {}) as { items: Report[] }).items;
    const cls = reps.find((r) => r.kind === "classification")!;
    expect(cls.kind === "classification" && cls.data.roc.every((c) => c.fpr.length <= 200 && c.auc > 0.5)).toBe(true);
    const arts = runsGet("/api/v1/runs/run_churn_gbm/artifacts", {}) as { items: { artifact: { id: string } }[] };
    expect(runArtifactDownload(`/api/v1/artifacts/${arts.items[0].artifact.id}/download`)?.status).toBe(200);
  });

  it("PATCH marks a run killed and DELETE removes it", () => {
    const run = runsSend("PATCH", "/api/v1/runs/run_llama3_dpo", { status: "killed" }) as Run;
    expect(run.status).toBe("killed");
    runsSend("DELETE", "/api/v1/runs/run_llama3_dpo", undefined);
    expect(() => runsGet("/api/v1/runs/run_llama3_dpo", {})).toThrow();
  });
});
