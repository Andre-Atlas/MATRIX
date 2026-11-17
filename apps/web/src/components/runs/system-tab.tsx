"use client";

import { useMemo } from "react";
import { systemKeys, type GpuMetric } from "@/lib/runs/keys";
import { formatSystemValue } from "@/lib/runs/format";
import { lastFinite } from "@/lib/runs/series";
import type { MetricKeyStat, SeriesData } from "@/lib/runs/types";
import { EmptyState } from "@/components/ui";
import { MetricLineChart, useRunSeries, type ChartSettings } from "./metric-chart";

const SETTINGS: ChartSettings = { smoothing: 0, log: false, x: "time" };
const EMPTY: SeriesData = { steps: [], values: [], ts: [] };
const GPU_LABEL: Record<GpuMetric, string> = { utilization: "Utilization", memory_used_mb: "Memory used", temperature_c: "Temperature", power_w: "Power" };
const short = (k: string) => k.replace(/^sys\//, "");

export function SystemTab({ runId, keys, refreshKey }: { runId: string; keys: MetricKeyStat[]; refreshKey: string }) {
  const names = useMemo(() => keys.map((k) => k.key).filter((k) => k.startsWith("sys/")), [keys]);
  const sys = useMemo(() => systemKeys(names), [names]);
  const { data, error, loading } = useRunSeries(runId, names, "time", refreshKey);

  if (!names.length) {
    return (
      <EmptyState title="No system telemetry">
        This run sent no <code className="font-mono text-zinc-300">sys/</code> metrics. Start runs with{" "}
        <code className="font-mono text-zinc-300">system_metrics=True</code> (the default) to collect CPU, memory, disk, network and GPU usage.
      </EmptyState>
    );
  }
  if (error) return <p role="alert" className="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-300">Could not load the telemetry: {error}</p>;
  if (!data) {
    return (
      <div role="status" className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => <div key={i} className="h-52 animate-pulse rounded-md bg-zinc-900/60" />)}
        <span className="sr-only">{loading ? "Loading telemetry…" : ""}</span>
      </div>
    );
  }
  const latest = (k: string) => formatSystemValue(k, lastFinite(data[k]?.values ?? []));
  const lines = (ks: string[]) => ks.map((k) => ({ name: short(k), data: data[k] ?? EMPTY }));
  const host = [
    { title: "CPU", keys: sys.cpu },
    { title: "Memory", keys: sys.memory },
    { title: "Disk I/O", keys: sys.disk },
    { title: "Network", keys: sys.network },
    { title: "Process", keys: sys.process },
    { title: "Other", keys: sys.other },
  ].filter((p) => p.keys.length);

  return (
    <div className="space-y-5">
      <section aria-labelledby="sys-host">
        <h2 id="sys-host" className="mb-2 font-mono text-xs uppercase tracking-wider text-zinc-500">Host</h2>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {host.map((p) => (
            <div key={p.title}>
              <MetricLineChart title={`${p.title} · ${p.keys.map(latest).join(" / ")}`} lines={lines(p.keys)} settings={SETTINGS} group={`sys-${runId}`} height={180} valueFormat={(v) => formatSystemValue(p.keys[0], v)} />
            </div>
          ))}
        </div>
      </section>
      {sys.gpus.map((g) => (
        <section key={g.index} aria-labelledby={`gpu-${g.index}`}>
          <h2 id={`gpu-${g.index}`} className="mb-2 font-mono text-xs uppercase tracking-wider text-zinc-500">GPU {g.index}</h2>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-4">
            {(Object.keys(GPU_LABEL) as GpuMetric[])
              .filter((m) => g.keys[m])
              .map((m) => {
                const k = g.keys[m]!;
                return <MetricLineChart key={k} title={`${GPU_LABEL[m]} · ${latest(k)}`} lines={lines([k])} settings={SETTINGS} group={`sys-${runId}`} height={160} valueFormat={(v) => formatSystemValue(k, v)} />;
              })}
          </div>
        </section>
      ))}
    </div>
  );
}
