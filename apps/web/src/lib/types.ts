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
