"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { useCan } from "@/components/auth/role-context";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { explain, proxy } from "@/lib/proxy-client";
import { getSweepStatusStyle, normalizeImportance } from "@/lib/sweeps";
import type {
  CanonicalSweep,
  CanonicalSweepDetail,
  CanonicalSweepRun,
  CanonicalSweepStatus,
} from "@/lib/types";

export interface SweepDetailClientProps {
  sweepId: string;
  initialDetail: CanonicalSweepDetail | null;
  initialError?: string | null;
}

type SortKey = "best_value" | "last_value" | "step" | "status";

export function SweepDetailClient({
  sweepId,
  initialDetail,
  initialError = null,
}: SweepDetailClientProps) {
  const canEdit = useCan("member");
  const [detail, setDetail] = useState<CanonicalSweepDetail | null>(initialDetail);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [statusBusy, setStatusBusy] = useState(false);

  const [sortKey, setSortKey] = useState<SortKey>("best_value");
  const [sortDir, setSortDir] = useState<"asc" | "desc">(
    initialDetail?.sweep?.config?.metric?.goal === "maximize" ? "desc" : "asc",
  );
  const [hidePruned, setHidePruned] = useState(false);

  const sweep = detail?.sweep ?? null;
  const runs = detail?.runs ?? [];
  const importanceEntries = useMemo(
    () => normalizeImportance(detail?.importance),
    [detail?.importance],
  );

  const paramKeys = useMemo(() => {
    const keys = new Set<string>(Object.keys(sweep?.config?.parameters ?? {}));
    for (const r of runs) {
      for (const k of Object.keys(r.params ?? {})) keys.add(k);
    }
    return [...keys];
  }, [sweep, runs]);

  const prunedCount = useMemo(() => runs.filter((r) => r.pruned).length, [runs]);
  const finishedCount = useMemo(
    () => runs.filter((r) => r.status === "finished" || r.status === "completed").length,
    [runs],
  );

  const sortedRuns = useMemo(() => {
    const filtered = hidePruned ? runs.filter((r) => !r.pruned) : runs;
    return [...filtered].sort((a, b) => {
      const mul = sortDir === "asc" ? 1 : -1;
      if (sortKey === "status") {
        return a.status.localeCompare(b.status) * mul;
      }
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (Number(av) - Number(bv)) * mul;
    });
  }, [runs, hidePruned, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(
        key === "best_value" && sweep?.config?.metric?.goal === "maximize" ? "desc" : "asc",
      );
    }
  }

  async function handleRefresh() {
    setLoading(true);
    setError(null);
    try {
      const next = await proxy<CanonicalSweepDetail>(
        "GET",
        `sweeps/${encodeURIComponent(sweepId)}`,
      );
      setDetail(next);
    } catch (err) {
      setError(explain(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleStatusPatch(newStatus: CanonicalSweepStatus) {
    setStatusBusy(true);
    setError(null);
    try {
      const updated = await proxy<CanonicalSweep>(
        "PATCH",
        `sweeps/${encodeURIComponent(sweepId)}`,
        { status: newStatus },
      );
      setDetail((prev) => (prev ? { ...prev, sweep: updated } : prev));
    } catch (err) {
      setError(explain(err));
    } finally {
      setStatusBusy(false);
    }
  }

  if (loading && !detail) {
    return (
      <div role="status" aria-label="Loading sweep details" className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
        <Skeleton className="h-56 w-full" />
        <span className="sr-only">Loading sweep details…</span>
      </div>
    );
  }

  if (!sweep) {
    return (
      <div className="space-y-4">
        <Link href="/sweeps" className="text-xs text-emerald-300 hover:underline">
          ← Back to Sweeps
        </Link>
        {error ? (
          <div
            role="alert"
            className="flex items-center justify-between rounded-lg border border-red-800/70 bg-red-950/40 p-4 text-xs text-red-200"
          >
            <span>{error}</span>
            <button
              type="button"
              onClick={handleRefresh}
              className="rounded border border-red-700 bg-red-900/50 px-3 py-1 font-medium text-red-100"
            >
              Retry
            </button>
          </div>
        ) : (
          <EmptyState title="Sweep not found" />
        )}
      </div>
    );
  }

  const st = getSweepStatusStyle(sweep.status);
  const earlyStop = sweep.config?.early_stop;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2 text-xs">
        <Link href="/sweeps" className="text-emerald-300 hover:underline">
          ← Back to Sweeps
        </Link>
        <Link
          href={`/lineage?node=${encodeURIComponent(`sweep:${sweep.id}`)}`}
          className="rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-zinc-200 hover:bg-zinc-800"
        >
          Inspect Lineage DAG →
        </Link>
      </div>

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2.5">
            <span>{sweep.name}</span>
            <span
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-medium ${st.badge}`}
            >
              <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
              {st.label}
            </span>
          </span>
        }
        subtitle={
          <span className="font-mono text-xs text-zinc-400">
            {sweep.id} · method: {sweep.config?.method} · metric: {sweep.config?.metric?.key} (
            {sweep.config?.metric?.goal}) · created {formatTimestamp(sweep.created_at)}
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleRefresh}
              className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
            >
              Refresh
            </button>
            {canEdit && sweep.status === "running" && (
              <button
                type="button"
                disabled={statusBusy}
                onClick={() => handleStatusPatch("paused")}
                className="rounded border border-amber-700/80 bg-amber-950/60 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-900/70 disabled:opacity-50"
              >
                Pause
              </button>
            )}
            {canEdit && sweep.status === "paused" && (
              <button
                type="button"
                disabled={statusBusy}
                onClick={() => handleStatusPatch("running")}
                className="rounded border border-emerald-700/80 bg-emerald-950/60 px-3 py-1.5 text-xs font-semibold text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
              >
                Resume
              </button>
            )}
            {canEdit && (sweep.status === "running" || sweep.status === "paused") && (
              <button
                type="button"
                disabled={statusBusy}
                onClick={() => handleStatusPatch("stopped")}
                className="rounded border border-red-700/80 bg-red-950/60 px-3 py-1.5 text-xs font-semibold text-red-200 hover:bg-red-900/70 disabled:opacity-50"
              >
                Stop Sweep
              </button>
            )}
          </div>
        }
      />

      {error && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-lg border border-red-800/70 bg-red-950/40 p-3.5 text-xs text-red-200"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={handleRefresh}
            className="rounded border border-red-700 bg-red-900/50 px-3 py-1 font-medium text-red-100"
          >
            Retry
          </button>
        </div>
      )}

      {/* KPIs */}
      <section aria-label="Sweep KPIs" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Trials (Finished)
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
            {runs.length}
            <span className="ml-1.5 text-sm font-normal text-zinc-400">
              ({finishedCount} done)
            </span>
          </div>
        </div>

        <div className="rounded-lg border border-emerald-800/60 bg-emerald-950/20 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-emerald-300">
            Best {sweep.config?.metric?.key}
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-emerald-200">
            {sweep.best_value !== null && sweep.best_value !== undefined
              ? sweep.best_value.toFixed(4)
              : "—"}
          </div>
          {sweep.best_run_id && (
            <Link
              href={`/runs/${encodeURIComponent(sweep.best_run_id)}`}
              className="mt-0.5 inline-block font-mono text-[11px] text-emerald-300 hover:underline"
            >
              Best run: {sweep.best_run_id.slice(0, 12)} →
            </Link>
          )}
        </div>

        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Pruned Trials
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-amber-300">
            {prunedCount}
          </div>
          <div className="mt-0.5 text-[11px] text-zinc-400">
            {runs.length > 0 ? `${Math.round((prunedCount / runs.length) * 100)}% pruned` : "0%"}
          </div>
        </div>

        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Early Stopping
          </div>
          <div className="mt-1 font-mono text-lg font-semibold uppercase text-violet-200">
            {earlyStop ? earlyStop.type : "Disabled"}
          </div>
          <div className="mt-0.5 font-mono text-[11px] text-zinc-400">
            {earlyStop
              ? `min_iter=${earlyStop.min_iter}, eta=${earlyStop.eta ?? 3}`
              : "All trials run to completion"}
          </div>
        </div>
      </section>

      {/* Hyperparameter Importance Bar Chart */}
      <section
        aria-label="Hyperparameter Importance"
        className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 pb-2.5">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">
              Hyperparameter Importance
            </h2>
            <p className="text-xs text-zinc-400">
              Random Forest mean impurity decrease normalized to 100% across finished trials
            </p>
          </div>
        </div>

        {importanceEntries.length > 0 ? (
          <div className="mt-4 space-y-3" role="list" aria-label="Parameter importance scores">
            {importanceEntries.map((item) => {
              const pct = Math.max(0, Math.min(100, item.score * 100));
              return (
                <div key={item.param} role="listitem" className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono font-medium text-zinc-200">{item.param}</span>
                    <span className="font-mono tabular-nums text-emerald-300">
                      {pct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="h-2.5 w-full overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className="h-full rounded-full bg-emerald-500 transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div
            role="status"
            className="mt-4 rounded-md border border-zinc-800 bg-zinc-950/60 px-4 py-3 text-xs text-zinc-300"
          >
            {detail?.importance_note ||
              "Hyperparameter importance is unavailable until at least 10 trials finish (and scikit-learn analytics is installed on the server)."}
          </div>
        )}
      </section>

      {/* Trials / Runs Table */}
      <section
        aria-label="Sweep trials"
        className="rounded-lg border border-zinc-800 bg-zinc-900/60 overflow-hidden"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">
              Trials ({sortedRuns.length})
            </h2>
            <p className="text-xs text-zinc-400">
              Click column headers to sort. Best trial is highlighted.
            </p>
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-zinc-300">
            <input
              type="checkbox"
              checked={hidePruned}
              onChange={(e) => setHidePruned(e.target.checked)}
              className="rounded border-zinc-700 bg-zinc-900 text-emerald-500"
            />
            Hide pruned trials ({prunedCount})
          </label>
        </div>

        {sortedRuns.length === 0 ? (
          <div className="p-8">
            <EmptyState title="No trials recorded yet">
              <p className="text-xs text-zinc-400">
                Start a sweep agent with{" "}
                <code className="font-mono text-zinc-200">
                  matrix_sdk.agent(&quot;{sweep.id}&quot;, function=train)
                </code>
                .
              </p>
            </EmptyState>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-zinc-800 bg-zinc-950/60 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                  <th scope="col" className="px-3 py-2.5">
                    Run
                  </th>
                  <th scope="col" className="px-3 py-2.5">
                    <button
                      type="button"
                      onClick={() => toggleSort("status")}
                      className="hover:text-zinc-100"
                    >
                      Status {sortKey === "status" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right">
                    <button
                      type="button"
                      onClick={() => toggleSort("best_value")}
                      className="hover:text-zinc-100"
                    >
                      Best {sweep.config?.metric?.key}{" "}
                      {sortKey === "best_value" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right">
                    <button
                      type="button"
                      onClick={() => toggleSort("last_value")}
                      className="hover:text-zinc-100"
                    >
                      Last {sortKey === "last_value" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right">
                    <button
                      type="button"
                      onClick={() => toggleSort("step")}
                      className="hover:text-zinc-100"
                    >
                      Step {sortKey === "step" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </th>
                  {paramKeys.map((pk) => (
                    <th key={pk} scope="col" className="px-3 py-2.5 font-mono">
                      {pk}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70 font-mono">
                {sortedRuns.map((run: CanonicalSweepRun) => {
                  const isBest = sweep.best_run_id === run.run_id;
                  return (
                    <tr
                      key={run.run_id}
                      className={
                        isBest
                          ? "bg-emerald-950/30 ring-1 ring-inset ring-emerald-700/60"
                          : run.pruned
                            ? "bg-zinc-950/40 text-zinc-400"
                            : "hover:bg-zinc-800/40"
                      }
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <Link
                            href={`/runs/${encodeURIComponent(run.run_id)}`}
                            className="text-emerald-300 hover:underline"
                          >
                            {run.run_id.slice(0, 12)}
                          </Link>
                          {isBest && (
                            <span className="rounded border border-emerald-600/80 bg-emerald-950/80 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-200">
                              Best
                            </span>
                          )}
                          {run.pruned && (
                            <span className="rounded border border-amber-700/80 bg-amber-950/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-200">
                              Pruned
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-zinc-200">{run.status}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-zinc-100">
                        {run.best_value !== null && run.best_value !== undefined
                          ? run.best_value.toFixed(4)
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                        {run.last_value !== null && run.last_value !== undefined
                          ? run.last_value.toFixed(4)
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                        {run.step ?? "—"}
                      </td>
                      {paramKeys.map((pk) => {
                        const val = run.params?.[pk];
                        return (
                          <td key={pk} className="px-3 py-2 text-zinc-300">
                            {val === undefined || val === null
                              ? "—"
                              : typeof val === "number"
                                ? Number.isInteger(val)
                                  ? val
                                  : val.toPrecision(4)
                                : String(val)}
                          </td>
                        );
                      })}
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
