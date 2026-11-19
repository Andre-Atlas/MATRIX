"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { compareHref } from "@/lib/runs/query";
import { defaultMetricColumns, defaultParamColumns, flattenParams, paramText, readStoredColumns, writeStoredColumns } from "@/lib/runs/params";
import { formatMetric, runDuration } from "@/lib/runs/format";
import { formatTimestamp } from "@/lib/format";
import { MAX_COMPARE_RUNS, type Run } from "@/lib/runs/types";
import { td, th } from "@/components/ui";
import { RunStatusBadge, TagChips } from "./ui";

const STORAGE_KEY = "matrix.runs.columns.v1";

export function RunsTable({ runs, now, sortHeaders }: { runs: Run[]; now: number; sortHeaders: { name: string; started: string; nameDir: string; startedDir: string } }) {
  const flat = useMemo(() => runs.map((r) => flattenParams(r.params)), [runs]);
  const paramKeys = useMemo(
    () => [...new Set(flat.flatMap((f) => Object.entries(f).filter(([, v]) => v === null || typeof v !== "object").map(([k]) => k)))].sort(),
    [flat],
  );
  const metricKeys = useMemo(() => [...new Set(runs.flatMap((r) => Object.keys(r.summary)))].sort(), [runs]);
  const [cols, setCols] = useState<{ params: string[]; metrics: string[] }>(() => ({
    params: defaultParamColumns(runs.map((r) => r.params)),
    metrics: defaultMetricColumns(runs.map((r) => r.summary)),
  }));
  useEffect(() => {
    const stored = readStoredColumns(STORAGE_KEY);
    if (stored && (stored.params.length || stored.metrics.length)) setCols(stored);
  }, []);
  const toggle = (kind: "params" | "metrics", key: string) =>
    setCols((c) => {
      const list = c[kind].includes(key) ? c[kind].filter((k) => k !== key) : [...c[kind], key];
      const next = { ...c, [kind]: list };
      writeStoredColumns(STORAGE_KEY, next);
      return next;
    });

  const [selected, setSelected] = useState<string[]>([]);
  const pick = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= MAX_COMPARE_RUNS ? s : [...s, id]));
  const canCompare = selected.length >= 2 && selected.length <= MAX_COMPARE_RUNS;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs text-zinc-500" aria-live="polite">
          {selected.length ? `${selected.length} selected` : "Select 2–20 runs to compare"}
          {selected.length > 0 && (
            <button type="button" onClick={() => setSelected([])} className="text-zinc-400 hover:text-zinc-100">
              Clear
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <details className="relative">
            <summary className="flex h-7 cursor-pointer list-none items-center rounded border border-zinc-700 bg-zinc-900 px-2.5 text-xs text-zinc-300 hover:border-zinc-600">
              Columns
            </summary>
            <div className="absolute right-0 z-20 mt-1 max-h-96 w-72 overflow-auto rounded-md border border-zinc-700 bg-zinc-900 p-2 shadow-xl">
              <ColumnGroup title="Parameters" keys={paramKeys} chosen={cols.params} onToggle={(k) => toggle("params", k)} />
              <ColumnGroup title="Latest metrics" keys={metricKeys} chosen={cols.metrics} onToggle={(k) => toggle("metrics", k)} />
            </div>
          </details>
          {canCompare ? (
            <Link href={compareHref(selected)} className="flex h-7 items-center rounded border border-emerald-700/70 bg-emerald-900/40 px-3 text-xs text-emerald-200 hover:bg-emerald-900/70">
              Compare ({selected.length})
            </Link>
          ) : (
            <button type="button" disabled className="h-7 cursor-not-allowed rounded border border-zinc-800 px-3 text-xs text-zinc-500">
              Compare
            </button>
          )}
        </div>
      </div>
      <div className="overflow-x-auto rounded-md border border-zinc-800">
        <table className="w-full">
          <thead className="border-b border-zinc-800 bg-zinc-900/60">
            <tr>
              <th className={`${th} w-8`}>
                <span className="sr-only">Select</span>
              </th>
              <th className={th}>Status</th>
              <th className={th} aria-sort={sortHeaders.nameDir as "ascending" | "descending" | "none"}>
                <Link href={sortHeaders.name} className="hover:text-zinc-200">
                  Name{sortHeaders.nameDir === "ascending" ? " ↑" : sortHeaders.nameDir === "descending" ? " ↓" : ""}
                </Link>
              </th>
              <th className={th}>Group</th>
              <th className={th}>Tags</th>
              <th className={th} aria-sort={sortHeaders.startedDir as "ascending" | "descending" | "none"}>
                <Link href={sortHeaders.started} className="hover:text-zinc-200">
                  Started (UTC){sortHeaders.startedDir === "ascending" ? " ↑" : sortHeaders.startedDir === "descending" ? " ↓" : ""}
                </Link>
              </th>
              <th className={th}>Duration</th>
              {cols.params.map((k) => (
                <th key={`p-${k}`} className={`${th} normal-case text-sky-300/70`} title={`param ${k}`}>
                  {k}
                </th>
              ))}
              {cols.metrics.map((k) => (
                <th key={`m-${k}`} className={`${th} normal-case text-violet-300/70`} title={`latest ${k}`}>
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {runs.map((r, i) => {
              const checked = selected.includes(r.id);
              return (
                <tr key={r.id} className={`hover:bg-zinc-900/70 ${checked ? "bg-emerald-950/20" : ""}`}>
                  <td className={td}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!checked && selected.length >= MAX_COMPARE_RUNS}
                      onChange={() => pick(r.id)}
                      aria-label={`Select ${r.name} for comparison`}
                      className="accent-emerald-500"
                    />
                  </td>
                  <td className={td}>
                    <RunStatusBadge status={r.status} />
                  </td>
                  <td className={`${td} max-w-[16rem] truncate`}>
                    <Link href={`/runs/${encodeURIComponent(r.id)}`} className="font-medium text-zinc-100 hover:text-emerald-300" title={r.name}>
                      {r.name}
                    </Link>
                  </td>
                  <td className={`${td} max-w-[12rem] truncate text-xs text-zinc-400`}>{r.group ?? <span className="text-zinc-600">—</span>}</td>
                  <td className={td}>
                    <TagChips tags={r.tags} max={2} />
                  </td>
                  <td className={`${td} font-mono text-xs text-zinc-400`}>{formatTimestamp(r.started_at)}</td>
                  <td className={`${td} font-mono text-xs text-zinc-300`}>{runDuration(r.started_at, r.ended_at, now)}</td>
                  {cols.params.map((k) => {
                    const v = flat[i][k];
                    return (
                      <td key={`p-${k}`} className={`${td} max-w-[10rem] truncate font-mono text-xs text-zinc-300`} title={paramText(v)}>
                        {v === undefined ? <span className="text-zinc-600">—</span> : typeof v === "number" ? formatMetric(v) : paramText(v)}
                      </td>
                    );
                  })}
                  {cols.metrics.map((k) => (
                    <td key={`m-${k}`} className={`${td} font-mono text-xs tabular-nums text-zinc-200`}>
                      {k in r.summary ? formatMetric(r.summary[k]) : <span className="text-zinc-600">—</span>}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ColumnGroup({ title, keys, chosen, onToggle }: { title: string; keys: string[]; chosen: string[]; onToggle: (k: string) => void }) {
  return (
    <fieldset className="mb-2">
      <legend className="mb-1 text-[11px] uppercase tracking-wider text-zinc-500">{title}</legend>
      {keys.length === 0 && <p className="text-xs text-zinc-600">None on this page</p>}
      {keys.map((k) => (
        <label key={k} className="flex items-center gap-2 rounded px-1 py-0.5 font-mono text-xs text-zinc-300 hover:bg-zinc-800">
          <input type="checkbox" checked={chosen.includes(k)} onChange={() => onToggle(k)} className="accent-emerald-500" />
          <span className="truncate" title={k}>
            {k}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
