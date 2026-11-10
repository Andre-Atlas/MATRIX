import Link from "next/link";
import { apiGet } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { PromptList } from "@/lib/types";
import { EmptyState, Mono, PageHeader, td, th } from "@/components/ui";
import { LabelChip, NewLink, promptHref } from "@/components/prompts/ui";

export const dynamic = "force-dynamic";

export default async function PromptsPage() {
  const data = await apiGet<PromptList>("/api/v1/prompts");
  const items = [...data.items].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return (
    <div>
      <PageHeader
        title="Prompts"
        subtitle="Versioned templates. Versions are immutable; labels like production move between them"
        actions={<NewLink href="/prompts/new">New prompt</NewLink>}
      />
      {items.length === 0 ? (
        <EmptyState title="No prompts yet">Create one to version it and reference it by name from experiments and your app.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Name</th>
                <th className={`${th} text-right`}>Latest</th>
                <th className={th}>Labels</th>
                <th className={th}>Updated (UTC)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {items.map((p) => (
                <tr key={p.name} className="relative hover:bg-zinc-900/70 focus-within:bg-zinc-900/70">
                  <td className={td}>
                    <Link href={promptHref(p.name)} className="font-mono font-medium text-zinc-100 after:absolute after:inset-0 after:content-['']">
                      {p.name}
                    </Link>
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono>v{p.latest_version}</Mono>
                  </td>
                  <td className={td}>
                    {Object.keys(p.labels).length === 0 ? (
                      <span className="text-zinc-600">—</span>
                    ) : (
                      <span className="flex flex-wrap gap-1">
                        {Object.entries(p.labels).map(([l, v]) => (
                          <LabelChip key={l} label={l} version={v} />
                        ))}
                      </span>
                    )}
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(p.updated_at)}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
