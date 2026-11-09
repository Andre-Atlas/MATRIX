// API contract types — copied verbatim from docs/atlas/2026-11-09-plataforma-mvp/spec.md §4.

export type SpanKind =
  | "LLM"
  | "RETRIEVER"
  | "RERANKER"
  | "TOOL"
  | "AGENT"
  | "GUARDRAIL"
  | "CHAIN"
  | "EMBEDDING"
  | "EVALUATOR"
  | "UNKNOWN";

export type TraceSummary = {
  trace_id: string;
  name: string;
  session_id: string | null;
  user_id: string | null;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: "ok" | "error";
  span_count: number;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
  root_kind: SpanKind;
  input_preview: string | null;
  output_preview: string | null;
  tags: string[];
  scores: Record<string, number>;
};

export type Message = {
  role: string;
  content: string;
  tool_calls?: { name: string; arguments: string }[];
};

export type Document = {
  id: string | null;
  content: string;
  score: number | null;
  metadata: Record<string, unknown>;
};

export type Media = { kind: "image" | "audio" | "file"; url: string; mime: string | null };

export type Span = {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  kind: SpanKind;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: "ok" | "error" | "unset";
  status_message: string | null;
  model: string | null;
  provider: string | null;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  ttft_ms: number | null;
  input: string | null;
  output: string | null;
  input_messages: Message[];
  output_messages: Message[];
  documents: Document[];
  tool: { name: string; description: string | null; parameters: string | null } | null;
  media: Media[];
  invocation_parameters: Record<string, unknown>;
  attributes: Record<string, unknown>;
  events: { name: string; time: string; attributes: Record<string, unknown> }[];
};

export type SessionSummary = {
  session_id: string;
  user_id: string | null;
  trace_count: number;
  first_seen: string;
  last_seen: string;
  total_cost_usd: number;
};

export type Score = {
  id: string;
  trace_id: string;
  span_id: string | null;
  name: string;
  value: number;
  label: string | null;
  comment: string | null;
  source: string;
  created_at: string;
};

// Response wrappers.

export type ListResponse<T> = { items: T[]; next_cursor: string | null };

export type TraceDetail = { trace: TraceSummary; spans: Span[] };

export type SessionDetail = { session: SessionSummary; traces: TraceSummary[] };

export type OverviewStats = {
  trace_count: number;
  error_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  total_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  by_model: { model: string; calls: number; cost_usd: number }[];
};

export const SPAN_KINDS: SpanKind[] = [
  "LLM",
  "RETRIEVER",
  "RERANKER",
  "TOOL",
  "AGENT",
  "GUARDRAIL",
  "CHAIN",
  "EMBEDDING",
  "EVALUATOR",
  "UNKNOWN",
];

// ── Datasets and experiments (spec §4 "Datasets e experimentos") ────────────────────────────

export type Dataset = {
  id: string;
  name: string;
  description: string | null;
  item_count: number;
  created_at: string;
};

export type DatasetItem = {
  id: string;
  dataset_id: string;
  input: Record<string, unknown>;
  expected_output: string | null;
  context: string[];
  metadata: Record<string, unknown>;
  source_trace_id: string | null;
  source_span_id: string | null;
  created_at: string;
};

export type DatasetDetail = { dataset: Dataset; items: DatasetItem[] };

export type CreateDatasetRequest = { name: string; description?: string };
export type CreateDatasetItemRequest = {
  input: Record<string, unknown>;
  expected_output?: string | null;
  context?: string[];
  metadata?: Record<string, unknown>;
};
export type DatasetItemFromSpanRequest = { trace_id: string; span_id?: string };

export type MetricType =
  | "faithfulness"
  | "answer_relevancy"
  | "context_precision"
  | "g_eval"
  | "equals"
  | "contains"
  | "regex"
  | "is_json"
  | "levenshtein"
  | "rouge_l"
  | "cost"
  | "latency";

export const METRIC_TYPES: MetricType[] = [
  "faithfulness",
  "answer_relevancy",
  "context_precision",
  "g_eval",
  "equals",
  "contains",
  "regex",
  "is_json",
  "levenshtein",
  "rouge_l",
  "cost",
  "latency",
];

/** `name` defaults to `type`; scores, summary and regressions are keyed by that name. */
export type MetricSpec = {
  type: MetricType;
  name?: string;
  params?: Record<string, unknown>;
  threshold?: number | null;
};

export type ModelProvider = "openai" | "anthropic" | "fake";
export const MODEL_PROVIDERS: ModelProvider[] = ["openai", "anthropic", "fake"];

export type PromptRef =
  | { name: string; version: number }
  | { template: string | Message[]; type: "text" | "chat" };

export type Variant = {
  label: string;
  prompt: PromptRef;
  model: { provider: ModelProvider; name: string; params?: Record<string, unknown> };
};

export type ExperimentStatus = "queued" | "running" | "done" | "failed";

export type MetricAggregate = { mean: number | null; pass_rate: number | null };

/** variant label → metric name → aggregate. */
export type ExperimentSummary = Record<string, Record<string, MetricAggregate>>;

export type Experiment = {
  id: string;
  name: string;
  dataset_id: string;
  baseline_experiment_id: string | null;
  status: ExperimentStatus;
  variants: Variant[];
  metrics: MetricSpec[];
  error: string | null;
  summary: ExperimentSummary | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type ExperimentListItem = Pick<
  Experiment,
  "id" | "name" | "dataset_id" | "status" | "created_at" | "finished_at" | "summary"
>;

export type FaithfulnessStatement = { statement: string; supported: boolean; reason: string };

/** `value`/`passed` are null when the metric does not apply (n/a). */
export type MetricScore = {
  value: number | null;
  passed: boolean | null;
  reason: string | null;
  details?: Record<string, unknown>;
};

export type ExperimentResult = {
  item_id: string;
  variant: string;
  output: string | null;
  latency_ms: number | null;
  cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  error: string | null;
  scores: Record<string, MetricScore>;
};

export type Regression = {
  item_id: string;
  variant: string;
  metric: string;
  baseline: number | null;
  current: number | null;
};

export type ExperimentDetail = {
  experiment: Experiment;
  results: ExperimentResult[];
  summary: ExperimentSummary;
  regressions: Regression[];
};

export type CreateExperimentRequest = {
  name: string;
  dataset_id: string;
  baseline_experiment_id?: string | null;
  variants: Variant[];
  metrics: MetricSpec[];
};

export const metricKey = (m: MetricSpec): string => m.name || m.type;
