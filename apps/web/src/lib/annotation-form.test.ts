import { describe, expect, it } from "vitest";
import {
  buildCompleteRequest,
  buildOnlineEvalRequest,
  buildRuleRequest,
  buildScores,
  describeCondition,
  emptyOnlineEvalForm,
  emptyRuleForm,
  originalOutput,
  scoreReducer,
  toggleAction,
  type ScoreRow,
} from "./annotation-form";
import type { Span } from "./types";

const row = (id: number, name: string, value: string): ScoreRow => ({ id, name, value });

describe("scoreReducer", () => {
  it("adds, edits and removes rows with unique ids", () => {
    let rows = scoreReducer([], { type: "add", name: "helpfulness" });
    rows = scoreReducer(rows, { type: "add" });
    expect(rows.map((r) => r.id)).toEqual([1, 2]);
    rows = scoreReducer(rows, { type: "set", id: 1, field: "value", text: "0.7" });
    expect(rows[0]).toEqual({ id: 1, name: "helpfulness", value: "0.7" });
    rows = scoreReducer(rows, { type: "remove", id: 1 });
    expect(scoreReducer(rows, { type: "add" }).map((r) => r.id)).toEqual([2, 3]);
  });

  it("thumbs upsert a single user_feedback row", () => {
    let rows = scoreReducer([], { type: "feedback", value: 1 });
    expect(rows).toEqual([{ id: 1, name: "user_feedback", value: "1" }]);
    rows = scoreReducer(rows, { type: "feedback", value: 0 });
    expect(rows).toEqual([{ id: 1, name: "user_feedback", value: "0" }]);
  });
});

describe("buildScores", () => {
  it("accepts 0..1 and skips blank rows", () => {
    expect(buildScores([row(1, "a", "0"), row(2, "", ""), row(3, "b", " 0.25 ")])).toEqual({
      scores: [
        { name: "a", value: 0 },
        { name: "b", value: 0.25 },
      ],
      error: null,
    });
  });

  it("rejects out of range, missing parts and duplicates", () => {
    expect(buildScores([row(1, "a", "1.2")]).error).toMatch(/between 0 and 1/);
    expect(buildScores([row(1, "a", "-0.1")]).error).toMatch(/between 0 and 1/);
    expect(buildScores([row(1, "a", "x")]).error).toMatch(/between 0 and 1/);
    expect(buildScores([row(1, "", "0.5")]).error).toMatch(/needs a name/);
    expect(buildScores([row(1, "a", "")]).error).toMatch(/needs a value/);
    expect(buildScores([row(1, "a", "1"), row(2, "a", "0")]).error).toMatch(/twice/);
  });
});

describe("originalOutput / buildCompleteRequest", () => {
  const span = (over: Partial<Span>) => ({ output: null, output_messages: [], ...over }) as unknown as Span;

  it("prefers output, then last non-empty message", () => {
    expect(originalOutput(span({ output: "out" }))).toBe("out");
    expect(
      originalOutput(span({ output_messages: [{ role: "assistant", content: "first" }, { role: "assistant", content: "" }] })),
    ).toBe("first");
    expect(originalOutput(null)).toBe("");
  });

  it("omits corrected_output when untouched or blank", () => {
    const base = { scores: [], original: "a", datasetId: "", comment: "" };
    expect(buildCompleteRequest({ ...base, corrected: "a" })).toEqual({});
    expect(buildCompleteRequest({ ...base, corrected: "  " })).toEqual({});
    expect(buildCompleteRequest({ ...base, corrected: "b", datasetId: "ds1", comment: " hi ", scores: [{ name: "x", value: 1 }] })).toEqual({
      scores: [{ name: "x", value: 1 }],
      corrected_output: "b",
      add_to_dataset_id: "ds1",
      comment: "hi",
    });
  });
});

describe("rule form", () => {
  it("builds each condition kind", () => {
    const f = { ...emptyRuleForm(), name: " r ", scoreName: "faithfulness" };
    expect(buildRuleRequest(f).body).toEqual({
      name: "r",
      enabled: true,
      condition: { score_name: "faithfulness", op: "<", value: 0.5 },
      actions: ["annotate"],
    });
    expect(buildRuleRequest({ ...f, kind: "feedback" }).body?.condition).toEqual({ feedback: "negative" });
    expect(buildRuleRequest({ ...f, kind: "latency", minLatencyMs: "2500" }).body?.condition).toEqual({ min_latency_ms: 2500 });
    expect(buildRuleRequest({ ...f, kind: "error" }).body?.condition).toEqual({ status: "error" });
  });

  it("validates inputs", () => {
    const f = emptyRuleForm();
    expect(buildRuleRequest(f).error).toMatch(/name/);
    expect(buildRuleRequest({ ...f, name: "x" }).error).toMatch(/Score name/);
    expect(buildRuleRequest({ ...f, name: "x", kind: "error", actions: [] }).error).toMatch(/action/);
    expect(buildRuleRequest({ ...f, name: "x", kind: "error", actions: ["add_to_dataset"] }).error).toMatch(/dataset/);
    expect(buildRuleRequest({ ...f, name: "x", kind: "latency", minLatencyMs: "-1" }).error).toMatch(/Latency/);
    const ok = buildRuleRequest({ ...f, name: "x", kind: "error", actions: ["add_to_dataset", "alert"], datasetId: "d" });
    expect(ok.body?.dataset_id).toBe("d");
  });

  it("toggles actions and describes conditions", () => {
    expect(toggleAction(["annotate"], "alert")).toEqual(["annotate", "alert"]);
    expect(toggleAction(["annotate", "alert"], "annotate")).toEqual(["alert"]);
    expect(describeCondition({ score_name: "f", op: "<", value: 0.5 })).toBe("score f < 0.5");
    expect(describeCondition({ status: "error" })).toBe("status = error");
  });
});

describe("online eval form", () => {
  it("converts percent to rate and drops empty filter/model", () => {
    const f = { ...emptyOnlineEvalForm(), name: "n", samplingPct: 25 };
    expect(buildOnlineEvalRequest(f).body).toEqual({ name: "n", metric: "faithfulness", sampling_rate: 0.25 });
    const g = buildOnlineEvalRequest({ ...f, kind: "LLM", spanName: "answer", model: "gpt-4o-mini", params: '{"criteria":"x"}' });
    expect(g.body).toEqual({
      name: "n",
      metric: "faithfulness",
      params: { criteria: "x" },
      sampling_rate: 0.25,
      filter: { kind: "LLM", name: "answer" },
      model: { provider: "openai", name: "gpt-4o-mini" },
    });
  });

  it("rejects bad params and sampling", () => {
    const f = { ...emptyOnlineEvalForm(), name: "n" };
    expect(buildOnlineEvalRequest({ ...f, name: "" }).error).toMatch(/name/);
    expect(buildOnlineEvalRequest({ ...f, params: "{" }).error).toMatch(/JSON/);
    expect(buildOnlineEvalRequest({ ...f, params: "[]" }).error).toMatch(/object/);
    expect(buildOnlineEvalRequest({ ...f, samplingPct: 101 }).error).toMatch(/0–100/);
  });
});
