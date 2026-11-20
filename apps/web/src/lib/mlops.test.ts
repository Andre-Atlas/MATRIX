import { beforeEach, describe, expect, it } from "vitest";
import { mockGet, mockSend } from "./mock-api";
import { resetMockMlops } from "./mock-mlops";
import {
  adaptLineageGraph,
  adaptRunDetail,
  adaptSweepAnalysis,
  computeHyperparamDiff,
  filterModels,
  filterRuns,
  formatBytes,
  normalizeClassicConfusion,
  shortSha,
  smoothEma,
} from "./mlops";
import { getAliasStyle, getModelStageStyle, getRunStatusStyle, RUN_STATUS_STYLE } from "./mlops-tokens";
import type {
  CanonicalSweep,
  CanonicalSweepDetail,
  LineageGraph,
  ListResponse,
  ModelAlias,
  ModelVersion,
  RegisteredModel,
  RegisteredModelDetail,
} from "./types";
import type { Run, SeriesResponse } from "./runs/types";

describe("mlops helpers", () => {
  it("smoothEma smooths series with exponential moving average", () => {
    expect(smoothEma([], 0.3)).toEqual([]);

    const pts = [
      { step: 1, value: 10 },
      { step: 2, value: 20 },
      { step: 3, value: 30 },
    ];
    const smoothed = smoothEma(pts, 0.5);
    expect(smoothed[0].value).toBe(10);
    expect(smoothed[1].value).toBe(15); // 0.5 * 20 + 0.5 * 10
    expect(smoothed[2].value).toBe(22.5); // 0.5 * 30 + 0.5 * 15

    const identity = smoothEma(pts, 1.0);
    expect(identity.map((p) => p.value)).toEqual([10, 20, 30]);
  });

  it("normalizeClassicConfusion handles counts and row-wise percentages", () => {
    const matrix = [
      [80, 20],
      [10, 90],
    ];
    expect(normalizeClassicConfusion(matrix, "counts")).toEqual([
      [80, 20],
      [10, 90],
    ]);
    expect(normalizeClassicConfusion(matrix, "percent")).toEqual([
      [80, 20],
      [10, 90],
    ]);

    const uneven = [
      [3, 1],
      [0, 0],
    ];
    expect(normalizeClassicConfusion(uneven, "percent")).toEqual([
      [75, 25],
      [0, 0],
    ]);
  });

  it("formatBytes and shortSha format cleanly", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(null)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.50 KB");
    expect(formatBytes(168_427_520)).toBe("161 MB");
    expect(formatBytes(15_420_882_944)).toBe("14.4 GB");

    expect(shortSha("a3f9c2d1e4b890123456")).toBe("a3f9c2d1");
    expect(shortSha("a3f9c2d1e4b890123456", 12)).toBe("a3f9c2d1e4b8");
    expect(shortSha(null)).toBe("—");
  });

  it("computeHyperparamDiff detects differing and shared hyperparams", () => {
    const r1 = adaptRunDetail({
      id: "r1",
      name: "run-1",
      hyperparams: { learning_rate: 2e-4, r: 16, scheduler: "cosine" },
    });
    const r2 = adaptRunDetail({
      id: "r2",
      name: "run-2",
      hyperparams: { learning_rate: 5e-5, r: 16, beta: 0.1 },
    });

    const diff = computeHyperparamDiff([r1, r2]);
    expect(diff.keys).toEqual(["beta", "learning_rate", "r", "scheduler"]);
    expect(diff.differs.r).toBe(false);
    expect(diff.differs.learning_rate).toBe(true);
    expect(diff.differs.beta).toBe(true);
    expect(diff.differs.scheduler).toBe(true);
  });

  it("filterRuns and filterModels filter by search, status, type, stage, and alias", () => {
    const runs = [
      adaptRunDetail({ id: "1", name: "lora-support", run_type: "lora", status: "completed", tags: ["prod"] }),
      adaptRunDetail({ id: "2", name: "dpo-align", run_type: "dpo", status: "running", tags: ["align"] }),
    ];
    expect(filterRuns(runs, { status: "running" })).toHaveLength(1);
    expect(filterRuns(runs, { run_type: "lora" })).toHaveLength(1);
    expect(filterRuns(runs, { search: "align" })).toHaveLength(1);

    const models: RegisteredModel[] = [
      {
        id: "m1",
        name: "support-copilot-lora",
        description: null,
        model_type: "lora",
        base_model: "llama-3",
        tags: [],
        created_at: "",
        updated_at: "",
        aliases: { champion: 3 },
        stages: { production: [3] },
      },
      {
        id: "m2",
        name: "churn-xgboost",
        description: null,
        model_type: "classifier",
        base_model: "xgboost",
        tags: [],
        created_at: "",
        updated_at: "",
        aliases: {},
        stages: { staging: [1] },
      },
    ];
    expect(filterModels(models, { model_type: "lora" })).toHaveLength(1);
    expect(filterModels(models, { stage: "production" })).toHaveLength(1);
    expect(filterModels(models, { alias: "@champion" })).toHaveLength(1);
    expect(filterModels(models, { search: "xgboost" })).toHaveLength(1);
  });

  it("safe adapters handle null/partial payloads", () => {
    const run = adaptRunDetail(null);
    expect(run.hyperparams).toEqual({});
    expect(run.artifacts).toEqual([]);

    const analysis = adaptSweepAnalysis(undefined);
    expect(analysis.parallel_coordinates.axes).toEqual([]);
    expect(analysis.importance).toEqual([]);

    const graph = adaptLineageGraph(null);
    expect(graph.nodes).toEqual([]);
    expect(graph.edges).toEqual([]);
  });

  it("tokens satisfy WCAG 2.2 AA and reduced-motion rules", () => {
    expect(RUN_STATUS_STYLE.running.dot).toContain("motion-reduce:animate-none");
    expect(getRunStatusStyle("completed").badge).toContain("text-emerald-300");
    expect(getModelStageStyle("production").badge).toContain("text-emerald-300");
    expect(getAliasStyle("@champion")).toContain("text-emerald-300");
    expect(getAliasStyle("challenger")).toContain("text-violet-300");
  });
});

describe("mock-api canonical MLOps, Sweeps, Registry & Lineage routes", () => {
  beforeEach(() => {
    resetMockMlops();
  });

  it("lists canonical runs, metrics, and bounded run lineage", async () => {
    const runsResp = await mockGet<ListResponse<Run>>("/api/v1/runs");
    expect(runsResp.items.length).toBeGreaterThanOrEqual(5);
    expect(runsResp.items.some((r) => r.status === "failed")).toBe(true);

    const dpoRun = await mockGet<Run>("/api/v1/runs/run_llama3_dpo");
    expect(dpoRun.id).toBe("run_llama3_dpo");

    const metrics = await mockGet<SeriesResponse>("/api/v1/runs/run_llama3_dpo/metrics", {
      keys: "train/loss,sys/gpu.0.utilization",
    });
    expect(metrics.series["train/loss"].steps.length).toBeGreaterThan(5);
    expect(metrics.series["sys/gpu.0.utilization"].steps.length).toBeGreaterThan(5);

    const lineage = await mockGet<LineageGraph>("/api/v1/lineage", {
      node: "run:run-lora-01",
      direction: "both",
      depth: 3,
    });
    expect(lineage.nodes.length).toBeGreaterThanOrEqual(5);
    expect(lineage.edges.some((e) => e.relation === "trained_on")).toBe(true);
  });

  it("lists canonical sweeps and sweep detail with runs and importance", async () => {
    const sweepsResp = await mockGet<{ items: CanonicalSweep[] }>("/api/v1/sweeps");
    expect(sweepsResp.items).toHaveLength(2);

    const tpe = await mockGet<CanonicalSweepDetail>("/api/v1/sweeps/swp-tpe-01");
    expect(tpe.sweep.config.method).toBe("tpe");
    expect(tpe.runs).toHaveLength(6);
    expect(tpe.importance && tpe.importance.length).toBeGreaterThan(0);

    const grid = await mockGet<CanonicalSweepDetail>("/api/v1/sweeps/swp-grid-02");
    expect(grid.sweep.config.method).toBe("grid");
    expect(grid.runs).toHaveLength(3);
  });

  it("lists models, model detail, canonical stage/alias updates, and canonical lineage", async () => {
    const modelsResp = await mockGet<{ items: RegisteredModel[] }>("/api/v1/mlops/models");
    expect(modelsResp.items).toHaveLength(3);

    const loraModel = await mockGet<RegisteredModelDetail>("/api/v1/mlops/models/model-lora-01");
    expect(loraModel.versions).toHaveLength(3);
    expect(loraModel.aliases).toEqual({ champion: 3, challenger: 2 });

    const movedAlias = await mockSend<ModelAlias>("PUT", "/api/v1/models/model-lora-01/aliases/champion", {
      version: 2,
    });
    expect(movedAlias).toEqual({ model_id: "model-lora-01", alias: "champion", version: 2 });

    const updatedModel = await mockGet<RegisteredModelDetail>("/api/v1/mlops/models/model-lora-01");
    expect(updatedModel.aliases.champion).toBe(2);

    const promoted = await mockSend<ModelVersion>(
      "POST",
      "/api/v1/models/model-lora-01/versions/2/stage",
      { stage: "production" },
    );
    expect(promoted.stage).toBe("production");

    const modelLineage = await mockGet<LineageGraph>("/api/v1/lineage", {
      node: "model_version:mv-lora-v3",
      direction: "both",
      depth: 3,
    });
    expect(modelLineage.nodes.some((n) => n.node_type === "dataset")).toBe(true);
    expect(modelLineage.nodes.some((n) => n.node_type === "safety_scan")).toBe(true);
    expect(modelLineage.edges.some((e) => e.relation === "audited_by")).toBe(true);

    const truncatedLineage = await mockGet<LineageGraph>("/api/v1/lineage", {
      node: "model_version:mv-lora-v3",
      direction: "both",
      depth: 1,
    });
    expect(truncatedLineage.truncated).toBe(true);
    expect(adaptLineageGraph(truncatedLineage).truncated).toBe(true);
  });
});
