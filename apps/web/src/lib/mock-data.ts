// Development/screenshot fixtures. Only loaded when MATRIX_MOCK=1 (see lib/api.ts).
import type { Document, Media, Message, SessionSummary, Span, SpanKind, TraceSummary } from "./types";

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
