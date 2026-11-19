import { afterEach, describe, expect, it, vi } from "vitest";
import {
  defaultMetricColumns,
  defaultParamColumns,
  diffParams,
  flattenParams,
  paramText,
  readStoredColumns,
  writeStoredColumns,
} from "./params";

describe("flattenParams", () => {
  it("dots nested objects and keeps arrays as leaves", () => {
    expect(flattenParams({ lr: 1e-3, optim: { name: "adamw", betas: [0.9, 0.99] }, empty: {} })).toEqual({
      lr: 1e-3,
      "optim.name": "adamw",
      "optim.betas": [0.9, 0.99],
      empty: {},
    });
  });
  it("paramText", () => {
    expect(paramText("x")).toBe("x");
    expect(paramText([1, 2])).toBe("[1,2]");
    expect(paramText(undefined)).toBe("—");
  });
});

describe("diffParams", () => {
  const runs = [
    { lr: 1e-3, bs: 32, optim: { name: "adamw" } },
    { lr: 3e-4, bs: 32, optim: { name: "adamw" }, warmup: 100 },
    { lr: 1e-3, bs: 32, optim: { name: "sgd" } },
  ];
  it("keeps only differing keys (missing counts as different)", () => {
    const rows = diffParams(runs);
    expect(rows.map((r) => r.key)).toEqual(["lr", "optim.name", "warmup"]);
    expect(rows.find((r) => r.key === "warmup")!.values).toEqual([undefined, 100, undefined]);
  });
  it("returns every key with the flag when asked", () => {
    const rows = diffParams(runs, false);
    expect(rows.find((r) => r.key === "bs")).toEqual({ key: "bs", values: [32, 32, 32], differs: false });
  });
});

describe("default columns", () => {
  it("picks the params that vary most", () => {
    const cols = defaultParamColumns(
      [
        { lr: 1, seed: 1, model: "a", nested: { x: 1 } },
        { lr: 2, seed: 1, model: "a", nested: { x: 2 } },
        { lr: 3, seed: 1, model: "b", nested: { x: 2 } },
      ],
      2,
    );
    expect(cols).toEqual(["lr", "model"]);
  });
  it("picks the most common metrics, loss/acc first on ties", () => {
    expect(defaultMetricColumns([{ "train/loss": 1, zeta: 2 }, { "train/loss": 1, zeta: 1, "eval/acc": 1 }], 2)).toEqual(["train/loss", "zeta"]);
  });
});

describe("stored columns", () => {
  afterEach(() => vi.restoreAllMocks());
  it("round-trips and ignores junk", () => {
    writeStoredColumns("k", { params: ["lr"], metrics: ["loss"] });
    expect(readStoredColumns("k")).toEqual({ params: ["lr"], metrics: ["loss"] });
    window.localStorage.setItem("k2", "{not json");
    expect(readStoredColumns("k2")).toBeNull();
    window.localStorage.setItem("k3", JSON.stringify({ params: [1, "a"], metrics: "x" }));
    expect(readStoredColumns("k3")).toEqual({ params: ["a"], metrics: [] });
  });
  it("never throws when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(readStoredColumns("k")).toBeNull();
    expect(() => writeStoredColumns("k", { params: [], metrics: [] })).not.toThrow();
  });
});
