import Link from "next/link";
import { apiGet } from "@/lib/api";
import { formatCost, formatDuration, formatTimestamp } from "@/lib/format";
import type { ListResponse, SessionSummary } from "@/lib/types";
import { EmptyState, Mono, PageHeader, td, th } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function SessionsPage({ searchParams }: { searchParams: Promise<{ cursor?: string }> }) {
  const sp = await searchParams;
  const data = await apiGet<ListResponse<SessionSummary>>("/api/v1/sessions", { cursor: sp.cursor, limit: 50 });

  return (
    <div className="max-w-6xl">
      <PageHeader title="Sessions" subtitle="Multi-turn conversations grouped by session.id" />
      {data.items.length === 0 ? (
        <EmptyState title="No sessions yet">
          Set the <code className="font-mono text-zinc-300">session.id</code> attribute on your root spans to group
          traces into conversations.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Session</th>
                <th className={th}>User</th>
                <th className={`${th} text-right`}>Traces</th>
                <th className={th}>First seen (UTC)</th>
                <th className={th}>Last seen (UTC)</th>
                <th className={`${th} text-right`}>Duration</th>
                <th className={`${th} text-right`}>Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {data.items.map((s) => (
                <tr key={s.session_id} className="relative hover:bg-zinc-900/70 focus-within:bg-zinc-900/70">
                  <td className={td}>
                    <Link
                      href={`/sessions/${encodeURIComponent(s.session_id)}`}
                      className="font-mono text-zinc-100 after:absolute after:inset-0 after:content-['']"
                    >
                      {s.session_id}
                    </Link>
                  </td>
                  <td className={td}>
                    <Mono className="text-zinc-400">{s.user_id ?? "—"}</Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono>{s.trace_count}</Mono>
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(s.first_seen)}</Mono>
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(s.last_seen)}</Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono className="text-zinc-400">
                      {formatDuration(Date.parse(s.last_seen) - Date.parse(s.first_seen))}
                    </Mono>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono>{formatCost(s.total_cost_usd)}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.next_cursor && (
        <nav aria-label="Pagination" className="mt-3 flex justify-end">
          <Link
            href={`/sessions?cursor=${encodeURIComponent(data.next_cursor)}`}
            className="rounded border border-zinc-700 px-3 py-1 text-zinc-200 hover:bg-zinc-800"
          >
            Next →
          </Link>
        </nav>
      )}
    </div>
  );
}
