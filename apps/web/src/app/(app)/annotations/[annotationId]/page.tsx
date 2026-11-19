import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import { originalOutput, rootSpan } from "@/lib/annotation-form";
import { formatCost, formatDuration, formatTimestamp } from "@/lib/format";
import { traceBounds, waterfallRows } from "@/lib/trace-tree";
import type { AnnotationListItem, Dataset, ListResponse, TraceDetail } from "@/lib/types";
import { EmptyState, KindBadge, Mono, ScoreChips, StatusBadge } from "@/components/ui";
import { SpanPanel } from "@/components/trace/span-panel";
import { Waterfall } from "@/components/trace/waterfall";
import { ReviewForm } from "@/components/annotations/review-form";

export const dynamic = "force-dynamic";

const PAGE = 100;
const MAX_PAGES = 10;

/** The API has no GET /annotations/{id}; look the item up in the queue, pending first. */
async function findAnnotation(id: string): Promise<AnnotationListItem | null> {
  for (const status of ["pending", "done"] as const) {
    let cursor: string | undefined;
    for (let i = 0; i < MAX_PAGES; i++) {
      const res = await apiGet<ListResponse<AnnotationListItem>>("/api/v1/annotations", { status, limit: PAGE, cursor });
      const hit = res.items.find((a) => a.id === id);
      if (hit) return hit;
      if (!res.next_cursor) break;
      cursor = res.next_cursor;
    }
  }
  return null;
}

export default async function AnnotationPage({
  params,
  searchParams,
}: {
  params: Promise<{ annotationId: string }>;
  searchParams: Promise<{ span?: string }>;
}) {
  const { annotationId } = await params;
  const sp = await searchParams;
  const annotation = await findAnnotation(annotationId);
  if (!annotation) notFound();

  let detail: TraceDetail | null = null;
  try {
    detail = await apiGet<TraceDetail>(`/api/v1/traces/${encodeURIComponent(annotation.trace_id)}`);
  } catch (e) {
    if (!isNotFound(e)) throw e;
  }
  const datasets = annotation.status === "pending" ? (await apiGet<ListResponse<Dataset>>("/api/v1/datasets")).items : [];

  const base = `/annotations/${encodeURIComponent(annotationId)}`;
  const trace = detail?.trace;
  const spans = detail?.spans ?? [];
  const rows = waterfallRows(spans);
  const bounds = traceBounds(spans);
  const selected = spans.find((s) => s.span_id === sp.span) ?? rows[0]?.span ?? null;
  const original = originalOutput(rootSpan(spans));

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/annotations" className="hover:text-zinc-200">
          Annotations
        </Link>{" "}
        / <Mono>{annotationId}</Mono>
      </nav>
      <header className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-zinc-800 pb-3">
        <div className="flex min-w-0 items-center gap-2">
          {trace && <KindBadge kind={trace.root_kind} />}
          <h1 className="truncate text-base font-semibold text-zinc-50">{trace?.name ?? "Trace unavailable"}</h1>
          {trace && <StatusBadge status={trace.status} />}
        </div>
        <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <div className="flex items-baseline gap-1.5">
            <dt className="text-zinc-500">Reason</dt>
            <dd className="text-zinc-200">{annotation.reason ?? "—"}</dd>
          </div>
          <div className="flex items-baseline gap-1.5">
            <dt className="text-zinc-500">Queued</dt>
            <dd className="font-mono tabular-nums text-zinc-200">{formatTimestamp(annotation.created_at)}</dd>
          </div>
          {trace && (
            <>
              <div className="flex items-baseline gap-1.5">
                <dt className="text-zinc-500">Duration</dt>
                <dd className="font-mono tabular-nums text-zinc-200">{formatDuration(trace.duration_ms)}</dd>
              </div>
              <div className="flex items-baseline gap-1.5">
                <dt className="text-zinc-500">Cost</dt>
                <dd className="font-mono tabular-nums text-zinc-200">{formatCost(trace.total_cost_usd)}</dd>
              </div>
            </>
          )}
        </dl>
        <div className="ml-auto flex items-center gap-3">
          {trace && <ScoreChips scores={trace.scores} max={4} />}
          <Link href={`/traces/${encodeURIComponent(annotation.trace_id)}`} className="text-xs text-emerald-300 hover:underline">
            Open full trace →
          </Link>
        </div>
      </header>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section aria-label="Trace (read-only)" className="min-w-0 space-y-3">
          {spans.length === 0 ? (
            <EmptyState title="No spans to show">The trace is missing or still arriving. You can still record scores below.</EmptyState>
          ) : (
            <>
              <Waterfall
                rows={rows}
                totalMs={bounds.end - bounds.start}
                selectedId={selected?.span_id ?? null}
                hrefFor={(id) => `${base}?span=${id}`}
              />
              {selected && (
                <div className="rounded-md border border-zinc-800 bg-zinc-900/40">
                  <SpanPanel key={selected.span_id} span={selected} traceStart={bounds.start} readOnly />
                </div>
              )}
            </>
          )}
        </section>
        <aside aria-label="Annotation" className="min-w-0 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
          {annotation.status === "done" ? (
            <section className="rounded-md border border-zinc-800 bg-zinc-900/30 p-4">
              <h2 className="font-medium text-zinc-200">Already reviewed</h2>
              <p className="mt-1 text-zinc-400">
                Completed {annotation.completed_at ? <Mono>{formatTimestamp(annotation.completed_at)}</Mono> : null}. A completed annotation cannot be edited.
              </p>
              <Link href="/annotations" className="mt-3 inline-block text-emerald-300 hover:underline">
                Back to queue →
              </Link>
            </section>
          ) : (
            <ReviewForm annotationId={annotationId} original={original} datasets={datasets} />
          )}
        </aside>
      </div>
    </div>
  );
}
