import Link from "next/link";
import { apiGet } from "@/lib/api";
import { formatTimestamp, truncate } from "@/lib/format";
import type { AnnotationListItem, Dataset, ListResponse, OnlineEval, Rule } from "@/lib/types";
import { EmptyState, Mono, PageHeader, Segmented, td, th } from "@/components/ui";
import { OnlineEvalsSection } from "@/components/annotations/online-evals-section";
import { RulesSection } from "@/components/annotations/rules-section";
import { secondaryBtn } from "@/components/evals/ui";

export const dynamic = "force-dynamic";

type SP = { status?: string; cursor?: string };

export default async function AnnotationsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const status = sp.status === "done" ? "done" : "pending";
  const [queue, rules, evals, datasets] = await Promise.all([
    apiGet<ListResponse<AnnotationListItem>>("/api/v1/annotations", { status, cursor: sp.cursor, limit: 25 }),
    apiGet<{ items: Rule[] }>("/api/v1/rules"),
    apiGet<{ items: OnlineEval[] }>("/api/v1/online-evals"),
    apiGet<ListResponse<Dataset>>("/api/v1/datasets"),
  ]);

  return (
    <div>
      <PageHeader
        title="Annotations"
        subtitle="Human review queue. Corrections become DPO pairs and regression cases"
        actions={
          <a href="/api/proxy/exports/dpo.jsonl" download="dpo.jsonl" className={`${secondaryBtn} inline-flex items-center`}>
            Download DPO JSONL
          </a>
        }
      />
      <div className="mb-3 flex items-center gap-3">
        <Segmented
          label="Queue status"
          options={[
            { href: "/annotations", label: "pending", active: status === "pending" },
            { href: "/annotations?status=done", label: "done", active: status === "done" },
          ]}
        />
        <Link href="/alerts" className="text-xs text-zinc-400 hover:text-zinc-100">
          View alerts →
        </Link>
      </div>

      {queue.items.length === 0 ? (
        <EmptyState title={status === "pending" ? "Queue is empty" : "Nothing reviewed yet"}>
          {status === "pending"
            ? "Create a rule below to route bad traces here, or use “Annotate” on any trace."
            : "Completed reviews appear here."}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[960px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Reason</th>
                <th className={th}>Trace</th>
                <th className={th}>Input</th>
                <th className={th}>Output</th>
                <th className={th}>{status === "done" ? "Completed (UTC)" : "Created (UTC)"}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {queue.items.map((a) => (
                <tr key={a.id} className="relative hover:bg-zinc-900/70 focus-within:bg-zinc-900/70">
                  <td className={td}>
                    <Link
                      href={`/annotations/${encodeURIComponent(a.id)}`}
                      className="rounded border border-zinc-700 bg-zinc-900 px-1.5 text-xs leading-5 text-zinc-200 after:absolute after:inset-0 after:content-['']"
                    >
                      {a.reason ?? "no reason"}
                    </Link>
                  </td>
                  <td className={td}>
                    <span className="font-medium text-zinc-100">{a.trace_name ?? <span className="text-zinc-500">trace unavailable</span>}</span>
                    <Mono className="ml-2 text-[11px] text-zinc-600">{a.trace_id.slice(0, 8)}</Mono>
                  </td>
                  <td className={`${td} max-w-[18rem] truncate text-zinc-300`} title={a.input_preview ?? undefined}>
                    {a.input_preview ? truncate(a.input_preview, 90) : <span className="text-zinc-600">—</span>}
                  </td>
                  <td className={`${td} max-w-[18rem] truncate text-zinc-400`} title={a.output_preview ?? undefined}>
                    {a.output_preview ? truncate(a.output_preview, 90) : <span className="text-zinc-600">—</span>}
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp((status === "done" ? a.completed_at : null) ?? a.created_at)}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(sp.cursor || queue.next_cursor) && (
        <nav aria-label="Pagination" className="mt-3 flex gap-3 text-xs">
          {sp.cursor && (
            <Link href={status === "done" ? "/annotations?status=done" : "/annotations"} className="text-zinc-400 hover:text-zinc-100">
              ← First page
            </Link>
          )}
          {queue.next_cursor && (
            <Link
              href={`/annotations?${new URLSearchParams({ ...(status === "done" ? { status } : {}), cursor: queue.next_cursor }).toString()}`}
              className="text-emerald-300 hover:underline"
            >
              Older →
            </Link>
          )}
        </nav>
      )}

      <RulesSection rules={rules.items} datasets={datasets.items} />
      <OnlineEvalsSection evals={evals.items} />
    </div>
  );
}
