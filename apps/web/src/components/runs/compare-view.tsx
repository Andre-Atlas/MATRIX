"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { explain, proxy } from "@/lib/proxy-client";
import { bestIndex, groupKeysByPrefix, lowerIsBetter } from "@/lib/runs/keys";
import { diffParams, paramText } from "@/lib/runs/params";
import { formatMetric } from "@/lib/runs/format";
import { MAX_COMPARE_KEYS, type CompareResponse, type Run } from "@/lib/runs/types";
import { seriesColor } from "@/components/charts/theme";
import { td, th } from "@/components/ui";
import { ChartControls } from "./charts-tab";
import { MetricLineChart, useStoredState, type ChartSettings } from "./metric-chart";
import { RunStatusBadge, toggleBtn } from "./ui";

const DEFAULTS: ChartSettings = { smoothing: 0.6, log: false, x: "step" };

/** Colors are stable per run: its position in `ids` (the same in every chart and table). */
export function CompareView({ runs, keys }: { runs: Run[]; keys: string[] }) {
  const [settings, setSettings] = useStoredState("matrix.runs.compare-settings.v1", DEFAULTS);
  const [hidden, setHidden] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>(() => groupKeysByPrefix(keys).flatMap((g) => g.keys).slice(0, 6));
  const [allParams, setAllParams] = useState(false);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const color = useMemo(() => new Map(runs.map((r, i) => [r.id, seriesColor(i)])), [runs]);
  const ids = runs.map((r) => r.id).join(",");
  const sig = chosen.join(",");

  useEffect(() => {
    if (!sig || !ids) return;
    let alive = true;
    setError(null);
    proxy<CompareResponse>("GET", `runs/compare?run_ids=${encodeURIComponent(ids)}&keys=${encodeURIComponent(sig)}&max_points=1000`)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(explain(e)));
    return () => {
      alive = false;
    };
  }, [ids, sig]);

  const rows = useMemo(() => diffParams(runs.map((r) => r.params), !allParams), [runs, allParams]);
  const metricKeys = useMemo(() => groupKeysByPrefix([...new Set(runs.flatMap((r) => Object.keys(r.summary)))]).flatMap((g) => g.keys), [runs]);
  const visible = runs.filter((r) => !hidden.includes(r.id));
  const toggleKey = (k: string) => setChosen((c) => (c.includes(k) ? c.filter((x) => x !== k) : c.length >= MAX_COMPARE_KEYS ? c : [...c, k]));

  return (
    <div className="space-y-5">
      <div role="group" aria-label="Runs in this comparison (click to show or hide)" className="flex flex-wrap gap-2">
        {runs.map((r) => {
          const off = hidden.includes(r.id);
          return (
            <button
              key={r.id}
              type="button"
              aria-pressed={!off}
              onClick={() => setHidden((h) => (off ? h.filter((x) => x !== r.id) : [...h, r.id]))}
              className={`flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs ${off ? "border-zinc-800 text-zinc-600 line-through" : "border-zinc-700 text-zinc-200 hover:border-zinc-500"}`}
            >
              <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: color.get(r.id), opacity: off ? 0.3 : 1 }} />
              {r.name}
            </button>
          );
        })}
      </div>

      <ChartControls settings={settings} onChange={setSettings} />
      <details className="text-xs">
        <summary className="cursor-pointer text-zinc-400 hover:text-zinc-100">
          Keys ({chosen.length}/{MAX_COMPARE_KEYS}) — choose which metrics to overlay
        </summary>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {groupKeysByPrefix(keys).flatMap((g) => g.keys).map((k) => (
            <button key={k} type="button" aria-pressed={chosen.includes(k)} onClick={() => toggleKey(k)} className={`${toggleBtn(chosen.includes(k))} h-6 font-mono`}>
              {k}
            </button>
          ))}
        </div>
      </details>
      {error && <p role="alert" className="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-300">Could not load the series: {error}</p>}
      {!data && !error && chosen.length > 0 && (
        <div role="status" className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {chosen.slice(0, 4).map((k) => <div key={k} className="h-60 animate-pulse rounded-md bg-zinc-900/60" />)}
          <span className="sr-only">Loading series…</span>
        </div>
      )}
      {data && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {chosen.map((k) => (
            <MetricLineChart
              key={k}
              title={k}
              group="compare"
              settings={settings}
              lines={visible
                .filter((r) => data.runs[r.id])
                .map((r) => ({ name: r.name, color: color.get(r.id), data: data.runs[r.id].series[k] ?? { steps: [], values: [], ts: [] } }))}
            />
          ))}
        </div>
      )}

      <section aria-labelledby="cmp-params">
        <div className="mb-2 flex items-center justify-between">
          <h2 id="cmp-params" className="text-sm font-medium text-zinc-100">
            Parameters {allParams ? "" : <span className="font-normal text-zinc-500">· only those that differ ({rows.length})</span>}
          </h2>
          <button type="button" aria-pressed={allParams} onClick={() => setAllParams(!allParams)} className={toggleBtn(allParams)}>
            Show all
          </button>
        </div>
        {rows.length === 0 ? (
          <p className="text-xs text-zinc-500">All parameters are identical.</p>
        ) : (
          <RunMatrix runs={runs} color={color}>
            {rows.map((row) => (
              <tr key={row.key} className={row.differs ? "bg-amber-950/20" : ""}>
                <th scope="row" className={`${td} text-left font-mono text-xs font-normal ${row.differs ? "text-amber-200" : "text-zinc-400"}`}>{row.key}</th>
                {row.values.map((v, i) => (
                  <td key={i} className={`${td} max-w-[14rem] truncate font-mono text-xs ${v === undefined ? "text-zinc-600" : row.differs ? "text-zinc-100" : "text-zinc-400"}`} title={paramText(v)}>
                    {typeof v === "number" ? formatMetric(v) : paramText(v)}
                  </td>
                ))}
              </tr>
            ))}
          </RunMatrix>
        )}
      </section>

      <section aria-labelledby="cmp-metrics">
        <h2 id="cmp-metrics" className="mb-2 text-sm font-medium text-zinc-100">
          Final metrics <span className="font-normal text-zinc-500">· ★ best (lower is better for loss, error, perplexity, rmse, time)</span>
        </h2>
        <RunMatrix runs={runs} color={color}>
          {metricKeys.map((k) => {
            const values = runs.map((r) => r.summary[k]);
            const best = runs.length > 1 ? bestIndex(k, values) : null;
            return (
              <tr key={k}>
                <th scope="row" className={`${td} text-left font-mono text-xs font-normal text-zinc-400`}>
                  {k} <span className="text-zinc-600">{lowerIsBetter(k) ? "↓" : "↑"}</span>
                </th>
                {values.map((v, i) => (
                  <td key={i} className={`${td} font-mono text-xs tabular-nums ${i === best ? "font-semibold text-emerald-300" : v === undefined ? "text-zinc-600" : "text-zinc-300"}`}>
                    {v === undefined ? "—" : formatMetric(v)}
                    {i === best && <span aria-label="best"> ★</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </RunMatrix>
      </section>
    </div>
  );
}

function RunMatrix({ runs, color, children }: { runs: Run[]; color: Map<string, string>; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-md border border-zinc-800">
      <table className="w-full">
        <thead className="border-b border-zinc-800 bg-zinc-900/60">
          <tr>
            <th scope="col" className={th}>Key</th>
            {runs.map((r) => (
              <th key={r.id} scope="col" className={`${th} normal-case`}>
                <span className="flex items-center gap-1.5">
                  <span aria-hidden className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color.get(r.id) }} />
                  <Link href={`/runs/${encodeURIComponent(r.id)}`} className="max-w-[12rem] truncate text-zinc-200 hover:text-emerald-300">{r.name}</Link>
                  <RunStatusBadge status={r.status} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/70">{children}</tbody>
      </table>
    </div>
  );
}
