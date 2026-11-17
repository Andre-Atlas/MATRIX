import type { GatewayStats } from "@/lib/types";
import { formatLatencyMs, formatUSD } from "@/lib/gateway";

export interface GatewayMetricsProps {
  stats: GatewayStats;
}

export function GatewayMetrics({ stats }: GatewayMetricsProps) {
  const hitRatePct = (stats.cache_hit_rate * 100).toFixed(1);
  const totalCost = stats.total_cost_usd ?? 0;
  const savedCost = stats.saved_cost_usd ?? stats.cost_saved_usd ?? 0;
  const grossCost = totalCost + savedCost;
  const savingsPct = grossCost > 0 ? ((savedCost / grossCost) * 100).toFixed(1) : "0.0";
  const cacheHits = stats.cache_hits ?? Math.round(stats.total_requests * stats.cache_hit_rate);
  const p50Ms = stats.latency_p50_ms ?? stats.avg_latency_ms;
  const p99Ms = stats.latency_p99_ms ?? Math.round(stats.avg_latency_ms * 2.5);

  return (
    <section aria-label="Gateway KPI Metrics" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {/* 1. Total Requests */}
      <div className="relative overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/70 p-4 transition-colors hover:border-zinc-700/80">
        <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
          <span>Total de Requisições</span>
          <span className="inline-flex items-center gap-1 rounded bg-zinc-800/80 px-1.5 py-0.5 text-[11px] text-zinc-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
            {stats.throughput_rpm ? `${stats.throughput_rpm} req/min` : "Live"}
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-2xl font-bold tracking-tight text-zinc-100">
            {stats.total_requests.toLocaleString("en-US")}
          </span>
          <span className="text-xs text-zinc-500 font-mono">24h</span>
        </div>
        <div className="mt-3 flex items-center justify-between text-xs text-zinc-400">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            Direct: {(stats.requests_by_status?.DIRECT ?? Math.max(0, stats.total_requests - cacheHits)).toLocaleString("en-US")}
          </span>
          <span className="text-zinc-500">
            Fallbacks: {stats.fallback_events_24h ?? 0}
          </span>
        </div>
      </div>

      {/* 2. Cache Hit Rate */}
      <div className="relative overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/70 p-4 transition-colors hover:border-zinc-700/80">
        <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
          <span>Taxa de Cache Hit %</span>
          <span className="inline-flex items-center rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-300">
            SEMANTIC
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-2xl font-bold tracking-tight text-emerald-400">
            {hitRatePct}%
          </span>
          <span className="text-xs text-zinc-400 font-mono">
            ({cacheHits.toLocaleString("en-US")} hits)
          </span>
        </div>
        {/* Progress Bar */}
        <div className="mt-3">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
            <div
              className="h-full rounded-full bg-emerald-500 transition-all duration-500"
              style={{ width: `${Math.min(100, Math.max(0, stats.cache_hit_rate * 100))}%` }}
              role="progressbar"
              aria-valuenow={Number(hitRatePct)}
              aria-valuemin={0}
              aria-valuemax={100}
            />
          </div>
          <div className="mt-1.5 flex justify-between text-[11px] text-zinc-400">
            <span>Cache exato & similar</span>
            <span>Threshold &ge; 0.90</span>
          </div>
        </div>
      </div>

      {/* 3. Cost Saved in USD */}
      <div className="relative overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/70 p-4 transition-colors hover:border-zinc-700/80">
        <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
          <span>Economia em USD ($)</span>
          <span className="inline-flex items-center rounded bg-emerald-900/40 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-300">
            -{savingsPct}% custo
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-2xl font-bold tracking-tight text-emerald-400">
            {formatUSD(savedCost)}
          </span>
          <span className="text-xs text-zinc-500 font-mono">poupado</span>
        </div>
        <div className="mt-3 flex items-center justify-between text-xs text-zinc-400">
          <span>Gasto Real:</span>
          <span className="font-mono text-zinc-300">{formatUSD(totalCost)}</span>
        </div>
      </div>

      {/* 4. Latency p50 / p99 */}
      <div className="relative overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/70 p-4 transition-colors hover:border-zinc-700/80">
        <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
          <span>Latência p50 / p99</span>
          <span className="inline-flex items-center rounded bg-zinc-800/80 px-1.5 py-0.5 text-[10px] font-mono text-zinc-300">
            {formatLatencyMs(stats.avg_latency_ms)} avg
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-3">
          <div>
            <span className="text-xs uppercase text-zinc-500 font-semibold mr-1">p50</span>
            <span className="text-2xl font-bold tracking-tight text-sky-400">
              {formatLatencyMs(p50Ms)}
            </span>
          </div>
          <div className="text-zinc-600">/</div>
          <div>
            <span className="text-xs uppercase text-zinc-500 font-semibold mr-1">p99</span>
            <span className="text-xl font-bold tracking-tight text-amber-400">
              {formatLatencyMs(p99Ms)}
            </span>
          </div>
        </div>
        <div className="mt-3 flex items-center justify-between text-xs text-zinc-400">
          <span>Cache: &lt;15ms</span>
          <span className="text-emerald-400 font-mono">99.8% SLO</span>
        </div>
      </div>
    </section>
  );
}
