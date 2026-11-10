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
  cache_read_tokens: number;
  cache_write_tokens: number;
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

export type SessionDetail = { session: SessionSummary; traces: TraceSummary[]; next_cursor: string | null };

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

// ── Prompts (spec §4 "Prompts"; backend: server/src/matrix_server/api/prompts.py) ───────────

export type PromptType = "text" | "chat";
export const PROMPT_ROLES = ["system", "user", "assistant", "tool"] as const;
export type PromptRole = (typeof PROMPT_ROLES)[number];
export type ChatTemplateMessage = { role: string; content: string };
export type PromptTemplate = string | ChatTemplateMessage[];

export type PromptSummary = {
  name: string;
  latest_version: number;
  labels: Record<string, number>;
  updated_at: string;
};

export type PromptVersion = {
  name: string;
  version: number;
  type: PromptType;
  template: PromptTemplate;
  variables: string[];
  config: Record<string, unknown>;
  response_schema: Record<string, unknown> | boolean | null;
  labels: string[];
  commit_message: string | null;
  created_at: string;
};

/** Prompt lists are `{items}` only (no cursor). */
export type PromptList = { items: PromptSummary[] };
export type PromptVersionList = { items: PromptVersion[] };

export type CreatePromptRequest = {
  name: string;
  type: PromptType;
  template: PromptTemplate;
  config?: Record<string, unknown>;
  response_schema?: Record<string, unknown> | boolean | null;
  labels?: string[];
  commit_message?: string | null;
};

export type MoveLabelRequest = { label: string; version: number };

// ── Annotation, rules, online evals, alerts (spec §4 "Anotação, regras e avaliação online") ──

export type AnnotationStatus = "pending" | "done";

export type Annotation = {
  id: string;
  trace_id: string;
  reason: string | null;
  status: AnnotationStatus;
  created_at: string;
  completed_at: string | null;
};

/** List items are enriched with the trace summary (null when the trace is gone). */
export type AnnotationListItem = Annotation & {
  trace_name: string | null;
  input_preview: string | null;
  output_preview: string | null;
};

export type CreateAnnotationRequest = { trace_id: string; reason?: string | null };
export type CompleteAnnotationRequest = {
  scores?: { name: string; value: number }[];
  corrected_output?: string | null;
  add_to_dataset_id?: string | null;
  comment?: string | null;
};

export type RuleCondition = {
  score_name?: string;
  op?: "<" | "<=" | ">" | ">=" | "==";
  value?: number;
  feedback?: "negative";
  min_latency_ms?: number;
  status?: "error";
};
export const RULE_OPS = ["<", "<=", ">", ">=", "=="] as const;
export type RuleAction = "annotate" | "add_to_dataset" | "alert";
export const RULE_ACTIONS: RuleAction[] = ["annotate", "add_to_dataset", "alert"];

export type Rule = {
  id: string;
  name: string;
  enabled: boolean;
  condition: RuleCondition;
  actions: RuleAction[];
  dataset_id: string | null;
  created_at: string;
};
export type CreateRuleRequest = {
  name: string;
  enabled?: boolean;
  condition: RuleCondition;
  actions: RuleAction[];
  dataset_id?: string | null;
};

export type OnlineEval = {
  id: string;
  name: string;
  metric: string;
  params: Record<string, unknown>;
  sampling_rate: number;
  filter: { kind?: SpanKind; name?: string };
  model: { provider: string; name: string } | null;
  enabled: boolean;
  created_at: string;
};
export type CreateOnlineEvalRequest = {
  name: string;
  metric: string;
  params?: Record<string, unknown>;
  sampling_rate: number;
  filter?: { kind?: SpanKind; name?: string };
  model?: { provider: string; name: string };
};

export type Alert = {
  id: string;
  rule_id: string | null;
  trace_id: string;
  message: string;
  created_at: string;
};

// ── Vector Space & Drift Observatory (Module 5) ──

export type VectorPointType = "query" | "document" | "gap";

export type VectorPoint = {
  id: string;
  label: string;
  type: VectorPointType;
  x: number;
  y: number;
  z?: number;
  cluster_id: string;
  cluster_name?: string;
  score?: number;
  preview?: string;
  trace_id?: string | null;
  document_id?: string | null;
  metadata?: Record<string, unknown>;
};

export type VectorCluster = {
  id: string;
  name: string;
  description?: string;
  point_count: number;
  document_count: number;
  query_count: number;
  gap_count: number;
  confidence_rate: number;
  centroid: [number, number] | [number, number, number];
  health_status: "healthy" | "warning" | "critical";
  keywords?: string[];
};

export type KnowledgeGap = {
  id: string;
  cluster_id: string;
  cluster_name: string;
  query_text: string;
  missing_topic: string;
  frequency: number;
  severity: "critical" | "warning" | "low";
  suggested_action: string;
  last_detected: string;
};

export type DriftMetricStatus = "healthy" | "warning" | "critical";

export type DriftMetricValue = {
  name: string;
  value: number;
  threshold_warning: number;
  threshold_critical: number;
  status: DriftMetricStatus;
  description?: string;
};

export type DriftMetrics = {
  jensen_shannon: DriftMetricValue;
  psi: DriftMetricValue;
  centroid_cosine: DriftMetricValue;
  wasserstein?: DriftMetricValue;
  overall_status: DriftMetricStatus;
  baseline_period: string;
  target_period: string;
  last_calculated: string;
  total_queries_analyzed: number;
};

export type VectorProjectionResponse = {
  points: VectorPoint[];
  clusters: VectorCluster[];
  gaps: KnowledgeGap[];
  dimensions: 2 | 3;
  total_points: number;
  reduction_method: "umap" | "tsne" | "pca";
};

export type VectorDriftResponse = {
  metrics: DriftMetrics;
  status: DriftMetricStatus;
  clusters_drifting: number;
  top_drifting_clusters: {
    cluster_id: string;
    cluster_name: string;
    drift_score: number;
    status: DriftMetricStatus;
  }[];
  timeline: {
    timestamp: string;
    jensen_shannon: number;
    psi: number;
    centroid_cosine: number;
  }[];
};
