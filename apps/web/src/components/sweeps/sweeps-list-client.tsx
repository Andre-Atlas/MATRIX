"use client";

import React, { useEffect, useId, useMemo, useState } from "react";
import Link from "next/link";
import { useCan } from "@/components/auth/role-context";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { explain, proxy } from "@/lib/proxy-client";
import { getSweepStatusStyle, validateSweepDraft } from "@/lib/sweeps";
import type {
  CanonicalSweep,
  CanonicalSweepConfig,
  CanonicalSweepStatus,
  ListResponse,
} from "@/lib/types";

export interface SweepsListClientProps {
  initialSweeps: CanonicalSweep[];
  initialError?: string | null;
}

const DEFAULT_PARAMS_JSON = JSON.stringify(
  {
    lr: { type: "float", min: 0.00001, max: 0.001, log: true },
    batch_size: { type: "choice", values: [16, 32, 64] },
    lora_r: { type: "choice", values: [8, 16, 32] },
  },
  null,
  2,
);

export function SweepsListClient({
  initialSweeps,
  initialError = null,
}: SweepsListClientProps) {
  const canEdit = useCan("member");
  const [sweeps, setSweeps] = useState<CanonicalSweep[]>(initialSweeps);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [createOpen, setCreateOpen] = useState(false);

  // Create form state
  const [name, setName] = useState("");
  const [method, setMethod] = useState<"grid" | "random" | "tpe">("tpe");
  const [metricKey, setMetricKey] = useState("val_loss");
  const [metricGoal, setMetricGoal] = useState<"minimize" | "maximize">("minimize");
  const [maxRuns, setMaxRuns] = useState("24");
  const [seed, setSeed] = useState("42");
  const [enableEarlyStop, setEnableEarlyStop] = useState(true);
  const [earlyStopType, setEarlyStopType] = useState<"asha" | "hyperband">("asha");
  const [minIter, setMinIter] = useState("2");
  const [eta, setEta] = useState("3");
  const [paramsJson, setParamsJson] = useState(DEFAULT_PARAMS_JSON);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const dialogTitleId = useId();

  useEffect(() => {
    if (!createOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setCreateOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [createOpen]);

  const parsedDraft = useMemo(() => {
    try {
      const parameters = JSON.parse(paramsJson);
      const config: CanonicalSweepConfig = {
        method,
        metric: { key: metricKey.trim(), goal: metricGoal },
        parameters,
        ...(maxRuns.trim() ? { max_runs: Number(maxRuns) } : {}),
        ...(seed.trim() ? { seed: Number(seed) } : {}),
        ...(enableEarlyStop
          ? {
              early_stop: {
                type: earlyStopType,
                min_iter: Number(minIter) || 1,
                eta: Number(eta) || 3,
              },
            }
          : {}),
      };
      return {
        parseError: null as string | null,
        config,
        validation: validateSweepDraft(name, config),
      };
    } catch (err) {
      return {
        parseError: err instanceof Error ? err.message : "Invalid JSON in parameters.",
        config: null,
        validation: { valid: false, errors: ["Parameters must be valid JSON."], gridSize: null },
      };
    }
  }, [name, method, metricKey, metricGoal, maxRuns, seed, enableEarlyStop, earlyStopType, minIter, eta, paramsJson]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sweeps.filter((s) => {
      if (statusFilter !== "all" && s.status !== statusFilter) return false;
      if (q) {
        const mKey = s.config?.metric?.key ?? "";
        return (
          s.name.toLowerCase().includes(q) ||
          s.id.toLowerCase().includes(q) ||
          mKey.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [sweeps, search, statusFilter]);

  const kpis = useMemo(() => {
    const total = sweeps.length;
    const running = sweeps.filter((s) => s.status === "running").length;
    const paused = sweeps.filter((s) => s.status === "paused").length;
    const totalTrials = sweeps.reduce((acc, s) => acc + (s.run_count || 0), 0);
    return { total, running, paused, totalTrials };
  }, [sweeps]);

  async function handleRefresh() {
    setLoading(true);
    setError(null);
    try {
      const resp = await proxy<ListResponse<CanonicalSweep>>("GET", "sweeps");
      setSweeps(Array.isArray(resp?.items) ? resp.items : []);
    } catch (err) {
      setError(explain(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleStatusChange(sweepId: string, status: CanonicalSweepStatus) {
    setBusyId(sweepId);
    setActionError(null);
    try {
      const updated = await proxy<CanonicalSweep>(
        "PATCH",
        `sweeps/${encodeURIComponent(sweepId)}`,
        { status },
      );
      setSweeps((prev) => prev.map((s) => (s.id === sweepId ? updated : s)));
    } catch (err) {
      setActionError(explain(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleCreateSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (parsedDraft.parseError || !parsedDraft.config || !parsedDraft.validation.valid) {
      setFormError(parsedDraft.parseError ?? parsedDraft.validation.errors[0] ?? "Invalid sweep config.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await proxy<CanonicalSweep>("POST", "sweeps", {
        name: name.trim(),
        config: parsedDraft.config,
      });
      setSweeps((prev) => [created, ...prev]);
      setCreateOpen(false);
      setName("");
    } catch (err) {
      setFormError(explain(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Hyperparameter Sweeps"
        subtitle={
          <span className="text-zinc-400">
            Deterministic Grid, Random, and TPE optimization with ASHA / Hyperband early stopping
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleRefresh}
              className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Refresh
            </button>
            {canEdit && (
              <button
                type="button"
                onClick={() => {
                  setFormError(null);
                  setCreateOpen(true);
                }}
                className={`rounded px-3.5 py-1.5 text-xs font-semibold shadow-sm ${PRIMARY_BTN_CLASS}`}
              >
                + New Sweep
              </button>
            )}
          </div>
        }
      />

      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-800/70 bg-red-950/40 p-3.5 text-xs text-red-200"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={handleRefresh}
            className="rounded border border-red-700 bg-red-900/50 px-3 py-1 font-medium text-red-100 hover:bg-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
          >
            Retry
          </button>
        </div>
      )}

      {actionError && (
        <div
          role="alert"
          className="rounded-lg border border-amber-700/70 bg-amber-950/40 p-3 text-xs text-amber-200"
        >
          {actionError}
        </div>
      )}

      {/* KPIs */}
      <section aria-label="Sweeps summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Total Sweeps
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
            {kpis.total}
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Running
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-emerald-300">
            {kpis.running}
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Paused
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-amber-300">
            {kpis.paused}
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Total Trials
          </div>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
            {kpis.totalTrials}
          </div>
        </div>
      </section>

      {/* Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="sweep-search" className="sr-only">
            Search sweeps
          </label>
          <input
            id="sweep-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by name, ID, or metric..."
            className="w-64 rounded border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
          />
          <label htmlFor="sweep-status-filter" className="sr-only">
            Filter by status
          </label>
          <select
            id="sweep-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none"
          >
            <option value="all">All statuses</option>
            <option value="running">Running</option>
            <option value="paused">Paused</option>
            <option value="finished">Finished</option>
            <option value="stopped">Stopped</option>
          </select>
        </div>
        <span className="text-xs text-zinc-400">
          Showing {filtered.length} of {sweeps.length} sweeps
        </span>
      </div>

      {/* Loading state */}
      {loading && (
        <div role="status" aria-label="Loading sweeps" className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full opacity-70" />
          <Skeleton className="h-10 w-full opacity-50" />
          <span className="sr-only">Loading sweeps…</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && filtered.length === 0 && (
        <EmptyState title="No sweeps found">
          <p className="text-xs text-zinc-400">
            Create a sweep to explore hyperparameters with Grid, Random, or TPE search.
          </p>
        </EmptyState>
      )}

      {/* Table */}
      {!loading && filtered.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-900/50">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-950/60 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                <th scope="col" className="px-3.5 py-2.5">
                  Sweep
                </th>
                <th scope="col" className="px-3.5 py-2.5">
                  Method
                </th>
                <th scope="col" className="px-3.5 py-2.5">
                  Target Metric
                </th>
                <th scope="col" className="px-3.5 py-2.5">
                  Pruning
                </th>
                <th scope="col" className="px-3.5 py-2.5">
                  Status
                </th>
                <th scope="col" className="px-3.5 py-2.5 text-right">
                  Trials
                </th>
                <th scope="col" className="px-3.5 py-2.5 text-right">
                  Best Value
                </th>
                <th scope="col" className="px-3.5 py-2.5">
                  Created
                </th>
                {canEdit && (
                  <th scope="col" className="px-3.5 py-2.5 text-right">
                    Controls
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {filtered.map((s) => {
                const st = getSweepStatusStyle(s.status);
                const isBusy = busyId === s.id;
                const earlyStop = s.config?.early_stop;

                return (
                  <tr key={s.id} className="hover:bg-zinc-800/40">
                    <td className="px-3.5 py-2.5">
                      <Link
                        href={`/sweeps/${encodeURIComponent(s.id)}`}
                        className="font-semibold text-emerald-300 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 rounded"
                      >
                        {s.name}
                      </Link>
                      <div className="font-mono text-[11px] text-zinc-400">{s.id}</div>
                    </td>
                    <td className="px-3.5 py-2.5 font-mono uppercase text-zinc-200">
                      {s.config?.method ?? "random"}
                    </td>
                    <td className="px-3.5 py-2.5 font-mono text-zinc-200">
                      <span>{s.config?.metric?.key ?? "loss"}</span>{" "}
                      <span className="text-zinc-400">
                        ({s.config?.metric?.goal ?? "minimize"})
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5 font-mono text-zinc-300">
                      {earlyStop ? (
                        <span className="rounded border border-violet-700/70 bg-violet-950/50 px-1.5 py-0.5 text-[11px] uppercase text-violet-200">
                          {earlyStop.type} (min {earlyStop.min_iter})
                        </span>
                      ) : (
                        <span className="text-zinc-400">None</span>
                      )}
                    </td>
                    <td className="px-3.5 py-2.5">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-medium ${st.badge}`}
                      >
                        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
                        {st.label}
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-zinc-200">
                      {s.run_count}
                      {s.config?.max_runs ? ` / ${s.config.max_runs}` : ""}
                    </td>
                    <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-emerald-300">
                      {s.best_value !== null && s.best_value !== undefined
                        ? s.best_value.toFixed(4)
                        : "—"}
                    </td>
                    <td className="px-3.5 py-2.5 font-mono text-[11px] text-zinc-400">
                      {formatTimestamp(s.created_at)}
                    </td>
                    {canEdit && (
                      <td className="px-3.5 py-2.5 text-right">
                        <div className="inline-flex items-center gap-1.5">
                          {s.status === "running" && (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => handleStatusChange(s.id, "paused")}
                              className="rounded border border-amber-700/80 bg-amber-950/50 px-2 py-0.5 text-[11px] font-medium text-amber-200 hover:bg-amber-900/60 disabled:opacity-50"
                            >
                              Pause
                            </button>
                          )}
                          {s.status === "paused" && (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => handleStatusChange(s.id, "running")}
                              className="rounded border border-emerald-700/80 bg-emerald-950/50 px-2 py-0.5 text-[11px] font-medium text-emerald-200 hover:bg-emerald-900/60 disabled:opacity-50"
                            >
                              Resume
                            </button>
                          )}
                          {(s.status === "running" || s.status === "paused") && (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => handleStatusChange(s.id, "stopped")}
                              className="rounded border border-red-700/80 bg-red-950/50 px-2 py-0.5 text-[11px] font-medium text-red-200 hover:bg-red-900/60 disabled:opacity-50"
                            >
                              Stop
                            </button>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Sweep Modal */}
      {createOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby={dialogTitleId}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget) setCreateOpen(false);
          }}
        >
          <div className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
              <h2 id={dialogTitleId} className="text-base font-semibold text-zinc-100">
                Create Hyperparameter Sweep
              </h2>
              <button
                type="button"
                onClick={() => setCreateOpen(false)}
                aria-label="Close dialog"
                className="rounded px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateSubmit} className="mt-4 space-y-4 text-xs">
              <div>
                <label htmlFor="new-sweep-name" className="block font-medium text-zinc-300">
                  Sweep Name
                </label>
                <input
                  id="new-sweep-name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. llama3-lora-lr-search"
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-zinc-100 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label htmlFor="new-sweep-method" className="block font-medium text-zinc-300">
                    Method
                  </label>
                  <select
                    id="new-sweep-method"
                    value={method}
                    onChange={(e) => setMethod(e.target.value as "grid" | "random" | "tpe")}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-zinc-100 focus:border-emerald-500 focus:outline-none"
                  >
                    <option value="tpe">TPE (Bayesian)</option>
                    <option value="random">Random</option>
                    <option value="grid">Grid</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="new-sweep-metric" className="block font-medium text-zinc-300">
                    Metric Key
                  </label>
                  <input
                    id="new-sweep-metric"
                    required
                    value={metricKey}
                    onChange={(e) => setMetricKey(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 font-mono text-zinc-100 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label htmlFor="new-sweep-goal" className="block font-medium text-zinc-300">
                    Goal
                  </label>
                  <select
                    id="new-sweep-goal"
                    value={metricGoal}
                    onChange={(e) => setMetricGoal(e.target.value as "minimize" | "maximize")}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-zinc-100 focus:border-emerald-500 focus:outline-none"
                  >
                    <option value="minimize">Minimize</option>
                    <option value="maximize">Maximize</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="new-sweep-max-runs" className="block font-medium text-zinc-300">
                    Max Runs
                  </label>
                  <input
                    id="new-sweep-max-runs"
                    type="number"
                    min={1}
                    max={10000}
                    value={maxRuns}
                    onChange={(e) => setMaxRuns(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 font-mono text-zinc-100 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label htmlFor="new-sweep-seed" className="block font-medium text-zinc-300">
                    Seed
                  </label>
                  <input
                    id="new-sweep-seed"
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 font-mono text-zinc-100 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
              </div>

              {/* Early Stopping */}
              <div className="rounded border border-zinc-800 bg-zinc-900/50 p-3 space-y-2">
                <label className="inline-flex items-center gap-2 font-medium text-zinc-200">
                  <input
                    type="checkbox"
                    checked={enableEarlyStop}
                    onChange={(e) => setEnableEarlyStop(e.target.checked)}
                    className="rounded border-zinc-700 bg-zinc-900 text-emerald-500"
                  />
                  Enable Early Stopping (ASHA / Hyperband)
                </label>
                {enableEarlyStop && (
                  <div className="grid grid-cols-3 gap-2 pt-1">
                    <div>
                      <label htmlFor="es-type" className="block text-[11px] text-zinc-400">
                        Strategy
                      </label>
                      <select
                        id="es-type"
                        value={earlyStopType}
                        onChange={(e) => setEarlyStopType(e.target.value as "asha" | "hyperband")}
                        className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-100"
                      >
                        <option value="asha">ASHA</option>
                        <option value="hyperband">Hyperband</option>
                      </select>
                    </div>
                    <div>
                      <label htmlFor="es-min-iter" className="block text-[11px] text-zinc-400">
                        Min Iter
                      </label>
                      <input
                        id="es-min-iter"
                        type="number"
                        min={1}
                        value={minIter}
                        onChange={(e) => setMinIter(e.target.value)}
                        className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 font-mono text-zinc-100"
                      />
                    </div>
                    <div>
                      <label htmlFor="es-eta" className="block text-[11px] text-zinc-400">
                        Eta (Reduction)
                      </label>
                      <input
                        id="es-eta"
                        type="number"
                        min={2}
                        value={eta}
                        onChange={(e) => setEta(e.target.value)}
                        className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 font-mono text-zinc-100"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Search Space JSON */}
              <div>
                <div className="flex items-center justify-between">
                  <label htmlFor="new-sweep-params" className="font-medium text-zinc-300">
                    Search Space (JSON)
                  </label>
                  {method === "grid" && parsedDraft.validation.gridSize !== null && (
                    <span className="font-mono text-[11px] text-emerald-300">
                      Grid combinations: {parsedDraft.validation.gridSize.toLocaleString()}
                    </span>
                  )}
                </div>
                <textarea
                  id="new-sweep-params"
                  rows={6}
                  value={paramsJson}
                  onChange={(e) => setParamsJson(e.target.value)}
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 p-2.5 font-mono text-[11px] text-zinc-100 focus:border-emerald-500 focus:outline-none"
                />
                {parsedDraft.validation.errors.length > 0 && (
                  <ul className="mt-1 list-disc pl-4 text-[11px] text-amber-300">
                    {parsedDraft.validation.errors.map((errMsg) => (
                      <li key={errMsg}>{errMsg}</li>
                    ))}
                  </ul>
                )}
              </div>

              {formError && (
                <div
                  role="alert"
                  className="rounded border border-red-800/70 bg-red-950/40 p-2.5 text-xs text-red-200"
                >
                  {formError}
                </div>
              )}

              <div className="flex items-center justify-end gap-2 border-t border-zinc-800 pt-3">
                <button
                  type="button"
                  onClick={() => setCreateOpen(false)}
                  className="rounded border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting || !parsedDraft.validation.valid}
                  className={`rounded px-3.5 py-1.5 text-xs font-semibold disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
                >
                  {submitting ? "Creating..." : "Create Sweep"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
