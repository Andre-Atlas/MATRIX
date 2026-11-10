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

// ── Safety & OWASP LLM Top 10 (Trilha C) ──

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type ComplianceStatus = "compliant" | "needs_review" | "vulnerable";

export type SafetySeverity = "info" | "low" | "medium" | "high" | "critical";

export type SafetyCategoryCode =
  | "LLM01"
  | "LLM02"
  | "LLM03"
  | "LLM04"
  | "LLM05"
  | "LLM06"
  | "LLM07"
  | "LLM08"
  | "LLM09"
  | "LLM10";

export type SafetyProbe = {
  id: string;
  category_id: string;
  category_code: SafetyCategoryCode;
  name: string;
  description: string;
  attack_vector: string;
  payload_prompt: string;
  expected_defense: string;
  recommended_guardrail: string;
  severity: SafetySeverity;
};

export type SafetyCategory = {
  id: string;
  code: SafetyCategoryCode;
  name: string;
  short_description: string;
  description: string;
  probes_count: number;
  severity_default: SafetySeverity;
  owasp_url?: string;
  probes?: SafetyProbe[];
};

export type SafetyFinding = {
  id: string;
  scan_id: string;
  category_id: string;
  category_code: SafetyCategoryCode;
  category_name: string;
  probe_id: string;
  probe_name: string;
  severity: SafetySeverity;
  status: "passed" | "failed" | "flagged";
  passed?: boolean;
  attack_prompt: string;
  prompt?: string;
  observed_output: string;
  response?: string;
  defense_analysis: string;
  analysis?: string;
  recommended_guardrail: string;
  mitigation?: string;
  cwe_id?: string;
  mitigation_applied?: boolean;
  created_at: string;
};

export type SafetyCategoryScore = {
  category_id: string;
  category_code: SafetyCategoryCode;
  name: string;
  probes_tested: number;
  probes_passed: number;
  probes_failed: number;
  pass_rate: number; // 0.0 to 1.0
  status: ComplianceStatus;
  findings_count: number;
  critical_findings: number;
};

export type SafetyScanStatus = "queued" | "running" | "completed" | "failed" | "passed" | "warning";

export type SafetyScan = {
  id: string;
  name: string;
  target_model: string;
  target_prompt?: string;
  target_system_prompt?: string;
  status: SafetyScanStatus;
  categories_tested: string[];
  total_probes: number;
  probes_passed: number;
  passed_count?: number;
  probes_failed: number;
  failed_count?: number;
  pass_rate?: number;
  safety_score: number; // 0 to 100
  global_score?: number;
  risk_level: RiskLevel;
  findings_count: number;
  created_at: string;
  completed_at?: string;
  duration_ms?: number;
};

export type SafetyScanSummary = {
  total_probes: number;
  passed: number;
  failed: number;
  compliance_rate: number; // 0 to 100
  score: number; // 0 to 100
  risk_level: RiskLevel;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
};

export type SafetyScanReport = {
  scan: SafetyScan;
  category_scores: SafetyCategoryScore[];
  findings: SafetyFinding[];
  summary: SafetyScanSummary;
};

export type CreateSafetyScanRequest = {
  name: string;
  target_model: string;
  target_prompt?: string;
  target_system_prompt?: string;
  categories?: string[];
  max_probes_per_category?: number;
};

export type SafetyCategoriesResponse = {
  categories: SafetyCategory[];
};

export type SafetyScansResponse = {
  scans: SafetyScan[];
  total: number;
};

// ── AI Gateway & Proxy de Inferência (Trilha D) ──

export type CircuitBreakerState =
  | "closed"
  | "half_open"
  | "open"
  | "CLOSED"
  | "HALF_OPEN"
  | "OPEN";

export type GatewayProviderHealth = "healthy" | "degraded" | "outage";

export type GatewayLogStatus = "CACHE_HIT" | "DIRECT" | "FALLBACK" | "ERROR";

export type GatewayStats = {
  total_requests: number;
  cache_hits?: number;
  cache_hit_rate: number; // 0.0 to 1.0 (e.g. 0.384)
  saved_cost_usd?: number;
  cost_saved_usd?: number;
  tokens_saved?: number;
  total_cost_usd?: number;
  latency_p50_ms?: number;
  latency_p99_ms?: number;
  avg_latency_ms: number;
  active_providers?: number;
  healthy_providers?: number;
  fallback_events_24h?: number;
  requests_by_status?: Record<GatewayLogStatus, number>;
  throughput_rpm?: number;
};

export type GatewayProviderStatus = {
  id?: string;
  name: string;
  provider?: string;
  endpoint?: string;
  model_count?: number;
  models?: string[];
  status?: GatewayProviderHealth;
  circuit_state?: CircuitBreakerState;
  circuit_breaker?: string;
  failure_count: number;
  failure_threshold?: number;
  latency_p50_ms?: number;
  latency_p99_ms?: number;
  avg_latency_ms?: number;
  uptime_percentage?: number;
  last_checked_at?: string;
  error_message?: string | null;
  weight?: number;
  total_requests?: number;
};

export type FallbackNode = {
  provider: string;
  model: string;
  priority: number;
  max_retries: number;
  timeout_ms: number;
};

export type GatewayRoutingRule = {
  id: string;
  name: string;
  description?: string;
  match_pattern: string;
  primary_provider: string;
  primary_model: string;
  fallback_chain: FallbackNode[];
  semantic_cache_enabled: boolean;
  cache_similarity_threshold: number; // e.g. 0.92
  rate_limit_rpm: number;
  retry_count: number;
  timeout_ms: number;
  is_active: boolean;
};

export type GatewayLogEntry = {
  id: string;
  timestamp: string;
  project_id?: string;
  request_id?: string;
  route_name?: string;
  model?: string;
  requested_model?: string;
  served_model?: string;
  provider: string;
  status: GatewayLogStatus;
  cache_hit?: boolean;
  fallback_triggered?: boolean;
  fallback_chain_traversed?: string[];
  status_code?: number;
  latency_ms: number;
  tokens?: { prompt?: number; completion?: number; total?: number };
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  cost_usd?: number;
  saved_cost_usd?: number;
  cost_saved_usd?: number;
  cache_score?: number | null;
  error_message?: string | null;
  client_ip?: string;
};

export type GatewayStatsResponse = GatewayStats;

export type GatewayProvidersResponse = {
  providers: GatewayProviderStatus[];
  total: number;
};

export type GatewayLogsResponse = {
  logs: GatewayLogEntry[];
  total: number;
  next_cursor: string | null;
};

export type GatewayRulesResponse = {
  rules: GatewayRoutingRule[];
  total: number;
};

