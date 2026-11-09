// In-memory prompts, annotation queue, rules, online evals and alerts for MATRIX_MOCK=1.
// Mirrors server/src/matrix_server/api/{prompts,annotations,rules,online_evals}.py closely enough for the UI.
import { ApiError } from "./api-error";
import { evalGet, mockAddTraceToDataset } from "./mock-evals";
import { mockDb } from "./mock-data";
import { extractVariables, isValidLabel, isValidPromptName, validateDraft } from "./prompt-template";
import { originalOutput, rootSpan } from "./annotation-form";
import {
  METRIC_TYPES,
  RULE_ACTIONS,
  RULE_OPS,
  SPAN_KINDS,
  type Alert,
  type Annotation,
  type AnnotationListItem,
  type CompleteAnnotationRequest,
  type CreateAnnotationRequest,
  type CreateOnlineEvalRequest,
  type CreatePromptRequest,
  type CreateRuleRequest,
  type Dataset,
  type ListResponse,
  type MoveLabelRequest,
  type OnlineEval,
  type PromptList,
  type PromptSummary,
  type PromptVersion,
  type Rule,
} from "./types";

type Pair = { prompt: string; chosen: string; rejected: string; trace_id: string; created_at: string };
type PromptRec = { name: string; versions: Omit<PromptVersion, "labels">[]; labels: Record<string, number>; labelsTouchedAt?: number };

type Store = {
  prompts: PromptRec[];
  annotations: Annotation[];
  rules: Rule[];
  evals: OnlineEval[];
  alerts: Alert[];
  pairs: Pair[];
};

type Params = Record<string, string | number | undefined>;

let counter = 0;
const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}${(counter++).toString(36).padStart(3, "0")}`;
const iso = (ms: number) => new Date(ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;

function bad(detail: string): never {
  throw new ApiError(422, JSON.stringify({ detail }));
}
const isObject = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

// ── seed ─────────────────────────────────────────────────────────────────────────────────

const SUPPORT_V1 = `You are a support engineer for {{product}}.
Answer the customer's question.

Question: {{question}}`;

const SUPPORT_V2 = `You are a support engineer for {{product}}.
Answer the customer's question using only the documentation below.
If the documentation does not cover it, say so.

Documentation:
{{context}}

Question: {{question}}`;

const SUPPORT_V3 = `You are a support engineer for {{product}}.
Answer the customer's question using only the documentation below.
If the documentation does not cover it, say so and offer to escalate.
Keep answers under {{max_words}} words and cite the section you used.

Documentation:
{{context}}

Question: {{question}}`;

const RAG_V1 = `Context:
{{context}}

Answer the question using only the context.
Question: {{question}}`;

const RAG_V2 = `Context:
{{context}}

Answer the question using only the context. Quote the passage you relied on.
If the answer is not in the context, reply "I don't know".
Question: {{question}}`;

function ver(
  name: string,
  version: number,
  type: "text" | "chat",
  template: PromptVersion["template"],
  config: Record<string, unknown>,
  commit: string | null,
  at: number,
  response_schema: PromptVersion["response_schema"] = null,
): Omit<PromptVersion, "labels"> {
  return {
    name,
    version,
    type,
    template,
    variables: extractVariables(template),
    config,
    response_schema,
    commit_message: commit,
    created_at: iso(at),
  };
}

function seed(now: number): Store {
  const traces = mockDb().traces.map((t) => t.summary);
  const failed = traces.filter((t) => t.status === "error");
  const ok = traces.filter((t) => t.status === "ok");
  const pick = (list: typeof traces, i: number) => list[i % list.length].trace_id;

  const prompts: PromptRec[] = [
    {
      name: "support-answer",
      labels: { production: 2, staging: 3 },
      versions: [
        ver("support-answer", 1, "text", SUPPORT_V1, { model: "gpt-4o-mini", temperature: 0.3, max_tokens: 400 }, "Initial prompt", now - 9 * 24 * HOUR),
        ver("support-answer", 2, "text", SUPPORT_V2, { model: "gpt-4o-mini", temperature: 0.2, max_tokens: 500 }, "Ground answers in retrieved docs", now - 5 * 24 * HOUR),
        ver("support-answer", 3, "text", SUPPORT_V3, { model: "gpt-4o", temperature: 0.2, max_tokens: 400 }, "Cap length, cite sections, offer escalation", now - 3 * HOUR),
      ],
    },
    {
      name: "triage-chat",
      labels: { production: 1, development: 1 },
      versions: [
        ver(
          "triage-chat",
          1,
          "chat",
          [
            { role: "system", content: "You triage incoming support tickets for {{product}}. Reply with JSON only." },
            { role: "user", content: "Ticket from {{customer}}:\n{{ticket}}" },
          ],
          { model: "claude-haiku-4.5", temperature: 0, max_tokens: 200 },
          "Chat-style triage with JSON output",
          now - 2 * 24 * HOUR,
          {
            type: "object",
            properties: { severity: { type: "string", enum: ["low", "medium", "high"] }, team: { type: "string" } },
            required: ["severity", "team"],
          },
        ),
      ],
    },
    {
      name: "rag/answer",
      labels: { production: 2, staging: 2 },
      versions: [
        ver("rag/answer", 1, "text", RAG_V1, { model: "gpt-4o-mini", temperature: 0 }, "Baseline RAG prompt", now - 14 * 24 * HOUR),
        ver("rag/answer", 2, "text", RAG_V2, { model: "gpt-4o-mini", temperature: 0, max_tokens: 300 }, "Quote the source, allow I don't know", now - 26 * HOUR),
      ],
    },
  ];

  const annotations: Annotation[] = [
    { id: "ann_01", trace_id: pick(failed, 0), reason: "rule: status error", status: "pending", created_at: iso(now - 20 * MIN), completed_at: null },
    { id: "ann_02", trace_id: pick(ok, 3), reason: "rule: faithfulness < 0.5", status: "pending", created_at: iso(now - 70 * MIN), completed_at: null },
    { id: "ann_03", trace_id: pick(ok, 0), reason: "manual", status: "pending", created_at: iso(now - 3 * HOUR), completed_at: null },
    { id: "ann_04", trace_id: pick(failed, 1), reason: "rule: negative user feedback", status: "pending", created_at: iso(now - 5 * HOUR), completed_at: null },
    { id: "ann_05", trace_id: pick(ok, 5), reason: "rule: faithfulness < 0.5", status: "done", created_at: iso(now - 26 * HOUR), completed_at: iso(now - 24 * HOUR) },
    { id: "ann_06", trace_id: pick(ok, 7), reason: "manual", status: "done", created_at: iso(now - 30 * HOUR), completed_at: iso(now - 28 * HOUR) },
  ];

  const rules: Rule[] = [
    {
      id: "rule_low_faith",
      name: "Low faithfulness to queue",
      enabled: true,
      condition: { score_name: "faithfulness", op: "<", value: 0.5 },
      actions: ["annotate", "alert"],
      dataset_id: null,
      created_at: iso(now - 6 * 24 * HOUR),
    },
    {
      id: "rule_errors",
      name: "Errors become regression cases",
      enabled: true,
      condition: { status: "error" },
      actions: ["annotate", "add_to_dataset"],
      dataset_id: "ds_rag_golden",
      created_at: iso(now - 4 * 24 * HOUR),
    },
  ];

  const evals: OnlineEval[] = [
    {
      id: "oe_faith",
      name: "Faithfulness sample",
      metric: "faithfulness",
      params: {},
      sampling_rate: 0.1,
      filter: { kind: "LLM", name: "answer" },
      model: { provider: "openai", name: "gpt-4o-mini" },
      enabled: true,
      created_at: iso(now - 5 * 24 * HOUR),
    },
  ];

  const alerts: Alert[] = [
    { id: "alr_1", rule_id: "rule_low_faith", trace_id: pick(ok, 3), message: "Rule “Low faithfulness to queue”: faithfulness 0.42 < 0.5", created_at: iso(now - 70 * MIN) },
    { id: "alr_2", rule_id: "rule_low_faith", trace_id: pick(ok, 6), message: "Rule “Low faithfulness to queue”: faithfulness 0.31 < 0.5", created_at: iso(now - 9 * HOUR) },
    { id: "alr_3", rule_id: null, trace_id: pick(failed, 0), message: "Online eval “Faithfulness sample”: faithfulness 0.40 below threshold 0.7", created_at: iso(now - 22 * HOUR) },
  ];

  const pairs: Pair[] = [
    { prompt: "Which span kinds exist?", chosen: "LLM, RETRIEVER, RERANKER, TOOL, AGENT, GUARDRAIL, CHAIN, EMBEDDING, EVALUATOR, UNKNOWN.", rejected: "LLM, TOOL and AGENT.", trace_id: pick(ok, 5), created_at: iso(now - 24 * HOUR) },
  ];

  return { prompts, annotations, rules, evals, alerts, pairs };
}

function store(): Store {
  const g = globalThis as unknown as { __matrixOpsStore?: Store };
  g.__matrixOpsStore ??= seed(Date.now());
  return g.__matrixOpsStore;
}

export function resetMockOps() {
  (globalThis as unknown as { __matrixOpsStore?: Store }).__matrixOpsStore = undefined;
}

// ── views ────────────────────────────────────────────────────────────────────────────────

function versionView(rec: PromptRec, v: Omit<PromptVersion, "labels">): PromptVersion {
  return { ...v, labels: Object.entries(rec.labels).filter(([, n]) => n === v.version).map(([l]) => l).sort() };
}

function summaryView(rec: PromptRec): PromptSummary {
  const last = rec.versions[rec.versions.length - 1];
  return {
    name: rec.name,
    latest_version: last.version,
    labels: Object.fromEntries(Object.entries(rec.labels).sort(([a], [b]) => a.localeCompare(b))),
    updated_at: (rec.labelsTouchedAt ?? 0) > Date.parse(last.created_at) ? iso(rec.labelsTouchedAt!) : last.created_at,
  };
}

function annotationItem(a: Annotation): AnnotationListItem {
  const t = mockDb().traces.find((x) => x.summary.trace_id === a.trace_id);
  return {
    ...a,
    trace_name: t?.summary.name ?? null,
    input_preview: t?.summary.input_preview ?? null,
    output_preview: t?.summary.output_preview ?? null,
  };
}

function page<T>(items: T[], params: Params): ListResponse<T> {
  const limit = Math.min(500, Math.max(1, Number(params.limit ?? 50)));
  const offset = Number(params.cursor ?? 0) || 0;
  return { items: items.slice(offset, offset + limit), next_cursor: offset + limit < items.length ? String(offset + limit) : null };
}

// ── GET ──────────────────────────────────────────────────────────────────────────────────

export function opsGet(p: string, params: Params = {}): unknown {
  const s = store();
  let m: RegExpExecArray | null;
  if (p === "/api/v1/prompts") {
    const items = s.prompts.map(summaryView).sort((a, b) => a.name.localeCompare(b.name));
    return { items } satisfies PromptList;
  }
  if ((m = /^\/api\/v1\/prompts\/(.+)\/versions$/.exec(p))) {
    const rec = s.prompts.find((x) => x.name === decodeURIComponent(m![1]));
    if (!rec) throw new ApiError(404, "prompt not found");
    return { items: [...rec.versions].reverse().map((v) => versionView(rec, v)) };
  }
  if ((m = /^\/api\/v1\/prompts\/(.+)$/.exec(p))) {
    const rec = s.prompts.find((x) => x.name === decodeURIComponent(m![1]));
    if (!rec) throw new ApiError(404, "prompt not found");
    if (params.label !== undefined && params.version !== undefined) throw new ApiError(422, JSON.stringify({ detail: "pass either label or version, not both" }));
    const target =
      params.version !== undefined ? Number(params.version) : rec.labels[String(params.label ?? "production")] ?? (params.label ? undefined : rec.versions.at(-1)!.version);
    const v = rec.versions.find((x) => x.version === target);
    if (!v) throw new ApiError(404, "prompt not found");
    return versionView(rec, v);
  }
  if (p === "/api/v1/annotations") {
    const items = s.annotations
      .filter((a) => !params.status || a.status === params.status)
      .sort((a, b) => (params.status === "done" ? (b.completed_at ?? "").localeCompare(a.completed_at ?? "") : b.created_at.localeCompare(a.created_at)))
      .map(annotationItem);
    return page(items, params);
  }
  if ((m = /^\/api\/v1\/annotations\/([^/]+)$/.exec(p))) {
    const a = s.annotations.find((x) => x.id === m![1]);
    if (!a) throw new ApiError(404, "annotation not found");
    return a;
  }
  if (p === "/api/v1/rules") return { items: s.rules };
  if (p === "/api/v1/online-evals") return { items: s.evals };
  if (p === "/api/v1/alerts") return page([...s.alerts].sort((a, b) => b.created_at.localeCompare(a.created_at)), params);
  return undefined;
}

export function dpoExportJsonl(p: string): string | undefined {
  if (p !== "/api/v1/exports/dpo.jsonl") return undefined;
  return store()
    .pairs.map((x) => JSON.stringify(x))
    .join("\n")
    .concat(store().pairs.length ? "\n" : "");
}

// ── POST / DELETE ────────────────────────────────────────────────────────────────────────

function createPrompt(s: Store, b: CreatePromptRequest): PromptVersion {
  if (!isObject(b)) bad("body must be an object");
  if (typeof b.name !== "string" || !isValidPromptName(b.name)) bad("invalid prompt name: must match ^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$");
  if (b.type !== "text" && b.type !== "chat") bad("type must be 'text' or 'chat'");
  const tErr = validateDraft(b.type, b.template);
  if (tErr) bad(tErr);
  if (b.config !== undefined && !isObject(b.config)) bad("config must be an object");
  if (b.response_schema !== undefined && b.response_schema !== null && typeof b.response_schema !== "boolean" && !isObject(b.response_schema))
    bad("response_schema must be a JSON Schema object");
  const labels = [...new Set(b.labels ?? [])];
  for (const l of labels) if (!isValidLabel(l)) bad("invalid label: must match ^[a-z0-9][a-z0-9_-]{0,49}$");
  let rec = s.prompts.find((x) => x.name === b.name);
  if (!rec) {
    rec = { name: b.name, versions: [], labels: {} };
    s.prompts.push(rec);
  }
  const v = ver(b.name, rec.versions.length + 1, b.type, b.template, b.config ?? {}, b.commit_message?.trim() || null, Date.now(), b.response_schema ?? null);
  rec.versions.push(v);
  for (const l of labels) rec.labels[l] = v.version;
  return versionView(rec, v);
}

function evalNames(): Dataset[] {
  return (evalGet("/api/v1/datasets") as ListResponse<Dataset>).items;
}

function validateRule(b: CreateRuleRequest): Omit<Rule, "id" | "created_at"> {
  if (typeof b?.name !== "string" || !b.name.trim()) bad("name must be a non-empty string up to 200 chars");
  const c = b.condition;
  if (!isObject(c)) bad("condition must be an object");
  const cond: Rule["condition"] = {};
  if (c.score_name !== undefined) {
    if (typeof c.score_name !== "string" || !c.score_name) bad("score_name must be a non-empty string");
    if (!(RULE_OPS as readonly string[]).includes(c.op as string)) bad("op must be one of <, <=, >, >=, ==");
    if (typeof c.value !== "number" || !Number.isFinite(c.value)) bad("value must be a finite number");
    Object.assign(cond, { score_name: c.score_name, op: c.op, value: c.value });
  }
  if (c.feedback !== undefined) {
    if (c.feedback !== "negative") bad('feedback must be "negative"');
    cond.feedback = "negative";
  }
  if (c.min_latency_ms !== undefined) {
    if (typeof c.min_latency_ms !== "number" || c.min_latency_ms < 0) bad("min_latency_ms must be >= 0");
    cond.min_latency_ms = c.min_latency_ms;
  }
  if (c.status !== undefined) {
    if (c.status !== "error") bad('status must be "error"');
    cond.status = "error";
  }
  if (!Object.keys(cond).length) bad("condition needs at least one of score_name, feedback, min_latency_ms, status");
  if (("score_name" in cond || "feedback" in cond) && ("min_latency_ms" in cond || "status" in cond))
    bad("score/feedback conditions cannot be combined with latency/status conditions");
  if (!Array.isArray(b.actions) || !b.actions.length || b.actions.some((a) => !RULE_ACTIONS.includes(a)))
    bad("actions must be a non-empty list of: annotate, add_to_dataset, alert");
  const actions = [...new Set(b.actions)];
  if (actions.includes("add_to_dataset")) {
    if (!b.dataset_id) bad("add_to_dataset requires dataset_id");
    if (!evalNames().some((d) => d.id === b.dataset_id)) bad("dataset_id does not exist in this project");
  }
  return { name: b.name.trim(), enabled: b.enabled ?? true, condition: cond, actions, dataset_id: b.dataset_id || null };
}

function validateEval(s: Store, b: CreateOnlineEvalRequest): OnlineEval {
  if (typeof b?.name !== "string" || !b.name.trim()) bad("name must not be empty");
  if (!(METRIC_TYPES as string[]).includes(b.metric)) bad(`invalid metric: unknown metric type '${b.metric}'`);
  if (typeof b.sampling_rate !== "number" || !(b.sampling_rate >= 0 && b.sampling_rate <= 1)) bad("sampling_rate must be between 0 and 1");
  if (b.params !== undefined && !isObject(b.params)) bad("params must be an object");
  if (b.filter?.kind && !SPAN_KINDS.includes(b.filter.kind)) bad(`filter.kind: unknown span kind '${b.filter.kind}'`);
  if (s.evals.some((e) => e.name === b.name.trim())) throw new ApiError(409, JSON.stringify({ detail: "an online eval with this name already exists" }));
  return {
    id: newId("oe"),
    name: b.name.trim(),
    metric: b.metric,
    params: b.params ?? {},
    sampling_rate: b.sampling_rate,
    filter: b.filter ?? {},
    model: b.model ?? null,
    enabled: true,
    created_at: iso(Date.now()),
  };
}

function completeAnnotation(s: Store, a: Annotation, b: CompleteAnnotationRequest): Annotation {
  if (a.status === "done") throw new ApiError(409, JSON.stringify({ detail: "annotation already completed" }));
  const scores = b?.scores ?? [];
  if (!Array.isArray(scores) || scores.some((x) => !x?.name || typeof x.value !== "number" || !Number.isFinite(x.value)))
    bad("score values must be finite");
  if (b?.add_to_dataset_id && !evalNames().some((d) => d.id === b.add_to_dataset_id)) throw new ApiError(404, JSON.stringify({ detail: "dataset not found" }));
  const trace = mockDb().traces.find((t) => t.summary.trace_id === a.trace_id);
  for (const sc of scores) if (trace) trace.summary.scores[sc.name] = sc.value;
  const root = trace ? rootSpan(trace.spans) : null;
  if (typeof b?.corrected_output === "string" && root) {
    s.pairs.push({
      prompt: root.input ?? "",
      chosen: b.corrected_output,
      rejected: originalOutput(root),
      trace_id: a.trace_id,
      created_at: iso(Date.now()),
    });
  }
  if (b?.add_to_dataset_id) mockAddTraceToDataset(b.add_to_dataset_id, a.trace_id, b.corrected_output ?? null);
  a.status = "done";
  a.completed_at = iso(Date.now());
  return a;
}

export function opsSend(method: string, p: string, body: unknown): unknown {
  const s = store();
  let m: RegExpExecArray | null;
  if (method === "POST" && p === "/api/v1/prompts") return createPrompt(s, body as CreatePromptRequest);
  if (method === "POST" && (m = /^\/api\/v1\/prompts\/(.+)\/labels$/.exec(p))) {
    const rec = s.prompts.find((x) => x.name === decodeURIComponent(m![1]));
    if (!rec) throw new ApiError(404, "prompt not found");
    const b = body as MoveLabelRequest;
    if (typeof b?.label !== "string" || !isValidLabel(b.label)) bad("invalid label: must match ^[a-z0-9][a-z0-9_-]{0,49}$");
    const v = rec.versions.find((x) => x.version === b.version);
    if (!v) throw new ApiError(404, "prompt version not found");
    rec.labels[b.label] = v.version;
    rec.labelsTouchedAt = Date.now();
    return versionView(rec, v);
  }
  if (method === "POST" && p === "/api/v1/annotations") {
    const b = body as CreateAnnotationRequest;
    if (!b?.trace_id) bad("trace_id: Field required");
    if (!mockDb().traces.some((t) => t.summary.trace_id === b.trace_id)) throw new ApiError(404, "trace not found");
    const pending = s.annotations.find((a) => a.trace_id === b.trace_id && a.status === "pending");
    if (pending) return pending;
    const a: Annotation = { id: newId("ann"), trace_id: b.trace_id, reason: b.reason ?? null, status: "pending", created_at: iso(Date.now()), completed_at: null };
    s.annotations.push(a);
    return a;
  }
  if (method === "POST" && (m = /^\/api\/v1\/annotations\/([^/]+)\/complete$/.exec(p))) {
    const a = s.annotations.find((x) => x.id === m![1]);
    if (!a) throw new ApiError(404, "annotation not found");
    return completeAnnotation(s, a, body as CompleteAnnotationRequest);
  }
  if (method === "POST" && p === "/api/v1/rules") {
    const rule: Rule = { id: newId("rule"), created_at: iso(Date.now()), ...validateRule(body as CreateRuleRequest) };
    s.rules.push(rule);
    return rule;
  }
  if (method === "DELETE" && (m = /^\/api\/v1\/rules\/([^/]+)$/.exec(p))) {
    const i = s.rules.findIndex((r) => r.id === m![1]);
    if (i < 0) throw new ApiError(404, "rule not found");
    s.rules.splice(i, 1);
    return null;
  }
  if (method === "POST" && p === "/api/v1/online-evals") {
    const ev = validateEval(s, body as CreateOnlineEvalRequest);
    s.evals.push(ev);
    return ev;
  }
  if (method === "DELETE" && (m = /^\/api\/v1\/online-evals\/([^/]+)$/.exec(p))) {
    const i = s.evals.findIndex((r) => r.id === m![1]);
    if (i < 0) throw new ApiError(404, "online eval not found");
    s.evals.splice(i, 1);
    return null;
  }
  return undefined;
}
