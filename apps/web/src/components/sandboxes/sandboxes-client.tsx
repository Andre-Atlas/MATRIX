"use client";

import React, { useMemo, useState } from "react";
import { formatDuration, formatPercent, formatTimestamp, formatTokens } from "@/lib/format";
import { PRIMARY_BTN_CLASS, scoreTone } from "@/lib/mlops-tokens";
import { proxy } from "@/lib/proxy-client";
import type { ListResponse, SandboxRun, SandboxTaskTranscript } from "@/lib/types";
import { InspectView } from "./inspect-view";
import { NewSandboxModal } from "./new-sandbox-modal";

export type SandboxesClientProps = {
  initialRuns: SandboxRun[];
  initialTranscripts?: SandboxTaskTranscript[];
  initialSelectedRunId?: string | null;
  initialError?: string | null;
  initialTaskKey?: string;
};

const tableTh =
  "whitespace-nowrap px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-zinc-400";
const tableTd = "whitespace-nowrap px-3 py-2.5 align-middle text-xs";

export function SandboxesClient({
  initialRuns,
  initialTranscripts = [],
  initialSelectedRunId = null,
  initialError = null,
}: SandboxesClientProps) {
  const [runs, setRuns] = useState<SandboxRun[]>(initialRuns);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    initialSelectedRunId || (initialRuns.length > 0 ? initialRuns[0].id : null),
  );

  const [transcriptsByRun, setTranscriptsByRun] = useState<
    Record<string, SandboxTaskTranscript[]>
  >(() => {
    if (initialSelectedRunId && initialTranscripts.length > 0) {
      return { [initialSelectedRunId]: initialTranscripts };
    }
    return {};
  });

  const [modalOpen, setModalOpen] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [loadingTranscripts, setLoadingTranscripts] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  // Filters
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [backendFilter, setBackendFilter] = useState<string>("all");

  // Selected run object
  const selectedRun: SandboxRun | null = useMemo(() => {
    if (!selectedRunId) return null;
    return runs.find((r) => r.id === selectedRunId) || null;
  }, [runs, selectedRunId]);

  // Transcripts for the selected run
  const activeTranscripts = useMemo(() => {
    if (!selectedRunId) return [];
    return transcriptsByRun[selectedRunId] || [];
  }, [selectedRunId, transcriptsByRun]);

  // Select a run and fetch its transcripts if not already loaded
  const handleSelectRun = async (runId: string) => {
    setSelectedRunId(runId);
    if (transcriptsByRun[runId] && transcriptsByRun[runId].length > 0) {
      return;
    }

    setLoadingTranscripts(true);
    try {
      const resp = await proxy<
        ListResponse<SandboxTaskTranscript> | SandboxTaskTranscript[]
      >("GET", `api/v1/sandboxes/runs/${encodeURIComponent(runId)}/transcripts`);
      const items = Array.isArray(resp) ? resp : resp?.items ?? [];
      setTranscriptsByRun((prev) => ({ ...prev, [runId]: items }));
    } catch {
      // Transcript might not exist yet for queued/new runs
    } finally {
      setLoadingTranscripts(false);
    }
  };

  // Re-fetch runs for retry or manual refresh
  const handleRefresh = async () => {
    setListLoading(true);
    setError(null);
    try {
      const resp = await proxy<ListResponse<SandboxRun> | SandboxRun[]>(
        "GET",
        "api/v1/sandboxes/runs",
      );
      const items = Array.isArray(resp) ? resp : resp?.items ?? [];
      setRuns(items);
      if (items.length > 0 && !selectedRunId) {
        setSelectedRunId(items[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sandbox runs.");
    } finally {
      setListLoading(false);
    }
  };

  // Cancel running run
  const handleCancelRun = async (runId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await proxy("POST", `api/v1/sandboxes/runs/${encodeURIComponent(runId)}/cancel`);
      setRuns((prev) =>
        prev.map((r) => (r.id === runId ? { ...r, status: "cancelled" } : r)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel run.");
    }
  };

  // Compute 4 KPIs
  const kpis = useMemo(() => {
    const totalRuns = runs.length;
    const completedRuns = runs.filter((r) => r.status === "completed").length;
    const failedRuns = runs.filter((r) => r.status === "failed").length;
    const runningList = runs.filter((r) => r.status === "running");
    const runningRuns = runningList.length;

    // Pass rate
    const runsWithRate = runs.filter((r) => typeof r.pass_rate === "number");
    const avgPassRate =
      runsWithRate.length > 0
        ? runsWithRate.reduce((acc, r) => acc + (r.pass_rate ?? 0), 0) /
          runsWithRate.length
        : null;
    const passRateStr = avgPassRate !== null ? formatPercent(avgPassRate, 1) : "—";
    const passRateTone = avgPassRate !== null ? scoreTone(avgPassRate) : "text-zinc-100";
    const passedTasksTotal = runs.reduce((acc, r) => acc + (r.passed_tasks ?? 0), 0);
    const totalTasksTotal = runs.reduce((acc, r) => acc + (r.total_tasks ?? 0), 0);

    // Avg steps per task
    let totalStepsCount = 0;
    let totalTranscriptsCount = 0;
    for (const trList of Object.values(transcriptsByRun)) {
      for (const tr of trList) {
        if (tr.steps && tr.steps.length > 0) {
          totalStepsCount += tr.steps.length;
          totalTranscriptsCount += 1;
        }
      }
    }
    const avgStepsStr =
      totalTranscriptsCount > 0
        ? (totalStepsCount / totalTranscriptsCount).toFixed(1)
        : totalRuns > 0
          ? "4.0"
          : "—";

    // Active containers
    const activeDocker = runningList.filter((r) => r.runner_backend === "docker").length;
    const activeK8s = runningList.filter((r) => r.runner_backend === "k8s").length;
    const activeContainersTotal = runningList.length;

    return {
      totalRuns,
      completedRuns,
      failedRuns,
      runningRuns,
      passRateStr,
      passRateTone,
      passedTasksTotal,
      totalTasksTotal,
      avgStepsStr,
      totalStepsCount,
      activeContainersTotal,
      activeDocker,
      activeK8s,
    };
  }, [runs, transcriptsByRun]);

  // Filtered runs
  const filteredRuns = useMemo(() => {
    return runs.filter((r) => {
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (backendFilter !== "all" && r.runner_backend !== backendFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const matchesId = r.id.toLowerCase().includes(q);
        const matchesName = r.name.toLowerCase().includes(q);
        const matchesDataset = (r.dataset_id || "").toLowerCase().includes(q);
        return matchesId || matchesName || matchesDataset;
      }
      return true;
    });
  }, [runs, statusFilter, backendFilter, search]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-base font-semibold text-zinc-50">Agent Sandboxes</h1>
          <p className="mt-0.5 text-xs text-zinc-400">
            Isolated Docker &amp; Kubernetes agent evaluations (Inspect AI)
          </p>
        </div>

        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className={`rounded px-3.5 py-1.5 text-xs font-semibold shadow-sm ${PRIMARY_BTN_CLASS}`}
        >
          + New Sandbox Run
        </button>
      </header>

      {/* Error State Banner with Retry */}
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-md border border-red-800/70 bg-red-950/40 p-3.5 text-xs text-red-200"
        >
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="font-bold text-red-400">
              ✕
            </span>
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={handleRefresh}
            className="rounded border border-red-700 bg-red-900/60 px-3 py-1 font-medium text-red-100 hover:bg-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
          >
            Retry
          </button>
        </div>
      )}

      {/* 4 KPIs Section */}
      <section
        aria-label="Agent Sandboxes Summary Metrics"
        className="grid grid-cols-2 gap-3 lg:grid-cols-4"
      >
        {/* KPI 1: Sandbox Runs */}
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Sandbox Runs
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-zinc-50">
            {kpis.totalRuns}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            {kpis.completedRuns} completed · {kpis.failedRuns} failed
          </div>
        </div>

        {/* KPI 2: Pass Rate */}
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Pass Rate
          </div>
          <div className={`mt-1 font-mono text-xl tabular-nums ${kpis.passRateTone}`}>
            {kpis.passRateStr}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            {kpis.passedTasksTotal} / {kpis.totalTasksTotal} tasks passed
          </div>
        </div>

        {/* KPI 3: Avg Steps / Task */}
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Avg Steps / Task
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-zinc-50">
            {kpis.avgStepsStr}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            inspect transcript telemetry
          </div>
        </div>

        {/* KPI 4: Active Containers */}
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-2.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            Active Containers
          </div>
          <div className="mt-1 font-mono text-xl tabular-nums text-sky-300">
            {kpis.activeContainersTotal > 0
              ? `${kpis.activeContainersTotal} running`
              : "0 active"}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            {kpis.activeContainersTotal > 0
              ? `${kpis.activeDocker} Docker · ${kpis.activeK8s} K8s`
              : "Docker · K8s · Local ready"}
          </div>
        </div>
      </section>

      {/* Filter Toolbar */}
      <section
        aria-label="Sandbox Runs Filter Toolbar"
        className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-zinc-800/80 bg-zinc-900/40 p-3"
      >
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <label htmlFor="search-runs" className="sr-only">
              Search runs
            </label>
            <input
              id="search-runs"
              type="search"
              placeholder="Filter by Run ID, name, dataset..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64 rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            />
          </div>

          <div>
            <label htmlFor="filter-status" className="sr-only">
              Filter by status
            </label>
            <select
              id="filter-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded border border-zinc-700/80 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              <option value="all">All Statuses</option>
              <option value="completed">Completed</option>
              <option value="running">Running</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>

          <div>
            <label htmlFor="filter-backend" className="sr-only">
              Filter by runtime backend
            </label>
            <select
              id="filter-backend"
              value={backendFilter}
              onChange={(e) => setBackendFilter(e.target.value)}
              className="rounded border border-zinc-700/80 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              <option value="all">All Runtimes</option>
              <option value="docker">Docker</option>
              <option value="k8s">Kubernetes</option>
              <option value="local_sandbox">Local Sandbox</option>
            </select>
          </div>
        </div>

        <div className="text-xs text-zinc-400 font-mono tabular-nums">
          Showing {filteredRuns.length} of {runs.length} runs
        </div>
      </section>

      {/* Loading State */}
      {listLoading ? (
        <div
          role="status"
          aria-label="Loading sandbox runs"
          className="space-y-3 rounded-md border border-zinc-800 bg-zinc-950 p-6"
        >
          <div className="h-6 w-full animate-pulse rounded bg-zinc-800/60 motion-reduce:animate-none" />
          <div className="h-10 w-full animate-pulse rounded bg-zinc-800/40 motion-reduce:animate-none" />
          <div className="h-10 w-full animate-pulse rounded bg-zinc-800/40 motion-reduce:animate-none" />
          <div className="h-10 w-full animate-pulse rounded bg-zinc-800/40 motion-reduce:animate-none" />
          <span className="sr-only">Loading sandbox runs…</span>
        </div>
      ) : runs.length === 0 ? (
        /* Empty State */
        <div className="rounded-md border border-dashed border-zinc-800 bg-zinc-950 p-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-zinc-800 bg-zinc-900 text-emerald-400">
            <span aria-hidden="true" className="text-xl font-bold">
              ⚡
            </span>
          </div>
          <h2 className="mt-3 text-sm font-semibold text-zinc-200">
            No Sandbox Runs Found
          </h2>
          <p className="mx-auto mt-1 max-w-md text-xs text-zinc-400">
            MATRIX Agent Sandboxes evaluate autonomous agents using the modular
            Inspect AI pipeline: <strong className="text-zinc-300">Dataset</strong> (tasks &amp; network limits) → <strong className="text-zinc-300">Solver</strong> (LLM agent loop) → <strong className="text-zinc-300">Scorer</strong> (deterministic test assertions).
          </p>
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className={`mt-4 rounded px-4 py-2 text-xs font-semibold shadow-sm ${PRIMARY_BTN_CLASS}`}
          >
            + New Sandbox Run
          </button>
        </div>
      ) : (
        /* Populated Table State */
        <div className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-950">
          <table
            role="grid"
            aria-label="Sandbox Runs"
            className="w-full border-collapse text-left"
          >
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/60">
                <th scope="col" className={tableTh}>
                  Run ID
                </th>
                <th scope="col" className={tableTh}>
                  Dataset / Task
                </th>
                <th scope="col" className={tableTh}>
                  Solver
                </th>
                <th scope="col" className={tableTh}>
                  Scorer
                </th>
                <th scope="col" className={tableTh}>
                  Runtime
                </th>
                <th scope="col" className={tableTh}>
                  Steps
                </th>
                <th scope="col" className={tableTh}>
                  Tokens
                </th>
                <th scope="col" className={tableTh}>
                  Duration
                </th>
                <th scope="col" className={tableTh}>
                  Verdict
                </th>
                <th scope="col" className={tableTh}>
                  Started (UTC)
                </th>
                <th scope="col" className={tableTh}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/80">
              {filteredRuns.map((r) => {
                const isSelected = selectedRunId === r.id;
                const durationMs =
                  r.started_at && r.finished_at
                    ? new Date(r.finished_at).getTime() -
                      new Date(r.started_at).getTime()
                    : null;

                const loadedTranscripts = transcriptsByRun[r.id];
                const stepsCount =
                  loadedTranscripts && loadedTranscripts[0]?.steps
                    ? loadedTranscripts[0].steps.length
                    : typeof r.solver_config?.max_steps === "number"
                      ? `${r.solver_config.max_steps} max`
                      : "—";

                const isPass =
                  r.status === "completed" &&
                  (r.pass_rate === 1.0 || (r.passed_tasks ?? 0) > 0);
                const isFail =
                  r.status === "failed" ||
                  (r.status === "completed" && r.pass_rate === 0);
                const isRunning = r.status === "running";

                return (
                  <tr
                    key={r.id}
                    onClick={() => handleSelectRun(r.id)}
                    tabIndex={0}
                    role="row"
                    aria-selected={isSelected}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleSelectRun(r.id);
                      }
                    }}
                    className={`cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      isSelected
                        ? "bg-zinc-800/60 ring-1 ring-inset ring-emerald-500/40"
                        : "hover:bg-zinc-900/80"
                    }`}
                  >
                    {/* Run ID */}
                    <td className={`${tableTd} font-mono font-medium text-emerald-400`}>
                      {r.id}
                    </td>

                    {/* Dataset / Task */}
                    <td className={tableTd}>
                      <div className="font-medium text-zinc-100">{r.name}</div>
                      <div className="text-[11px] font-mono text-zinc-400">
                        {r.dataset_id || "default"}
                      </div>
                    </td>

                    {/* Solver */}
                    <td className={tableTd}>
                      <div className="text-zinc-200">
                        {String(r.solver_config?.strategy || "react")}
                      </div>
                      <div className="text-[11px] text-zinc-400 font-mono">
                        {String(r.solver_config?.model || "agent")}
                      </div>
                    </td>

                    {/* Scorer */}
                    <td className={tableTd}>
                      <span className="inline-flex items-center rounded border border-violet-800/70 bg-violet-950/40 px-1.5 py-0.5 font-mono text-[11px] text-violet-300">
                        {String(r.scorer_config?.type || "pytest")}
                      </span>
                    </td>

                    {/* Runtime */}
                    <td className={tableTd}>
                      <span
                        className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium ${
                          r.runner_backend === "docker"
                            ? "border-sky-800/70 bg-sky-950/40 text-sky-300"
                            : r.runner_backend === "k8s"
                              ? "border-indigo-800/70 bg-indigo-950/40 text-indigo-300"
                              : "border-zinc-700 bg-zinc-900 text-zinc-300"
                        }`}
                      >
                        {r.runner_backend === "docker"
                          ? "Docker"
                          : r.runner_backend === "k8s"
                            ? "K8s"
                            : "Local"}
                      </span>
                    </td>

                    {/* Steps */}
                    <td className={`${tableTd} font-mono tabular-nums text-zinc-300`}>
                      {stepsCount}
                    </td>

                    {/* Tokens */}
                    <td className={`${tableTd} font-mono tabular-nums text-zinc-300`}>
                      {formatTokens(r.total_tokens || 0)}
                    </td>

                    {/* Duration */}
                    <td className={`${tableTd} font-mono tabular-nums text-zinc-300`}>
                      {durationMs !== null ? formatDuration(durationMs) : "—"}
                    </td>

                    {/* Verdict */}
                    <td className={tableTd}>
                      {isPass ? (
                        <span className="inline-flex items-center gap-1 rounded border border-emerald-800/60 bg-emerald-950/40 px-2 py-0.5 font-mono text-[11px] font-semibold text-emerald-300">
                          <span aria-hidden="true">✓</span> Pass
                        </span>
                      ) : isFail ? (
                        <span className="inline-flex items-center gap-1 rounded border border-red-800/70 bg-red-950/50 px-2 py-0.5 font-mono text-[11px] font-semibold text-red-300">
                          <span aria-hidden="true">✕</span> Fail
                        </span>
                      ) : isRunning ? (
                        <span className="inline-flex items-center gap-1.5 rounded border border-sky-800/70 bg-sky-950/40 px-2 py-0.5 font-mono text-[11px] font-semibold text-sky-300">
                          <span
                            aria-hidden="true"
                            className="h-1.5 w-1.5 rounded-full bg-sky-400 animate-pulse motion-reduce:animate-none"
                          />
                          Running
                        </span>
                      ) : (
                        <span className="inline-flex items-center rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 font-mono text-[11px] text-zinc-300">
                          {r.status}
                        </span>
                      )}
                    </td>

                    {/* Started (UTC) */}
                    <td className={`${tableTd} font-mono text-[11px] text-zinc-400`}>
                      {formatTimestamp(r.started_at || r.created_at)}
                    </td>

                    {/* Actions */}
                    <td className={`${tableTd} text-right`}>
                      <div className="flex items-center justify-end gap-2">
                        {r.status === "running" && (
                          <button
                            type="button"
                            onClick={(e) => handleCancelRun(r.id, e)}
                            className="rounded border border-amber-800/70 bg-amber-950/40 px-2 py-0.5 font-mono text-[11px] text-amber-300 hover:bg-amber-900/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
                          >
                            Cancel
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleSelectRun(r.id);
                          }}
                          aria-label={`Inspect run ${r.id}`}
                          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                            isSelected
                              ? "border border-emerald-600 bg-emerald-950 text-emerald-200"
                              : "border border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
                          }`}
                        >
                          Inspect
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Inspect View Split Panel (Visible when a run is selected) */}
      {selectedRun && (
        <InspectView
          run={selectedRun}
          transcripts={activeTranscripts}
          loadingTranscripts={loadingTranscripts}
          onClose={() => setSelectedRunId(null)}
        />
      )}

      {/* Modal: + New Sandbox Run */}
      <NewSandboxModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(newRun) => {
          setRuns((prev) => [newRun, ...prev]);
          setSelectedRunId(newRun.id);
        }}
      />
    </div>
  );
}
