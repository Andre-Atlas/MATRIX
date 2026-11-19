import Link from "next/link";
import { apiGet } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { Dataset, ListResponse } from "@/lib/types";
import { EmptyState, Mono, PageHeader, td, th } from "@/components/ui";
import { CreateDatasetForm } from "@/components/evals/create-dataset-form";

export const dynamic = "force-dynamic";

export default async function DatasetsPage() {
  const data = await apiGet<ListResponse<Dataset>>("/api/v1/datasets");
  return (
    <div>
      <PageHeader title="Datasets" subtitle="Golden cases for offline experiments and regression checks" />
      <CreateDatasetForm />
      {data.items.length === 0 ? (
        <EmptyState title="No datasets yet">
          Create one above, or open a span in a trace and use “Add to dataset” to turn it into a case.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Description</th>
                <th className={`${th} text-right`}>Items</th>
                <th className={th}>Created (UTC)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {data.items.map((d) => (
                <tr key={d.id} className="relative hover:bg-zinc-900/70 focus-within:bg-zinc-900/70">
                  <td className={td}>
                    <Link
                      href={`/datasets/${encodeURIComponent(d.id)}`}
                      className="font-mono font-medium text-zinc-100 after:absolute after:inset-0 after:content-['']"
                    >
                      {d.name}
                    </Link>
                  </td>
                  <td className={`${td} max-w-[28rem] truncate text-zinc-400`} title={d.description ?? undefined}>
                    {d.description ?? <span className="text-zinc-600">—</span>}
                  </td>
                  <td className={`${td} text-right`}>
                    <Mono>{d.item_count}</Mono>
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(d.created_at)}</Mono>
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
