import { describe, expect, it } from "vitest";
import { cellKey, inputPreview, matrixRows, regressionMap, summarizeMatrix, toSummaryRecord } from "./eval-matrix";
import type { DatasetItem, ExperimentResult, MetricScore, Regression } from "./types";

const s = (value: number | null, passed: boolean | null = value === null ? null : value >= 0.7): MetricScore => ({
  value,
  passed,
  reason: null,
});

function result(item: string, variant: string, scores: Record<string, MetricScore>, extra: Partial<ExperimentResult> = {}): ExperimentResult {
  return {
    item_id: item,
    variant,
    output: "ok",
    latency_ms: 1000,
    cost_usd: 0.001,
    input_tokens: 10,
    output_tokens: 5,
    error: null,
    scores,
    ...extra,
  };
}

function item(id: string): DatasetItem {
  return {
    id,
    dataset_id: "d",
    input: { question: `q-${id}` },
    expected_output: null,
    context: [],
    metadata: {},
    source_trace_id: null,
    source_span_id: null,
    created_at: "2026-11-09T00:00:00Z",
  };
}

describe("summarizeMatrix", () => {
  const results = [
    result("i1", "A", { f: s(1), lat: s(1, true) }),
    result("i2", "A", { f: s(0.5), lat: s(0, false) }, { latency_ms: 3000, cost_usd: 0.002 }),
    result("i1", "B", { f: s(0.9), lat: s(1, true) }),
    result("i2", "B", { f: s(null), lat: s(1, true) }, { error: "timeout", latency_ms: null }),
  ];

  it("computes means and pass rates ignoring n/a", () => {
    const m = summarizeMatrix(results, [{ type: "faithfulness", name: "f" }, "lat"]);
    expect(m.variants).toEqual(["A", "B"]);
    expect(m.cells.A.f.mean).toBeCloseTo(0.75);
    expect(m.cells.A.f.pass_rate).toBe(0.5);
    expect(m.cells.B.f).toEqual({ mean: 0.9, pass_rate: 1, n: 1 });
    expect(m.cells.B.lat.pass_rate).toBe(1);
  });

  it("picks the best variant per metric and skips columns with nothing to compare", () => {
    const m = summarizeMatrix(results, ["f", "lat", "missing"]);
    expect(m.best.f).toEqual(["B"]);
    expect(m.best.lat).toEqual(["B"]);
    expect(m.best.missing).toEqual([]);
    expect(m.cells.A.missing).toEqual({ mean: null, pass_rate: null, n: 0 });
  });

  it("does not highlight a column where every variant ties", () => {
    const tie = [result("i1", "A", { x: s(1) }), result("i1", "B", { x: s(1) }), result("i1", "C", { x: s(0.2) })];
    expect(summarizeMatrix(tie, ["x"]).best.x).toEqual(["A", "B"]);
    expect(summarizeMatrix(tie.slice(0, 2), ["x"]).best.x).toEqual([]);
  });

  it("keeps the explicit variant order, includes variants without results, and reports perf", () => {
    const m = summarizeMatrix(results, ["f"], ["B", "Z"]);
    expect(m.variants).toEqual(["B", "Z", "A"]);
    expect(m.cells.Z.f.mean).toBeNull();
    expect(m.perf.A).toEqual({ count: 2, errors: 0, latency_mean: 2000, cost_total: 0.003 });
    expect(m.perf.B.errors).toBe(1);
    expect(m.perf.B.latency_mean).toBe(1000);
  });

  it("converts to the contract summary record", () => {
    const rec = toSummaryRecord(summarizeMatrix(results, ["f"]));
    expect(rec).toEqual({ A: { f: { mean: 0.75, pass_rate: 0.5 } }, B: { f: { mean: 0.9, pass_rate: 1 } } });
  });
});

describe("regressionMap", () => {
  it("groups regressions per cell", () => {
    const regs: Regression[] = [
      { item_id: "i1", variant: "A", metric: "f", baseline: 1, current: 0.5 },
      { item_id: "i1", variant: "A", metric: "lat", baseline: 1, current: 0 },
      { item_id: "i2", variant: "A", metric: "f", baseline: 0.9, current: 0.4 },
    ];
    const map = regressionMap(regs);
    expect(map.size).toBe(2);
    expect(map.get(cellKey("i1", "A"))!.map((r) => r.metric)).toEqual(["f", "lat"]);
    expect(map.get(cellKey("i1", "B"))).toBeUndefined();
  });
});

describe("matrixRows", () => {
  const items = [item("i1"), item("i2"), item("i3")];
  const results = [
    result("i1", "A", { f: s(1), g: s(0.9) }),
    result("i1", "B", { f: s(1), g: s(0.2) }),
    result("i2", "A", { f: s(0.3), g: s(0.9) }),
    result("i2", "B", { f: s(0.9), g: s(0.9) }),
    result("gone", "A", { f: s(1) }),
  ];
  const regs = regressionMap([{ item_id: "i2", variant: "A", metric: "f", baseline: 1, current: 0.3 }]);

  it("lays out items × variants, keeping pending cells and orphaned results", () => {
    const rows = matrixRows(items, results, ["A", "B"]);
    expect(rows.map((r) => r.item_id)).toEqual(["i1", "i2", "i3", "gone"]);
    expect(rows[2].cells.map((c) => c.result)).toEqual([null, null]);
    expect(rows[3].item).toBeNull();
    expect(rows[1].cells[0].failed).toBe(true);
    expect(rows[1].cells[1].failed).toBe(false);
  });

  it("filters to rows with a failing cell", () => {
    expect(matrixRows(items, results, ["A", "B"], { onlyFailures: true }).map((r) => r.item_id)).toEqual(["i1", "i2"]);
  });

  it("restricts failures and regressions to the selected metric", () => {
    expect(matrixRows(items, results, ["A", "B"], { onlyFailures: true, metric: "g" }).map((r) => r.item_id)).toEqual(["i1"]);
    expect(matrixRows(items, results, ["A", "B"], { onlyRegressions: true, metric: "g" }, regs)).toEqual([]);
  });

  it("filters to rows with a regression and attaches them to cells", () => {
    const rows = matrixRows(items, results, ["A", "B"], { onlyRegressions: true }, regs);
    expect(rows.map((r) => r.item_id)).toEqual(["i2"]);
    expect(rows[0].cells[0].regressions).toHaveLength(1);
    expect(rows[0].cells[1].regressions).toHaveLength(0);
  });

  it("treats an errored result as failed", () => {
    const rows = matrixRows([item("x")], [result("x", "A", {}, { error: "boom", output: null })], ["A"], { onlyFailures: true });
    expect(rows).toHaveLength(1);
  });
});

describe("inputPreview", () => {
  it("renders keys compactly and truncates", () => {
    expect(inputPreview({ question: "hi", k: 3 })).toBe("question: hi\nk: 3");
    expect(inputPreview({ q: "x".repeat(50) }, 10)).toHaveLength(10);
  });
});
