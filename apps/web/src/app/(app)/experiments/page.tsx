import Link from "next/link";
import { apiGet } from "@/lib/api";
import { summaryMeta, summaryVariants, usesHeuristicJudge } from "@/lib/eval-matrix";
import { formatTimestamp } from "@/lib/format";
import type { Dataset, ExperimentListItem, ExperimentSummary, ListResponse } from "@/lib/types";
import { EmptyState, Mono, PageHeader, ScoreChips, td, th } from "@/components/ui";
import { ExperimentStatusBadge, HeuristicJudgeBadge, PartialBadge, primaryBtn } from "@/components/evals/ui";
import { StatusPoller } from "@/components/evals/status-poller";

export const dynamic = "force-dynamic";

/** Best variant mean per metric: the headline number of a run. */
function headline(summary: ExperimentSummary | null): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [, metrics] of summaryVariants(summary)) {
    for (const [m, agg] of Object.entries(metrics)) {
      if (agg.mean === null) continue;
      out[m] = Math.max(out[m] ?? -Infinity, agg.mean);
    }
  }
  return out;
}

export default async function ExperimentsPage() {
  const [data, datasets] = await Promise.all([
    apiGet<ListResponse<ExperimentListItem>>("/api/v1/experiments"),
    apiGet<ListResponse<Dataset>>("/api/v1/datasets"),
  ]);
  const dsName = new Map(datasets.items.map((d) => [d.id, d.name]));
  const active = data.items.some((e) => e.status === "queued" || e.status === "running");

  return (
    <div>
      <PageHeader
        title="Experiments"
        subtitle="Prompt × model variants run over a dataset and scored"
        actions={
          <div className="flex items-center gap-3">
            {active && <StatusPoller />}
            <Link href="/experiments/new" className={`${primaryBtn} leading-7`}>
              New experiment
            </Link>
          </div>
        }
      />
      {data.items.length === 0 ? (
        <EmptyState title="No experiments yet">
          Pick a dataset and define variants and metrics in{" "}
          <Link href="/experiments/new" className="text-emerald-300 hover:underline">
            New experiment
          </Link>
          .
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[1080px]">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Dataset</th>
                <th className={th}>Status</th>
                <th className={th}>Created (UTC)</th>
                <th className={th}>Finished (UTC)</th>
                <th className={th}>Best mean per metric</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {data.items.map((e) => (
                <tr key={e.id} className="relative hover:bg-zinc-900/70 focus-within:bg-zinc-900/70">
                  <td className={`${td} max-w-[18rem] truncate`}>
                    <Link
                      href={`/experiments/${encodeURIComponent(e.id)}`}
                      className="font-medium text-zinc-100 after:absolute after:inset-0 after:content-['']"
                    >
                      {e.name}
                    </Link>
                  </td>
                  <td className={td}>
                    <Link
                      href={`/datasets/${encodeURIComponent(e.dataset_id)}`}
                      className="relative z-10 font-mono text-xs text-zinc-400 hover:text-emerald-300"
                    >
                      {dsName.get(e.dataset_id) ?? e.dataset_id}
                    </Link>
                  </td>
                  <td className={td}>
                    <div className="flex flex-wrap items-center gap-1">
                      <ExperimentStatusBadge status={e.status} />
                      {usesHeuristicJudge(e.summary) && <HeuristicJudgeBadge />}
                      {summaryMeta(e.summary)?.partial && <PartialBadge meta={summaryMeta(e.summary)!} />}
                    </div>
                  </td>
                  <td className={td}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(e.created_at)}</Mono>
                  </td>
                  <td className={td}>
                    {e.finished_at ? (
                      <Mono className="text-xs text-zinc-400">{formatTimestamp(e.finished_at)}</Mono>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className={td}>
                    {e.status === "failed" && e.error ? (
                      <span className="block max-w-[22rem] truncate font-mono text-xs text-red-300" title={e.error}>
                        {e.error}
                      </span>
                    ) : (
                      <ScoreChips scores={headline(e.summary)} max={3} labelWidth="max-w-[7rem]" />
                    )}
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
