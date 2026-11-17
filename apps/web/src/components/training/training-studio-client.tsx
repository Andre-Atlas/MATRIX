"use client";

import React, { useCallback, useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/ui";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { proxy } from "@/lib/proxy-client";
import type {
  ListResponse,
  MLOpsSweep,
  SweepDetail,
  TrainingMetricPoint,
  TrainingMetricSeries,
  TrainingRun,
} from "@/lib/types";
import { ClassicMlTab } from "./classic-ml-tab";
import { CompareDrawer } from "./compare-drawer";
import { RegisterModelModal } from "./register-model-modal";
import { RunsCurvesTab } from "./runs-curves-tab";
import { SweepsTab } from "./sweeps-tab";

export type TrainingTabId = "runs" | "sweeps" | "classic";

const TABS: Array<{ id: TrainingTabId; label: string }> = [
  { id: "runs", label: "Runs & Live Curves" },
  { id: "sweeps", label: "Hyperparameter Sweeps" },
  { id: "classic", label: "Classic ML & Eval" },
];

type TrainingStudioClientProps = {
  initialRuns: TrainingRun[];
  initialSweeps: MLOpsSweep[];
  initialSeriesByRun: Record<string, Record<string, TrainingMetricPoint[]>>;
  initialSweepDetail: SweepDetail | null;
  initialRunId?: string;
  initialTab?: TrainingTabId;
  initialError?: string | null;
};

export function TrainingStudioClient({
  initialRuns,
  initialSweeps,
  initialSeriesByRun,
  initialSweepDetail,
  initialRunId,
  initialTab = "runs",
  initialError = null,
}: TrainingStudioClientProps) {
  const [runs, setRuns] = useState<TrainingRun[]>(initialRuns);
  const [sweeps, setSweeps] = useState<MLOpsSweep[]>(initialSweeps);
  const [seriesByRun, setSeriesByRun] =
    useState<Record<string, Record<string, TrainingMetricPoint[]>>>(initialSeriesByRun);
  const [sweepDetailById, setSweepDetailById] = useState<Record<string, SweepDetail>>(() =>
    initialSweepDetail ? { [initialSweepDetail.id]: initialSweepDetail } : {},
  );

  const [activeTab, setActiveTab] = useState<TrainingTabId>(initialTab);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>(() => {
    if (initialRunId && initialRuns.some((r) => r.id === initialRunId)) {
      return [initialRunId];
    }
    return initialRuns.slice(0, 3).map((r) => r.id);
  });
  const [selectedSweepId, setSelectedSweepId] = useState<string | null>(
    initialSweeps[0]?.id ?? null,
  );
  const [classicRunId, setClassicRunId] = useState<string | null>(
    () =>
      (initialRunId && initialRuns.find((r) => r.id === initialRunId)?.classic_eval
        ? initialRunId
        : initialRuns.find((r) => Boolean(r.classic_eval))?.id) ??
      initialRuns[0]?.id ??
      null,
  );

  const [loadingRunIds, setLoadingRunIds] = useState<string[]>([]);
  const [loadingSweep, setLoadingSweep] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  const [compareOpen, setCompareOpen] = useState(false);
  const [registerRun, setRegisterRun] = useState<TrainingRun | null>(null);

  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const syncUrlParams = useCallback((nextTab: TrainingTabId, primaryRunId?: string) => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", nextTab);
    if (primaryRunId) {
      url.searchParams.set("run", primaryRunId);
    } else {
      url.searchParams.delete("run");
    }
    window.history.replaceState(null, "", url.toString());
  }, []);

  const handleTabChange = useCallback(
    (tabId: TrainingTabId) => {
      setActiveTab(tabId);
      syncUrlParams(tabId, selectedRunIds[0]);
    },
    [selectedRunIds, syncUrlParams],
  );

  function handleTabKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, idx: number) {
    let nextIdx: number | null = null;
    if (e.key === "ArrowRight") nextIdx = (idx + 1) % TABS.length;
    else if (e.key === "ArrowLeft") nextIdx = (idx - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") nextIdx = 0;
    else if (e.key === "End") nextIdx = TABS.length - 1;

    if (nextIdx !== null) {
      e.preventDefault();
      const targetTab = TABS[nextIdx];
      handleTabChange(targetTab.id);
      tabRefs.current[nextIdx]?.focus();
    }
  }

  const fetchRunSeriesIfNeeded = useCallback(
    async (runId: string) => {
      if (seriesByRun[runId]) return;
      setLoadingRunIds((prev) => (prev.includes(runId) ? prev : [...prev, runId]));
      try {
        const res = await proxy<TrainingMetricSeries>(
          "GET",
          `/api/v1/mlops/runs/${encodeURIComponent(runId)}/metrics`,
        );
        setSeriesByRun((prev) => ({
          ...prev,
          [runId]: res?.series ?? {},
        }));
      } catch {
        // Non-fatal: leave empty series if metric store has no data
        setSeriesByRun((prev) => ({ ...prev, [runId]: {} }));
      } finally {
        setLoadingRunIds((prev) => prev.filter((id) => id !== runId));
      }
    },
    [seriesByRun],
  );

  const handleToggleRun = useCallback(
    (runId: string) => {
      setSelectedRunIds((prev) => {
        const exists = prev.includes(runId);
        let next: string[];
        if (exists) {
          next = prev.filter((id) => id !== runId);
        } else {
          if (prev.length >= 6) return prev;
          next = [...prev, runId];
          void fetchRunSeriesIfNeeded(runId);
        }
        syncUrlParams(activeTab, next[0]);
        return next;
      });
    },
    [activeTab, fetchRunSeriesIfNeeded, syncUrlParams],
  );

  const handleSelectSweep = useCallback(
    async (sweepId: string) => {
      setSelectedSweepId(sweepId);
      if (sweepDetailById[sweepId]) return;
      setLoadingSweep(true);
      try {
        const detail = await proxy<SweepDetail>(
          "GET",
          `/api/v1/mlops/sweeps/${encodeURIComponent(sweepId)}`,
        );
        setSweepDetailById((prev) => ({ ...prev, [sweepId]: detail }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load sweep detail.");
      } finally {
        setLoadingSweep(false);
      }
    },
    [sweepDetailById],
  );

  const handleInspectRunFromSweep = useCallback(
    (runId: string) => {
      setSelectedRunIds((prev) => {
        const next = prev.includes(runId) ? prev : [runId, ...prev].slice(0, 6);
        return next;
      });
      void fetchRunSeriesIfNeeded(runId);
      setActiveTab("runs");
      syncUrlParams("runs", runId);
    },
    [fetchRunSeriesIfNeeded, syncUrlParams],
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const [runsResp, sweepsResp] = await Promise.all([
        proxy<ListResponse<TrainingRun>>("GET", "/api/v1/mlops/runs?limit=100"),
        proxy<ListResponse<MLOpsSweep>>("GET", "/api/v1/mlops/sweeps"),
      ]);
      const nextRuns = Array.isArray(runsResp?.items) ? runsResp.items : [];
      const nextSweeps = Array.isArray(sweepsResp?.items) ? sweepsResp.items : [];
      setRuns(nextRuns);
      setSweeps(nextSweeps);
      if (nextSweeps.length > 0 && !selectedSweepId) {
        setSelectedSweepId(nextSweeps[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refresh training runs.");
    } finally {
      setRefreshing(false);
    }
  }, [selectedSweepId]);

  // 4 KPIs
  const kpis = useMemo(() => {
    const activeCount = runs.filter((r) => r.status === "running" || r.status === "queued").length;

    const nowMs = Date.now();
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;
    const completedRuns = runs.filter((r) => r.status === "completed");
    const completed7d = completedRuns.filter((r) => {
      const ts = Date.parse(r.finished_at ?? r.created_at);
      return Number.isNaN(ts) || nowMs - ts <= sevenDaysMs;
    }).length;

    const evalLosses = runs
      .map((r) => r.summary_metrics?.["eval/loss"] ?? r.latest_metrics?.["eval/loss"])
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const bestEvalLoss = evalLosses.length > 0 ? Math.min(...evalLosses) : null;

    const vramValues = runs
      .map((r) => r.summary_metrics?.["gpu/vram_gb"] ?? r.latest_metrics?.["gpu/vram_gb"])
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const avgVram =
      vramValues.length > 0
        ? vramValues.reduce((acc, v) => acc + v, 0) / vramValues.length
        : null;

    return { activeCount, completed7d, bestEvalLoss, avgVram };
  }, [runs]);

  const selectedRuns = useMemo(
    () =>
      selectedRunIds
        .map((id) => runs.find((r) => r.id === id))
        .filter((r): r is TrainingRun => Boolean(r)),
    [selectedRunIds, runs],
  );

  const currentSweepDetail = selectedSweepId ? sweepDetailById[selectedSweepId] ?? null : null;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Training Studio"
        subtitle={
          <span className="text-xs text-zinc-400">
            Fine-tuning telemetry, Ask-and-Tell hyperparameter sweeps, and classic ML evaluation
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing}
              className="h-8 rounded border border-zinc-700 bg-zinc-800/80 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
            <button
              type="button"
              disabled={runs.length === 0}
              onClick={() => setRegisterRun(selectedRuns[0] ?? runs[0] ?? null)}
              className={`h-8 rounded px-3 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
            >
              + Register Model
            </button>
          </div>
        }
      />

      {/* 4 KPI Cards (WCAG AA: text-zinc-400 labels, font-mono tabular-nums values) */}
      <section aria-label="Training Summary KPIs" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-3">
          <div className="text-[11px] uppercase tracking-wider text-zinc-400">Active Runs</div>
          <div className="mt-1 font-mono text-xl tabular-nums text-sky-300">
            {kpis.activeCount}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            {runs.length} total tracked
          </div>
        </div>

        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-3">
          <div className="text-[11px] uppercase tracking-wider text-zinc-400">Completed (7d)</div>
          <div className="mt-1 font-mono text-xl tabular-nums text-emerald-300">
            {kpis.completed7d}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            ready for registry
          </div>
        </div>

        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-3">
          <div className="text-[11px] uppercase tracking-wider text-zinc-400">Best Eval Loss</div>
          <div className="mt-1 font-mono text-xl tabular-nums text-zinc-50">
            {kpis.bestEvalLoss !== null ? kpis.bestEvalLoss.toFixed(4) : "—"}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            lowest validation loss
          </div>
        </div>

        <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3.5 py-3">
          <div className="text-[11px] uppercase tracking-wider text-zinc-400">Avg GPU VRAM</div>
          <div className="mt-1 font-mono text-xl tabular-nums text-zinc-50">
            {kpis.avgVram !== null ? `${kpis.avgVram.toFixed(1)} GB` : "—"}
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">
            across GPU runs
          </div>
        </div>
      </section>

      {/* Error Banner with Retry */}
      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-800/70 bg-red-950/40 px-4 py-3 text-xs text-red-200"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={handleRefresh}
            className="h-7 rounded border border-red-700/70 bg-red-900/50 px-3 font-medium text-red-100 hover:bg-red-900/80"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading Skeleton State */}
      {refreshing ? (
        <div role="status" aria-label="Refreshing training runs" className="space-y-3">
          <div className="h-10 w-full animate-pulse rounded bg-zinc-800/60 motion-reduce:animate-none" />
          <div className="h-64 w-full animate-pulse rounded bg-zinc-800/60 motion-reduce:animate-none" />
          <span className="sr-only">Refreshing training data…</span>
        </div>
      ) : runs.length === 0 && !error ? (
        <div className="rounded-md border border-dashed border-zinc-800 px-6 py-14 text-center">
          <p className="text-sm font-medium text-zinc-200">No training runs recorded yet</p>
          <p className="mx-auto mt-1 max-w-lg text-xs text-zinc-400">
            Start logging runs with{" "}
            <code className="font-mono text-emerald-300">matrix_sdk.mlops.start_run()</code> or{" "}
            <code className="font-mono text-emerald-300">MatrixTrainerCallback</code> to stream loss
            curves, GPU telemetry, and sweeps.
          </p>
        </div>
      ) : (
        <>
          {/* WAI-ARIA Tablist */}
          <div
            role="tablist"
            aria-label="Training Studio sections"
            className="flex flex-wrap gap-1 border-b border-zinc-800 pb-2"
          >
            {TABS.map((tab, idx) => {
              const isSelected = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  ref={(el) => {
                    tabRefs.current[idx] = el;
                  }}
                  id={`training-tab-${tab.id}`}
                  role="tab"
                  type="button"
                  aria-selected={isSelected}
                  aria-controls={`training-panel-${tab.id}`}
                  tabIndex={isSelected ? 0 : -1}
                  onClick={() => handleTabChange(tab.id)}
                  onKeyDown={(e) => handleTabKeyDown(e, idx)}
                  className={`h-8 rounded border px-3 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                    isSelected
                      ? "border-emerald-700/70 bg-emerald-950/50 text-emerald-200"
                      : "border-transparent bg-transparent text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100"
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          {/* Tab Panels */}
          <div
            id={`training-panel-${activeTab}`}
            role="tabpanel"
            aria-labelledby={`training-tab-${activeTab}`}
          >
            {activeTab === "runs" && (
              <RunsCurvesTab
                runs={runs}
                selectedRunIds={selectedRunIds}
                seriesByRun={seriesByRun}
                loadingRunIds={loadingRunIds}
                onToggleRun={handleToggleRun}
                onOpenCompare={() => setCompareOpen(true)}
                onOpenRegister={(run) => setRegisterRun(run)}
              />
            )}

            {activeTab === "sweeps" && (
              <SweepsTab
                sweeps={sweeps}
                selectedSweepId={selectedSweepId}
                sweepDetail={currentSweepDetail}
                loadingSweep={loadingSweep}
                onSelectSweep={handleSelectSweep}
                onInspectRun={handleInspectRunFromSweep}
              />
            )}

            {activeTab === "classic" && (
              <ClassicMlTab
                runs={runs}
                selectedRunId={classicRunId}
                onSelectRun={(id) => setClassicRunId(id)}
              />
            )}
          </div>
        </>
      )}

      {/* Drawers & Modals */}
      <CompareDrawer
        open={compareOpen}
        runs={selectedRuns}
        onClose={() => setCompareOpen(false)}
      />

      <RegisterModelModal
        open={registerRun !== null}
        run={registerRun}
        onClose={() => setRegisterRun(null)}
      />
    </div>
  );
}
