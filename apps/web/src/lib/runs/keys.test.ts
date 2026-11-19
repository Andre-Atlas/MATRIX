import { describe, expect, it } from "vitest";
import { bestIndex, groupKeysByPrefix, keyPrefix, lowerIsBetter, systemKeys } from "./keys";

describe("groupKeysByPrefix", () => {
  it("groups by the first segment, train/eval first, unprefixed last, sys/ left out", () => {
    const groups = groupKeysByPrefix([
      "rewards/margins",
      "eval/loss",
      "train/loss",
      "perplexity",
      "train/learning_rate",
      "sys/cpu.percent",
      "rewards/chosen",
      "train/loss",
    ]);
    expect(groups).toEqual([
      { prefix: "train", keys: ["train/learning_rate", "train/loss"] },
      { prefix: "eval", keys: ["eval/loss"] },
      { prefix: "rewards", keys: ["rewards/chosen", "rewards/margins"] },
      { prefix: "", keys: ["perplexity"] },
    ]);
  });
  it("orders numbered keys naturally and can include system keys", () => {
    const g = groupKeysByPrefix(["layer/10", "layer/2", "sys/gpu.0.power_w"], true);
    expect(g.find((x) => x.prefix === "layer")!.keys).toEqual(["layer/2", "layer/10"]);
    expect(g.some((x) => x.prefix === "sys")).toBe(true);
    expect(keyPrefix("/x")).toBe("");
  });
});

describe("systemKeys", () => {
  it("sorts telemetry into panels and GPUs by numeric index", () => {
    const s = systemKeys([
      "sys/cpu.percent",
      "sys/memory.used_mb",
      "sys/disk.read_mb",
      "sys/disk.write_mb",
      "sys/net.sent_mb",
      "sys/net.recv_mb",
      "sys/proc.rss_mb",
      "sys/gpu.10.utilization",
      "sys/gpu.2.power_w",
      "sys/gpu.2.temperature_c",
      "sys/gpu.2.fan",
      "train/loss",
    ]);
    expect(s.cpu).toEqual(["sys/cpu.percent"]);
    expect(s.disk).toHaveLength(2);
    expect(s.network).toHaveLength(2);
    expect(s.process).toEqual(["sys/proc.rss_mb"]);
    expect(s.gpus.map((g) => g.index)).toEqual([2, 10]);
    expect(s.gpus[0].keys).toEqual({ power_w: "sys/gpu.2.power_w", temperature_c: "sys/gpu.2.temperature_c" });
    expect(s.other).toEqual(["sys/gpu.2.fan"]);
  });
});

describe("best value", () => {
  it("knows the direction from the key name", () => {
    for (const k of ["train/loss", "eval_loss", "perplexity", "eval/rmse", "fit_time", "wer"]) expect(lowerIsBetter(k)).toBe(true);
    for (const k of ["eval/accuracy", "f1_macro", "rewards/margins", "auc"]) expect(lowerIsBetter(k)).toBe(false);
  });
  it("returns the index of the best finite value", () => {
    expect(bestIndex("eval/loss", [0.5, 0.2, null, 0.3])).toBe(1);
    expect(bestIndex("eval/acc", [0.5, NaN, 0.9, 0.9])).toBe(2);
    expect(bestIndex("x", [null, undefined])).toBeNull();
  });
});
