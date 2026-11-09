import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import { formatClock, formatCost, formatDuration, formatTimestamp, formatTokens } from "@/lib/format";
import type { SessionDetail } from "@/lib/types";
import { EmptyState, Mono, ScoreChips, StatusBadge } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function SessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  let detail: SessionDetail;
  try {
    detail = await apiGet<SessionDetail>(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
  } catch (e) {
    if (isNotFound(e)) notFound();
    throw e;
  }
  const { session, traces } = detail;
  const turns = [...traces].sort((a, b) => a.start_time.localeCompare(b.start_time));

  return (
    <div className="max-w-4xl">
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/sessions" className="hover:text-zinc-200">
          Sessions
        </Link>{" "}
        / <Mono>{session.session_id}</Mono>
      </nav>
      <header className="mb-5 flex flex-wrap items-baseline gap-x-6 gap-y-1 border-b border-zinc-800 pb-3">
        <h1 className="font-mono text-base font-semibold text-zinc-50">{session.session_id}</h1>
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          {[
            ["User", session.user_id ?? "—"],
            ["Turns", String(session.trace_count)],
            ["Cost", formatCost(session.total_cost_usd)],
            ["First", formatTimestamp(session.first_seen)],
            ["Last", formatTimestamp(session.last_seen)],
          ].map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-1.5">
              <dt className="text-zinc-500">{k}</dt>
              <dd className="font-mono tabular-nums text-zinc-200">{v}</dd>
            </div>
          ))}
        </dl>
      </header>

      {turns.length === 0 ? (
        <EmptyState title="No traces in this session" />
      ) : (
        <ol aria-label="Conversation" className="relative space-y-5 border-l border-zinc-800 pl-5">
          {turns.map((t, i) => (
            <li key={t.trace_id} className="relative">
              <span
                aria-hidden
                className={`absolute -left-[25px] top-1 h-2 w-2 rounded-full ring-4 ring-zinc-950 ${
                  t.status === "error" ? "bg-red-400" : "bg-emerald-400"
                }`}
              />
              <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
                <span className="font-medium text-zinc-300">Turn {i + 1}</span>
                <Mono>{formatClock(t.start_time)}</Mono>
                <StatusBadge status={t.status} />
                <Mono>{formatDuration(t.duration_ms)}</Mono>
                <Mono>
                  {formatTokens(t.input_tokens)} / {formatTokens(t.output_tokens)} tok
                </Mono>
                <Mono>{formatCost(t.total_cost_usd)}</Mono>
                <ScoreChips scores={t.scores} max={3} />
                <Link
                  href={`/traces/${encodeURIComponent(t.trace_id)}`}
                  className="ml-auto text-emerald-300 hover:underline"
                >
                  Open trace →
                </Link>
              </div>
              <div className="space-y-1.5">
                <div className="rounded-md border-l-2 border-sky-500/70 bg-sky-950/20 px-3 py-2">
                  <div className="mb-0.5 text-[10px] uppercase tracking-wider text-zinc-500">user</div>
                  <p className="whitespace-pre-wrap break-words text-zinc-100">
                    {t.input_preview ?? <span className="text-zinc-500">(no input captured)</span>}
                  </p>
                </div>
                <div
                  className={`rounded-md border-l-2 px-3 py-2 ${
                    t.status === "error" && !t.output_preview
                      ? "border-red-500/70 bg-red-950/20"
                      : "border-emerald-500/70 bg-emerald-950/20"
                  }`}
                >
                  <div className="mb-0.5 text-[10px] uppercase tracking-wider text-zinc-500">{t.name}</div>
                  <p className="whitespace-pre-wrap break-words text-zinc-200">
                    {t.output_preview ?? (
                      <span className={t.status === "error" ? "text-red-300" : "text-zinc-500"}>
                        {t.status === "error" ? "Failed without output — open the trace for details." : "(no output captured)"}
                      </span>
                    )}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
