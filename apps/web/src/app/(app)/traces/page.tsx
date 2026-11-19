import Link from "next/link";
import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { formatCost, formatDuration, formatTimestamp, formatTokens, truncate } from "@/lib/format";
import { SPAN_KINDS, type ListResponse, type TraceSummary } from "@/lib/types";
import { EmptyState, KindBadge, Mono, PageHeader, ScoreChips, StatusBadge, TableSkeleton, td, th } from "@/components/ui";

export const dynamic = "force-dynamic";

type SP = { name?: string; status?: string; session_id?: string; kind?: string; cursor?: string };

const FILTER_KEYS = ["name", "status", "session_id", "kind"] as const;

function qs(sp: SP, extra: Record<string, string | undefined> = {}): string {
  const p = new URLSearchParams();
  for (const k of FILTER_KEYS) if (sp[k]) p.set(k, sp[k]!);
  for (const [k, v] of Object.entries(extra)) if (v) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
}

const input =
  "h-7 rounded border border-zinc-700 bg-zinc-900 px-2 text-zinc-100 placeholder:text-zinc-600 hover:border-zinc-600";

async function TraceList({ sp }: { sp: SP }) {
  const data = await apiGet<ListResponse<TraceSummary>>("/api/v1/traces", {
    name: sp.name,
    status: sp.status,
    session_id: sp.session_id,
    kind: sp.kind,
    cursor: sp.cursor,
    limit: 50,
  });
  const filtered = FILTER_KEYS.some((k) => sp[k]);

  return (
    <>
      {data.items.length === 0 ? (
        <EmptyState title={filtered ? "No traces match these filters" : "No traces yet"}>
          {filtered
            ? "Try widening the filters or clearing them."
            : "Send spans to POST /v1/traces with your project key (MATRIX SDK or any OTLP exporter)."}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[1080px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Kind</th>
                <th className={th}>Status</th>
                <th className={th}>Start (UTC)</th>
                <th className={`${th} text-right`}>Duration</th>
                <th className={`${th} text-right`}>Tokens in / out</th>
                <th className={`${th} text-right`}>Cost</th>
                <th className={th}>Session</th>
                <th className={th}>Scores</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {data.items.map((t) => (
                <tr key={t.trace_id} className="relative hover:bg-zinc-900/70 focus-within:bg-zinc-900/70">
                  <td className={`${td} max-w-[15rem]`}>
                    <Link
                      href={`/traces/${encodeURIComponent(t.trace_id)}`}
                      className="font-medium text-zinc-100 after:absolute after:inset-0 after:content-['']"
                    >
                      {t.name}
                    </Link>
                    {t.input_preview && (
                      <div className="truncate text-xs text-zinc-500" title={t.input_preview}>
                        {truncate(t.input_preview, 64)}
                      </div>
                    )}
                  </td>
                  <td className={td}>
                    <KindBadge kind={t.root_kind} compact />
                  </td>
                  <td className={td}>
                    <StatusBadge status={t.status} />
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(t.start_time)}</Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono>{formatDuration(t.duration_ms)}</Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono className="text-zinc-300">
                      {formatTokens(t.input_tokens)} <span className="text-zinc-600">/</span>{" "}
                      {formatTokens(t.output_tokens)}
                    </Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono>{formatCost(t.total_cost_usd)}</Mono>
                  </td>
                  <td className={td}>
                    {t.session_id ? (
                      <Link
                        href={`/sessions/${encodeURIComponent(t.session_id)}`}
                        className="relative z-10 font-mono text-xs text-zinc-400 hover:text-emerald-300"
                        title={t.session_id}
                      >
                        {truncate(t.session_id, 18)}
                      </Link>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className={td}>
                    <ScoreChips scores={t.scores} max={2} labelWidth="max-w-[4.75rem]" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <nav aria-label="Pagination" className="mt-3 flex items-center justify-end gap-3">
        {sp.cursor && (
          <Link href={`/traces${qs(sp)}`} className="text-zinc-400 hover:text-zinc-100">
            ← First page
          </Link>
        )}
        {data.next_cursor && (
          <Link
            href={`/traces${qs(sp, { cursor: data.next_cursor })}`}
            className="rounded border border-zinc-700 px-3 py-1 text-zinc-200 hover:bg-zinc-800"
          >
            Next →
          </Link>
        )}
      </nav>
    </>
  );
}

export default async function TracesPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const filtered = FILTER_KEYS.some((k) => sp[k]);

  return (
    <div>
      <PageHeader title="Traces" subtitle="Most recent first" />

      <form method="get" role="search" aria-label="Filter traces" className="mb-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          Name
          <input name="name" defaultValue={sp.name} placeholder="support-agent" className={`${input} w-44`} />
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          Status
          <select name="status" defaultValue={sp.status ?? ""} className={input}>
            <option value="">any</option>
            <option value="ok">ok</option>
            <option value="error">error</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          Root kind
          <select name="kind" defaultValue={sp.kind ?? ""} className={input}>
            <option value="">any</option>
            {SPAN_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          Session
          <input
            name="session_id"
            defaultValue={sp.session_id}
            placeholder="session id"
            className={`${input} w-48 font-mono`}
          />
        </label>
        <button
          type="submit"
          className="h-7 rounded border border-emerald-700/70 bg-emerald-900/40 px-3 text-emerald-200 hover:bg-emerald-900/70"
        >
          Apply
        </button>
        {filtered && (
          <Link href="/traces" className="h-7 px-2 leading-7 text-zinc-400 hover:text-zinc-100">
            Clear
          </Link>
        )}
      </form>

      <Suspense key={qs(sp, { cursor: sp.cursor })} fallback={<TableSkeleton />}>
        <TraceList sp={sp} />
      </Suspense>
    </div>
  );
}
