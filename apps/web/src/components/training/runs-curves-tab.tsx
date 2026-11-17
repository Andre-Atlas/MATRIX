"use client";

import React, { useMemo, useState } from "react";
import { filterRuns, smoothEma } from "@/lib/mlops";
import {
  getRunStatusStyle,
  getTaskTypeStyle,
  PRIMARY_BTN_CLASS,
  VARIANT_COLORS,
} from "@/lib/mlops-tokens";
import type { TrainingMetricPoint, TrainingRun } from "@/lib/types";

const CURVE_METRICS = [
  { key: "train/loss", label: "Train Loss" },
  { key: "eval/loss", label: "Eval Loss" },
  { key: "eval/perplexity", label: "Perplexity" },
  { key: "dpo/reward_margin", label: "DPO Margin" },
  { key: "train/learning_rate", label: "Learning Rate" },
] as const;

type RunsCurvesTabProps = {
  runs: TrainingRun[];
  selectedRunIds: string[];
  seriesByRun: Record<string, Record<string, TrainingMetricPoint[]>>;
  loadingRunIds: string[];
  onToggleRun: (runId: string) => void;
  onOpenCompare: () => void;
  onOpenRegister: (run: TrainingRun) => void;
};

function formatMetricNumber(val: number, metricKey: string): string {
  if (!Number.isFinite(val)) return "—";
  if (metricKey === "train/learning_rate") return val.toExponential(2);
  if (Math.abs(val) >= 100) return val.toFixed(1);
  return val.toFixed(4);
}

type TelemetryMiniChartProps = {
  title: string;
  metricKey: "gpu/utilization" | "gpu/vram_gb";
  unit: string;
  threshold: number;
  thresholdLabel: string;
  selectedRuns: TrainingRun[];
  seriesByRun: Record<string, Record<string, TrainingMetricPoint[]>>;
};

function TelemetryMiniChart({
  title,
  metricKey,
  unit,
  threshold,
  thresholdLabel,
  selectedRuns,
  seriesByRun,
}: TelemetryMiniChartProps) {
  const [showTable, setShowTable] = useState(false);

  const runSeries = useMemo(() => {
    return selectedRuns.map((run, idx) => ({
      run,
      color: VARIANT_COLORS[idx % VARIANT_COLORS.length],
      points: seriesByRun[run.id]?.[metricKey] ?? [],
    }));
  }, [selectedRuns, seriesByRun, metricKey]);

  const allPoints = runSeries.flatMap((r) => r.points);
  const steps = allPoints.map((p) => p.step);
  const values = allPoints.map((p) => p.value);
  const minStep = steps.length ? Math.min(...steps) : 0;
  const maxStep = steps.length ? Math.max(...steps) : 100;
  const maxVal =
    metricKey === "gpu/utilization"
      ? 100
      : Math.max(threshold * 1.15, values.length ? Math.max(...values) * 1.1 : 80);
  const minVal = 0;

  const w = 440;
  const h = 150;
  const pad = { top: 14, right: 16, bottom: 26, left: 42 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  const sx = (step: number) =>
    maxStep === minStep ? pad.left + plotW / 2 : pad.left + ((step - minStep) / (maxStep - minStep)) * plotW;
  const sy = (val: number) =>
    pad.top + plotH - ((Math.min(maxVal, Math.max(minVal, val)) - minVal) / (maxVal - minVal || 1)) * plotH;

  const thresholdY = sy(threshold);

  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">{title}</h3>
          <p className="text-[11px] text-zinc-400">Threshold: {thresholdLabel}</p>
        </div>
        <button
          type="button"
          aria-pressed={showTable}
          onClick={() => setShowTable((v) => !v)}
          className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-[11px] font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          {showTable ? "Chart" : "Data Table"}
        </button>
      </div>

      {allPoints.length === 0 ? (
        <div className="flex h-36 items-center justify-center rounded border border-dashed border-zinc-800 text-xs text-zinc-400">
          No {metricKey} telemetry recorded for selected runs.
        </div>
      ) : showTable ? (
        <div className="max-h-40 overflow-auto rounded border border-zinc-800">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-zinc-800 bg-zinc-950/60">
              <tr>
                <th scope="col" className="px-2.5 py-1.5 font-mono text-[11px] text-zinc-400">
                  Step
                </th>
                {runSeries.map((r) => (
                  <th key={r.run.id} scope="col" className="px-2.5 py-1.5 text-[11px] text-zinc-300">
                    {r.run.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {Array.from(new Set(steps))
                .sort((a, b) => a - b)
                .map((step) => (
                  <tr key={step}>
                    <td className="px-2.5 py-1 font-mono tabular-nums text-zinc-300">{step}</td>
                    {runSeries.map((r) => {
                      const pt = r.points.find((p) => p.step === step);
                      return (
                        <td key={r.run.id} className="px-2.5 py-1 font-mono tabular-nums text-zinc-200">
                          {pt ? `${pt.value.toFixed(1)}${unit}` : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${w} ${h}`}
          role="img"
          aria-labelledby={`telemetry-title-${metricKey}`}
          aria-describedby={`telemetry-desc-${metricKey}`}
          className="h-36 w-full"
        >
          <title id={`telemetry-title-${metricKey}`}>{title} telemetry chart</title>
          <desc id={`telemetry-desc-${metricKey}`}>
            Time series of {metricKey} across {selectedRuns.length} selected runs with threshold at{" "}
            {thresholdLabel}.
          </desc>

          {/* Grid lines */}
          {[0, 0.5, 1].map((t) => {
            const v = minVal + (maxVal - minVal) * t;
            const y = sy(v);
            return (
              <g key={t}>
                <line x1={pad.left} y1={y} x2={w - pad.right} y2={y} stroke="#27272a" strokeWidth={1} />
                <text
                  x={pad.left - 6}
                  y={y + 3}
                  textAnchor="end"
                  className="fill-zinc-400 font-mono text-[10px]"
                >
                  {Math.round(v)}
                </text>
              </g>
            );
          })}

          {/* 90% threshold line */}
          <line
            x1={pad.left}
            y1={thresholdY}
            x2={w - pad.right}
            y2={thresholdY}
            stroke="#fbbf24"
            strokeWidth={1}
            strokeDasharray="4 3"
          />
          <text
            x={w - pad.right}
            y={thresholdY - 4}
            textAnchor="end"
            className="fill-amber-300 font-mono text-[9px]"
          >
            {thresholdLabel}
          </text>

          {/* Series paths */}
          {runSeries.map((r) => {
            if (r.points.length === 0) return null;
            const d = r.points
              .map((p, idx) => `${idx === 0 ? "M" : "L"} ${sx(p.step).toFixed(1)} ${sy(p.value).toFixed(1)}`)
              .join(" ");
            return (
              <path
                key={r.run.id}
                d={d}
                fill="none"
                stroke={r.color}
                strokeWidth={1.75}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            );
          })}

          {/* X axis labels */}
          <text x={pad.left} y={h - 6} className="fill-zinc-400 font-mono text-[10px]">
            {minStep}
          </text>
          <text x={w - pad.right} y={h - 6} textAnchor="end" className="fill-zinc-400 font-mono text-[10px]">
            {maxStep} steps
          </text>
        </svg>
      )}
    </div>
  );
}

export function RunsCurvesTab({
  runs,
  selectedRunIds,
  seriesByRun,
  loadingRunIds,
  onToggleRun,
  onOpenCompare,
  onOpenRegister,
}: RunsCurvesTabProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [activeMetric, setActiveMetric] = useState<string>("train/loss");
  const [emaAlpha, setEmaAlpha] = useState(0.6);
  const [logY, setLogY] = useState(false);
  const [showTable, setShowTable] = useState(false);
  const [hoverStep, setHoverStep] = useState<number | null>(null);

  const filteredRuns = useMemo(
    () =>
      filterRuns(runs, {
        search,
        status: statusFilter,
        run_type: typeFilter,
      }),
    [runs, search, statusFilter, typeFilter],
  );

  const selectedRuns = useMemo(
    () =>
      selectedRunIds
        .map((id) => runs.find((r) => r.id === id))
        .filter((r): r is TrainingRun => Boolean(r)),
    [selectedRunIds, runs],
  );

  const primarySelectedRun = selectedRuns[0] ?? filteredRuns[0] ?? runs[0] ?? null;

  const smoothedSeries = useMemo(() => {
    return selectedRuns.map((run, idx) => {
      const raw = seriesByRun[run.id]?.[activeMetric] ?? [];
      const filtered = logY ? raw.filter((p) => p.value > 0) : raw;
      const smoothed = smoothEma(filtered, emaAlpha);
      return {
        run,
        color: VARIANT_COLORS[idx % VARIANT_COLORS.length],
        points: smoothed,
      };
    });
  }, [selectedRuns, seriesByRun, activeMetric, emaAlpha, logY]);

  const allPoints = smoothedSeries.flatMap((s) => s.points);
  const allSteps = useMemo(
    () => Array.from(new Set(allPoints.map((p) => p.step))).sort((a, b) => a - b),
    [allPoints],
  );

  const minStep = allSteps.length ? allSteps[0] : 0;
  const maxStep = allSteps.length ? allSteps[allSteps.length - 1] : 100;

  const transformedVals = allPoints.map((p) => (logY ? Math.log10(Math.max(1e-9, p.value)) : p.value));
  const rawMinY = transformedVals.length ? Math.min(...transformedVals) : 0;
  const rawMaxY = transformedVals.length ? Math.max(...transformedVals) : 1;
  const yPad = (rawMaxY - rawMinY) * 0.08 || 0.1;
  const minY = rawMinY - yPad;
  const maxY = rawMaxY + yPad;

  const svgW = 860;
  const svgH = 290;
  const pad = { top: 18, right: 20, bottom: 32, left: 64 };
  const plotW = svgW - pad.left - pad.right;
  const plotH = svgH - pad.top - pad.bottom;

  const sx = (step: number) =>
    maxStep === minStep ? pad.left + plotW / 2 : pad.left + ((step - minStep) / (maxStep - minStep)) * plotW;

  const sy = (val: number) => {
    const tVal = logY ? Math.log10(Math.max(1e-9, val)) : val;
    return pad.top + plotH - ((tVal - minY) / (maxY - minY || 1)) * plotH;
  };

  function handleSvgMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (allSteps.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * svgW;
    const clampedX = Math.max(pad.left, Math.min(svgW - pad.right, relX));
    const ratio = (clampedX - pad.left) / (plotW || 1);
    const targetStep = minStep + ratio * (maxStep - minStep);
    let nearest = allSteps[0];
    let bestDist = Math.abs(nearest - targetStep);
    for (const s of allSteps) {
      const d = Math.abs(s - targetStep);
      if (d < bestDist) {
        bestDist = d;
        nearest = s;
      }
    }
    setHoverStep(nearest);
  }

  const maxVramCap = useMemo(() => {
    const caps = selectedRuns
      .map((r) => r.hardware_info?.gpu_memory_gb)
      .filter((v): v is number => typeof v === "number" && v > 0);
    return caps.length > 0 ? Math.max(...caps) : 80;
  }, [selectedRuns]);

  return (
    <div className="space-y-5">
      {/* Main Metric Curves Card */}
      <section
        aria-label="Training Metric Curves"
        className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4"
      >
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          {/* Metric selector pills */}
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Metric series">
            {CURVE_METRICS.map((m) => {
              const active = activeMetric === m.key;
              return (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setActiveMetric(m.key)}
                  className={`h-7 rounded border px-2.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                    active
                      ? "border-emerald-700/70 bg-emerald-950/50 text-emerald-200"
                      : "border-zinc-800 bg-zinc-950/60 text-zinc-300 hover:border-zinc-700 hover:text-zinc-100"
                  }`}
                >
                  {m.label}
                </button>
              );
            })}
          </div>

          {/* Chart controls: EMA, Log Y, Chart/Table */}
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-zinc-300">
              <span>EMA α</span>
              <input
                type="range"
                min={0.1}
                max={1}
                step={0.05}
                value={emaAlpha}
                onChange={(e) => setEmaAlpha(Number(e.target.value))}
                aria-label="EMA smoothing factor"
                className="h-1.5 w-24 cursor-pointer accent-emerald-400"
              />
              <span className="w-8 font-mono tabular-nums text-zinc-200">{emaAlpha.toFixed(2)}</span>
            </label>

            <button
              type="button"
              aria-pressed={logY}
              onClick={() => setLogY((v) => !v)}
              className={`h-7 rounded border px-2.5 font-mono text-xs transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                logY
                  ? "border-sky-700/70 bg-sky-950/50 text-sky-200"
                  : "border-zinc-700 bg-zinc-800/80 text-zinc-200 hover:bg-zinc-700"
              }`}
            >
              {logY ? "Log Y" : "Linear Y"}
            </button>

            <button
              type="button"
              aria-pressed={showTable}
              onClick={() => setShowTable((v) => !v)}
              className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              {showTable ? "Chart View" : "Data Table"}
            </button>
          </div>
        </div>

        {/* Legend + Hover readout */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex flex-wrap items-center gap-3">
            {smoothedSeries.map((s) => (
              <span key={s.run.id} className="inline-flex items-center gap-1.5 text-zinc-200">
                <span
                  aria-hidden="true"
                  className="h-2.5 w-2.5 rounded-sm"
                  style={{ backgroundColor: s.color }}
                />
                <span className="font-mono">{s.run.name}</span>
                {loadingRunIds.includes(s.run.id) && (
                  <span className="text-[11px] text-sky-300">(loading…)</span>
                )}
              </span>
            ))}
          </div>

          {hoverStep !== null && (
            <div className="flex flex-wrap items-center gap-3 rounded border border-zinc-800 bg-zinc-950/80 px-2.5 py-1 font-mono text-[11px] tabular-nums text-zinc-200">
              <span className="text-zinc-400">step {hoverStep}</span>
              {smoothedSeries.map((s) => {
                const pt = s.points.find((p) => p.step === hoverStep);
                return (
                  <span key={s.run.id} className="inline-flex items-center gap-1">
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: s.color }}
                    />
                    <span>{pt ? formatMetricNumber(pt.value, activeMetric) : "—"}</span>
                  </span>
                );
              })}
            </div>
          )}
        </div>

        {selectedRuns.length === 0 ? (
          <div className="flex h-64 items-center justify-center rounded border border-dashed border-zinc-800 text-sm text-zinc-400">
            Select up to 6 runs in the table below to plot live training curves.
          </div>
        ) : allPoints.length === 0 ? (
          <div className="flex h-64 items-center justify-center rounded border border-dashed border-zinc-800 text-sm text-zinc-400">
            No <span className="mx-1 font-mono text-zinc-200">{activeMetric}</span> points recorded for
            the selected runs.
          </div>
        ) : showTable ? (
          <div className="max-h-72 overflow-auto rounded border border-zinc-800">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 border-b border-zinc-800 bg-zinc-950">
                <tr>
                  <th scope="col" className="px-3 py-2 font-mono text-zinc-400">
                    Step
                  </th>
                  {smoothedSeries.map((s) => (
                    <th key={s.run.id} scope="col" className="px-3 py-2 text-zinc-200">
                      {s.run.name} ({activeMetric})
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70">
                {allSteps.map((step) => (
                  <tr key={step} className="hover:bg-zinc-900/40">
                    <td className="px-3 py-1.5 font-mono tabular-nums text-zinc-300">{step}</td>
                    {smoothedSeries.map((s) => {
                      const pt = s.points.find((p) => p.step === step);
                      return (
                        <td key={s.run.id} className="px-3 py-1.5 font-mono tabular-nums text-zinc-200">
                          {pt ? formatMetricNumber(pt.value, activeMetric) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <svg
            viewBox={`0 0 ${svgW} ${svgH}`}
            role="img"
            aria-labelledby="training-curves-title"
            aria-describedby="training-curves-desc"
            onMouseMove={handleSvgMouseMove}
            onMouseLeave={() => setHoverStep(null)}
            className="h-72 w-full select-none"
          >
            <title id="training-curves-title">{activeMetric} training curves</title>
            <desc id="training-curves-desc">
              Line chart comparing {activeMetric} across {selectedRuns.length} runs from step {minStep}{" "}
              to {maxStep} with EMA smoothing alpha {emaAlpha.toFixed(2)}.
            </desc>

            {/* Horizontal grid lines & Y ticks */}
            {[0, 0.25, 0.5, 0.75, 1].map((t) => {
              const tVal = minY + (maxY - minY) * t;
              const displayVal = logY ? Math.pow(10, tVal) : tVal;
              const y = pad.top + plotH - t * plotH;
              return (
                <g key={t}>
                  <line x1={pad.left} y1={y} x2={svgW - pad.right} y2={y} stroke="#27272a" strokeWidth={1} />
                  <text
                    x={pad.left - 8}
                    y={y + 3.5}
                    textAnchor="end"
                    className="fill-zinc-400 font-mono text-[10px]"
                  >
                    {formatMetricNumber(displayVal, activeMetric)}
                  </text>
                </g>
              );
            })}

            {/* Vertical step ticks */}
            {[0, 0.25, 0.5, 0.75, 1].map((t) => {
              const stepVal = Math.round(minStep + (maxStep - minStep) * t);
              const x = pad.left + t * plotW;
              return (
                <g key={t}>
                  <line x1={x} y1={pad.top} x2={x} y2={svgH - pad.bottom} stroke="#18181b" strokeWidth={1} />
                  <text
                    x={x}
                    y={svgH - 10}
                    textAnchor="middle"
                    className="fill-zinc-400 font-mono text-[10px]"
                  >
                    {stepVal}
                  </text>
                </g>
              );
            })}

            {/* Hover crosshair */}
            {hoverStep !== null && (
              <line
                x1={sx(hoverStep)}
                y1={pad.top}
                x2={sx(hoverStep)}
                y2={svgH - pad.bottom}
                stroke="#71717a"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            )}

            {/* Run curves */}
            {smoothedSeries.map((s) => {
              if (s.points.length === 0) return null;
              const pathD = s.points
                .map(
                  (p, i) =>
                    `${i === 0 ? "M" : "L"} ${sx(p.step).toFixed(1)} ${sy(p.value).toFixed(1)}`,
                )
                .join(" ");
              const hoverPt = hoverStep !== null ? s.points.find((p) => p.step === hoverStep) : null;
              return (
                <g key={s.run.id}>
                  <path
                    d={pathD}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                  {hoverPt && (
                    <circle
                      cx={sx(hoverPt.step)}
                      cy={sy(hoverPt.value)}
                      r={4}
                      fill={s.color}
                      stroke="#09090b"
                      strokeWidth={1.5}
                    />
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </section>

      {/* GPU Telemetry Mini Charts */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TelemetryMiniChart
          title="GPU Utilization (%)"
          metricKey="gpu/utilization"
          unit="%"
          threshold={90}
          thresholdLabel="90% target"
          selectedRuns={selectedRuns}
          seriesByRun={seriesByRun}
        />
        <TelemetryMiniChart
          title="GPU VRAM Usage (GB)"
          metricKey="gpu/vram_gb"
          unit=" GB"
          threshold={Number((maxVramCap * 0.9).toFixed(1))}
          thresholdLabel={`90% (${(maxVramCap * 0.9).toFixed(0)} GB)`}
          selectedRuns={selectedRuns}
          seriesByRun={seriesByRun}
        />
      </div>

      {/* Filters + Actions Bar */}
      <section aria-label="Training Runs Table" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search runs, models, tags…"
              aria-label="Search runs"
              className="h-8 w-60 rounded border border-zinc-700 bg-zinc-900 px-2.5 text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-400 focus:outline-none"
            />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by status"
              className="h-8 rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-200 focus:border-emerald-400 focus:outline-none"
            >
              <option value="all">All statuses</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="stopped_early">Stopped Early</option>
              <option value="queued">Queued</option>
            </select>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              aria-label="Filter by run type"
              className="h-8 rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-200 focus:border-emerald-400 focus:outline-none"
            >
              <option value="all">All types</option>
              <option value="sft">SFT</option>
              <option value="lora">LoRA</option>
              <option value="qlora">QLoRA</option>
              <option value="dpo">DPO</option>
              <option value="grpo">GRPO</option>
              <option value="ppo">PPO</option>
              <option value="classic_ml">Classic ML</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={selectedRuns.length < 2}
              onClick={onOpenCompare}
              className="h-8 rounded border border-zinc-700 bg-zinc-800/80 px-3 text-xs font-medium text-zinc-100 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Compare Selected ({selectedRuns.length})
            </button>
            <button
              type="button"
              disabled={!primarySelectedRun}
              onClick={() => primarySelectedRun && onOpenRegister(primarySelectedRun)}
              className={`h-8 rounded px-3 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
            >
              Register Model
            </button>
          </div>
        </div>

        {/* Runs Multi-Select Table */}
        <div className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-900/40">
          <table className="w-full min-w-[920px] text-left text-xs">
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th scope="col" className="w-12 px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Plot
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Run
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Type
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Status
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Eval Loss
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Train Loss
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Steps
                </th>
                <th scope="col" className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Action
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {filteredRuns.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-sm text-zinc-400">
                    No runs match the current filters.
                  </td>
                </tr>
              ) : (
                filteredRuns.map((run) => {
                  const selIdx = selectedRunIds.indexOf(run.id);
                  const isSelected = selIdx !== -1;
                  const atMax = !isSelected && selectedRunIds.length >= 6;
                  const statusStyle = getRunStatusStyle(run.status);
                  const typeStyle = getTaskTypeStyle(run.run_type);
                  const evalLoss = run.summary_metrics?.["eval/loss"] ?? run.latest_metrics?.["eval/loss"];
                  const trainLoss = run.summary_metrics?.["train/loss"] ?? run.latest_metrics?.["train/loss"];

                  return (
                    <tr
                      key={run.id}
                      className={isSelected ? "bg-zinc-900/70" : "hover:bg-zinc-900/40"}
                    >
                      <td className="px-3 py-2">
                        <label className="inline-flex h-7 w-10 cursor-pointer items-center gap-1.5">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            disabled={atMax}
                            onChange={() => onToggleRun(run.id)}
                            aria-label={`Select run ${run.name}`}
                            className="h-4 w-4 rounded border-zinc-700 bg-zinc-950 accent-emerald-400 disabled:opacity-40"
                          />
                          {isSelected && (
                            <span
                              aria-hidden="true"
                              className="h-2.5 w-2.5 shrink-0 rounded-sm"
                              style={{ backgroundColor: VARIANT_COLORS[selIdx % VARIANT_COLORS.length] }}
                            />
                          )}
                        </label>
                      </td>
                      <td className="px-3 py-2">
                        <div className="font-medium text-zinc-100">{run.name}</div>
                        <div className="font-mono text-[11px] text-zinc-400">
                          {run.base_model ?? run.id}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`inline-flex rounded border px-1.5 py-0.5 text-[11px] ${typeStyle.badge}`}>
                          {typeStyle.label}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] ${statusStyle.badge}`}
                        >
                          <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${statusStyle.dot}`} />
                          {statusStyle.label}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums text-zinc-200">
                        {typeof evalLoss === "number" ? evalLoss.toFixed(4) : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums text-zinc-300">
                        {typeof trainLoss === "number" ? trainLoss.toFixed(4) : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums text-zinc-300">
                        {run.total_steps ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => onOpenRegister(run)}
                          className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-[11px] font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                        >
                          Register Model
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
