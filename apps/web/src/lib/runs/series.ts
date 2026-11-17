// Pure helpers for metric series: EMA smoothing, M4 downsampling, axis bounds and point caps.
import { MAX_POINTS_PER_SERIES } from "./types";

/**
 * Exponential moving average with TensorBoard-style debiasing (the first points are not pulled
 * towards 0). `weight` is in [0, 1): 0 returns the input. Gaps (`null`, NaN, ±Inf) stay gaps and do
 * not reset the average, so a single bad step does not break the smoothed curve.
 */
export function ema(values: readonly (number | null)[], weight: number): (number | null)[] {
  const w = Math.min(0.999, Math.max(0, Number.isFinite(weight) ? weight : 0));
  if (w === 0) return values.map((v) => (v !== null && Number.isFinite(v) ? v : null));
  let last = 0;
  let n = 0;
  return values.map((v) => {
    if (v === null || !Number.isFinite(v)) return null;
    last = last * w + (1 - w) * v;
    n += 1;
    return last / (1 - Math.pow(w, n));
  });
}

/** A point in M4 terms: x is the bucketing axis (step or time), `i` the step used for ties. */
export type M4Point = { step: number; value: number; ts: number };

/**
 * M4 downsampling (first, last, min, max per bucket), same rules as the server's Python fallback:
 * `max_points / 4` equal-width buckets over the axis; min/max use finite values only; the union is
 * ordered by axis then step. Series at or under `maxPoints` come back unchanged.
 */
export function m4(points: readonly M4Point[], maxPoints: number, axis: "step" | "time" = "step"): { points: M4Point[]; downsampled: boolean } {
  const cap = Math.max(4, Math.min(Math.floor(maxPoints), MAX_POINTS_PER_SERIES));
  if (points.length <= cap) return { points: [...points], downsampled: false };
  const ax = (p: M4Point) => (axis === "step" ? p.step : p.ts);
  const nb = Math.floor(cap / 4);
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of points) {
    lo = Math.min(lo, ax(p));
    hi = Math.max(hi, ax(p));
  }
  const width = hi - lo + 1;
  type B = { first: M4Point; last: M4Point; low: M4Point | null; high: M4Point | null };
  const buckets = new Map<number, B>();
  const before = (a: M4Point, b: M4Point) => ax(a) < ax(b) || (ax(a) === ax(b) && a.step < b.step);
  for (const p of points) {
    const k = Math.floor(((ax(p) - lo) * nb) / width);
    const b = buckets.get(k);
    const finite = Number.isFinite(p.value);
    if (!b) {
      buckets.set(k, { first: p, last: p, low: finite ? p : null, high: finite ? p : null });
      continue;
    }
    if (before(p, b.first)) b.first = p;
    if (before(b.last, p)) b.last = p;
    if (finite) {
      if (!b.low || p.value < b.low.value || (p.value === b.low.value && p.step < b.low.step)) b.low = p;
      if (!b.high || p.value > b.high.value || (p.value === b.high.value && p.step < b.high.step)) b.high = p;
    }
  }
  const byStep = new Map<number, M4Point>();
  for (const b of buckets.values()) for (const p of [b.first, b.last, b.low, b.high]) if (p) byStep.set(p.step, p);
  const out = [...byStep.values()].sort((a, b) => ax(a) - ax(b) || a.step - b.step);
  return { points: out, downsampled: true };
}

/**
 * Keeps at most `max` points by even striding (the last point is always kept). The API already
 * returns M4 series; this only guards the chart against a misbehaving backend.
 */
export function capPoints<T>(xs: readonly T[], max = MAX_POINTS_PER_SERIES): T[] {
  if (xs.length <= max) return [...xs];
  const out: T[] = [];
  const stride = (xs.length - 1) / (max - 1);
  for (let i = 0; i < max; i++) out.push(xs[Math.round(i * stride)]);
  return out;
}

/** Values usable on a log axis: non-positive and non-finite become gaps. */
export function logSafe(values: readonly (number | null)[]): (number | null)[] {
  return values.map((v) => (v !== null && Number.isFinite(v) && v > 0 ? v : null));
}

export type AxisBounds = { min: number; max: number };

/**
 * Y bounds for a set of series with 5% padding (linear) or a decade-aligned range (log). Returns
 * null when there is no usable value. A flat series gets a symmetric band so the line is visible.
 */
export function axisBounds(series: readonly (readonly (number | null)[])[], log = false): AxisBounds | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of series) {
    for (const v of s) {
      if (v === null || !Number.isFinite(v) || (log && v <= 0)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (lo === Infinity) return null;
  if (log) {
    const min = Math.pow(10, Math.floor(Math.log10(lo)));
    const max = Math.pow(10, Math.ceil(Math.log10(hi)));
    return { min, max: max > min ? max : min * 10 };
  }
  if (lo === hi) {
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1;
    return { min: lo - pad, max: hi + pad };
  }
  const pad = (hi - lo) * 0.05;
  return { min: lo - pad, max: hi + pad };
}

/** Seconds since the first timestamp: the "time" x axis is relative wall time, so runs line up. */
export function relativeSeconds(ts: readonly string[]): number[] {
  if (!ts.length) return [];
  const t0 = Date.parse(ts[0]);
  let first = t0;
  for (const t of ts) {
    const v = Date.parse(t);
    if (v < first) first = v;
  }
  return ts.map((t) => (Date.parse(t) - first) / 1000);
}

/** Last finite value of a series (what a "latest" readout shows). */
export function lastFinite(values: readonly (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i];
    if (v !== null && Number.isFinite(v)) return v;
  }
  return null;
}
