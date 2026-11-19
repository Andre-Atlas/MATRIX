"use client";

import { useMemo, useState } from "react";
import { groupKeysByPrefix } from "@/lib/runs/keys";
import type { MetricKeyStat, XAxisMode } from "@/lib/runs/types";
import { EmptyState } from "@/components/ui";
import { field } from "@/components/evals/ui";
import { MetricLineChart, useRunSeries, useStoredState, type ChartSettings } from "./metric-chart";
import { toggleBtn } from "./ui";

const DEFAULTS: ChartSettings & { combine: boolean } = { smoothing: 0.6, log: false, x: "step", combine: false };

export function ChartControls({ settings, onChange, extra }: { settings: ChartSettings; onChange: (s: ChartSettings) => void; extra?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-xs">
      <label className="flex items-center gap-2 text-zinc-400">
        Smoothing (EMA)
        <input
          type="range"
          min={0}
          max={0.99}
          step={0.01}
          value={settings.smoothing}
          onChange={(e) => onChange({ ...settings, smoothing: Number(e.target.value) })}
          aria-valuetext={settings.smoothing === 0 ? "off" : String(settings.smoothing)}
          className="w-32 accent-emerald-500"
        />
        <span className="w-8 font-mono text-zinc-200">{settings.smoothing === 0 ? "off" : settings.smoothing.toFixed(2)}</span>
      </label>
      <button type="button" aria-pressed={settings.log} onClick={() => onChange({ ...settings, log: !settings.log })} className={toggleBtn(settings.log)}>
        Log Y
      </button>
      <div role="group" aria-label="X axis" className="inline-flex rounded-md border border-zinc-800 bg-zinc-900 p-0.5">
        {(["step", "time"] as XAxisMode[]).map((x) => (
          <button
            key={x}
            type="button"
            aria-pressed={settings.x === x}
            onClick={() => onChange({ ...settings, x })}
            className={`rounded px-2.5 py-0.5 font-mono ${settings.x === x ? "bg-zinc-700 text-zinc-50" : "text-zinc-400 hover:text-zinc-100"}`}
          >
            {x}
          </button>
        ))}
      </div>
      {extra}
      <span className="text-zinc-500">Drag the box-zoom icon to zoom; scroll to zoom the step axis.</span>
    </div>
  );
}

export function ChartsTab({ runId, keys, refreshKey }: { runId: string; keys: MetricKeyStat[]; refreshKey: string }) {
  const [stored, setStored] = useStoredState("matrix.runs.chart-settings.v1", DEFAULTS);
  const [filter, setFilter] = useState("");
  const settings: ChartSettings = { smoothing: stored.smoothing, log: stored.log, x: stored.x };
  const names = useMemo(() => keys.map((k) => k.key), [keys]);
  const groups = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return groupKeysByPrefix(f ? names.filter((n) => n.toLowerCase().includes(f)) : names);
  }, [names, filter]);
  const fetchKeys = useMemo(() => groupKeysByPrefix(names).flatMap((g) => g.keys), [names]);
  const { data, error, loading } = useRunSeries(runId, fetchKeys, settings.x, refreshKey);
  const memoSettings = useMemo(() => settings, [settings.smoothing, settings.log, settings.x]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!names.length) return <EmptyState title="No metrics logged">Log with <code className="font-mono text-zinc-300">run.log({"{"}&quot;loss&quot;: 0.2{"}"})</code>.</EmptyState>;

  return (
    <div className="space-y-4">
      <ChartControls
        settings={settings}
        onChange={(s) => setStored({ ...stored, ...s })}
        extra={
          <>
            <button type="button" aria-pressed={stored.combine} onClick={() => setStored({ ...stored, combine: !stored.combine })} className={toggleBtn(stored.combine)}>
              One chart per group
            </button>
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter keys" aria-label="Filter metric keys" className={`${field} w-40 text-xs`} />
          </>
        }
      />
      {error && (
        <p role="alert" className="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-300">
          Could not load the series: {error}
        </p>
      )}
      {!data && loading && (
        <div role="status" className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="h-60 animate-pulse rounded-md bg-zinc-900/60" />
          ))}
          <span className="sr-only">Loading series…</span>
        </div>
      )}
      {data &&
        groups.map((g) => (
          <section key={g.prefix || "_"} aria-labelledby={`grp-${g.prefix || "none"}`}>
            <h2 id={`grp-${g.prefix || "none"}`} className="mb-2 font-mono text-xs uppercase tracking-wider text-zinc-500">
              {g.prefix ? `${g.prefix}/` : "other"} <span className="text-zinc-600">· {g.keys.length}</span>
            </h2>
            {stored.combine ? (
              <MetricLineChart title={`${g.prefix || "other"} (${g.keys.length} keys)`} lines={g.keys.map((k) => ({ name: k, data: data[k] ?? { steps: [], values: [], ts: [] } }))} settings={memoSettings} group={`run-${runId}`} height={280} />
            ) : (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {g.keys.map((k) => (
                  <MetricLineChart key={k} title={k} lines={[{ name: k, data: data[k] ?? { steps: [], values: [], ts: [] } }]} settings={memoSettings} group={`run-${runId}`} />
                ))}
              </div>
            )}
          </section>
        ))}
    </div>
  );
}
