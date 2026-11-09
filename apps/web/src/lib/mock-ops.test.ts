import { beforeEach, describe, expect, it } from "vitest";
import { MOCK_DATASET_ID, evalGet, resetMockEvals } from "./mock-evals";
import { dpoExportJsonl, opsGet, opsSend, resetMockOps } from "./mock-ops";
import type { AnnotationListItem, DatasetDetail, ListResponse, PromptList, PromptVersion } from "./types";

beforeEach(() => {
  resetMockOps();
  resetMockEvals();
});

describe("mock prompts", () => {
  it("seeds three prompts including a 3-version one with production on v2 and a slash name", () => {
    const list = opsGet("/api/v1/prompts") as PromptList;
    expect(list.items.map((p) => p.name)).toEqual(["rag/answer", "support-answer", "triage-chat"]);
    const sup = list.items.find((p) => p.name === "support-answer")!;
    expect(sup.latest_version).toBe(3);
    expect(sup.labels.production).toBe(2);
    expect((opsGet("/api/v1/prompts/support-answer/versions") as { items: PromptVersion[] }).items).toHaveLength(3);
    expect((opsGet("/api/v1/prompts/rag/answer") as PromptVersion).name).toBe("rag/answer");
    expect((opsGet("/api/v1/prompts/triage-chat") as PromptVersion).type).toBe("chat");
  });

  it("resolves by label, version and default", () => {
    expect((opsGet("/api/v1/prompts/support-answer") as PromptVersion).version).toBe(2);
    expect((opsGet("/api/v1/prompts/support-answer", { label: "staging" }) as PromptVersion).version).toBe(3);
    expect((opsGet("/api/v1/prompts/support-answer", { version: "1" }) as PromptVersion).labels).toEqual([]);
    expect(() => opsGet("/api/v1/prompts/support-answer", { label: "nope" })).toThrow();
  });

  it("creates immutable versions and moves labels (rollback)", () => {
    const v = opsSend("POST", "/api/v1/prompts", {
      name: "support-answer",
      type: "text",
      template: "Hi {{ who }}",
      labels: ["staging"],
    }) as PromptVersion;
    expect(v.version).toBe(4);
    expect(v.variables).toEqual(["who"]);
    expect(v.labels).toEqual(["staging"]);
    expect((opsGet("/api/v1/prompts/support-answer", { version: "3" }) as PromptVersion).labels).toEqual([]);
    opsSend("POST", "/api/v1/prompts/support-answer/labels", { label: "production", version: 1 });
    expect((opsGet("/api/v1/prompts/support-answer") as PromptVersion).version).toBe(1);
    expect(() => opsSend("POST", "/api/v1/prompts/support-answer/labels", { label: "production", version: 9 })).toThrow();
    expect(() => opsSend("POST", "/api/v1/prompts", { name: "bad name", type: "text", template: "x" })).toThrow();
  });
});

describe("mock annotations", () => {
  it("seeds 4 pending and 2 done with trace previews", () => {
    const pending = opsGet("/api/v1/annotations", { status: "pending" }) as ListResponse<AnnotationListItem>;
    const done = opsGet("/api/v1/annotations", { status: "done" }) as ListResponse<AnnotationListItem>;
    expect(pending.items).toHaveLength(4);
    expect(done.items).toHaveLength(2);
    expect(pending.items.every((a) => a.trace_name && a.input_preview)).toBe(true);
  });

  it("completes with correction, dataset and scores, then rejects a second completion", () => {
    const a = (opsGet("/api/v1/annotations", { status: "pending" }) as ListResponse<AnnotationListItem>).items[0];
    const before = (evalGet(`/api/v1/datasets/${MOCK_DATASET_ID}`) as DatasetDetail).items.length;
    const out = opsSend("POST", `/api/v1/annotations/${a.id}/complete`, {
      scores: [{ name: "user_feedback", value: 0 }],
      corrected_output: "Fixed answer",
      add_to_dataset_id: MOCK_DATASET_ID,
    }) as { status: string };
    expect(out.status).toBe("done");
    expect((evalGet(`/api/v1/datasets/${MOCK_DATASET_ID}`) as DatasetDetail).items).toHaveLength(before + 1);
    expect(dpoExportJsonl("/api/v1/exports/dpo.jsonl")).toContain("Fixed answer");
    expect(() => opsSend("POST", `/api/v1/annotations/${a.id}/complete`, {})).toThrow(/already/);
    expect((opsGet("/api/v1/annotations", { status: "pending" }) as ListResponse<AnnotationListItem>).items).toHaveLength(3);
  });

  it("returns the existing pending annotation for the same trace", () => {
    const a = (opsGet("/api/v1/annotations", { status: "pending" }) as ListResponse<AnnotationListItem>).items[0];
    const again = opsSend("POST", "/api/v1/annotations", { trace_id: a.trace_id, reason: "manual" }) as { id: string };
    expect(again.id).toBe(a.id);
  });
});

describe("mock rules, online evals, alerts", () => {
  it("seeds and mutates", () => {
    expect((opsGet("/api/v1/rules") as { items: unknown[] }).items).toHaveLength(2);
    expect((opsGet("/api/v1/online-evals") as { items: unknown[] }).items).toHaveLength(1);
    expect((opsGet("/api/v1/alerts") as ListResponse<unknown>).items).toHaveLength(3);
    const r = opsSend("POST", "/api/v1/rules", { name: "x", condition: { feedback: "negative" }, actions: ["alert"] }) as { id: string };
    expect(() => opsSend("POST", "/api/v1/rules", { name: "x", condition: { feedback: "negative", status: "error" }, actions: ["alert"] })).toThrow();
    expect(() => opsSend("POST", "/api/v1/rules", { name: "x", condition: { status: "error" }, actions: ["add_to_dataset"] })).toThrow();
    opsSend("DELETE", `/api/v1/rules/${r.id}`, undefined);
    expect((opsGet("/api/v1/rules") as { items: unknown[] }).items).toHaveLength(2);
    expect(() => opsSend("POST", "/api/v1/online-evals", { name: "Faithfulness sample", metric: "faithfulness", sampling_rate: 0.5 })).toThrow();
    expect(() => opsSend("POST", "/api/v1/online-evals", { name: "n", metric: "faithfulness", sampling_rate: 2 })).toThrow();
  });
});
