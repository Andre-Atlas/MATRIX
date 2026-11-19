// Pure helpers behind the Eval Matrix page (and the mock API's summary computation).
import {
  metricKey,
  type DatasetItem,
  type ExperimentMeta,
  type ExperimentResult,
  type ExperimentSummary,
  type MetricAggregate,
  type MetricSpec,
  type Regression,
} from "./types";

export type MetricCell = {
  /** Mean of non-null values; null when no result has a value for this metric. */
  mean: number | null;
  /** Share of passed among results with a pass/fail verdict; null when none has one. */
  pass_rate: number | null;
  /** Results with a value. */
  n: number;
};

export type VariantPerf = { count: number; errors: number; latency_mean: number | null; cost_total: number };

export type MatrixSummary = {
  variants: string[];
  metrics: string[];
  cells: Record<string, Record<string, MetricCell>>;
  /** metric → variants with the highest mean (ties kept). Empty when there is nothing to compare. */
  best: Record<string, string[]>;
  perf: Record<string, VariantPerf>;
};

const mean = (xs: number[]): number | null => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

/** Variant order: the explicit list first, then any other label in order of first appearance. */
function variantOrder(results: ExperimentResult[], variants?: string[]): string[] {
  const out = [...(variants ?? [])];
  for (const r of results) if (!out.includes(r.variant)) out.push(r.variant);
  return out;
}

export function summarizeMatrix(
  results: ExperimentResult[],
  metrics: (MetricSpec | string)[],
  variants?: string[],
): MatrixSummary {
  const names = metrics.map((m) => (typeof m === "string" ? m : metricKey(m)));
  const order = variantOrder(results, variants);
  const cells: MatrixSummary["cells"] = {};
  const perf: MatrixSummary["perf"] = {};

  for (const v of order) {
    const rs = results.filter((r) => r.variant === v);
    cells[v] = {};
    for (const m of names) {
      const scored = rs.map((r) => r.scores[m]).filter((s) => s !== undefined);
      const values = scored.map((s) => s.value).filter((x): x is number => typeof x === "number");
      const verdicts = scored.map((s) => s.passed).filter((p): p is boolean => typeof p === "boolean");
      cells[v][m] = {
        mean: mean(values),
        pass_rate: verdicts.length ? verdicts.filter(Boolean).length / verdicts.length : null,
        n: values.length,
      };
    }
    const lat = rs.map((r) => r.latency_ms).filter((x): x is number => typeof x === "number");
    perf[v] = {
      count: rs.length,
      errors: rs.filter((r) => r.error).length,
      latency_mean: mean(lat),
      cost_total: rs.reduce((a, r) => a + (r.cost_usd ?? 0), 0),
    };
  }

  const best: MatrixSummary["best"] = {};
  for (const m of names) {
    const scored = order.filter((v) => cells[v][m].mean !== null);
    if (scored.length < 2) {
      best[m] = [];
      continue;
    }
    const top = Math.max(...scored.map((v) => cells[v][m].mean!));
    const winners = scored.filter((v) => Math.abs(cells[v][m].mean! - top) < 1e-9);
    best[m] = winners.length === scored.length ? [] : winners;
  }

  return { variants: order, metrics: names, cells, best, perf };
}

export const SUMMARY_META_KEY = "_meta";

/** The summary's `_meta`, or null when absent (older backends). */
export function summaryMeta(summary: ExperimentSummary | null | undefined): ExperimentMeta | null {
  const m = summary?.[SUMMARY_META_KEY];
  return m && typeof (m as ExperimentMeta).partial === "boolean" ? (m as ExperimentMeta) : null;
}

/** Variant entries of a summary, skipping the reserved `_meta` key. */
export function summaryVariants(summary: ExperimentSummary | null | undefined): [string, Record<string, MetricAggregate>][] {
  return Object.entries(summary ?? {}).filter(([k]) => k !== SUMMARY_META_KEY) as [string, Record<string, MetricAggregate>][];
}

/** True when `_meta.judge` is heuristic, or any cell score reports a heuristic judge in its details. */
export function usesHeuristicJudge(summary: ExperimentSummary | null | undefined, results: ExperimentResult[] = []): boolean {
  if (summaryMeta(summary)?.judge === "heuristic") return true;
  return results.some((r) => Object.values(r.scores).some((s) => s?.details?.judge === "heuristic"));
}

/** Contract shape `Record<variant, Record<metric, {mean, pass_rate}>>`. */
export function toSummaryRecord(s: MatrixSummary): ExperimentSummary {
  const out: ExperimentSummary = {};
  for (const v of s.variants) {
    out[v] = {};
    for (const m of s.metrics) out[v][m] = { mean: s.cells[v][m].mean, pass_rate: s.cells[v][m].pass_rate };
  }
  return out;
}

export const cellKey = (itemId: string, variant: string): string => `${itemId}\u0000${variant}`;

export type RegressionMap = Map<string, Regression[]>;

/** Groups regressions by (item, variant) cell. */
export function regressionMap(regressions: Regression[]): RegressionMap {
  const map: RegressionMap = new Map();
  for (const r of regressions) {
    const k = cellKey(r.item_id, r.variant);
    const list = map.get(k);
    if (list) list.push(r);
    else map.set(k, [r]);
  }
  return map;
}

export type MatrixFilters = {
  onlyFailures?: boolean;
  onlyRegressions?: boolean;
  /** Restricts failure and regression checks to one metric. */
  metric?: string | null;
};

export type MatrixCell = {
  variant: string;
  /** null while the cell has not run yet. */
  result: ExperimentResult | null;
  failed: boolean;
  regressions: Regression[];
};

export type MatrixRow = { item_id: string; item: DatasetItem | null; cells: MatrixCell[] };

export function cellFailed(result: ExperimentResult, metric?: string | null): boolean {
  if (result.error) return true;
  const scores = metric ? [result.scores[metric]] : Object.values(result.scores);
  return scores.some((s) => s?.passed === false);
}

/**
 * One row per dataset item (dataset order), then rows for results whose item is no longer in
 * the dataset. A row is kept when at least one of its cells matches every active filter.
 */
export function matrixRows(
  items: DatasetItem[],
  results: ExperimentResult[],
  variants: string[],
  filters: MatrixFilters = {},
  regressions: RegressionMap = new Map(),
): MatrixRow[] {
  const byCell = new Map(results.map((r) => [cellKey(r.item_id, r.variant), r]));
  const ids = items.map((i) => i.id);
  for (const r of results) if (!ids.includes(r.item_id)) ids.push(r.item_id);
  const itemById = new Map(items.map((i) => [i.id, i]));
  const metric = filters.metric || null;

  const rows: MatrixRow[] = ids.map((id) => ({
    item_id: id,
    item: itemById.get(id) ?? null,
    cells: variants.map((v) => {
      const result = byCell.get(cellKey(id, v)) ?? null;
      const regs = (regressions.get(cellKey(id, v)) ?? []).filter((r) => !metric || r.metric === metric);
      return { variant: v, result, failed: result ? cellFailed(result, metric) : false, regressions: regs };
    }),
  }));

  return rows.filter(
    (row) =>
      (!filters.onlyFailures || row.cells.some((c) => c.failed)) &&
      (!filters.onlyRegressions || row.cells.some((c) => c.regressions.length > 0)),
  );
}

/** Compact one-line-per-key preview of an item input: `question: How do I…`. */
export function inputPreview(input: Record<string, unknown>, max = 160): string {
  const parts = Object.entries(input).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  const s = parts.join("\n");
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}
