"use client";

import type { DriftMetricStatus, DriftMetricValue, VectorDriftResponse } from "@/lib/types";
import { formatDriftMetric } from "@/lib/vectors";

interface DriftMonitorProps {
  driftData: VectorDriftResponse;
}

const STATUS_BADGE_STYLE: Record<DriftMetricStatus, { text: string; bg: string; dot: string; label: string }> = {
  healthy: {
    text: "text-emerald-300",
    bg: "border-emerald-800/60 bg-emerald-950/40",
    dot: "bg-emerald-400",
    label: "Healthy",
  },
  warning: {
    text: "text-amber-300",
    bg: "border-amber-800/70 bg-amber-950/50",
    dot: "bg-amber-400",
    label: "Warning",
  },
  critical: {
    text: "text-red-300",
    bg: "border-red-800/80 bg-red-950/60",
    dot: "bg-red-400",
    label: "Critical",
  },
};

function DriftStatusBadge({ status }: { status: DriftMetricStatus }) {
  const s = STATUS_BADGE_STYLE[status] ?? STATUS_BADGE_STYLE.healthy;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${s.bg} ${s.text}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

function ThresholdBar({
  value,
  thresholdWarning,
  thresholdCritical,
  status,
}: {
  value: number;
  thresholdWarning: number;
  thresholdCritical: number;
  status: DriftMetricStatus;
}) {
  const maxScale = Math.max(thresholdCritical * 1.5, value * 1.2, 0.25);
  const percentValue = Math.min(100, Math.max(0, (value / maxScale) * 100));
  const percentWarn = Math.min(100, Math.max(0, (thresholdWarning / maxScale) * 100));
  const percentCrit = Math.min(100, Math.max(0, (thresholdCritical / maxScale) * 100));

  const barColor =
    status === "critical" ? "bg-red-500" : status === "warning" ? "bg-amber-400" : "bg-emerald-400";

  return (
    <div className="mt-2.5">
      <div className="flex items-center justify-between text-[11px] text-zinc-500 font-mono">
        <span>0</span>
        <span className="text-amber-400/90">warn: {thresholdWarning.toFixed(2)}</span>
        <span className="text-red-400/90">crit: {thresholdCritical.toFixed(2)}</span>
        <span>{maxScale.toFixed(2)}</span>
      </div>
      <div className="relative mt-1 h-2 w-full overflow-hidden rounded-full bg-zinc-800">
        {/* Warning threshold vertical marker */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-amber-400/80 z-10"
          style={{ left: `${percentWarn}%` }}
        />
        {/* Critical threshold vertical marker */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-red-400/80 z-10"
          style={{ left: `${percentCrit}%` }}
        />
        {/* Value progress fill */}
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${percentValue}%` }}
        />
      </div>
    </div>
  );
}

function MetricCard({
  metric,
  sublabel,
}: {
  metric: DriftMetricValue;
  sublabel?: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 shadow-sm flex flex-col justify-between">
      <div>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
            {metric.name}
          </h3>
          <DriftStatusBadge status={metric.status} />
        </div>
        {sublabel && <p className="mt-0.5 text-[11px] text-zinc-500">{sublabel}</p>}

        <div className="mt-3 flex items-baseline gap-2">
          <span className="font-mono text-2xl font-semibold tabular-nums text-zinc-100">
            {formatDriftMetric(metric.value, { decimals: 3 })}
          </span>
          <span className="text-xs text-zinc-500 font-mono">drift</span>
        </div>

        {metric.description && (
          <p className="mt-2 text-xs leading-relaxed text-zinc-400">
            {metric.description}
          </p>
        )}
      </div>

      <div className="mt-4 border-t border-zinc-800/80 pt-3">
        <div className="text-[11px] font-medium text-zinc-400">Threshold Monitoring</div>
        <ThresholdBar
          value={metric.value}
          thresholdWarning={metric.threshold_warning}
          thresholdCritical={metric.threshold_critical}
          status={metric.status}
        />
      </div>
    </div>
  );
}

export function DriftMonitor({ driftData }: DriftMonitorProps) {
  const { metrics, timeline, clusters_drifting, top_drifting_clusters } = driftData;

  // Primary statistical metrics
  const primaryMetrics: { key: string; item: DriftMetricValue; sublabel: string }[] = [
    {
      key: "js",
      item: metrics.jensen_shannon,
      sublabel: "Divergência Jensen-Shannon entre embeddings da baseline e recentes",
    },
    {
      key: "psi",
      item: metrics.psi,
      sublabel: "Population Stability Index sobre distribuição de densidade latente",
    },
    {
      key: "cosine",
      item: metrics.centroid_cosine,
      sublabel: "Distância cosseno (1 - cos_sim) entre centroides documentais e queries",
    },
  ];

  if (metrics.wasserstein) {
    primaryMetrics.push({
      key: "wasserstein",
      item: metrics.wasserstein,
      sublabel: "Earth Mover's Distance sobre ativações vetoriais",
    });
  }

  // Mini timeline points calculation
  const maxJS = Math.max(...timeline.map((t) => t.jensen_shannon), 0.2);

  return (
    <div className="space-y-4">
      {/* Observatory Overview Card */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex items-center gap-3">
          <div className="flex flex-col">
            <span className="text-xs uppercase tracking-wider text-zinc-500">Drift Observatory</span>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-base font-semibold text-zinc-100">Embedding Distribution Drift</span>
              <DriftStatusBadge status={metrics.overall_status} />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-6 text-xs">
          <div>
            <span className="text-zinc-500">Baseline Window:</span>
            <div className="font-mono text-zinc-300">{metrics.baseline_period}</div>
          </div>
          <div>
            <span className="text-zinc-500">Evaluation Target:</span>
            <div className="font-mono text-zinc-300">{metrics.target_period}</div>
          </div>
          <div>
            <span className="text-zinc-500">Analyzed Queries:</span>
            <div className="font-mono text-zinc-300">
              {(metrics.total_queries_analyzed ?? 0).toLocaleString()}
            </div>
          </div>
          <div>
            <span className="text-zinc-500">Drifting Clusters:</span>
            <div className="font-mono font-medium text-amber-300">
              {clusters_drifting} clusters
            </div>
          </div>
        </div>
      </div>

      {/* Grid of Statistical Metric Cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {primaryMetrics.map(({ key, item, sublabel }) => (
          <MetricCard key={key} metric={item} sublabel={sublabel} />
        ))}
      </div>

      {/* Cluster Drift Summary & Historical Timeline Sparkline */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Top Drifting Clusters */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 lg:col-span-1">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
            Cluster Drift Ranking
          </h4>
          <p className="mt-0.5 text-xs text-zinc-500">
            Clusters com maior taxa de descolamento semântico
          </p>

          <div className="mt-3 space-y-2">
            {top_drifting_clusters.map((c) => (
              <div
                key={c.cluster_id}
                className="flex items-center justify-between rounded border border-zinc-800/80 bg-zinc-950/60 p-2 text-xs"
              >
                <div className="min-w-0 pr-2">
                  <div className="truncate font-medium text-zinc-200">{c.cluster_name}</div>
                  <div className="font-mono text-[11px] text-zinc-500">{c.cluster_id}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="font-mono text-xs tabular-nums text-zinc-200">
                    {c.drift_score.toFixed(3)}
                  </span>
                  <DriftStatusBadge status={c.status} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 7-Day Drift Timeline Sparkline Chart */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 lg:col-span-2">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                Drift Progression Timeline
              </h4>
              <p className="mt-0.5 text-xs text-zinc-500">
                Evolução diária da Divergência Jensen-Shannon (7 dias)
              </p>
            </div>
            <div className="flex items-center gap-3 text-xs text-zinc-400">
              <span className="inline-flex items-center gap-1.5 font-mono text-[11px]">
                <span className="h-2 w-2 rounded-full bg-sky-400" />
                JS Div
              </span>
              <span className="inline-flex items-center gap-1.5 font-mono text-[11px]">
                <span className="h-2 w-2 rounded-full bg-amber-400" />
                PSI
              </span>
            </div>
          </div>

          <div className="mt-4 flex h-32 items-end gap-2 border-b border-zinc-800 pb-2">
            {timeline.map((point, index) => {
              const heightPercentJS = Math.min(100, Math.round((point.jensen_shannon / maxJS) * 100));
              const heightPercentPSI = Math.min(100, Math.round((point.psi / maxJS) * 100));
              const dateStr = point.timestamp.split("T")[0]?.slice(5);

              return (
                <div
                  key={point.timestamp}
                  className="flex flex-1 flex-col items-center gap-1 h-full justify-end group relative"
                >
                  {/* Tooltip on hover */}
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity absolute -top-12 z-20 pointer-events-none rounded bg-zinc-800 px-2 py-1 text-[10px] font-mono shadow text-zinc-200 whitespace-nowrap">
                    JS: {point.jensen_shannon.toFixed(3)} | PSI: {point.psi.toFixed(3)}
                  </div>

                  <div className="flex items-end gap-1 w-full justify-center h-24">
                    <div
                      className="w-2.5 rounded-t bg-sky-400/80 transition-all hover:bg-sky-400"
                      style={{ height: `${heightPercentJS}%` }}
                      title={`JS: ${point.jensen_shannon.toFixed(3)}`}
                    />
                    <div
                      className="w-2.5 rounded-t bg-amber-400/80 transition-all hover:bg-amber-400"
                      style={{ height: `${heightPercentPSI}%` }}
                      title={`PSI: ${point.psi.toFixed(3)}`}
                    />
                  </div>
                  <span className="font-mono text-[10px] text-zinc-500">{dateStr}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
