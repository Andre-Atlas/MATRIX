// In-memory implementation of the API, used only when MATRIX_MOCK=1.
import { ApiError } from "./api-error";
import { authGet, authSend, mockGate, NO_CONTENT, type MockCtx } from "./mock-auth";
import { mockDb } from "./mock-data";
import { evalExportJsonl, evalGet, evalSend } from "./mock-evals";
import { dpoExportJsonl, opsGet, opsSend } from "./mock-ops";
import type { ListResponse, OverviewStats, Score, SessionDetail, TraceDetail, TraceSummary } from "./types";

type Params = Record<string, string | number | undefined>;

function page<T>(items: T[], params: Params): ListResponse<T> {
  const limit = Math.min(500, Math.max(1, Number(params.limit ?? 50)));
  const offset = Number(params.cursor ?? 0) || 0;
  const slice = items.slice(offset, offset + limit);
  return { items: slice, next_cursor: offset + limit < items.length ? String(offset + limit) : null };
}

function percentile(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[idx];
}

function sinceMs(params: Params): number {
  const t = params.since ? Date.parse(String(params.since)) : NaN;
  return Number.isNaN(t) ? -Infinity : t;
}

export type { MockCtx };
export { NO_CONTENT };

/** `ctx` is the session of the caller (token and active project). The web server always passes it. */
export async function mockGet<T>(path: string, params: Params = {}, ctx?: MockCtx): Promise<T> {
  const db = mockDb();
  const p = path.replace(/\/+$/, "");
  let m: RegExpExecArray | null;

  if (ctx) {
    mockGate("GET", p, ctx);
    const authed = authGet(ctx, p, params);
    if (authed !== undefined) return authed as T;
  }

  if (p === "/api/v1/stats/overview") {
    const from = sinceMs(params);
    const traces = db.traces.filter((t) => Date.parse(t.summary.start_time) >= from);
    const lat = traces.map((t) => t.summary.duration_ms).sort((a, b) => a - b);
    const byModel = new Map<string, { model: string; calls: number; cost_usd: number }>();
    for (const t of traces) {
      for (const s of t.spans) {
        if (!s.model) continue;
        const e = byModel.get(s.model) ?? { model: s.model, calls: 0, cost_usd: 0 };
        e.calls += 1;
        e.cost_usd += s.cost_usd;
        byModel.set(s.model, e);
      }
    }
    const stats: OverviewStats = {
      trace_count: traces.length,
      error_rate: traces.length ? traces.filter((t) => t.summary.status === "error").length / traces.length : 0,
      p50_latency_ms: percentile(lat, 0.5),
      p95_latency_ms: percentile(lat, 0.95),
      total_cost_usd: traces.reduce((a, t) => a + t.summary.total_cost_usd, 0),
      input_tokens: traces.reduce((a, t) => a + t.summary.input_tokens, 0),
      output_tokens: traces.reduce((a, t) => a + t.summary.output_tokens, 0),
      by_model: [...byModel.values()].sort((a, b) => b.cost_usd - a.cost_usd),
    };
    return stats as T;
  }

  if (p === "/api/v1/traces") {
    const from = sinceMs(params);
    const name = params.name ? String(params.name).toLowerCase() : null;
    const items: TraceSummary[] = db.traces
      .map((t) => t.summary)
      .filter(
        (t) =>
          (!name || t.name.toLowerCase().includes(name)) &&
          (!params.status || t.status === params.status) &&
          (!params.session_id || t.session_id === params.session_id) &&
          (!params.user_id || t.user_id === params.user_id) &&
          (!params.kind || t.root_kind === params.kind) &&
          Date.parse(t.start_time) >= from,
      );
    return page(items, params) as T;
  }

  if ((m = /^\/api\/v1\/traces\/([^/]+)\/scores$/.exec(p))) {
    const t = db.traces.find((x) => x.summary.trace_id === m![1]);
    if (!t) throw new ApiError(404, "trace not found");
    const scores: Score[] = Object.entries(t.summary.scores).map(([name, value], i) => ({
      id: `${t.summary.trace_id.slice(0, 8)}-${i}`,
      trace_id: t.summary.trace_id,
      span_id: null,
      name,
      value,
      label: null,
      comment: null,
      source: name === "helpfulness" ? "human" : "llm",
      created_at: t.summary.end_time,
    }));
    return scores as T;
  }

  if ((m = /^\/api\/v1\/traces\/([^/]+)$/.exec(p))) {
    const t = db.traces.find((x) => x.summary.trace_id === m![1]);
    if (!t) throw new ApiError(404, "trace not found");
    const detail: TraceDetail = { trace: t.summary, spans: t.spans };
    return detail as T;
  }

  if (p === "/api/v1/sessions") return page(db.sessions, params) as T;

  if ((m = /^\/api\/v1\/sessions\/([^/]+)$/.exec(p))) {
    const s = db.sessions.find((x) => x.session_id === m![1]);
    if (!s) throw new ApiError(404, "session not found");
    const traces = db.traces
      .filter((t) => t.summary.session_id === s.session_id)
      .map((t) => t.summary)
      .sort((a, b) => a.start_time.localeCompare(b.start_time));
    const { items, next_cursor } = page(traces, params);
    const detail: SessionDetail = { session: s, traces: items, next_cursor };
    return detail as T;
  }

  const evalResult = evalGet(p);
  if (evalResult !== undefined) return evalResult as T;

  const opsResult = opsGet(p, params);
  if (opsResult !== undefined) return opsResult as T;

  throw new ApiError(404, `mock: no handler for GET ${path}`);
}

export async function mockSend<T>(method: string, path: string, body?: unknown, ctx?: MockCtx): Promise<T> {
  const m = method.toUpperCase();
  const p = path.replace(/\/+$/, "");
  if (ctx) {
    mockGate(m, p, ctx);
    const authed = authSend(ctx, m, p, body);
    if (authed !== undefined) return authed as T;
  }
  const out = evalSend(m, p, body) ?? opsSend(m, p, body);
  if (out === undefined) throw new ApiError(404, `mock: no handler for ${method} ${path}`);
  return out as T;
}

/** Backs /api/proxy/* in mock mode: same status codes and bodies as the real API would return. */
export async function mockFetch(
  method: string,
  path: string,
  params: Params,
  rawBody: string | null,
  ctx?: MockCtx,
): Promise<Response> {
  try {
    if (method === "GET") {
      if (ctx) mockGate("GET", path.replace(/\/+$/, ""), ctx);
      const text = evalExportJsonl(path) ?? dpoExportJsonl(path);
      if (text !== undefined) {
        return new Response(text, {
          headers: {
            "content-type": "application/x-ndjson",
            "content-disposition": `attachment; filename="${path.endsWith("dpo.jsonl") ? "dpo" : path.split("/").at(-2)}.jsonl"`,
          },
        });
      }
      return Response.json(await mockGet(path, params, ctx));
    }
    let body: unknown;
    try {
      body = rawBody ? JSON.parse(rawBody) : undefined;
    } catch {
      return Response.json({ detail: "invalid JSON body" }, { status: 400 });
    }
    const out = await mockSend(method, path, body, ctx);
    if (out === NO_CONTENT) return new Response(null, { status: 204 });
    const login = path.replace(/\/+$/, "") === "/api/v1/auth/login";
    return Response.json(out, { status: method === "POST" && !login ? 201 : 200 });
  } catch (e) {
    if (e instanceof ApiError) {
      const isJson = e.body.startsWith("{");
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (e.retryAfter !== undefined) headers["retry-after"] = String(e.retryAfter);
      if (e.status === 401) headers["www-authenticate"] = "Bearer";
      return new Response(isJson ? e.body : JSON.stringify({ detail: e.body }), { status: e.status, headers });
    }
    throw e;
  }
}
