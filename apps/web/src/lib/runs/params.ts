// Run parameter helpers: flattening, key-param choice and the compare diff.

/** Nested objects become dotted keys (`optimizer.lr`); arrays and scalars are leaves. */
export function flattenParams(params: Record<string, unknown>, prefix = "", out: Record<string, unknown> = {}): Record<string, unknown> {
  for (const [k, v] of Object.entries(params)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length) {
      flattenParams(v as Record<string, unknown>, key, out);
    } else out[key] = v;
  }
  return out;
}

/** Display text of a param value (strings unquoted, the rest as JSON). */
export function paramText(v: unknown): string {
  if (v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export type ParamDiffRow = { key: string; values: unknown[]; differs: boolean };

/**
 * Rows of the compare param table: one per flattened key, `differs` when at least two runs disagree
 * (a key missing in some runs counts as a difference). `onlyDiff` keeps the differing rows.
 */
export function diffParams(runs: readonly Record<string, unknown>[], onlyDiff = true): ParamDiffRow[] {
  const flat = runs.map((p) => flattenParams(p));
  const keys = [...new Set(flat.flatMap((f) => Object.keys(f)))].sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  const rows = keys.map((key) => {
    const values = flat.map((f) => (key in f ? f[key] : undefined));
    const differs = values.some((v) => !same(v, values[0]));
    return { key, values, differs };
  });
  return onlyDiff ? rows.filter((r) => r.differs) : rows;
}

/**
 * Default "key params" for the runs table: the keys that vary most across the listed runs (most
 * distinct values), then the most common ones, scalars only. Up to `max`.
 */
export function defaultParamColumns(runs: readonly Record<string, unknown>[], max = 3): string[] {
  const flat = runs.map((p) => flattenParams(p));
  const stats = new Map<string, { count: number; distinct: Set<string> }>();
  for (const f of flat) {
    for (const [k, v] of Object.entries(f)) {
      if (v !== null && typeof v === "object") continue;
      const s = stats.get(k) ?? { count: 0, distinct: new Set<string>() };
      s.count += 1;
      s.distinct.add(JSON.stringify(v));
      stats.set(k, s);
    }
  }
  return [...stats.entries()]
    .sort(([ka, a], [kb, b]) => b.distinct.size - a.distinct.size || b.count - a.count || ka.localeCompare(kb))
    .slice(0, max)
    .map(([k]) => k);
}

/** Default metric columns: the most common summary keys, `loss`/`acc`-like first. */
export function defaultMetricColumns(summaries: readonly Record<string, number | null>[], max = 3): string[] {
  const count = new Map<string, number>();
  for (const s of summaries) for (const k of Object.keys(s)) count.set(k, (count.get(k) ?? 0) + 1);
  const score = (k: string) => (/(^|\/)(loss|acc|accuracy|f1|auc|rmse|r2)$/.test(k) ? 1 : 0) + (/^eval\//.test(k) ? 0.5 : 0);
  return [...count.entries()]
    .sort(([ka, a], [kb, b]) => b - a || score(kb) - score(ka) || ka.localeCompare(kb))
    .slice(0, max)
    .map(([k]) => k);
}

/** Column choice persisted in localStorage: read/write never throw (private mode, quota). */
export function readStoredColumns(storageKey: string): { params: string[]; metrics: string[] } | null {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    const v = JSON.parse(raw) as unknown;
    if (!v || typeof v !== "object") return null;
    const o = v as { params?: unknown; metrics?: unknown };
    const strs = (x: unknown) => (Array.isArray(x) ? x.filter((s): s is string => typeof s === "string").slice(0, 30) : []);
    return { params: strs(o.params), metrics: strs(o.metrics) };
  } catch {
    return null;
  }
}

export function writeStoredColumns(storageKey: string, value: { params: string[]; metrics: string[] }): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}
