import { describe, expect, it } from "vitest";
import {
  canonicalRunMetricKeys,
  canonicalRunToTrainingRun,
  canonicalSeriesToTrainingSeries,
  canonicalSweepDetailToSweepDetail,
  canonicalSweepToMLOpsSweep,
} from "./training-canonical";
import type { CanonicalSweep, CanonicalSweepDetail } from "./types";
import type { Report, Run, SeriesResponse } from "./runs/types";

const CANONICAL_RUN: Run = {
  id: "run-churn-01",
  name: "churn-xgb",
  status: "finished",
  group: "classic",
  tags: ["classic_ml"],
  params: { max_depth: 6, lr: 0.05 },
  git: null,
  env: null,
  summary: { "train/loss": 0.21, "sys/gpu.0.utilization": 88, "sys/gpu.0.memory_used_mb": 4096 },
  dataset_version_id: "dsv_churn_v1",
  sweep_id: null,
  parent_run_id: null,
  created_by: "admin@matrix.local",
  started_at: "2026-11-12T08:00:00Z",
  ended_at: "2026-11-12T08:15:00Z",
  heartbeat_at: "2026-11-12T08:15:00Z",
  error: null,
};

const CANONICAL_REPORTS: Report[] = [
  {
    id: "rep-1",
    run_id: "run-churn-01",
    kind: "classification",
    name: "eval",
    created_at: "2026-11-12T08:15:00Z",
    data: {
      labels: ["stay", "churn"],
      metrics: { accuracy: 0.91, precision_macro: 0.89, recall_macro: 0.87, f1_macro: 0.88, log_loss: 0.23 },
      per_class: [],
      confusion_matrix: [
        [412, 28],
        [35, 190],
      ],
      roc: [{ label: "churn", fpr: [0, 0.1, 1], tpr: [0, 0.85, 1], auc: 0.942 }],
      pr: [{ label: "churn", recall: [0, 0.85, 1], precision: [1, 0.88, 0.5], ap: 0.91 }],
    },
  },
  {
    id: "rep-2",
    run_id: "run-churn-01",
    kind: "importance",
    name: "permutation",
    created_at: "2026-11-12T08:15:00Z",
    data: {
      method: "permutation",
      features: [{ name: "tenure_months", importance: 0.42 }],
    },
  },
];

describe("training-canonical adapters", () => {
  it("maps canonical G1 Run + reports into TrainingRun with classic_eval and GPU aliases", () => {
    const tr = canonicalRunToTrainingRun(CANONICAL_RUN, CANONICAL_REPORTS);
    expect(tr.status).toBe("completed");
    expect(tr.run_type).toBe("classic_ml");
    expect(tr.hyperparams).toEqual({ max_depth: 6, lr: 0.05 });
    expect(tr.summary_metrics["gpu/utilization"]).toBe(88);
    expect(tr.summary_metrics["gpu/vram_gb"]).toBe(4);
    expect(tr.classic_eval?.roc_auc).toBe(0.942);
    expect(tr.classic_eval?.confusion_matrix?.matrix[0][0]).toBe(412);
    expect(tr.classic_eval?.feature_importance?.[0]).toEqual({
      feature: "tenure_months",
      importance: 0.42,
    });
  });

  it("converts SeriesResponse into TrainingMetricPoint arrays and filters null/non-finite", () => {
    const res: SeriesResponse = {
      downsampled: false,
      series: {
        "train/loss": { steps: [1, 2, 3], values: [0.9, null, 0.4], ts: ["t1", "t2", "t3"] },
        "sys/gpu.0.memory_used_mb": { steps: [1], values: [8192], ts: ["t1"] },
      },
    };
    const out = canonicalSeriesToTrainingSeries(res);
    expect(out["train/loss"]).toEqual([
      { step: 1, value: 0.9, recorded_at: "t1" },
      { step: 3, value: 0.4, recorded_at: "t3" },
    ]);
    expect(out["gpu/vram_gb"]).toEqual([{ step: 1, value: 8, recorded_at: "t1" }]);

    const emptyShadowed = canonicalSeriesToTrainingSeries({
      downsampled: false,
      series: {
        "gpu/utilization": { steps: [], values: [], ts: [] },
        "sys/gpu.0.utilization": { steps: [1], values: [72], ts: ["t1"] },
        "gpu/vram_gb": { steps: [], values: [], ts: [] },
        "sys/gpu.0.memory_used_mb": { steps: [1], values: [2048], ts: ["t1"] },
        "dpo/reward_margin": { steps: [], values: [], ts: [] },
        "rewards/margins": { steps: [1], values: [1.5], ts: ["t1"] },
      },
    });
    expect(emptyShadowed["gpu/utilization"]).toEqual([{ step: 1, value: 72, recorded_at: "t1" }]);
    expect(emptyShadowed["gpu/vram_gb"]).toEqual([{ step: 1, value: 2, recorded_at: "t1" }]);
    expect(emptyShadowed["dpo/reward_margin"]).toEqual([{ step: 1, value: 1.5, recorded_at: "t1" }]);
  });

  it("builds bounded metric keys list for ?keys=... queries", () => {
    const keys = canonicalRunMetricKeys(CANONICAL_RUN, [
      { key: "custom/acc", count: 5, first_step: 0, last_step: 4, last_value: 0.9, min: 0.5, max: 0.9 },
    ]);
    expect(keys).toContain("custom/acc");
    expect(keys).toContain("train/loss");
    expect(keys.length).toBeLessThanOrEqual(30);
  });

  it("adapts CanonicalSweep and CanonicalSweepDetail to SweepDetail", () => {
    const sw: CanonicalSweep = {
      id: "swp-1",
      name: "tpe-search",
      status: "running",
      config: {
        method: "tpe",
        metric: { key: "eval/loss", goal: "minimize" },
        parameters: {
          lr: { type: "float", min: 1e-5, max: 1e-3, log: true },
          r: { type: "choice", values: [8, 16] },
        },
        early_stop: { type: "asha", min_iter: 2, eta: 3 },
        max_runs: 10,
      },
      created_at: "2026-11-12T08:00:00Z",
      created_by: "admin",
      run_count: 1,
      best_run_id: "r1",
      best_value: 0.42,
    };
    const detail: CanonicalSweepDetail = {
      sweep: sw,
      runs: [
        { run_id: "r1", status: "finished", params: { lr: 1e-4, r: 16 }, last_value: 0.42, best_value: 0.42, pruned: false, step: 10 },
      ],
      importance: [{ lr: 0.7 }, { r: 0.3 }],
    };

    const m = canonicalSweepToMLOpsSweep(sw);
    expect(m.method).toBe("bayes");
    expect(m.metric_name).toBe("eval/loss");

    const paused = canonicalSweepToMLOpsSweep({ ...sw, status: "paused" });
    expect(paused.status).toBe("paused");

    const sd = canonicalSweepDetailToSweepDetail(detail);
    expect(sd.analysis.parallel_coordinates.axes).toHaveLength(3);
    expect(sd.analysis.parallel_coordinates.trials[0].metric_value).toBe(0.42);
    expect(sd.analysis.importance[0]).toEqual({ name: "lr", importance: 0.7, method: "random_forest" });
  });
});
