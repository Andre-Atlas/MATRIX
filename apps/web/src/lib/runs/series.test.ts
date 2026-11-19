import { describe, expect, it } from "vitest";
import { axisBounds, capPoints, ema, lastFinite, logSafe, m4, relativeSeconds, type M4Point } from "./series";

describe("ema", () => {
  it("weight 0 returns the input with non-finite values as gaps", () => {
    expect(ema([1, NaN, 3, Infinity, null], 0)).toEqual([1, null, 3, null, null]);
  });
  it("is debiased: a constant series stays constant", () => {
    const out = ema([5, 5, 5, 5], 0.9);
    for (const v of out) expect(v).toBeCloseTo(5, 10);
  });
  it("smooths towards the recent values and keeps gaps without resetting", () => {
    const out = ema([0, 10, null, 10], 0.5);
    expect(out[0]).toBe(0);
    expect(out[1]).toBeCloseTo(10 * 0.5 / 0.75, 10); // (0*.5 + .5*10) / (1 - .25)
    expect(out[2]).toBeNull();
    expect(out[3]).toBeGreaterThan(out[1] as number);
    expect(out[3]).toBeLessThan(10);
  });
  it("clamps the weight", () => {
    expect(ema([1, 2], -1)).toEqual([1, 2]);
    expect(ema([1, 2], 5).every((v) => v !== null && Number.isFinite(v))).toBe(true);
  });
});

describe("m4", () => {
  const series = (n: number, f: (i: number) => number): M4Point[] =>
    Array.from({ length: n }, (_, i) => ({ step: i, value: f(i), ts: 1_000_000 + i * 10 }));

  it("returns short series unchanged", () => {
    const s = series(10, (i) => i);
    expect(m4(s, 1000)).toEqual({ points: s, downsampled: false });
  });
  it("keeps first, last, min and max of each bucket and never exceeds max_points", () => {
    const s = series(20_000, (i) => Math.sin(i / 50) + (i === 12_345 ? 100 : 0));
    const { points, downsampled } = m4(s, 1000);
    expect(downsampled).toBe(true);
    expect(points.length).toBeLessThanOrEqual(1000);
    expect(points[0].step).toBe(0);
    expect(points.at(-1)!.step).toBe(19_999);
    expect(points.some((p) => p.step === 12_345)).toBe(true); // the spike survives
    for (let i = 1; i < points.length; i++) expect(points[i].step).toBeGreaterThan(points[i - 1].step);
  });
  it("ignores non-finite values for min/max but keeps them as first/last", () => {
    const s = series(400, (i) => (i === 0 ? NaN : i));
    const { points } = m4(s, 8);
    expect(Number.isNaN(points[0].value)).toBe(true);
    expect(points.length).toBeLessThanOrEqual(8);
  });
  it("buckets by time when asked", () => {
    const s = series(5000, (i) => i % 7);
    const { points } = m4(s, 400, "time");
    expect(points.length).toBeLessThanOrEqual(400);
    for (let i = 1; i < points.length; i++) expect(points[i].ts).toBeGreaterThanOrEqual(points[i - 1].ts);
  });
  it("caps max_points at 5000", () => {
    const { points } = m4(series(30_000, (i) => i), 100_000);
    expect(points.length).toBeLessThanOrEqual(5000);
  });
});

describe("axis helpers", () => {
  it("pads linear bounds and handles flat series", () => {
    expect(axisBounds([[0, 10]])).toEqual({ min: -0.5, max: 10.5 });
    expect(axisBounds([[2, 2, null]])).toEqual({ min: 1.8, max: 2.2 });
    expect(axisBounds([[null, NaN]])).toBeNull();
  });
  it("aligns log bounds to decades and skips non-positive values", () => {
    expect(axisBounds([[0, -1, 0.03, 250]], true)).toEqual({ min: 0.01, max: 1000 });
    expect(axisBounds([[1, 1]], true)).toEqual({ min: 1, max: 10 });
  });
  it("logSafe turns non-positive values into gaps", () => {
    expect(logSafe([1, 0, -2, null, NaN, 0.5])).toEqual([1, null, null, null, null, 0.5]);
  });
  it("capPoints strides and keeps the last point", () => {
    const xs = Array.from({ length: 12_000 }, (_, i) => i);
    const out = capPoints(xs, 5000);
    expect(out).toHaveLength(5000);
    expect(out[0]).toBe(0);
    expect(out.at(-1)).toBe(11_999);
    expect(capPoints([1, 2, 3])).toEqual([1, 2, 3]);
  });
  it("relativeSeconds and lastFinite", () => {
    expect(relativeSeconds(["2026-01-01T00:00:10Z", "2026-01-01T00:01:10Z"])).toEqual([0, 60]);
    expect(lastFinite([1, 2, null, NaN])).toBe(2);
    expect(lastFinite([null])).toBeNull();
  });
});
