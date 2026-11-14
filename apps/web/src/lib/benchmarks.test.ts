import { describe, expect, it } from "vitest";
import {
  calculateRadarPolygon,
  computeBenchmarkKpis,
  extractTaskBreakdown,
  filterBenchmarkRuns,
  formatPercent,
  getRadarAxisPoints,
  getTaskResultFromRun,
  getVariantColor,
  LM_EVAL_TASKS,
  VLM_EVAL_TASKS,
} from "./benchmarks";
import { MOCK_BENCHMARK_RUNS } from "./sota-mocks";

describe("benchmarks helpers", () => {
  it("formats percentages correctly", () => {
    expect(formatPercent(0.684)).toBe("68.4%");
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(1)).toBe("100.0%");
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
  });

  it("cycles variant colors", () => {
    expect(getVariantColor(0)).toBe("#38bdf8");
    expect(getVariantColor(6)).toBe("#38bdf8");
  });

  it("computes accurate KPIs from benchmark runs", () => {
    const kpis = computeBenchmarkKpis(MOCK_BENCHMARK_RUNS);
    expect(kpis.totalRuns).toBe(3);
    expect(kpis.completedRuns).toBe(3);
    expect(kpis.avgMmluPro).toBeCloseTo(0.648, 2);
    expect(kpis.avgMultimodal).toBeGreaterThan(0.5);
    expect(kpis.totalGpus).toBe(8);
  });

  it("filters benchmark runs by search, suite, runner, status", () => {
    const runs = MOCK_BENCHMARK_RUNS;

    const lmOnly = filterBenchmarkRuns(runs, { suite: "lm_eval" });
    expect(lmOnly).toHaveLength(2);

    const vlmOnly = filterBenchmarkRuns(runs, { suite: "vlmevalkit" });
    expect(vlmOnly).toHaveLength(1);

    const searchLlama = filterBenchmarkRuns(runs, { search: "Llama" });
    expect(searchLlama).toHaveLength(1);
    expect(searchLlama[0].target_model).toContain("Llama");

    const statusCompleted = filterBenchmarkRuns(runs, { status: "completed" });
    expect(statusCompleted).toHaveLength(3);

    const runnerVllm = filterBenchmarkRuns(runs, { runner: "vllm" });
    expect(runnerVllm).toHaveLength(3);
  });

  it("extracts task results and breakdown accurately", () => {
    const run = MOCK_BENCHMARK_RUNS[0];
    const tr = getTaskResultFromRun(run, "mmlu_pro");
    expect(tr).not.toBeNull();
    expect(tr?.primary_score).toBe(0.684);

    const breakdown = extractTaskBreakdown(run, "mmlu_pro");
    expect(breakdown.taskName).toBe("mmlu_pro");
    expect(breakdown.logitsAcc).toBe(0.702);
    expect(breakdown.genAcc).toBe(0.684);
    expect(breakdown.parseErrorPct).toBe(1.8);
    expect(breakdown.primaryScore).toBe(0.684);
  });

  it("calculates radar geometry correctly", () => {
    const run = MOCK_BENCHMARK_RUNS[0];
    const axis = getRadarAxisPoints(LM_EVAL_TASKS, 150, 150, 100);
    expect(axis).toHaveLength(6);
    expect(axis[0].x).toBeCloseTo(150, 1);
    expect(axis[0].y).toBeCloseTo(50, 1);

    const { pointsString, points } = calculateRadarPolygon(
      LM_EVAL_TASKS,
      run,
      150,
      150,
      100,
    );
    expect(points).toHaveLength(6);
    expect(pointsString).toBeTruthy();
    expect(points[0].score).toBe(0.684);
  });
});
