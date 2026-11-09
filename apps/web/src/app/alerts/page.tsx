import Link from "next/link";
import { apiGet } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { Alert, ListResponse, Rule } from "@/lib/types";
import { EmptyState, Mono, PageHeader, td, th } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function AlertsPage({ searchParams }: { searchParams: Promise<{ cursor?: string }> }) {
  const sp = await searchParams;
  const [alerts, rules] = await Promise.all([
    apiGet<ListResponse<Alert>>("/api/v1/alerts", { cursor: sp.cursor, limit: 50 }),
    apiGet<{ items: Rule[] }>("/api/v1/rules"),
  ]);
  const ruleName = (id: string | null) => (id ? (rules.items.find((r) => r.id === id)?.name ?? "deleted rule") : null);

  return (
    <div>
      <PageHeader title="Alerts" subtitle="Raised by rules when a trace matches. Most recent first" />
      {alerts.items.length === 0 ? (
        <EmptyState title="No alerts">
          Add a rule with the “Raise alert” action on the <Link href="/annotations" className="text-emerald-300 hover:underline">Annotations</Link> page.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[800px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Time (UTC)</th>
                <th className={th}>Message</th>
                <th className={th}>Rule</th>
                <th className={th}>Trace</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {alerts.items.map((a) => (
                <tr key={a.id} className="hover:bg-zinc-900/70">
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(a.created_at)}</Mono>
                  </td>
                  <td className="px-3 py-1.5 align-middle">
                    <span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-amber-400 align-middle" aria-hidden />
                    {a.message}
                  </td>
                  <td className={`${td} text-xs text-zinc-400`}>{ruleName(a.rule_id) ?? <span className="text-zinc-600">—</span>}</td>
                  <td className={td}>
                    <Link href={`/traces/${encodeURIComponent(a.trace_id)}`} className="font-mono text-emerald-300 hover:underline">
                      {a.trace_id.slice(0, 12)}…
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(sp.cursor || alerts.next_cursor) && (
        <nav aria-label="Pagination" className="mt-3 flex gap-3 text-xs">
          {sp.cursor && (
            <Link href="/alerts" className="text-zinc-400 hover:text-zinc-100">
              ← First page
            </Link>
          )}
          {alerts.next_cursor && (
            <Link href={`/alerts?cursor=${encodeURIComponent(alerts.next_cursor)}`} className="text-emerald-300 hover:underline">
              Older →
            </Link>
          )}
        </nav>
      )}
    </div>
  );
}
