import type {
  Document,
  DriftMetrics,
  KnowledgeGap,
  Media,
  Message,
  SessionSummary,
  Span,
  SpanKind,
  TraceSummary,
  VectorCluster,
  VectorDriftResponse,
  VectorPoint,
  VectorProjectionResponse,
} from "./types";

export const MOCK_AGENT_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";
export const MOCK_RAG_TRACE_ID = "0af7651916cd43dd8448eb211c80319c";

type SpanSeed = Partial<Span> & {
  id: string;
  parent: string | null;
  name: string;
  kind: SpanKind;
  at: number;
  dur: number;
};

const CHART_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">` +
      `<rect width="320" height="180" fill="#f4f4f5"/>` +
      `<text x="16" y="26" font-family="sans-serif" font-size="13" fill="#27272a">p95 latency by week (ms)</text>` +
      [60, 92, 75, 130, 118, 64]
        .map((h, i) => `<rect x="${24 + i * 48}" y="${160 - h}" width="30" height="${h}" fill="#10b981"/>`)
        .join("") +
      `<line x1="16" y1="160" x2="304" y2="160" stroke="#71717a"/></svg>`,
  );

function hex(seed: string, len: number): string {
  // FNV-1a over the whole seed, then extended block by block.
  let out = "";
  for (let block = 0; out.length < len; block++) {
    let h = 2166136261 ^ block;
    for (let i = 0; i < seed.length; i++) {
      h ^= seed.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    out += h.toString(16).padStart(8, "0");
  }
  return out.slice(0, len);
}

function makeSpans(traceId: string, t0: number, seeds: SpanSeed[]): Span[] {
  const ids = new Map(seeds.map((s) => [s.id, hex(`${traceId}:${s.id}`, 16)]));
  return seeds.map(({ id, parent, at, dur, ...rest }) => ({
    span_id: ids.get(id)!,
    trace_id: traceId,
    parent_span_id: parent ? ids.get(parent) ?? parent : null,
    start_time: new Date(t0 + at).toISOString(),
    end_time: new Date(t0 + at + dur).toISOString(),
    duration_ms: dur,
    status: "ok",
    status_message: null,
    model: null,
    provider: null,
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_usd: 0,
    ttft_ms: null,
    input: null,
    output: null,
    input_messages: [],
    output_messages: [],
    documents: [],
    tool: null,
    media: [],
    invocation_parameters: {},
    attributes: {},
    events: [],
    ...rest,
  }));
}

const DOCS: Document[] = [
  {
    id: "kb/runbooks/latency.md#p95",
    content:
      "When p95 latency regresses, first compare TTFT against total generation time. A TTFT spike usually means queueing at the provider; a long tail after the first token points to output length or tool loops.",
    score: 0.912,
    metadata: { source: "runbooks/latency.md", section: "p95", updated: "2026-10-28" },
  },
  {
    id: "kb/arch/gateway.md#retries",
    content:
      "The gateway retries 429 and 5xx responses with exponential backoff (base 200 ms, max 3 attempts). Retries are recorded as span events named `retry`.",
    score: 0.847,
    metadata: { source: "arch/gateway.md", section: "retries", updated: "2026-09-14" },
  },
  {
    id: "kb/faq/models.md#context",
    content:
      "Long system prompts increase TTFT roughly linearly with input tokens. Prompt caching cuts this for stable prefixes.",
    score: 0.731,
    metadata: { source: "faq/models.md", section: "context", updated: "2026-08-02" },
  },
];

function agentSeeds(question: string, answer: string, failed: boolean): SpanSeed[] {
  const sys: Message = {
    role: "system",
    content:
      "You are the MATRIX support supervisor. Plan, call tools when needed, and answer with citations. Keep answers under 120 words.",
  };
  const user: Message = { role: "user", content: question };
  const plannerOut: Message = {
    role: "assistant",
    content: "I'll search the knowledge base for latency runbooks before answering.",
    tool_calls: [
      { name: "search_docs", arguments: JSON.stringify({ query: "p95 latency regression TTFT", top_k: 3 }) },
    ],
  };
  const critic = (i: number, verdict: string): SpanSeed => ({
    id: `critic${i}`,
    parent: "sup",
    name: "critic",
    kind: "LLM",
    at: [5660, 6620, 7560][i],
    dur: [520, 470, 430][i],
    model: "gpt-4o-mini",
    provider: "openai",
    input_tokens: 812 + i * 140,
    output_tokens: 64 - i * 12,
    cost_usd: 0.00016 + i * 0.00002,
    ttft_ms: 190 + i * 10,
    input_messages: [
      { role: "system", content: "Critique the draft for groundedness and length. Reply PASS or a list of fixes." },
      { role: "user", content: `Draft v${i + 1}:\n${answer}` },
    ],
    output_messages: [{ role: "assistant", content: verdict }],
    invocation_parameters: { temperature: 0, max_tokens: 128 },
    attributes: { "llm.prompt_template.name": "critic", "reflection.iteration": i + 1 },
  });
  const revise = (i: number): SpanSeed => ({
    id: `revise${i}`,
    parent: "sup",
    name: "revise",
    kind: "LLM",
    at: [6200, 7110][i],
    dur: [400, 430][i],
    model: "gpt-4o",
    provider: "openai",
    input_tokens: 1320 + i * 90,
    output_tokens: 140 - i * 20,
    cost_usd: 0.0047 - i * 0.0003,
    ttft_ms: 260,
    input_messages: [{ role: "user", content: "Apply the critic's fixes to the draft." }],
    output_messages: [{ role: "assistant", content: answer }],
    invocation_parameters: { temperature: 0.2, max_tokens: 400 },
  });

  return [
    {
      id: "sup",
      parent: null,
      name: "support-agent",
      kind: "AGENT",
      at: 0,
      dur: 8100,
      status: failed ? "error" : "ok",
      status_message: failed ? "AgentError: max reflection iterations reached without PASS" : null,
      input: JSON.stringify({ question, user_tier: "pro" }),
      output: failed ? null : answer,
      attributes: {
        "input.mime_type": "application/json",
        "output.mime_type": "text/plain",
        "agent.name": "support-supervisor",
        "agent.version": "2026.11.2",
        "deployment.environment": "production",
        "session.id": "",
      },
    },
    {
      id: "plan",
      parent: "sup",
      name: "planner",
      kind: "LLM",
      at: 40,
      dur: 1580,
      model: "gpt-4o",
      provider: "openai",
      input_tokens: 1184,
      output_tokens: 96,
      cost_usd: 0.00392,
      ttft_ms: 412,
      input_messages: [sys, user],
      output_messages: [plannerOut],
      invocation_parameters: { temperature: 0.1, max_tokens: 512, tool_choice: "auto" },
      attributes: {
        "llm.prompt_template.name": "supervisor-plan",
        "llm.prompt_template.version": 7,
        "llm.token_count.prompt_details.cache_read": 896,
      },
    },
    {
      id: "search",
      parent: "sup",
      name: "search_docs",
      kind: "TOOL",
      at: 1660,
      dur: 1240,
      input: JSON.stringify({ query: "p95 latency regression TTFT", top_k: 3 }),
      output: JSON.stringify(DOCS.map((d) => ({ id: d.id, score: d.score }))),
      tool: {
        name: "search_docs",
        description: "Hybrid (BM25 + vector) search over the internal knowledge base.",
        parameters: JSON.stringify({
          type: "object",
          properties: { query: { type: "string" }, top_k: { type: "integer", default: 5 } },
          required: ["query"],
        }),
      },
      attributes: { "input.mime_type": "application/json", "output.mime_type": "application/json" },
    },
    {
      id: "retr",
      parent: "search",
      name: "vector_search",
      kind: "RETRIEVER",
      at: 1700,
      dur: 380,
      input: "p95 latency regression TTFT",
      documents: DOCS.map((d, i) => ({ ...d, score: [0.81, 0.79, 0.77][i] })),
      attributes: { "retrieval.index": "kb-prod-v3", "retrieval.top_k": 20, "db.system": "pgvector" },
    },
    {
      id: "embed",
      parent: "retr",
      name: "embed_query",
      kind: "EMBEDDING",
      at: 1710,
      dur: 95,
      model: "text-embedding-3-small",
      provider: "openai",
      input_tokens: 9,
      cost_usd: 0.0000002,
      input: "p95 latency regression TTFT",
    },
    {
      id: "rerank",
      parent: "search",
      name: "rerank",
      kind: "RERANKER",
      at: 2110,
      dur: 760,
      model: "rerank-v3.5",
      provider: "cohere",
      cost_usd: 0.0002,
      documents: DOCS,
      invocation_parameters: { top_n: 3 },
      attributes: { "reranker.query": "p95 latency regression TTFT" },
    },
    {
      id: "fetch",
      parent: "sup",
      name: "fetch_status_page",
      kind: "TOOL",
      at: 2930,
      dur: 520,
      status: "error",
      status_message: "TimeoutError: GET https://status.example.com/api timed out after 500 ms",
      input: JSON.stringify({ url: "https://status.example.com/api" }),
      tool: {
        name: "fetch_status_page",
        description: "Fetches the provider status page to rule out an incident.",
        parameters: JSON.stringify({ type: "object", properties: { url: { type: "string" } } }),
      },
      events: [
        {
          name: "exception",
          time: "",
          attributes: {
            "exception.type": "TimeoutError",
            "exception.message": "timed out after 500 ms",
            "exception.stacktrace":
              "Traceback (most recent call last):\n  File \"tools/http.py\", line 41, in fetch\n    resp = client.get(url, timeout=0.5)\nTimeoutError: timed out after 500 ms",
          },
        },
      ],
      attributes: { "http.method": "GET", "http.url": "https://status.example.com/api", "retry.count": 0 },
    },
    {
      id: "http",
      parent: "fetch",
      name: "HTTP GET",
      kind: "UNKNOWN",
      at: 2940,
      dur: 505,
      status: "error",
      attributes: { "http.status_code": null },
    },
    {
      id: "answer",
      parent: "sup",
      name: "answer",
      kind: "LLM",
      at: 3480,
      dur: 2140,
      model: "o4-mini",
      provider: "openai",
      input_tokens: 2412,
      output_tokens: 188,
      reasoning_tokens: 640,
      cost_usd: 0.00632,
      ttft_ms: 1460,
      input_messages: [
        sys,
        user,
        plannerOut,
        { role: "tool", content: DOCS.map((d) => `[${d.id}] ${d.content}`).join("\n\n") },
        { role: "user", content: "Also, here's the latency chart from our dashboard. [image]" },
      ],
      output_messages: [{ role: "assistant", content: answer }],
      media: [
        { kind: "image", url: CHART_SVG, mime: "image/svg+xml" },
      ] satisfies Media[],
      invocation_parameters: { reasoning_effort: "medium", max_completion_tokens: 1024 },
      attributes: { "llm.prompt_template.name": "supervisor-answer", "llm.prompt_template.version": 12 },
    },
    critic(0, "1. Cite the runbook explicitly.\n2. Cut the paragraph on retries; it is not relevant."),
    revise(0),
    critic(1, "1. Answer is 142 words; limit is 120."),
    revise(1),
    critic(2, failed ? "1. Still missing citation for TTFT claim." : "PASS"),
    {
      id: "guard",
      parent: "sup",
      name: "pii_guard",
      kind: "GUARDRAIL",
      at: 8000,
      dur: 60,
      input: answer,
      output: JSON.stringify({ passed: true, findings: [] }),
      attributes: { "guardrail.policy": "pii-v2" },
    },
  ];
}

function ragSeeds(question: string, answer: string): SpanSeed[] {
  return [
    {
      id: "root",
      parent: null,
      name: "rag_query",
      kind: "CHAIN",
      at: 0,
      dur: 1960,
      input: question,
      output: answer,
      attributes: { "input.mime_type": "text/plain", pipeline: "docs-qa" },
    },
    {
      id: "emb",
      parent: "root",
      name: "embed",
      kind: "EMBEDDING",
      at: 10,
      dur: 120,
      model: "text-embedding-3-small",
      provider: "openai",
      input_tokens: 14,
      cost_usd: 0.0000003,
      input: question,
    },
    {
      id: "ret",
      parent: "root",
      name: "retrieve",
      kind: "RETRIEVER",
      at: 140,
      dur: 210,
      input: question,
      documents: DOCS.slice(0, 2),
      attributes: { "retrieval.index": "docs-v1", "retrieval.top_k": 2 },
    },
    {
      id: "gen",
      parent: "root",
      name: "generate",
      kind: "LLM",
      at: 370,
      dur: 1570,
      model: "claude-sonnet-4-5",
      provider: "anthropic",
      input_tokens: 642,
      output_tokens: 121,
      cost_usd: 0.00374,
      ttft_ms: 540,
      input_messages: [
        { role: "system", content: "Answer using only the provided context." },
        { role: "user", content: `Context:\n${DOCS[0].content}\n\nQuestion: ${question}` },
      ],
      output_messages: [{ role: "assistant", content: answer }],
      invocation_parameters: { temperature: 0, max_tokens: 300 },
    },
  ];
}

export type MockTrace = { summary: TraceSummary; spans: Span[] };

type Turn = { q: string; a: string };

const SUPPORT_TURNS: Turn[] = [
  {
    q: "Our p95 latency doubled since Tuesday. Where should I look first?",
    a: "Start with TTFT vs. total time on the answer step. Your TTFT went from ~0.6 s to 1.4 s while output length is flat, which points to provider queueing rather than longer generations (runbooks/latency.md#p95).",
  },
  {
    q: "Is it the retries in the gateway?",
    a: "Unlikely: no `retry` events were recorded on these spans, and the gateway only retries 429/5xx (arch/gateway.md#retries).",
  },
  {
    q: "Could the bigger system prompt explain it?",
    a: "Partly. Input tokens grew 38% and TTFT scales roughly linearly with input; enabling prompt caching for the stable prefix should recover most of it (faq/models.md#context).",
  },
  {
    q: "Ok, how do I turn on prompt caching for that prefix?",
    a: "Move the static instructions to the start of the system message and keep variables at the end; the provider caches prefixes over 1024 tokens automatically.",
  },
];

const RESEARCH_TURNS: Turn[] = [
  {
    q: "What does faithfulness measure?",
    a: "Whether every claim in the answer is supported by the retrieved context.",
  },
  {
    q: "And context precision?",
    a: "How many of the retrieved chunks were actually relevant, weighted by rank.",
  },
  { q: "Which one should gate deploys?", a: "Faithfulness, with a 0.85 threshold on the golden set." },
];

const SOLO: (Turn & { kind: "agent" | "rag"; failed?: boolean })[] = [
  { kind: "agent", q: "Summarize yesterday's error spikes.", a: "Two spikes, both from fetch_status_page timeouts.", failed: true },
  { kind: "rag", q: "How do I rotate an API key?", a: "Use POST /api/v1/admin/projects/{id}/keys." },
  { kind: "agent", q: "Draft a postmortem outline for the 14:05 incident.", a: "Timeline, impact, root cause, actions." },
  { kind: "rag", q: "What is the default page size?", a: "50 items, maximum 500." },
  { kind: "rag", q: "Which span kinds exist?", a: "LLM, RETRIEVER, RERANKER, TOOL, AGENT, GUARDRAIL, CHAIN, EMBEDDING, EVALUATOR, UNKNOWN." },
  { kind: "agent", q: "Why did the critic loop three times?", a: "The first two drafts exceeded the word limit.", failed: true },
  { kind: "rag", q: "Does the SDK cache prompts?", a: "Yes, with a TTL of 60 s by default." },
  { kind: "agent", q: "Compare gpt-4o and o4-mini cost on the answer step.", a: "o4-mini is 41% cheaper per call at similar quality." },
];

function summarize(
  traceId: string,
  spans: Span[],
  extra: Pick<TraceSummary, "session_id" | "user_id" | "input_preview" | "output_preview" | "tags" | "scores">,
): TraceSummary {
  const root = spans[0];
  const end = Math.max(...spans.map((s) => Date.parse(s.end_time)));
  return {
    trace_id: traceId,
    name: root.name,
    start_time: root.start_time,
    end_time: new Date(end).toISOString(),
    duration_ms: end - Date.parse(root.start_time),
    status: root.status === "error" ? "error" : "ok",
    span_count: spans.length,
    input_tokens: spans.reduce((a, s) => a + s.input_tokens, 0),
    output_tokens: spans.reduce((a, s) => a + s.output_tokens, 0),
    total_cost_usd: spans.reduce((a, s) => a + s.cost_usd, 0),
    root_kind: root.kind,
    ...extra,
  };
}

function buildTrace(
  kind: "agent" | "rag",
  traceId: string,
  t0: number,
  turn: Turn,
  failed: boolean,
  sessionId: string | null,
  userId: string | null,
  i: number,
): MockTrace {
  const seeds = kind === "agent" ? agentSeeds(turn.q, turn.a, failed) : ragSeeds(turn.q, turn.a);
  // Jitter durations a little so the list does not look cloned.
  const scale = 0.8 + ((i * 37) % 40) / 100;
  for (const s of seeds) {
    s.at = Math.round(s.at * scale);
    s.dur = Math.max(1, Math.round(s.dur * scale));
    if (s.input_tokens) s.input_tokens = Math.round(s.input_tokens * scale);
    if (s.output_tokens) s.output_tokens = Math.round(s.output_tokens * (2 - scale));
    if (s.cost_usd) s.cost_usd = s.cost_usd * scale;
  }
  const spans = makeSpans(traceId, t0, seeds);
  for (const s of spans) {
    for (const e of s.events) if (!e.time) e.time = s.end_time;
    if ("session.id" in s.attributes) s.attributes["session.id"] = sessionId ?? "";
  }
  const scores: Record<string, number> =
    kind === "agent"
      ? { helpfulness: failed ? 0 : 1, faithfulness: failed ? 0.42 : 0.86 + (i % 3) * 0.04 }
      : { faithfulness: 0.9 - (i % 4) * 0.05, context_precision: 0.75 + (i % 2) * 0.1 };
  return {
    spans,
    summary: summarize(traceId, spans, {
      session_id: sessionId,
      user_id: userId,
      input_preview: turn.q,
      output_preview: failed ? null : turn.a,
      tags: kind === "agent" ? ["agent", "prod"] : ["rag"],
      scores,
    }),
  };
}

export type MockDb = { traces: MockTrace[]; sessions: SessionSummary[] };

let cache: { db: MockDb; at: number } | null = null;

/** Builds the fixture set anchored at `now` (cached for 10 minutes). */
export function mockDb(now = Date.now()): MockDb {
  if (cache && now - cache.at < 10 * 60_000) return cache.db;
  const anchor = Math.floor(now / 60_000) * 60_000;
  const traces: MockTrace[] = [];

  const SUPPORT = "sess_support_8d21";
  const RESEARCH = "sess_research_51c0";
  SUPPORT_TURNS.forEach((turn, i) => {
    const id = i === 0 ? MOCK_AGENT_TRACE_ID : hex(`support${i}`, 32);
    traces.push(buildTrace("agent", id, anchor - (40 - i * 6) * 60_000, turn, false, SUPPORT, "user_maria", i));
  });
  RESEARCH_TURNS.forEach((turn, i) => {
    const id = i === 0 ? MOCK_RAG_TRACE_ID : hex(`research${i}`, 32);
    traces.push(buildTrace("rag", id, anchor - (5 - i) * 3600_000, turn, false, RESEARCH, "user_ken", i));
  });
  SOLO.forEach((t, i) => {
    traces.push(
      buildTrace(t.kind, hex(`solo${i}`, 32), anchor - (i * 9 + 2) * 3600_000, t, !!t.failed, null, null, i + 7),
    );
  });
  traces.sort((a, b) => Date.parse(b.summary.start_time) - Date.parse(a.summary.start_time));

  const sessions: SessionSummary[] = [SUPPORT, RESEARCH].map((sid) => {
    const ts = traces.filter((t) => t.summary.session_id === sid).map((t) => t.summary);
    return {
      session_id: sid,
      user_id: ts[0]?.user_id ?? null,
      trace_count: ts.length,
      first_seen: ts.map((t) => t.start_time).sort()[0],
      last_seen: ts.map((t) => t.end_time).sort().at(-1)!,
      total_cost_usd: ts.reduce((a, t) => a + t.total_cost_usd, 0),
    };
  });
  sessions.sort((a, b) => b.last_seen.localeCompare(a.last_seen));

  const db = { traces, sessions };
  cache = { db, at: now };
  return db;
}

// ── Vector Space & Drift Observatory Mock Fixtures (Module 5) ──

export const MOCK_VECTOR_CLUSTERS: VectorCluster[] = [
  {
    id: "cluster-auth",
    name: "Authentication & Billing",
    description: "User session auth, OAuth2 callbacks, JWT verification, and Stripe webhook lifecycle",
    point_count: 24,
    document_count: 8,
    query_count: 14,
    gap_count: 2,
    confidence_rate: 0.94,
    centroid: [28, 32, 15],
    health_status: "healthy",
    keywords: ["oauth2", "stripe", "jwt", "session", "billing", "webhooks"],
  },
  {
    id: "cluster-agent",
    name: "Agent Tool Calling & Execution",
    description: "Function schemas, tool dispatch, JSON argument serialization and retry handling",
    point_count: 22,
    document_count: 7,
    query_count: 13,
    gap_count: 2,
    confidence_rate: 0.88,
    centroid: [-32, 22, -18],
    health_status: "healthy",
    keywords: ["tools", "functions", "json-schema", "dispatch", "retries", "timeout"],
  },
  {
    id: "cluster-rag",
    name: "RAG & Knowledge Retrieval",
    description: "Hybrid vector search, BM25 reranking, chunking boundaries, and context window assembly",
    point_count: 26,
    document_count: 9,
    query_count: 14,
    gap_count: 3,
    confidence_rate: 0.81,
    centroid: [18, -34, 12],
    health_status: "warning",
    keywords: ["chunks", "embeddings", "pgvector", "bm25", "rerank", "context"],
  },
  {
    id: "cluster-safety",
    name: "Guardrails & Safety Evals",
    description: "Adversarial jailbreak protection, PII masking, OWASP LLM 01 prompt injection, toxic filters",
    point_count: 20,
    document_count: 5,
    query_count: 11,
    gap_count: 4,
    confidence_rate: 0.73,
    centroid: [-26, -28, -24],
    health_status: "critical",
    keywords: ["jailbreak", "pii", "injection", "moderation", "guardrails", "owasp"],
  },
];

export const MOCK_KNOWLEDGE_GAPS: KnowledgeGap[] = [
  {
    id: "gap-saml-sso",
    cluster_id: "cluster-auth",
    cluster_name: "Authentication & Billing",
    query_text: "How to configure Okta and Entra ID SAML 2.0 metadata exchange with automated SCIM user provisioning?",
    missing_topic: "Enterprise SAML 2.0 & SCIM Provisioning",
    frequency: 46,
    severity: "critical",
    suggested_action: "Publish documentation and reference implementation for Okta/Entra SCIM v2 webhook listeners.",
    last_detected: "2026-11-10T11:45:00Z",
  },
  {
    id: "gap-pt-jailbreak",
    cluster_id: "cluster-safety",
    cluster_name: "Guardrails & Evals",
    query_text: "Como contornar restrições de sistema usando codificação base64 em português e personas de ficção científica?",
    missing_topic: "Multilingual Adversarial Jailbreak Defenses",
    frequency: 54,
    severity: "critical",
    suggested_action: "Integrate multilingual adversarial jailbreak detection dataset into online guardrail evaluations.",
    last_detected: "2026-11-10T13:00:00Z",
  },
  {
    id: "gap-stream-tool",
    cluster_id: "cluster-agent",
    cluster_name: "Agent Tool Calling & Execution",
    query_text: "How to handle streaming partial JSON arguments when LLM calls nested tools with chunked tokens?",
    missing_topic: "Streaming Partial JSON Tool Calls",
    frequency: 32,
    severity: "warning",
    suggested_action: "Add guide and helper library for incremental token parsing in function calling responses.",
    last_detected: "2026-11-10T09:30:00Z",
  },
  {
    id: "gap-multitenant-rag",
    cluster_id: "cluster-rag",
    cluster_name: "RAG & Knowledge Retrieval",
    query_text: "What is the best pgvector indexing strategy for strict per-tenant namespace isolation with HNSW partitions?",
    missing_topic: "Multi-Tenant Vector Isolation & Partitioned HNSW",
    frequency: 27,
    severity: "warning",
    suggested_action: "Document partitioned table schemas and row-level security (RLS) policies for pgvector.",
    last_detected: "2026-11-10T12:15:00Z",
  },
];

export const MOCK_VECTOR_POINTS: VectorPoint[] = [
  // Cluster Auth - Documents (Green)
  { id: "doc-auth-1", label: "Doc: OAuth 2.0 PKCE Flow", type: "document", x: 26, y: 34, z: 12, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.96, preview: "Authorization Code flow with Proof Key for Code Exchange specification and token exchange", document_id: "doc_oauth_pkce" },
  { id: "doc-auth-2", label: "Doc: Stripe Webhook Signatures", type: "document", x: 31, y: 36, z: 18, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.98, preview: "Verifying stripe-signature headers using secret key and HMAC SHA-256", document_id: "doc_stripe_webhooks" },
  { id: "doc-auth-3", label: "Doc: JWT Invalidation & Refresh", type: "document", x: 27, y: 29, z: 14, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.91, preview: "Short-lived access tokens with rolling refresh token rotation in redis store", document_id: "doc_jwt_refresh" },
  { id: "doc-auth-4", label: "Doc: Session Cookie Security", type: "document", x: 30, y: 33, z: 16, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.94, preview: "HttpOnly, SameSite=Lax, and Secure flags on matrix_session cookie", document_id: "doc_cookie_sec" },
  { id: "doc-auth-5", label: "Doc: Customer Portal Integration", type: "document", x: 29, y: 38, z: 11, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.89, preview: "Directing users to self-serve Stripe Billing Customer Portal for plan management", document_id: "doc_stripe_portal" },

  // Cluster Auth - Queries (Blue)
  { id: "q-auth-1", label: "User: Webhook signature verification failed", type: "query", x: 32, y: 37, z: 17, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.93, preview: "Error verifying stripe signature header in production next.js endpoint", trace_id: "tr_auth_441" },
  { id: "q-auth-2", label: "User: Refresh token expired after 7 days", type: "query", x: 26, y: 31, z: 13, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.88, preview: "User is forced to log in again because refresh token TTL expired", trace_id: "tr_auth_442" },
  { id: "q-auth-3", label: "User: Google OAuth redirect mismatch URI", type: "query", x: 25, y: 35, z: 15, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.87, preview: "redirect_uri_mismatch on staging environment callback url", trace_id: "tr_auth_443" },
  { id: "q-auth-4", label: "User: Cancel subscription prorated refund", type: "query", x: 30, y: 39, z: 12, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.84, preview: "How does invoice proration calculate when downgrading tier mid-month?", trace_id: "tr_auth_444" },
  { id: "q-auth-5", label: "User: Session cookie not sent on cross-domain", type: "query", x: 28, y: 30, z: 16, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.92, preview: "CORS preflight request does not include matrix_session cookie", trace_id: "tr_auth_445" },

  // Cluster Auth - Gap (Red/Amber)
  { id: "gap-pt-auth-1", label: "Gap: SAML 2.0 SCIM Provisioning", type: "gap", x: 38, y: 44, z: 24, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.42, preview: "Zero docs found for Okta SCIM v2 user synchronization endpoint", trace_id: "tr_gap_saml" },
  { id: "gap-pt-auth-2", label: "Gap: Multi-currency tax ID validation", type: "gap", x: 36, y: 42, z: 20, cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", score: 0.48, preview: "No coverage for Brazilian CNPJ / EU VAT automated tax calculation", trace_id: "tr_gap_tax" },

  // Cluster Agent - Documents (Green)
  { id: "doc-agent-1", label: "Doc: OpenAI Function Calling Spec", type: "document", x: -33, y: 24, z: -16, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.97, preview: "JSON schema parameter definitions, required fields, and enum values", document_id: "doc_agent_fcall" },
  { id: "doc-agent-2", label: "Doc: Tool Timeout & Retry Backoff", type: "document", x: -30, y: 21, z: -19, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.95, preview: "Exponential backoff with jitter on tool HTTP 504 and transient network errors", document_id: "doc_agent_retry" },
  { id: "doc-agent-3", label: "Doc: Dynamic Tool Dispatch Registry", type: "document", x: -35, y: 26, z: -14, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.92, preview: "Registering server-side actions and validating client input args", document_id: "doc_agent_registry" },
  { id: "doc-agent-4", label: "Doc: Parallel Tool Execution", type: "document", x: -31, y: 19, z: -20, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.94, preview: "Invoking independent tools concurrently via Promise.allSettled", document_id: "doc_agent_parallel" },

  // Cluster Agent - Queries (Blue)
  { id: "q-agent-1", label: "User: Tool arguments JSON parse error", type: "query", x: -34, y: 23, z: -17, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.89, preview: "LLM truncated JSON argument string before closing curly brace", trace_id: "tr_agent_501" },
  { id: "q-agent-2", label: "User: Tool invocation exceeded 30s timeout", type: "query", x: -29, y: 20, z: -18, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.86, preview: "Database query tool timed out waiting for analytical pool connection", trace_id: "tr_agent_502" },
  { id: "q-agent-3", label: "User: How to pass authorization header to tools", type: "query", x: -36, y: 25, z: -13, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.91, preview: "Forwarding user session token into subagent tool fetch context", trace_id: "tr_agent_503" },
  { id: "q-agent-4", label: "User: Retry policy on rate limited API", type: "query", x: -32, y: 18, z: -21, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.88, preview: "Handling 429 Too Many Requests response with retry-after header", trace_id: "tr_agent_504" },

  // Cluster Agent - Gap (Red/Amber)
  { id: "gap-pt-agent-1", label: "Gap: Streaming Partial JSON Tool Calls", type: "gap", x: -44, y: 32, z: -26, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.44, preview: "High query volume asking for partial token JSON parsers during streaming", trace_id: "tr_gap_stream" },
  { id: "gap-pt-agent-2", label: "Gap: Multi-agent Tool Handoff Protocol", type: "gap", x: -42, y: 30, z: -24, cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", score: 0.49, preview: "No documentation for structured handoff between supervisor and specialist agents", trace_id: "tr_gap_handoff" },

  // Cluster RAG - Documents (Green)
  { id: "doc-rag-1", label: "Doc: Hybrid Dense + BM25 Search", type: "document", x: 19, y: -33, z: 11, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.95, preview: "Reciprocal Rank Fusion (RRF) combining dense vector embeddings and sparse lexical tokens", document_id: "doc_rag_hybrid" },
  { id: "doc-rag-2", label: "Doc: Sentence Window Chunking", type: "document", x: 16, y: -36, z: 14, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.92, preview: "Chunking documents by sentence with dynamic surrounding context expansion", document_id: "doc_rag_chunking" },
  { id: "doc-rag-3", label: "Doc: Cohere Reranker Integration", type: "document", x: 21, y: -32, z: 9, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.97, preview: "Cross-encoder scoring top 50 retrieved chunks down to top 5 candidates", document_id: "doc_rag_rerank" },
  { id: "doc-rag-4", label: "Doc: pgvector HNSW Index Tuning", type: "document", x: 17, y: -38, z: 15, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.93, preview: "Setting m=16, ef_construction=64 and ef_search=40 for optimal recall vs latency", document_id: "doc_rag_hnsw" },

  // Cluster RAG - Queries (Blue)
  { id: "q-rag-1", label: "User: Vector search returning irrelevant chunks", type: "query", x: 18, y: -31, z: 13, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.77, preview: "Cos similarity 0.68 retrieved snippet that does not answer specific user question", trace_id: "tr_rag_601" },
  { id: "q-rag-2", label: "User: Reranker latency spiking over 400ms", type: "query", x: 22, y: -35, z: 8, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.82, preview: "Cross-encoder p95 latency causes timeout on client chat completion stream", trace_id: "tr_rag_602" },
  { id: "q-rag-3", label: "User: Chunk overlap size recommendation", type: "query", x: 15, y: -35, z: 12, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.85, preview: "What overlap ratio is optimal for markdown code blocks vs plain text prose?", trace_id: "tr_rag_603" },
  { id: "q-rag-4", label: "User: HNSW index build out of memory", type: "query", x: 20, y: -40, z: 16, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.79, preview: "Postgres OOM when building HNSW index on 2M 1536-dim embeddings", trace_id: "tr_rag_604" },

  // Cluster RAG - Gap (Red/Amber)
  { id: "gap-pt-rag-1", label: "Gap: Multi-Tenant Vector Namespace Isolation", type: "gap", x: 29, y: -46, z: 22, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.38, preview: "No guidance on Row-Level Security vs schema-per-tenant for pgvector", trace_id: "tr_gap_tenant" },
  { id: "gap-pt-rag-2", label: "Gap: GraphRAG Entity Disambiguation", type: "gap", x: 26, y: -48, z: 20, cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", score: 0.41, preview: "Users querying relationship hops between knowledge graph nodes without doc coverage", trace_id: "tr_gap_graph" },

  // Cluster Safety - Documents (Green)
  { id: "doc-safe-1", label: "Doc: OWASP LLM 01 Prompt Injection Defenses", type: "document", x: -25, y: -27, z: -22, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.94, preview: "Delimiter separation, XML tag isolation, and untrusted input tagging rules", document_id: "doc_safe_owasp" },
  { id: "doc-safe-2", label: "Doc: Regex PII Redaction & Masking", type: "document", x: -28, y: -29, z: -25, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.96, preview: "Redacting credit cards, social security numbers, and API tokens before logging", document_id: "doc_safe_pii" },
  { id: "doc-safe-3", label: "Doc: Toxic Content Threshold Tuning", type: "document", x: -24, y: -24, z: -21, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.90, preview: "Calibrating toxicity classification threshold to balance precision and false positives", document_id: "doc_safe_toxic" },

  // Cluster Safety - Queries (Blue)
  { id: "q-safe-1", label: "User: System prompt leaked via translation jailbreak", type: "query", x: -26, y: -30, z: -26, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.65, preview: "Adversarial query asking to translate hidden instructions into French", trace_id: "tr_safe_701" },
  { id: "q-safe-2", label: "User: False positive PII redacting customer UUIDs", type: "query", x: -29, y: -26, z: -23, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.74, preview: "UUID v4 format triggering overly eager credit card redaction rule", trace_id: "tr_safe_702" },
  { id: "q-safe-3", label: "User: Jailbreak evasion using base64 payloads", type: "query", x: -23, y: -31, z: -27, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.58, preview: "Model decodes base64 instruction and ignores preceding safety system message", trace_id: "tr_safe_703" },

  // Cluster Safety - Gap (Red/Amber)
  { id: "gap-pt-safe-1", label: "Gap: Multilingual Adversarial Jailbreak Defenses", type: "gap", x: -35, y: -38, z: -32, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.35, preview: "Significant cluster of Portuguese/Spanish adversarial jailbreaks undetected by english guardrail", trace_id: "tr_gap_jailbreak" },
  { id: "gap-pt-safe-2", label: "Gap: Indirect Prompt Injection via RAG Webpages", type: "gap", x: -33, y: -36, z: -30, cluster_id: "cluster-safety", cluster_name: "Guardrails & Evals", score: 0.40, preview: "Untrusted web scraped content instructing LLM to exfiltrate session data", trace_id: "tr_gap_indirect" },
];

export const MOCK_DRIFT_METRICS: DriftMetrics = {
  jensen_shannon: {
    name: "Jensen-Shannon Divergence",
    value: 0.142,
    threshold_warning: 0.1,
    threshold_critical: 0.2,
    status: "warning",
    description: "Mede a dissimilaridade simétrica entre a distribuição de embeddings da baseline e da produção recente.",
  },
  psi: {
    name: "Population Stability Index (PSI)",
    value: 0.185,
    threshold_warning: 0.1,
    threshold_critical: 0.25,
    status: "warning",
    description: "Deslocamento de densidade populacional entre os clusters de queries nas últimas 24h vs baseline histórica.",
  },
  centroid_cosine: {
    name: "Centroid Cosine Drift",
    value: 0.088,
    threshold_warning: 0.08,
    threshold_critical: 0.15,
    status: "warning",
    description: "Distância cosseno (1 - cos_sim) entre o centróide da base documental e o centróide das queries recebidas.",
  },
  wasserstein: {
    name: "Wasserstein Distance (EMD)",
    value: 0.076,
    threshold_warning: 0.07,
    threshold_critical: 0.14,
    status: "warning",
    description: "Earth Mover's Distance calculada sobre as projeções latentes de queries.",
  },
  overall_status: "warning",
  baseline_period: "2026-11-01 a 2026-11-07",
  target_period: "2026-11-08 a 2026-11-10 (últimas 48h)",
  last_calculated: "2026-11-10T13:30:00Z",
  total_queries_analyzed: 4280,
};

export const MOCK_DRIFT_RESPONSE: VectorDriftResponse = {
  metrics: MOCK_DRIFT_METRICS,
  status: "warning",
  clusters_drifting: 2,
  top_drifting_clusters: [
    { cluster_id: "cluster-safety", cluster_name: "Guardrails & Safety Evals", drift_score: 0.218, status: "critical" },
    { cluster_id: "cluster-rag", cluster_name: "RAG & Knowledge Retrieval", drift_score: 0.144, status: "warning" },
    { cluster_id: "cluster-auth", cluster_name: "Authentication & Billing", drift_score: 0.062, status: "healthy" },
    { cluster_id: "cluster-agent", cluster_name: "Agent Tool Calling & Execution", drift_score: 0.048, status: "healthy" },
  ],
  timeline: [
    { timestamp: "2026-11-04T00:00:00Z", jensen_shannon: 0.042, psi: 0.055, centroid_cosine: 0.031 },
    { timestamp: "2026-11-05T00:00:00Z", jensen_shannon: 0.048, psi: 0.061, centroid_cosine: 0.034 },
    { timestamp: "2026-11-06T00:00:00Z", jensen_shannon: 0.065, psi: 0.082, centroid_cosine: 0.045 },
    { timestamp: "2026-11-07T00:00:00Z", jensen_shannon: 0.078, psi: 0.095, centroid_cosine: 0.052 },
    { timestamp: "2026-11-08T00:00:00Z", jensen_shannon: 0.105, psi: 0.124, centroid_cosine: 0.068 },
    { timestamp: "2026-11-09T00:00:00Z", jensen_shannon: 0.128, psi: 0.162, centroid_cosine: 0.079 },
    { timestamp: "2026-11-10T13:30:00Z", jensen_shannon: 0.142, psi: 0.185, centroid_cosine: 0.088 },
  ],
};

export function mockVectorProjection(params?: Record<string, string | number | undefined>): VectorProjectionResponse {
  let points = [...MOCK_VECTOR_POINTS];
  if (params?.cluster && params.cluster !== "all") {
    points = points.filter((p) => p.cluster_id === params.cluster);
  }
  if (params?.type && params.type !== "all") {
    points = points.filter((p) => p.type === params.type);
  }

  const dimensions = params?.dimensions === "3" || params?.dimensions === 3 ? 3 : 2;

  return {
    points,
    clusters: MOCK_VECTOR_CLUSTERS,
    gaps: MOCK_KNOWLEDGE_GAPS,
    dimensions,
    total_points: points.length,
    reduction_method: "umap",
  };
}

export function mockVectorDrift(_params?: Record<string, string | number | undefined>): VectorDriftResponse {
  return MOCK_DRIFT_RESPONSE;
}
