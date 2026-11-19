import Link from "next/link";
import { Suspense } from "react";
import { apiGet } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { getActive } from "@/lib/me";
import { AUDIT_ACTIONS, roleAtLeast, type AuditEntry, type ProjectMember } from "@/lib/types";
import { EmptyState, Mono, TableSkeleton, td, th } from "@/components/ui";
import { Forbidden, Panel, inputCls, primaryBtn } from "@/components/settings/ui";

export const dynamic = "force-dynamic";

type SP = { action?: string; cursor?: string };

function tone(action: string): string {
  if (action.endsWith("_failed")) return "border-red-800/70 bg-red-950/50 text-red-300";
  if (/\.(revoke|remove|delete|host_remove)$/.test(action)) return "border-amber-800/60 bg-amber-950/40 text-amber-300";
  return "border-zinc-700 bg-zinc-900 text-zinc-300";
}

function detailText(d: Record<string, unknown>): string {
  const text = Object.entries(d)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
  return text.length > 140 ? `${text.slice(0, 139)}…` : text;
}

const qs = (action?: string, cursor?: string) => {
  const p = new URLSearchParams();
  if (action) p.set("action", action);
  if (cursor) p.set("cursor", cursor);
  const s = p.toString();
  return s ? `?${s}` : "";
};

async function AuditTable({ projectId, sp }: { projectId: string; sp: SP }) {
  const base = `/api/v1/projects/${encodeURIComponent(projectId)}`;
  const [log, members] = await Promise.all([
    apiGet<{ items: AuditEntry[]; next_cursor: string | null }>(`${base}/audit-log`, { action: sp.action, cursor: sp.cursor, limit: 25 }),
    apiGet<{ items: ProjectMember[] }>(`${base}/members`),
  ]);
  const who = new Map(members.items.map((m) => [m.user.id, m.user.email]));
  return (
    <>
      {log.items.length === 0 ? (
        <EmptyState title={sp.action ? "No entries for this action" : "No audit entries yet"} />
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[860px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>When (UTC)</th>
                <th className={th}>Actor</th>
                <th className={th}>Action</th>
                <th className={th}>Target</th>
                <th className={th}>Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {log.items.map((e) => (
                <tr key={e.id} className="hover:bg-zinc-900/60">
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(e.ts)}</Mono>
                  </td>
                  <td className={td}>
                    <span className="text-zinc-300">{(e.actor.id && who.get(e.actor.id)) || e.actor.id || e.actor.kind}</span>
                  </td>
                  <td className={td}>
                    <span className={`inline-flex rounded border px-1.5 font-mono text-[11px] leading-4 ${tone(e.action)}`}>{e.action}</span>
                  </td>
                  <td className={`${td} max-w-[14rem] truncate font-mono text-xs text-zinc-400`} title={e.target ?? undefined}>
                    {e.target ?? "—"}
                  </td>
                  <td className={`${td} max-w-[26rem] truncate font-mono text-xs text-zinc-500`} title={detailText(e.detail)}>
                    {detailText(e.detail) || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <nav aria-label="Pagination" className="mt-3 flex items-center justify-end gap-3">
        {sp.cursor && (
          <Link href={`/settings/audit${qs(sp.action)}`} className="text-zinc-400 hover:text-zinc-100">
            ← Newest
          </Link>
        )}
        {log.next_cursor && (
          <Link
            href={`/settings/audit${qs(sp.action, log.next_cursor)}`}
            className="rounded border border-zinc-700 px-3 py-1 text-zinc-200 hover:bg-zinc-800"
          >
            Older →
          </Link>
        )}
      </nav>
    </>
  );
}

export default async function AuditPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const { projectId, role } = await getActive();
  if (!projectId || !roleAtLeast(role, "admin")) return <Forbidden min="admin" what="the audit log" />;
  const action = sp.action && (AUDIT_ACTIONS as readonly string[]).includes(sp.action) ? sp.action : undefined;
  return (
    <Panel title="Audit log" id="audit" description="Sensitive actions in this project, newest first. Secrets and passwords are never recorded.">
      <form method="get" role="search" aria-label="Filter audit log" className="mb-3 flex items-end gap-2">
        <label className="flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
          Action
          <select name="action" defaultValue={action ?? ""} className={`${inputCls} w-52`}>
            <option value="">any</option>
            {AUDIT_ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className={primaryBtn}>
          Apply
        </button>
        {action && (
          <Link href="/settings/audit" className="h-7 px-2 leading-7 text-zinc-400 hover:text-zinc-100">
            Clear
          </Link>
        )}
      </form>
      <Suspense key={`${action ?? ""}|${sp.cursor ?? ""}`} fallback={<TableSkeleton rows={8} />}>
        <AuditTable projectId={projectId} sp={{ action, cursor: sp.cursor }} />
      </Suspense>
    </Panel>
  );
}
