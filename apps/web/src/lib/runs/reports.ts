// Preparation of typed report data for charts: confusion matrix normalization and ROC/PR curves.

/** Row-normalized confusion matrix (each true-class row sums to 1). Empty rows stay 0. */
export function normalizeRows(matrix: readonly (readonly number[])[]): number[][] {
  return matrix.map((row) => {
    const total = row.reduce((a, v) => a + (Number.isFinite(v) ? v : 0), 0);
    return row.map((v) => (total > 0 && Number.isFinite(v) ? v / total : 0));
  });
}

/** Heatmap cells `[col, row, value]` with row 0 at the top (true labels read top-down). */
export function confusionCells(matrix: readonly (readonly number[])[], normalized: boolean): [number, number, number][] {
  const m = normalized ? normalizeRows(matrix) : matrix.map((r) => [...r]);
  const n = m.length;
  const cells: [number, number, number][] = [];
  m.forEach((row, i) => row.forEach((v, j) => cells.push([j, n - 1 - i, v])));
  return cells;
}

export type CurvePoint = [number, number];

/**
 * ROC curve ready to draw: points clamped to [0, 1], sorted by FPR (then TPR) and anchored at (0,0)
 * and (1,1) so every class spans the full square.
 */
export function prepareRoc(fpr: readonly number[], tpr: readonly number[]): CurvePoint[] {
  const pts: CurvePoint[] = [];
  const n = Math.min(fpr.length, tpr.length);
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(fpr[i]) || !Number.isFinite(tpr[i])) continue;
    pts.push([clamp01(fpr[i]), clamp01(tpr[i])]);
  }
  pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (!pts.length || pts[0][0] !== 0 || pts[0][1] !== 0) pts.unshift([0, 0]);
  const last = pts[pts.length - 1];
  if (last[0] !== 1 || last[1] !== 1) pts.push([1, 1]);
  return pts;
}

/**
 * PR curve ready to draw as `[recall, precision]`, sorted by recall ascending (precision descending
 * on ties, the step shape scikit-learn produces).
 */
export function preparePr(precision: readonly number[], recall: readonly number[]): CurvePoint[] {
  const pts: CurvePoint[] = [];
  const n = Math.min(precision.length, recall.length);
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(precision[i]) || !Number.isFinite(recall[i])) continue;
    pts.push([clamp01(recall[i]), clamp01(precision[i])]);
  }
  return pts.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
}

/** Positive-class prevalence of a class from the confusion matrix: the PR baseline. */
export function prevalence(matrix: readonly (readonly number[])[], classIndex: number): number | null {
  const total = matrix.reduce((a, r) => a + r.reduce((b, v) => b + v, 0), 0);
  const row = matrix[classIndex];
  if (!row || total <= 0) return null;
  return row.reduce((a, v) => a + v, 0) / total;
}

/** Legend text of a curve: `label (AUC 0.953)`. */
export function curveLegend(label: string, metric: "AUC" | "AP", value: number): string {
  return `${label} (${metric} ${Number.isFinite(value) ? value.toFixed(3) : "n/a"})`;
}

/** Points of a regression scatter and the y = x reference spanning both axes. */
export function regressionScatter(yTrue: readonly number[], yPred: readonly number[]): { points: CurvePoint[]; diagonal: [CurvePoint, CurvePoint] | null } {
  const points: CurvePoint[] = [];
  const n = Math.min(yTrue.length, yPred.length);
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < n; i++) {
    const t = yTrue[i];
    const p = yPred[i];
    if (!Number.isFinite(t) || !Number.isFinite(p)) continue;
    points.push([t, p]);
    lo = Math.min(lo, t, p);
    hi = Math.max(hi, t, p);
  }
  return { points, diagonal: lo === Infinity ? null : [[lo, lo], [hi, hi]] };
}

/** Histogram bars from `edges` (n+1) and `counts` (n): bar centers, widths and labels. */
export function histogramBars(edges: readonly number[], counts: readonly number[]): { center: number; width: number; count: number; label: string }[] {
  const n = Math.min(counts.length, edges.length - 1);
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = edges[i];
    const b = edges[i + 1];
    out.push({ center: (a + b) / 2, width: b - a, count: counts[i], label: `${fmt(a)} – ${fmt(b)}` });
  }
  return out;
}

const fmt = (v: number) => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(1) : Number(v.toFixed(2)).toString());
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
