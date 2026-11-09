// Pure helpers behind the annotation review form, rules and online-eval forms.
import { buildTree } from "./trace-tree";
import type {
  CompleteAnnotationRequest,
  CreateOnlineEvalRequest,
  CreateRuleRequest,
  RuleAction,
  RuleCondition,
  Span,
  SpanKind,
} from "./types";

export const FEEDBACK_SCORE = "user_feedback";

export type ScoreRow = { id: number; name: string; value: string };

export type ScoreAction =
  | { type: "add"; name?: string }
  | { type: "remove"; id: number }
  | { type: "set"; id: number; field: "name" | "value"; text: string }
  | { type: "feedback"; value: 0 | 1 };

/** Reducer for the score rows. Thumbs buttons upsert the single `user_feedback` row. */
export function scoreReducer(rows: ScoreRow[], action: ScoreAction): ScoreRow[] {
  const nextId = rows.reduce((m, r) => Math.max(m, r.id), 0) + 1;
  switch (action.type) {
    case "add":
      return [...rows, { id: nextId, name: action.name ?? "", value: "" }];
    case "remove":
      return rows.filter((r) => r.id !== action.id);
    case "set":
      return rows.map((r) => (r.id === action.id ? { ...r, [action.field]: action.text } : r));
    case "feedback": {
      const value = String(action.value);
      if (rows.some((r) => r.name === FEEDBACK_SCORE)) return rows.map((r) => (r.name === FEEDBACK_SCORE ? { ...r, value } : r));
      return [...rows, { id: nextId, name: FEEDBACK_SCORE, value }];
    }
  }
}

/** Valid rows → `{name, value}` list; blank rows (no name and no value) are ignored. */
export function buildScores(rows: ScoreRow[]): { scores: { name: string; value: number }[]; error: string | null } {
  const scores: { name: string; value: number }[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    const name = r.name.trim();
    const raw = r.value.trim();
    if (!name && !raw) continue;
    if (!name) return { scores, error: "Every score needs a name." };
    if (raw === "") return { scores, error: `Score "${name}" needs a value between 0 and 1.` };
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0 || value > 1) return { scores, error: `Score "${name}" must be between 0 and 1.` };
    if (seen.has(name)) return { scores, error: `Score "${name}" appears twice.` };
    seen.add(name);
    scores.push({ name, value });
  }
  return { scores, error: null };
}

/** Root span = first parentless span in the tree (same as the waterfall's first row). */
export function rootSpan(spans: Span[]): Span | null {
  return buildTree(spans)[0]?.span ?? null;
}

/** Mirrors the server's notion of the original output: `output`, else the last non-empty output message. */
export function originalOutput(root: Span | null): string {
  if (!root) return "";
  if (root.output) return root.output;
  for (let i = root.output_messages.length - 1; i >= 0; i--) {
    if (root.output_messages[i].content) return root.output_messages[i].content;
  }
  return "";
}

export function buildCompleteRequest(args: {
  scores: { name: string; value: number }[];
  original: string;
  corrected: string;
  datasetId: string;
  comment: string;
}): CompleteAnnotationRequest {
  const body: CompleteAnnotationRequest = {};
  if (args.scores.length) body.scores = args.scores;
  // An untouched prefill is not a correction: sending it would create a DPO pair with chosen == rejected.
  if (args.corrected !== args.original && args.corrected.trim() !== "") body.corrected_output = args.corrected;
  if (args.datasetId) body.add_to_dataset_id = args.datasetId;
  if (args.comment.trim()) body.comment = args.comment.trim();
  return body;
}

// ── rules ────────────────────────────────────────────────────────────────────────────────

export type ConditionKind = "score" | "feedback" | "latency" | "error";

export type RuleForm = {
  name: string;
  kind: ConditionKind;
  scoreName: string;
  op: NonNullable<RuleCondition["op"]>;
  value: string;
  minLatencyMs: string;
  actions: RuleAction[];
  datasetId: string;
};

export const emptyRuleForm = (): RuleForm => ({
  name: "",
  kind: "score",
  scoreName: "",
  op: "<",
  value: "0.5",
  minLatencyMs: "5000",
  actions: ["annotate"],
  datasetId: "",
});

export function buildRuleRequest(f: RuleForm): { body: CreateRuleRequest | null; error: string | null } {
  if (!f.name.trim()) return { body: null, error: "Give the rule a name." };
  let condition: RuleCondition;
  if (f.kind === "score") {
    const value = Number(f.value);
    if (!f.scoreName.trim()) return { body: null, error: "Score name is required." };
    if (f.value.trim() === "" || !Number.isFinite(value)) return { body: null, error: "Score threshold must be a number." };
    condition = { score_name: f.scoreName.trim(), op: f.op, value };
  } else if (f.kind === "feedback") condition = { feedback: "negative" };
  else if (f.kind === "latency") {
    const ms = Number(f.minLatencyMs);
    if (f.minLatencyMs.trim() === "" || !Number.isFinite(ms) || ms < 0) return { body: null, error: "Latency must be a number of ms ≥ 0." };
    condition = { min_latency_ms: ms };
  } else condition = { status: "error" };
  if (f.actions.length === 0) return { body: null, error: "Pick at least one action." };
  if (f.actions.includes("add_to_dataset") && !f.datasetId) return { body: null, error: "Pick a dataset for add_to_dataset." };
  return {
    body: {
      name: f.name.trim(),
      enabled: true,
      condition,
      actions: f.actions,
      ...(f.actions.includes("add_to_dataset") ? { dataset_id: f.datasetId } : {}),
    },
    error: null,
  };
}

export function describeCondition(c: RuleCondition): string {
  if (c.score_name) return `score ${c.score_name} ${c.op} ${c.value}`;
  if (c.feedback) return "negative user feedback";
  if (c.min_latency_ms !== undefined) return `latency ≥ ${c.min_latency_ms} ms`;
  if (c.status) return `status = ${c.status}`;
  return "—";
}

export function toggleAction(actions: RuleAction[], a: RuleAction): RuleAction[] {
  return actions.includes(a) ? actions.filter((x) => x !== a) : [...actions, a];
}

// ── online evals ─────────────────────────────────────────────────────────────────────────

export type OnlineEvalForm = {
  name: string;
  metric: string;
  params: string;
  samplingPct: number;
  kind: SpanKind | "";
  spanName: string;
  provider: string;
  model: string;
};

export const emptyOnlineEvalForm = (): OnlineEvalForm => ({
  name: "",
  metric: "faithfulness",
  params: "",
  samplingPct: 10,
  kind: "",
  spanName: "",
  provider: "openai",
  model: "",
});

export function buildOnlineEvalRequest(f: OnlineEvalForm): { body: CreateOnlineEvalRequest | null; error: string | null } {
  if (!f.name.trim()) return { body: null, error: "Give the online eval a name." };
  let params: Record<string, unknown> | undefined;
  if (f.params.trim()) {
    try {
      const parsed: unknown = JSON.parse(f.params);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return { body: null, error: "Params must be a JSON object." };
      params = parsed as Record<string, unknown>;
    } catch {
      return { body: null, error: "Params is not valid JSON." };
    }
  }
  if (!Number.isFinite(f.samplingPct) || f.samplingPct < 0 || f.samplingPct > 100) return { body: null, error: "Sampling must be 0–100%." };
  const filter: NonNullable<CreateOnlineEvalRequest["filter"]> = {};
  if (f.kind) filter.kind = f.kind;
  if (f.spanName.trim()) filter.name = f.spanName.trim();
  const model = f.model.trim() ? { provider: f.provider, name: f.model.trim() } : undefined;
  return {
    body: {
      name: f.name.trim(),
      metric: f.metric,
      ...(params ? { params } : {}),
      sampling_rate: Math.round(f.samplingPct) / 100,
      ...(Object.keys(filter).length ? { filter } : {}),
      ...(model ? { model } : {}),
    },
    error: null,
  };
}

export function describeFilter(filter: { kind?: string; name?: string }): string {
  const parts = [filter.kind && `kind ${filter.kind}`, filter.name && `name ${filter.name}`].filter(Boolean);
  return parts.length ? parts.join(" · ") : "all spans";
}
