"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { computeHyperparamDiff } from "@/lib/mlops";
import { getRunStatusStyle, VARIANT_COLORS } from "@/lib/mlops-tokens";
import { formatValue } from "@/lib/format";
import type { TrainingRun } from "@/lib/types";

type CompareDrawerProps = {
  open: boolean;
  runs: TrainingRun[];
  onClose: () => void;
};

export function CompareDrawer({ open, runs, onClose }: CompareDrawerProps) {
  const [diffOnly, setDiffOnly] = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    closeBtnRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [open, onClose]);

  const hpDiff = useMemo(() => computeHyperparamDiff(runs), [runs]);

  const metricKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const r of runs) {
      for (const k of Object.keys(r.summary_metrics ?? {})) {
        keys.add(k);
      }
    }
    return [...keys].sort();
  }, [runs]);

  if (!open) return null;

  const baseline = runs[0];
  const hpRows = diffOnly ? hpDiff.rows.filter((row) => row.hasDiff) : hpDiff.rows;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-xs"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="compare-drawer-title"
        className="flex h-full w-full max-w-4xl flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900/60 px-5 py-4">
          <div>
            <h2 id="compare-drawer-title" className="text-base font-semibold text-zinc-50">
              Compare Training Runs ({runs.length})
            </h2>
            <p className="mt-0.5 text-xs text-zinc-400">
              Metric deltas relative to baseline{" "}
              <span className="font-mono text-zinc-200">{baseline?.name ?? "—"}</span>
            </p>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="h-8 rounded border border-zinc-700 bg-zinc-800/80 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            Close (Esc)
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto p-5">
          {runs.length < 2 ? (
            <div className="rounded-md border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-400">
              Select at least 2 runs in the table to compare metrics and hyperparameters.
            </div>
          ) : (
            <>
              {/* Selected runs header cards */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {runs.map((run, idx) => {
                  const statusStyle = getRunStatusStyle(run.status);
                  const color = VARIANT_COLORS[idx % VARIANT_COLORS.length];
                  return (
                    <div
                      key={run.id}
                      className="rounded-md border border-zinc-800 bg-zinc-900/60 p-3"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          aria-hidden="true"
                          className="h-2.5 w-2.5 shrink-0 rounded-sm"
                          style={{ backgroundColor: color }}
                        />
                        <span className="truncate text-sm font-medium text-zinc-100">
                          {run.name}
                        </span>
                        {idx === 0 && (
                          <span className="ml-auto rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">
                            Baseline
                          </span>
                        )}
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        <span
                          className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${statusStyle.badge}`}
                        >
                          <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${statusStyle.dot}`} />
                          {statusStyle.label}
                        </span>
                        <span className="font-mono text-zinc-400">{run.run_type}</span>
                        {run.total_steps !== null && (
                          <span className="font-mono tabular-nums text-zinc-400">
                            {run.total_steps} steps
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Summary Metrics Delta Table */}
              <section aria-labelledby="compare-metrics-heading">
                <h3 id="compare-metrics-heading" className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-300">
                  Summary Metrics & Deltas
                </h3>
                <div className="overflow-x-auto rounded-md border border-zinc-800">
                  <table className="w-full text-left text-xs">
                    <thead className="border-b border-zinc-800 bg-zinc-900/60">
                      <tr>
                        <th scope="col" className="px-3 py-2 font-medium uppercase tracking-wider text-zinc-400">
                          Metric
                        </th>
                        {runs.map((run, idx) => (
                          <th
                            key={run.id}
                            scope="col"
                            className="px-3 py-2 font-medium text-zinc-200"
                          >
                            <span className="inline-flex items-center gap-1.5">
                              <span
                                aria-hidden="true"
                                className="h-2 w-2 rounded-sm"
                                style={{ backgroundColor: VARIANT_COLORS[idx % VARIANT_COLORS.length] }}
                              />
                              <span className="max-w-[10rem] truncate">{run.name}</span>
                            </span>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/70">
                      {metricKeys.length === 0 ? (
                        <tr>
                          <td colSpan={runs.length + 1} className="px-3 py-4 text-center text-zinc-400">
                            No summary metrics recorded for the selected runs.
                          </td>
                        </tr>
                      ) : (
                        metricKeys.map((metric) => {
                          const baseVal = baseline?.summary_metrics?.[metric];
                          const isLoss = metric.includes("loss") || metric.includes("perplexity");
                          return (
                            <tr key={metric} className="hover:bg-zinc-900/40">
                              <th scope="row" className="px-3 py-2 font-mono font-normal text-zinc-300">
                                {metric}
                              </th>
                              {runs.map((run, idx) => {
                                const val = run.summary_metrics?.[metric];
                                const hasVal = typeof val === "number" && Number.isFinite(val);
                                const hasBase = typeof baseVal === "number" && Number.isFinite(baseVal);
                                const delta = idx > 0 && hasVal && hasBase ? val - baseVal : null;
                                const isImprovement =
                                  delta !== null && delta !== 0
                                    ? isLoss
                                      ? delta < 0
                                      : delta > 0
                                    : null;
                                return (
                                  <td key={run.id} className="px-3 py-2 font-mono tabular-nums text-zinc-200">
                                    <span>{hasVal ? val.toFixed(4) : "—"}</span>
                                    {delta !== null && (
                                      <span
                                        className={`ml-2 text-[11px] ${
                                          isImprovement === true
                                            ? "text-emerald-300"
                                            : isImprovement === false
                                              ? "text-red-300"
                                              : "text-zinc-400"
                                        }`}
                                      >
                                        ({delta >= 0 ? "+" : ""}
                                        {delta.toFixed(4)})
                                      </span>
                                    )}
                                  </td>
                                );
                              })}
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* Hyperparameter Diff Table */}
              <section aria-labelledby="compare-hp-heading">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h3 id="compare-hp-heading" className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                    Hyperparameter Diff
                  </h3>
                  <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-zinc-300">
                    <input
                      type="checkbox"
                      checked={diffOnly}
                      onChange={(e) => setDiffOnly(e.target.checked)}
                      className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 accent-emerald-400"
                    />
                    Show differences only
                  </label>
                </div>
                <div className="overflow-x-auto rounded-md border border-zinc-800">
                  <table className="w-full text-left text-xs">
                    <thead className="border-b border-zinc-800 bg-zinc-900/60">
                      <tr>
                        <th scope="col" className="px-3 py-2 font-medium uppercase tracking-wider text-zinc-400">
                          Parameter
                        </th>
                        {runs.map((run, idx) => (
                          <th key={run.id} scope="col" className="px-3 py-2 font-medium text-zinc-200">
                            <span className="inline-flex items-center gap-1.5">
                              <span
                                aria-hidden="true"
                                className="h-2 w-2 rounded-sm"
                                style={{ backgroundColor: VARIANT_COLORS[idx % VARIANT_COLORS.length] }}
                              />
                              <span className="max-w-[10rem] truncate">{run.name}</span>
                            </span>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/70">
                      {hpRows.length === 0 ? (
                        <tr>
                          <td colSpan={runs.length + 1} className="px-3 py-4 text-center text-zinc-400">
                            {diffOnly
                              ? "All hyperparameters are identical across the selected runs."
                              : "No hyperparameters recorded for the selected runs."}
                          </td>
                        </tr>
                      ) : (
                        hpRows.map((row) => (
                          <tr
                            key={row.key}
                            className={row.hasDiff ? "bg-amber-950/20" : "hover:bg-zinc-900/40"}
                          >
                            <th scope="row" className="px-3 py-2 font-mono font-normal text-zinc-300">
                              <span className="inline-flex items-center gap-1.5">
                                {row.key}
                                {row.hasDiff && (
                                  <span className="rounded border border-amber-800/70 bg-amber-950/40 px-1 py-0.5 text-[10px] text-amber-300">
                                    diff
                                  </span>
                                )}
                              </span>
                            </th>
                            {runs.map((run) => {
                              const val = row.values[run.id];
                              return (
                                <td
                                  key={run.id}
                                  className={`px-3 py-2 font-mono tabular-nums ${
                                    row.hasDiff ? "text-amber-200" : "text-zinc-300"
                                  }`}
                                >
                                  {val === undefined ? "—" : formatValue(val)}
                                </td>
                              );
                            })}
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
