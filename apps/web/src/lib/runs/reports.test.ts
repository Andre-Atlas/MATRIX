import { describe, expect, it } from "vitest";
import { confusionCells, curveLegend, histogramBars, normalizeRows, preparePr, prepareRoc, prevalence, regressionScatter } from "./reports";

describe("confusion matrix", () => {
  const cm = [
    [8, 2, 0],
    [1, 6, 3],
    [0, 0, 0],
  ];
  it("normalizes each row to 1 and leaves empty rows at 0", () => {
    const n = normalizeRows(cm);
    expect(n[0]).toEqual([0.8, 0.2, 0]);
    expect(n[1][2]).toBeCloseTo(0.3);
    expect(n[2]).toEqual([0, 0, 0]);
  });
  it("cells put the first true label at the top row", () => {
    const cells = confusionCells(cm, false);
    expect(cells).toHaveLength(9);
    expect(cells[0]).toEqual([0, 2, 8]);
    expect(confusionCells(cm, true)[1]).toEqual([1, 2, 0.2]);
  });
  it("prevalence of a class", () => {
    expect(prevalence(cm, 0)).toBe(0.5);
    expect(prevalence([], 0)).toBeNull();
  });
});

describe("curves", () => {
  it("ROC is sorted, clamped and anchored at both corners", () => {
    expect(prepareRoc([0.5, 0.1, 1.2], [0.9, 0.6, 1])).toEqual([
      [0, 0],
      [0.1, 0.6],
      [0.5, 0.9],
      [1, 1],
    ]);
    expect(prepareRoc([], [])).toEqual([
      [0, 0],
      [1, 1],
    ]);
  });
  it("PR becomes [recall, precision] sorted by recall", () => {
    expect(preparePr([1, 0.8, 0.5], [0.1, 0.5, 1])).toEqual([
      [0.1, 1],
      [0.5, 0.8],
      [1, 0.5],
    ]);
  });
  it("legend shows the metric with 3 decimals", () => {
    expect(curveLegend("cat", "AUC", 0.95312)).toBe("cat (AUC 0.953)");
    expect(curveLegend("cat", "AP", NaN)).toBe("cat (AP n/a)");
  });
});

describe("regression", () => {
  it("scatter skips non-finite pairs and spans the diagonal", () => {
    const { points, diagonal } = regressionScatter([1, 2, NaN, 5], [1.5, 1, 3, 4]);
    expect(points).toEqual([
      [1, 1.5],
      [2, 1],
      [5, 4],
    ]);
    expect(diagonal).toEqual([
      [1, 1],
      [5, 5],
    ]);
    expect(regressionScatter([], []).diagonal).toBeNull();
  });
  it("histogram bars from edges and counts", () => {
    const bars = histogramBars([-1, 0, 1], [3, 5]);
    expect(bars).toEqual([
      { center: -0.5, width: 1, count: 3, label: "-1 – 0" },
      { center: 0.5, width: 1, count: 5, label: "0 – 1" },
    ]);
  });
});
