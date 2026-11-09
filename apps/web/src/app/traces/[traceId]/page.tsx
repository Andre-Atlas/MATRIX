import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import { formatCost, formatDuration, formatTimestamp, formatTokens } from "@/lib/format";
import { agentGraph, traceBounds, waterfallRows } from "@/lib/trace-tree";
import type { TraceDetail } from "@/lib/types";
import { EmptyState, KindBadge, Mono, ScoreChips, StatusBadge } from "@/components/ui";
import { AnnotateButton } from "@/components/annotations/annotate-button";
import { AgentGraph } from "@/components/trace/agent-graph";
import { SpanPanel } from "@/components/trace/span-panel";
import { Waterfall } from "@/components/trace/waterfall";

export const dynamic = "force-dynamic";

type Tab = "waterfall" | "graph";

export default async function TracePage({
  params,
  searchParams,
}: {
  params: Promise<{ traceId: string }>;
  searchParams: Promise<{ span?: string; tab?: string }>;
}) {
  const { traceId } = await params;
  const sp = await searchParams;
  let detail: TraceDetail;
  try {
    detail = await apiGet<TraceDetail>(`/api/v1/traces/${encodeURIComponent(traceId)}`);
  } catch (e) {
    if (isNotFound(e)) notFound();
    throw e;
  }
  const { trace, spans } = detail;
  const tab: Tab = sp.tab === "graph" ? "graph" : "waterfall";
  const rows = waterfallRows(spans);
  const bounds = traceBounds(spans);
  const selected = spans.find((s) => s.span_id === sp.span) ?? rows[0]?.span ?? null;
  const base = `/traces/${encodeURIComponent(traceId)}`;
  const href = (t: Tab, spanId: string | null) => {
    const p = new URLSearchParams();
    if (t !== "waterfall") p.set("tab", t);
    if (spanId) p.set("span", spanId);
    const s = p.toString();
    return s ? `${base}?${s}` : base;
  };
  const errors = spans.filter((s) => s.status === "error").length;

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/traces" className="hover:text-zinc-200">
          Traces
        </Link>{" "}
        / <Mono>{traceId.slice(0, 12)}…</Mono>
      </nav>
      <header className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-zinc-800 pb-3">
        <div className="flex min-w-0 items-center gap-2">
          <KindBadge kind={trace.root_kind} />
          <h1 className="truncate text-base font-semibold text-zinc-50">{trace.name}</h1>
          <StatusBadge status={trace.status} />
        </div>
        <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          {[
            ["Duration", formatDuration(trace.duration_ms)],
            ["Cost", formatCost(trace.total_cost_usd)],
            ["Tokens", `${formatTokens(trace.input_tokens)} in · ${formatTokens(trace.output_tokens)} out`],
            ["Spans", `${trace.span_count}${errors ? ` · ${errors} err` : ""}`],
            ["Start", formatTimestamp(trace.start_time)],
          ].map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-1.5">
              <dt className="text-zinc-500">{k}</dt>
              <dd className={`font-mono tabular-nums ${k === "Spans" && errors ? "text-red-300" : "text-zinc-200"}`}>{v}</dd>
            </div>
          ))}
          {trace.session_id && (
            <div className="flex items-baseline gap-1.5">
              <dt className="text-zinc-500">Session</dt>
              <dd>
                <Link
                  href={`/sessions/${encodeURIComponent(trace.session_id)}`}
                  className="font-mono text-emerald-300 hover:underline"
                >
                  {trace.session_id}
                </Link>
              </dd>
            </div>
          )}
        </dl>
        <div className="ml-auto flex items-center gap-3">
          <ScoreChips scores={trace.scores} max={6} />
          <AnnotateButton traceId={trace.trace_id} />
        </div>
      </header>

      {spans.length === 0 ? (
        <EmptyState title="This trace has no spans yet">Spans may still be arriving; refresh in a moment.</EmptyState>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(400px,38%)]">
          <section aria-label="Trace views" className="min-w-0">
            <nav aria-label="Trace views" className="mb-3 flex gap-1 border-b border-zinc-800">
              {(["waterfall", "graph"] as Tab[]).map((t) => (
                <Link
                  key={t}
                  href={href(t, selected?.span_id ?? null)}
                  scroll={false}
                  replace
                  aria-current={tab === t ? "page" : undefined}
                  className={`-mb-px border-b-2 px-3 py-1.5 capitalize ${
                    tab === t ? "border-emerald-400 text-zinc-50" : "border-transparent text-zinc-400 hover:text-zinc-100"
                  }`}
                >
                  {t}
                </Link>
              ))}
            </nav>
            {tab === "waterfall" ? (
              <Waterfall
                rows={rows}
                totalMs={bounds.end - bounds.start}
                selectedId={selected?.span_id ?? null}
                hrefFor={(id) => href("waterfall", id)}
              />
            ) : (
              <AgentGraph {...agentGraph(spans)} selectedSpanId={selected?.span_id ?? null} />
            )}
          </section>
          <aside
            aria-label="Span detail"
            className="min-w-0 self-start rounded-md border border-zinc-800 bg-zinc-900/40 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto"
          >
            {selected ? (
              <SpanPanel key={selected.span_id} span={selected} traceStart={bounds.start} />
            ) : (
              <p className="p-4 text-zinc-500">Select a span.</p>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
