import { apiGet } from "@/lib/api";
import { formatCost, formatDuration, formatPercent, formatTokens } from "@/lib/format";
import type { OverviewStats } from "@/lib/types";
import { EmptyState, Kpi, Mono, PageHeader, Segmented, td, th } from "@/components/ui";

export const dynamic = "force-dynamic";

const WINDOWS = { "1h": 3600_000, "24h": 86_400_000, "7d": 7 * 86_400_000 } as const;
type Window = keyof typeof WINDOWS;

export default async function Overview({ searchParams }: { searchParams: Promise<{ since?: string }> }) {
  const sp = await searchParams;
  const win: Window = sp.since && sp.since in WINDOWS ? (sp.since as Window) : "24h";
  const since = new Date(Date.now() - WINDOWS[win]).toISOString();
  const stats = await apiGet<OverviewStats>("/api/v1/stats/overview", { since });
  const maxCost = Math.max(0, ...stats.by_model.map((m) => m.cost_usd));
  const errTone = stats.error_rate >= 0.05 ? "error" : stats.error_rate > 0 ? "warn" : "default";

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Overview"
        subtitle={`Last ${win}`}
        actions={
          <Segmented
            label="Time window"
            options={(Object.keys(WINDOWS) as Window[]).map((w) => ({
              href: `/?since=${w}`,
              label: w,
              active: w === win,
            }))}
          />
        }
      />
      <section aria-label="Key metrics" className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Traces" value={stats.trace_count.toLocaleString("en-US")} />
        <Kpi label="Error rate" value={formatPercent(stats.error_rate)} tone={errTone} />
        <Kpi label="p50 latency" value={formatDuration(stats.p50_latency_ms)} />
        <Kpi label="p95 latency" value={formatDuration(stats.p95_latency_ms)} />
        <Kpi label="Cost" value={formatCost(stats.total_cost_usd)} />
        <Kpi
          label="Tokens"
          value={formatTokens(stats.input_tokens + stats.output_tokens)}
          sub={`${formatTokens(stats.input_tokens)} in · ${formatTokens(stats.output_tokens)} out`}
        />
      </section>

      <section aria-labelledby="by-model" className="mt-6">
        <h2 id="by-model" className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-400">
          By model
        </h2>
        {stats.by_model.length === 0 ? (
          <EmptyState title="No model calls in this window">
            Instrument your app with the MATRIX SDK or any OpenInference exporter and LLM spans will appear here.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto rounded-md border border-zinc-800">
            <table className="w-full">
              <thead className="border-b border-zinc-800 bg-zinc-900/60">
                <tr>
                  <th className={th}>Model</th>
                  <th className={`${th} text-right`}>Calls</th>
                  <th className={`${th} text-right`}>Cost</th>
                  <th className={`${th} w-1/3`}>Share of cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70">
                {stats.by_model.map((m) => {
                  const share = stats.total_cost_usd > 0 ? m.cost_usd / stats.total_cost_usd : 0;
                  return (
                    <tr key={m.model} className="hover:bg-zinc-900/60">
                      <td className={td}>
                        <Mono className="text-zinc-200">{m.model}</Mono>
                      </td>
                      <td className={`${td} text-right`}>
                        <Mono>{m.calls.toLocaleString("en-US")}</Mono>
                      </td>
                      <td className={`${td} text-right`}>
                        <Mono>{formatCost(m.cost_usd)}</Mono>
                      </td>
                      <td className={td}>
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 flex-1 rounded-full bg-zinc-800">
                            <div
                              className="h-1.5 rounded-full bg-emerald-500/70"
                              style={{ width: `${maxCost > 0 ? (m.cost_usd / maxCost) * 100 : 0}%` }}
                            />
                          </div>
                          <Mono className="w-12 text-right text-xs text-zinc-400">{formatPercent(share, 0)}</Mono>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
