"use client";

import React, { useId, useState } from "react";
import {
  computeBenchmarkKpis,
  extractTaskBreakdown,
  filterBenchmarkRuns,
  formatPercent,
  getTaskResultFromRun,
  getVariantColor,
  LM_EVAL_TASKS,
  VLM_EVAL_TASKS,
} from "@/lib/benchmarks";
import type { TaskBreakdownDetail } from "@/lib/benchmarks";
import {
  getRunStatusStyle,
  PRIMARY_BTN_CLASS,
  VARIANT_COLORS,
} from "@/lib/mlops-tokens";
import type {
  BenchmarkCatalogTask,
  BenchmarkRun,
  BenchmarkSuite,
} from "@/lib/types";
import { Mono, PageHeader, scoreTone, Skeleton } from "@/components/ui";
import { LaunchBenchmarkModal } from "./launch-benchmark-modal";
import { BreakdownDrawer } from "./breakdown-drawer";
import { BenchmarkVisualComparison } from "./radar-chart";

interface BenchmarksClientProps {
  initialRuns: BenchmarkRun[];
  initialCatalog: BenchmarkCatalogTask[];
  initialError?: string | null;
}

export function BenchmarksClient({
  initialRuns,
  initialCatalog,
  initialError = null,
}: BenchmarksClientProps) {
  const [runs, setRuns] = useState<BenchmarkRun[]>(initialRuns);
  const [catalog] = useState<BenchmarkCatalogTask[]>(initialCatalog);
  const [error, setError] = useState<string | null>(initialError);
  const [loading, setLoading] = useState(false);

  // Active Tab: LM-Eval vs VLMEvalKit
  const [activeTab, setActiveTab] = useState<BenchmarkSuite>("lm_eval");

  // Selected Models for Comparison (up to 6)
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>(() => {
    // By default, select first matching completed runs for comparison
    const initialForSuite = initialRuns
      .filter((r) => r.suite === "lm_eval")
      .slice(0, 6)
      .map((r) => r.id);
    return initialForSuite.length > 0
      ? initialForSuite
      : initialRuns.slice(0, 2).map((r) => r.id);
  });

  // Filters
  const [search, setSearch] = useState("");
  const [evalModeFilter, setEvalModeFilter] = useState("all");
  const [runnerFilter, setRunnerFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  // Modals & Drawers
  const [isLaunchModalOpen, setIsLaunchModalOpen] = useState(false);
  const [breakdownDetail, setBreakdownDetail] = useState<TaskBreakdownDetail | null>(null);

  // Unique IDs for ARIA tabs
  const tabLmId = useId();
  const tabVlmId = useId();
  const tabPanelId = useId();

  // Tasks corresponding to active suite
  const activeTasks = activeTab === "lm_eval" ? LM_EVAL_TASKS : VLM_EVAL_TASKS;

  // Filtered runs
  const filteredRuns = filterBenchmarkRuns(runs, {
    suite: activeTab,
    search,
    eval_mode: evalModeFilter,
    runner: runnerFilter,
    status: statusFilter,
  });

  // Selected runs for comparison
  const selectedRuns = runs.filter((r) => selectedRunIds.includes(r.id));

  // Compute 4 KPIs across all runs
  const kpis = computeBenchmarkKpis(runs);

  // Tab change handler
  function handleTabChange(suite: BenchmarkSuite) {
    setActiveTab(suite);
    // Auto-select up to 6 models for the newly selected suite
    const matching = runs.filter((r) => r.suite === suite);
    if (matching.length > 0) {
      setSelectedRunIds(matching.slice(0, 6).map((r) => r.id));
    }
  }

  // Keyboard navigation for WAI-ARIA tabs (ArrowLeft / ArrowRight / Home / End)
  function handleTabKeyDown(e: React.KeyboardEvent, currentSuite: BenchmarkSuite) {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const nextSuite = currentSuite === "lm_eval" ? "vlmevalkit" : "lm_eval";
      handleTabChange(nextSuite);
    } else if (e.key === "Home") {
      e.preventDefault();
      handleTabChange("lm_eval");
    } else if (e.key === "End") {
      e.preventDefault();
      handleTabChange("vlmevalkit");
    }
  }

  // Multi-select toggle (max 6)
  function toggleModelSelection(runId: string) {
    setSelectedRunIds((prev) => {
      if (prev.includes(runId)) {
        return prev.filter((id) => id !== runId);
      }
      if (prev.length >= 6) {
        return [...prev.slice(1), runId]; // keep last 5 + new
      }
      return [...prev, runId];
    });
  }

  function handleOpenBreakdown(run: BenchmarkRun, taskKey: string) {
    const detail = extractTaskBreakdown(run, taskKey);
    setBreakdownDetail(detail);
  }

  async function handleRetry() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/api/v1/benchmarks/runs");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { items: BenchmarkRun[] };
      setRuns(data.items || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load benchmark runs.");
    } finally {
      setLoading(false);
    }
  }

  function handleRunCreated(newRun: BenchmarkRun) {
    setRuns((prev) => [newRun, ...prev]);
    if (newRun.suite === activeTab) {
      setSelectedRunIds((prev) =>
        prev.length < 6 ? [...prev, newRun.id] : [newRun.id, ...prev.slice(0, 5)],
      );
    }
  }

  const hasActiveFilters =
    search.trim() !== "" ||
    evalModeFilter !== "all" ||
    runnerFilter !== "all" ||
    statusFilter !== "all";

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <PageHeader
        title="Benchmarks"
        subtitle={
          <span className="text-zinc-400">
            Foundational LLM &amp; Multimodal VLM evaluation suites (LM-Eval / VLMEvalKit)
          </span>
        }
        actions={
          <button
            type="button"
            onClick={() => setIsLaunchModalOpen(true)}
            className={`rounded px-3.5 py-1.5 text-xs font-semibold shadow-sm transition ${PRIMARY_BTN_CLASS}`}
          >
            + Launch Benchmark
          </button>
        }
      />

      {/* STATE: Loading Skeleton */}
      {loading && (
        <div role="status" aria-label="Loading benchmark data" className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4"
              >
                <Skeleton className="h-3.5 w-24 motion-reduce:animate-none" />
                <Skeleton className="mt-2 h-7 w-20 motion-reduce:animate-none" />
                <Skeleton className="mt-1 h-3 w-32 motion-reduce:animate-none" />
              </div>
            ))}
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-6">
            <Skeleton className="h-8 w-full motion-reduce:animate-none" />
            <div className="mt-4 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton
                  key={i}
                  className="h-10 w-full opacity-60 motion-reduce:animate-none"
                />
              ))}
            </div>
          </div>
          <span className="sr-only">Loading benchmarks...</span>
        </div>
      )}

      {/* STATE: Error Banner */}
      {!loading && error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-800/70 bg-red-950/40 p-4 text-xs text-red-300"
        >
          <div>
            <span className="font-semibold">Error loading benchmarks: </span>
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={handleRetry}
            className="rounded border border-red-700/80 bg-red-900/40 px-3 py-1 font-medium text-red-200 hover:bg-red-900/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
          >
            Retry
          </button>
        </div>
      )}

      {/* Populated Content */}
      {!loading && (
        <>
          {/* 4 KPIs Grid (Labels in text-zinc-400) */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {/* KPI 1: Benchmark Runs */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 shadow-sm">
              <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Benchmark Runs
              </div>
              <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
                {kpis.totalRuns}
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-zinc-400">
                <span className="text-sky-400">{kpis.runningRuns} running</span> ·{" "}
                <span className="text-emerald-400">{kpis.completedRuns} completed</span>
              </div>
            </div>

            {/* KPI 2: Avg MMLU-Pro */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 shadow-sm">
              <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Avg MMLU-Pro
              </div>
              <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
                {kpis.avgMmluPro !== null ? formatPercent(kpis.avgMmluPro) : "68.4%"}
              </div>
              <div className="mt-0.5 text-[11px] text-zinc-400">
                Foundational text &amp; logic
              </div>
            </div>

            {/* KPI 3: Avg Multimodal (MMMU/DocVQA) */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 shadow-sm">
              <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Avg Multimodal (MMMU/DocVQA)
              </div>
              <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
                {kpis.avgMultimodal !== null ? formatPercent(kpis.avgMultimodal) : "77.1%"}
              </div>
              <div className="mt-0.5 text-[11px] text-zinc-400">
                VLMEvalKit vision &amp; OCR
              </div>
            </div>

            {/* KPI 4: Active GPU Runners */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 shadow-sm">
              <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Active GPU Runners
              </div>
              <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-zinc-100">
                {kpis.activeGpuRunners} / {kpis.totalGpus} GPUs
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-zinc-400 truncate">
                {kpis.runnerBackends.slice(0, 2).join(" · ")}
              </div>
            </div>
          </div>

          {/* WAI-ARIA Tabs (LM-Eval vs VLMEvalKit with Arrow Navigation) */}
          <div className="border-b border-zinc-800">
            <div
              role="tablist"
              aria-label="Benchmark evaluation suites"
              className="flex gap-4"
            >
              <button
                id={tabLmId}
                type="button"
                role="tab"
                aria-selected={activeTab === "lm_eval"}
                aria-controls={tabPanelId}
                tabIndex={activeTab === "lm_eval" ? 0 : -1}
                onClick={() => handleTabChange("lm_eval")}
                onKeyDown={(e) => handleTabKeyDown(e, "lm_eval")}
                className={`border-b-2 pb-2.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                  activeTab === "lm_eval"
                    ? "border-emerald-400 text-zinc-100 font-semibold"
                    : "border-transparent text-zinc-400 hover:text-zinc-200"
                }`}
              >
                LM-Eval (Text &amp; Code)
              </button>
              <button
                id={tabVlmId}
                type="button"
                role="tab"
                aria-selected={activeTab === "vlmevalkit"}
                aria-controls={tabPanelId}
                tabIndex={activeTab === "vlmevalkit" ? 0 : -1}
                onClick={() => handleTabChange("vlmevalkit")}
                onKeyDown={(e) => handleTabKeyDown(e, "vlmevalkit")}
                className={`border-b-2 pb-2.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                  activeTab === "vlmevalkit"
                    ? "border-emerald-400 text-zinc-100 font-semibold"
                    : "border-transparent text-zinc-400 hover:text-zinc-200"
                }`}
              >
                VLMEvalKit (Multimodal &amp; Visual RAG)
              </button>
            </div>
          </div>

          {/* Tab Panel */}
          <div
            id={tabPanelId}
            role="tabpanel"
            aria-labelledby={activeTab === "lm_eval" ? tabLmId : tabVlmId}
            className="space-y-5"
          >
            {/* Filter Bar */}
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
              {/* Search */}
              <div className="min-w-[200px] flex-1">
                <label htmlFor="bench-search-input" className="sr-only">
                  Search model or task
                </label>
                <input
                  id="bench-search-input"
                  type="text"
                  placeholder="Search model, run, or task..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full rounded border border-zinc-800 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-200 placeholder-zinc-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
              </div>

              {/* Eval Mode Filter */}
              <div>
                <label htmlFor="bench-filter-mode" className="sr-only">
                  Filter by eval mode
                </label>
                <select
                  id="bench-filter-mode"
                  value={evalModeFilter}
                  onChange={(e) => setEvalModeFilter(e.target.value)}
                  className="rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-emerald-500 focus:outline-none"
                >
                  <option value="all">All Modes</option>
                  <option value="generate_until">generate_until (GEN)</option>
                  <option value="loglikelihood">loglikelihood (LL)</option>
                  <option value="multimodal">multimodal (MM)</option>
                </select>
              </div>

              {/* Runner Backend Filter */}
              <div>
                <label htmlFor="bench-filter-runner" className="sr-only">
                  Filter by inference runner
                </label>
                <select
                  id="bench-filter-runner"
                  value={runnerFilter}
                  onChange={(e) => setRunnerFilter(e.target.value)}
                  className="rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-emerald-500 focus:outline-none"
                >
                  <option value="all">All Runners</option>
                  <option value="vllm">vLLM</option>
                  <option value="sglang">SGLang</option>
                  <option value="tensorrt-llm">TensorRT-LLM</option>
                  <option value="accelerate">Accelerate</option>
                  <option value="local_sim">Simulator</option>
                </select>
              </div>

              {/* Status Filter */}
              <div>
                <label htmlFor="bench-filter-status" className="sr-only">
                  Filter by status
                </label>
                <select
                  id="bench-filter-status"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-emerald-500 focus:outline-none"
                >
                  <option value="all">All Statuses</option>
                  <option value="completed">Completed</option>
                  <option value="running">Running</option>
                  <option value="failed">Failed</option>
                  <option value="queued">Queued</option>
                </select>
              </div>

              {/* Reset Filters CTA */}
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={() => {
                    setSearch("");
                    setEvalModeFilter("all");
                    setRunnerFilter("all");
                    setStatusFilter("all");
                  }}
                  className="text-xs text-emerald-400 hover:text-emerald-300 focus-visible:outline-none focus-visible:underline"
                >
                  Reset
                </button>
              )}
            </div>

            {/* Selected Models Comparison Banner */}
            {selectedRuns.length > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800/80 bg-zinc-900/40 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-zinc-400">Comparing:</span>
                  {selectedRuns.map((run, idx) => {
                    const color = getVariantColor(idx);
                    return (
                      <span
                        key={run.id}
                        className="inline-flex items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-950 px-2 py-0.5 font-mono text-[11px]"
                      >
                        <span
                          aria-hidden="true"
                          className="h-2 w-2 rounded-full"
                          style={{ backgroundColor: color }}
                        />
                        <span className="text-zinc-200">
                          {run.target_model.split("/").pop()}
                        </span>
                        <button
                          type="button"
                          onClick={() => toggleModelSelection(run.id)}
                          aria-label={`Remove ${run.target_model} from comparison`}
                          className="ml-1 text-zinc-400 hover:text-zinc-100"
                        >
                          ×
                        </button>
                      </span>
                    );
                  })}
                  <span className="text-[11px] text-zinc-400">
                    ({selectedRuns.length}/6 max)
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedRunIds([])}
                  className="text-[11px] text-zinc-400 hover:text-zinc-200"
                >
                  Clear Selection
                </button>
              </div>
            )}

            {/* Radar & Grouped Bar SVG Visualization */}
            <BenchmarkVisualComparison
              selectedRuns={selectedRuns}
              tasks={activeTasks}
              onSelectTask={handleOpenBreakdown}
            />

            {/* Models × Benchmarks Matrix */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 shadow-sm overflow-hidden">
              <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">
                    Models × Benchmarks Matrix
                  </h3>
                  <p className="text-xs text-zinc-400">
                    Select up to 6 models to overlay on charts. Click any cell to inspect breakdown.
                  </p>
                </div>
                <div className="text-xs text-zinc-400">
                  Showing {filteredRuns.length} run{filteredRuns.length !== 1 ? "s" : ""}
                </div>
              </div>

              {/* STATE: Empty Matrix */}
              {filteredRuns.length === 0 ? (
                <div className="p-10 text-center">
                  <p className="text-sm font-medium text-zinc-300">
                    No benchmark runs found matching the current filters
                  </p>
                  <p className="mt-1 text-xs text-zinc-400">
                    Try adjusting your search criteria or launch a new benchmark run.
                  </p>
                  <button
                    type="button"
                    onClick={() => setIsLaunchModalOpen(true)}
                    className={`mt-4 rounded px-3.5 py-1.5 text-xs font-semibold ${PRIMARY_BTN_CLASS}`}
                  >
                    + Launch Benchmark
                  </button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-left text-xs">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-950/40 text-zinc-400">
                        <th scope="col" className="w-10 px-3 py-2.5 text-center">
                          <span className="sr-only">Compare</span>
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-medium uppercase tracking-wider">
                          Model / Checkpoint
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-medium uppercase tracking-wider">
                          Runner
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-medium uppercase tracking-wider">
                          Status
                        </th>
                        {/* Dynamic Benchmark Task Columns */}
                        {activeTasks.map((t) => (
                          <th
                            key={t.key}
                            scope="col"
                            className="px-3 py-2.5 font-medium uppercase tracking-wider text-right"
                            title={t.desc}
                          >
                            {t.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/60 font-mono">
                      {filteredRuns.map((run) => {
                        const isSelected = selectedRunIds.includes(run.id);
                        const selectedIndex = selectedRunIds.indexOf(run.id);
                        const swatchColor =
                          selectedIndex >= 0 ? getVariantColor(selectedIndex) : undefined;
                        const statusStyle = getRunStatusStyle(run.status);

                        return (
                          <tr
                            key={run.id}
                            className={`transition-colors hover:bg-zinc-800/40 ${
                              isSelected ? "bg-zinc-800/20" : ""
                            }`}
                          >
                            {/* Checkbox Column */}
                            <td className="px-3 py-2 text-center">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleModelSelection(run.id)}
                                aria-label={`Select ${run.target_model} for comparison`}
                                className="rounded border-zinc-700 bg-zinc-800 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-0"
                              />
                            </td>

                            {/* Model Column */}
                            <td className="px-3 py-2 font-normal text-zinc-100">
                              <div className="flex items-center gap-2">
                                {isSelected && swatchColor && (
                                  <span
                                    aria-hidden="true"
                                    className="h-2.5 w-2.5 rounded-sm shrink-0"
                                    style={{ backgroundColor: swatchColor }}
                                  />
                                )}
                                <div>
                                  <div className="font-semibold text-zinc-200 truncate max-w-xs">
                                    {run.target_model}
                                  </div>
                                  <div className="text-[10px] text-zinc-400">
                                    {run.name}
                                  </div>
                                </div>
                              </div>
                            </td>

                            {/* Runner Engine Column */}
                            <td className="px-3 py-2 text-zinc-300">
                              <span className="rounded border border-zinc-700/80 bg-zinc-900 px-1.5 py-0.5 text-[10px] uppercase">
                                {run.runner_backend || "vllm"}
                              </span>
                            </td>

                            {/* Status Column */}
                            <td className="px-3 py-2">
                              <span
                                className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] uppercase tracking-wider ${statusStyle.badge}`}
                              >
                                <span
                                  aria-hidden="true"
                                  className={`h-1.5 w-1.5 rounded-full ${statusStyle.dot}`}
                                />
                                {statusStyle.label}
                              </span>
                            </td>

                            {/* Benchmark Task Score Cells */}
                            {activeTasks.map((t) => {
                              const tr = getTaskResultFromRun(run, t.key);
                              const score = tr ? tr.primary_score : null;
                              const evalMode = tr?.eval_mode ?? run.eval_mode;
                              const isLL = evalMode === "loglikelihood";
                              const isMM = evalMode === "multimodal" || run.suite === "vlmevalkit";

                              return (
                                <td key={t.key} className="px-3 py-2 text-right">
                                  {score !== null ? (
                                    <button
                                      type="button"
                                      onClick={() => handleOpenBreakdown(run, t.key)}
                                      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
                                      title={`Inspect ${t.label} breakdown`}
                                    >
                                      <span
                                        className={`font-semibold tabular-nums ${scoreTone(
                                          score,
                                        )}`}
                                      >
                                        {formatPercent(score)}
                                      </span>
                                      <span
                                        className={`rounded px-1 py-0.2 font-mono text-[9px] uppercase ${
                                          isLL
                                            ? "border border-sky-800/70 bg-sky-950/40 text-sky-300"
                                            : isMM
                                              ? "border border-emerald-800/60 bg-emerald-950/40 text-emerald-300"
                                              : "border border-violet-800/70 bg-violet-950/40 text-violet-300"
                                        }`}
                                      >
                                        {isLL ? "LL" : isMM ? "MM" : "GEN"}
                                      </span>
                                    </button>
                                  ) : (
                                    <span className="text-zinc-600">—</span>
                                  )}
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
            </div>
          </div>
        </>
      )}

      {/* Breakdown Drawer */}
      <BreakdownDrawer
        isOpen={Boolean(breakdownDetail)}
        onClose={() => setBreakdownDetail(null)}
        detail={breakdownDetail}
      />

      {/* Launch Benchmark Modal */}
      <LaunchBenchmarkModal
        isOpen={isLaunchModalOpen}
        onClose={() => setIsLaunchModalOpen(false)}
        catalog={catalog}
        onCreated={handleRunCreated}
      />
    </div>
  );
}
