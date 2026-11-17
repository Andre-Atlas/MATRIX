"use client";

import React, { useMemo, useRef, useState } from "react";
import { adaptSweepAnalysis } from "@/lib/mlops";
import { getRunStatusStyle } from "@/lib/mlops-tokens";
import type { MLOpsSweep, ParallelAxis, SweepDetail, SweepTrial } from "@/lib/types";

type SweepsTabProps = {
  sweeps: MLOpsSweep[];
  selectedSweepId: string | null;
  sweepDetail: SweepDetail | null;
  loadingSweep: boolean;
  onSelectSweep: (sweepId: string) => void;
  onInspectRun?: (runId: string) => void;
};

type AxisBrush = {
  min: number;
  max: number;
};

function getAxisDomain(axis: ParallelAxis, trials: SweepTrial[]): { min: number; max: number } {
  if (axis.type === "categorical") {
    const count = axis.values?.length ?? 0;
    return { min: 0, max: Math.max(0, count - 1) };
  }
  if (typeof axis.min === "number" && typeof axis.max === "number") {
    return { min: axis.min, max: axis.max };
  }
  const vals = trials
    .map((t) => (axis.name in t.params ? Number(t.params[axis.name]) : Number(t.metric_value)))
    .filter((v) => Number.isFinite(v));
  if (vals.length === 0) return { min: 0, max: 1 };
  return { min: Math.min(...vals), max: Math.max(...vals) };
}

function getTrialAxisRawValue(trial: SweepTrial, axis: ParallelAxis, metricName: string): unknown {
  if (axis.name === metricName) return trial.metric_value;
  if (axis.name in trial.params) return trial.params[axis.name];
  return trial.metric_value;
}

function normalizeTrialValue(trial: SweepTrial, axis: ParallelAxis, domain: { min: number; max: number }, metricName: string): number | null {
  const raw = getTrialAxisRawValue(trial, axis, metricName);
  if (raw === null || raw === undefined) return null;

  if (axis.type === "categorical") {
    const vals = axis.values ?? [];
    const idx = vals.findIndex((v) => String(v) === String(raw));
    if (idx === -1) return null;
    if (vals.length <= 1) return 0.5;
    return idx / (vals.length - 1);
  }

  const num = Number(raw);
  if (!Number.isFinite(num)) return null;
  if (axis.log && domain.min > 0 && domain.max > 0 && num > 0) {
    const lMin = Math.log10(domain.min);
    const lMax = Math.log10(domain.max);
    if (lMax === lMin) return 0.5;
    return Math.min(1, Math.max(0, (Math.log10(num) - lMin) / (lMax - lMin)));
  }
  if (domain.max === domain.min) return 0.5;
  return Math.min(1, Math.max(0, (num - domain.min) / (domain.max - domain.min)));
}

function getTrialNumericForBrush(trial: SweepTrial, axis: ParallelAxis, metricName: string): number | null {
  const raw = getTrialAxisRawValue(trial, axis, metricName);
  if (raw === null || raw === undefined) return null;
  if (axis.type === "categorical") {
    const vals = axis.values ?? [];
    const idx = vals.findIndex((v) => String(v) === String(raw));
    return idx === -1 ? null : idx;
  }
  const num = Number(raw);
  return Number.isFinite(num) ? num : null;
}

function formatAxisTick(val: number, axis: ParallelAxis): string {
  if (axis.type === "categorical") {
    const v = axis.values?.[Math.round(val)];
    return v !== undefined ? String(v) : "";
  }
  if (axis.log || (Math.abs(val) > 0 && Math.abs(val) < 0.01)) {
    return val.toExponential(1);
  }
  if (axis.type === "int") return String(Math.round(val));
  return val.toFixed(3);
}

export function SweepsTab({
  sweeps,
  selectedSweepId,
  sweepDetail,
  loadingSweep,
  onSelectSweep,
  onInspectRun,
}: SweepsTabProps) {
  const [brushes, setBrushes] = useState<Record<string, AxisBrush>>({});
  const [showTable, setShowTable] = useState(false);
  const [hoveredRunId, setHoveredRunId] = useState<string | null>(null);
  const dragStateRef = useRef<{ axisName: string; startY: number } | null>(null);

  const analysis = useMemo(
    () => adaptSweepAnalysis(sweepDetail?.analysis),
    [sweepDetail?.analysis],
  );
  const axes = analysis.parallel_coordinates.axes;
  const pcTrials = analysis.parallel_coordinates.trials;

  const domains = useMemo(() => {
    const map: Record<string, { min: number; max: number }> = {};
    for (const axis of axes) {
      map[axis.name] = getAxisDomain(axis, pcTrials);
    }
    return map;
  }, [axes, pcTrials]);

  const filteredTrials = useMemo(() => {
    return pcTrials.filter((trial) => {
      for (const axis of axes) {
        const brush = brushes[axis.name];
        if (!brush) continue;
        const val = getTrialNumericForBrush(trial, axis, analysis.metric_name);
        if (val === null) return false;
        const lo = Math.min(brush.min, brush.max);
        const hi = Math.max(brush.min, brush.max);
        if (val < lo - 1e-12 || val > hi + 1e-12) return false;
      }
      return true;
    });
  }, [pcTrials, axes, brushes, analysis.metric_name]);

  const metricValues = pcTrials
    .map((t) => t.metric_value)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const minMetric = metricValues.length ? Math.min(...metricValues) : 0;
  const maxMetric = metricValues.length ? Math.max(...metricValues) : 1;

  function trialStrokeColor(trial: SweepTrial): string {
    if (trial.metric_value === null || !Number.isFinite(trial.metric_value)) return "#71717a";
    const span = maxMetric - minMetric || 1;
    const norm = (trial.metric_value - minMetric) / span;
    const quality = analysis.metric_goal === "minimize" ? 1 - norm : norm;
    if (quality >= 0.75) return "#34d399"; // emerald-400
    if (quality >= 0.45) return "#38bdf8"; // sky-400
    if (quality >= 0.2) return "#fbbf24"; // amber-400
    return "#f87171"; // red-400
  }

  function updateBrush(axis: ParallelAxis, bound: "min" | "max", value: number) {
    const dom = domains[axis.name] ?? { min: 0, max: 1 };
    setBrushes((prev) => {
      const current = prev[axis.name] ?? { min: dom.min, max: dom.max };
      return {
        ...prev,
        [axis.name]: {
          ...current,
          [bound]: value,
        },
      };
    });
  }

  if (sweeps.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-zinc-800 px-6 py-12 text-center">
        <p className="font-medium text-zinc-200">No hyperparameter sweeps found</p>
        <p className="mt-1 text-xs text-zinc-400">
          Create a sweep via the Python SDK (<code className="font-mono text-zinc-300">matrix_sdk.mlops.create_sweep</code>) to visualize parallel coordinates and parameter importance.
        </p>
      </div>
    );
  }

  const svgW = 860;
  const svgH = 290;
  const pad = { top: 28, right: 48, bottom: 26, left: 48 };
  const plotW = svgW - pad.left - pad.right;
  const plotH = svgH - pad.top - pad.bottom;

  const axisX = (idx: number) =>
    axes.length <= 1 ? pad.left + plotW / 2 : pad.left + (idx / (axes.length - 1)) * plotW;

  const yFromNorm = (norm: number) => pad.top + plotH - norm * plotH;

  function normFromSvgY(svgY: number): number {
    return Math.min(1, Math.max(0, (pad.top + plotH - svgY) / (plotH || 1)));
  }

  function valueFromNorm(norm: number, axis: ParallelAxis, dom: { min: number; max: number }): number {
    if (axis.type === "categorical") {
      return Math.round(dom.min + norm * (dom.max - dom.min));
    }
    if (axis.log && dom.min > 0 && dom.max > 0) {
      const lMin = Math.log10(dom.min);
      const lMax = Math.log10(dom.max);
      return Number(Math.pow(10, lMin + norm * (lMax - lMin)).toPrecision(4));
    }
    const raw = dom.min + norm * (dom.max - dom.min);
    return axis.type === "int" ? Math.round(raw) : Number(raw.toFixed(4));
  }

  function handleAxisPointerDown(axis: ParallelAxis, e: React.PointerEvent<SVGRectElement>) {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const y = ((e.clientY - rect.top) / rect.height) * svgH;
    dragStateRef.current = { axisName: axis.name, startY: y };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handleAxisPointerMove(axis: ParallelAxis, e: React.PointerEvent<SVGRectElement>) {
    if (!dragStateRef.current || dragStateRef.current.axisName !== axis.name) return;
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const curY = ((e.clientY - rect.top) / rect.height) * svgH;
    const n1 = normFromSvgY(dragStateRef.current.startY);
    const n2 = normFromSvgY(curY);
    const dom = domains[axis.name] ?? { min: 0, max: 1 };
    const lo = valueFromNorm(Math.min(n1, n2), axis, dom);
    const hi = valueFromNorm(Math.max(n1, n2), axis, dom);
    setBrushes((prev) => ({ ...prev, [axis.name]: { min: lo, max: hi } }));
  }

  function handleAxisPointerUp(e: React.PointerEvent<SVGRectElement>) {
    if (dragStateRef.current) {
      dragStateRef.current = null;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        // ignore if not captured
      }
    }
  }

  const activeBrushCount = Object.keys(brushes).length;
  const activeSweep = sweeps.find((s) => s.id === selectedSweepId) ?? sweeps[0];

  return (
    <div className="space-y-5">
      {/* Sweep selector & metadata */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs font-medium text-zinc-300">
            <span>Sweep</span>
            <select
              aria-label="Select hyperparameter sweep"
              value={activeSweep?.id ?? ""}
              onChange={(e) => {
                setBrushes({});
                onSelectSweep(e.target.value);
              }}
              className="h-8 rounded border border-zinc-700 bg-zinc-950 px-2.5 text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
            >
              {sweeps.map((sw) => (
                <option key={sw.id} value={sw.id}>
                  {sw.name} ({sw.method})
                </option>
              ))}
            </select>
          </label>

          {activeSweep && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded border border-zinc-700 bg-zinc-950 px-2 py-0.5 font-mono text-zinc-300">
                {activeSweep.metric_goal} {activeSweep.metric_name}
              </span>
              {activeSweep.best_metric_value !== null && (
                <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-2 py-0.5 font-mono tabular-nums text-emerald-300">
                  best: {activeSweep.best_metric_value.toFixed(4)}
                </span>
              )}
              {activeSweep.early_stopping?.strategy && activeSweep.early_stopping.strategy !== "none" && (
                <span className="rounded border border-sky-800/70 bg-sky-950/40 px-2 py-0.5 font-mono text-sky-300">
                  early stop: {activeSweep.early_stopping.strategy}
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {activeBrushCount > 0 && (
            <button
              type="button"
              onClick={() => setBrushes({})}
              className="h-7 rounded border border-amber-800/70 bg-amber-950/40 px-2.5 text-xs font-medium text-amber-200 hover:bg-amber-950/60"
            >
              Clear Brushes ({activeBrushCount})
            </button>
          )}
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

      {/* Parallel Coordinates + Importance Grid */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section
          aria-label="Parallel Coordinates Chart"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4 xl:col-span-2"
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
                Parallel Coordinates
              </h3>
              <p className="text-[11px] text-zinc-400">
                Drag vertically on any axis or use the keyboard min/max filters below ({filteredTrials.length} /{" "}
                {pcTrials.length} trials shown).
              </p>
            </div>
            {loadingSweep && <span className="text-xs text-sky-300">Loading sweep…</span>}
          </div>

          {axes.length === 0 || pcTrials.length === 0 ? (
            <div className="flex h-64 items-center justify-center rounded border border-dashed border-zinc-800 text-sm text-zinc-400">
              No trial coordinates recorded for this sweep yet.
            </div>
          ) : showTable ? (
            <div className="max-h-72 overflow-auto rounded border border-zinc-800">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-zinc-800 bg-zinc-950">
                  <tr>
                    <th scope="col" className="px-3 py-2 text-zinc-400">
                      Trial
                    </th>
                    {axes.map((a) => (
                      <th key={a.name} scope="col" className="px-3 py-2 font-mono text-zinc-300">
                        {a.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/70">
                  {filteredTrials.map((trial) => (
                    <tr key={trial.run_id} className="hover:bg-zinc-900/40">
                      <td className="px-3 py-1.5 font-medium text-zinc-200">
                        {trial.run_name ?? trial.run_id}
                      </td>
                      {axes.map((a) => {
                        const val = getTrialAxisRawValue(trial, a, analysis.metric_name);
                        return (
                          <td key={a.name} className="px-3 py-1.5 font-mono tabular-nums text-zinc-300">
                            {val === null || val === undefined ? "—" : String(val)}
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
              aria-labelledby="parallel-coords-title"
              aria-describedby="parallel-coords-desc"
              className="h-72 w-full select-none"
            >
              <title id="parallel-coords-title">Parallel coordinates hyperparameter sweep chart</title>
              <desc id="parallel-coords-desc">
                Parallel coordinates plot with {axes.length} axes and {filteredTrials.length} active
                trials out of {pcTrials.length} total trials.
              </desc>

              {/* Trial polylines (unfiltered dimmed, filtered bright) */}
              {pcTrials.map((trial) => {
                const isFiltered = filteredTrials.some((f) => f.run_id === trial.run_id);
                const coords: string[] = [];
                for (let i = 0; i < axes.length; i++) {
                  const ax = axes[i];
                  const norm = normalizeTrialValue(trial, ax, domains[ax.name], analysis.metric_name);
                  if (norm === null) continue;
                  coords.push(`${coords.length === 0 ? "M" : "L"} ${axisX(i).toFixed(1)} ${yFromNorm(norm).toFixed(1)}`);
                }
                if (coords.length < 2) return null;
                const isHovered = hoveredRunId === trial.run_id;
                return (
                  <path
                    key={trial.run_id}
                    d={coords.join(" ")}
                    fill="none"
                    stroke={isFiltered ? trialStrokeColor(trial) : "#3f3f46"}
                    strokeWidth={isHovered ? 3 : isFiltered ? 1.8 : 1}
                    strokeOpacity={isFiltered ? (isHovered ? 1 : 0.85) : 0.25}
                    onMouseEnter={() => setHoveredRunId(trial.run_id)}
                    onMouseLeave={() => setHoveredRunId(null)}
                  />
                );
              })}

              {/* Vertical Axes + Brushing Hitboxes */}
              {axes.map((ax, i) => {
                const x = axisX(i);
                const dom = domains[ax.name];
                const brush = brushes[ax.name];

                let brushRectY = pad.top;
                let brushRectH = 0;
                if (brush) {
                  const nLo =
                    ax.type === "categorical"
                      ? (dom.max === dom.min ? 0 : (Math.min(brush.min, brush.max) - dom.min) / (dom.max - dom.min))
                      : ax.log && dom.min > 0 && dom.max > 0
                        ? (Math.log10(Math.max(1e-9, Math.min(brush.min, brush.max))) - Math.log10(dom.min)) /
                          (Math.log10(dom.max) - Math.log10(dom.min) || 1)
                        : (Math.min(brush.min, brush.max) - dom.min) / (dom.max - dom.min || 1);

                  const nHi =
                    ax.type === "categorical"
                      ? (dom.max === dom.min ? 1 : (Math.max(brush.min, brush.max) - dom.min) / (dom.max - dom.min))
                      : ax.log && dom.min > 0 && dom.max > 0
                        ? (Math.log10(Math.max(1e-9, Math.max(brush.min, brush.max))) - Math.log10(dom.min)) /
                          (Math.log10(dom.max) - Math.log10(dom.min) || 1)
                        : (Math.max(brush.min, brush.max) - dom.min) / (dom.max - dom.min || 1);

                  const yTop = yFromNorm(Math.min(1, Math.max(0, nHi)));
                  const yBot = yFromNorm(Math.min(1, Math.max(0, nLo)));
                  brushRectY = yTop;
                  brushRectH = Math.max(4, yBot - yTop);
                }

                return (
                  <g key={ax.name}>
                    <line
                      x1={x}
                      y1={pad.top}
                      x2={x}
                      y2={pad.top + plotH}
                      stroke="#52525b"
                      strokeWidth={1.5}
                    />
                    {brush && (
                      <rect
                        x={x - 6}
                        y={brushRectY}
                        width={12}
                        height={brushRectH}
                        rx={2}
                        fill="#34d399"
                        fillOpacity={0.28}
                        stroke="#34d399"
                        strokeWidth={1.2}
                      />
                    )}
                    <rect
                      x={x - 16}
                      y={pad.top}
                      width={32}
                      height={plotH}
                      fill="transparent"
                      className="cursor-ns-resize"
                      onPointerDown={(e) => handleAxisPointerDown(ax, e)}
                      onPointerMove={(e) => handleAxisPointerMove(ax, e)}
                      onPointerUp={handleAxisPointerUp}
                    />
                    <text
                      x={x}
                      y={16}
                      textAnchor="middle"
                      className="fill-zinc-200 font-mono text-[11px] font-medium"
                    >
                      {ax.name}
                    </text>
                    <text
                      x={x + 6}
                      y={pad.top + 8}
                      className="fill-zinc-400 font-mono text-[9px]"
                    >
                      {formatAxisTick(dom.max, ax)}
                    </text>
                    <text
                      x={x + 6}
                      y={pad.top + plotH}
                      className="fill-zinc-400 font-mono text-[9px]"
                    >
                      {formatAxisTick(dom.min, ax)}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}

          {/* Accessible Keyboard Min/Max Range Inputs per Axis */}
          {axes.length > 0 && (
            <div className="mt-4 grid grid-cols-1 gap-2 border-t border-zinc-800 pt-3 sm:grid-cols-2 lg:grid-cols-3">
              {axes.map((ax) => {
                const dom = domains[ax.name] ?? { min: 0, max: 1 };
                const brush = brushes[ax.name];
                const curMin = brush ? brush.min : dom.min;
                const curMax = brush ? brush.max : dom.max;
                const step = ax.type === "int" || ax.type === "categorical" ? 1 : "any";

                return (
                  <fieldset
                    key={ax.name}
                    className="rounded border border-zinc-800 bg-zinc-950/60 px-2.5 py-2"
                  >
                    <legend className="px-1 font-mono text-[11px] text-zinc-300">
                      {ax.name}
                      {ax.type === "categorical" && ax.values
                        ? ` (0..${ax.values.length - 1})`
                        : ""}
                    </legend>
                    <div className="mt-1 flex items-center gap-2">
                      <label className="flex flex-1 items-center gap-1 text-[11px] text-zinc-400">
                        <span>Min</span>
                        <input
                          type="number"
                          step={step}
                          min={dom.min}
                          max={dom.max}
                          value={curMin}
                          aria-label={`${ax.name} minimum`}
                          onChange={(e) => {
                            const v = Number(e.target.value);
                            if (Number.isFinite(v)) updateBrush(ax, "min", v);
                          }}
                          className="h-7 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 font-mono text-[11px] text-zinc-100 focus:border-emerald-400 focus:outline-none"
                        />
                      </label>
                      <label className="flex flex-1 items-center gap-1 text-[11px] text-zinc-400">
                        <span>Max</span>
                        <input
                          type="number"
                          step={step}
                          min={dom.min}
                          max={dom.max}
                          value={curMax}
                          aria-label={`${ax.name} maximum`}
                          onChange={(e) => {
                            const v = Number(e.target.value);
                            if (Number.isFinite(v)) updateBrush(ax, "max", v);
                          }}
                          className="h-7 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 font-mono text-[11px] text-zinc-100 focus:border-emerald-400 focus:outline-none"
                        />
                      </label>
                    </div>
                  </fieldset>
                );
              })}
            </div>
          )}
        </section>

        {/* Hyperparameter Importance Panel */}
        <section
          aria-label="Hyperparameter Importance"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4"
        >
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
            Hyperparameter Importance
          </h3>
          <p className="mt-0.5 text-[11px] text-zinc-400">
            Correlation with <span className="font-mono text-zinc-300">{analysis.metric_name}</span>
          </p>

          {analysis.importance.length === 0 ? (
            <div className="mt-6 rounded border border-dashed border-zinc-800 p-6 text-center text-xs text-zinc-400">
              Insufficient completed trials to compute parameter importance.
            </div>
          ) : (
            <ul className="mt-4 space-y-3">
              {analysis.importance.map((item) => {
                const pct = Math.round(Math.min(1, Math.max(0, item.importance)) * 100);
                return (
                  <li key={item.name} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono text-zinc-200">{item.name}</span>
                      <span className="font-mono tabular-nums text-zinc-300">
                        {pct}%{" "}
                        <span className="text-[10px] text-zinc-400">({item.method})</span>
                      </span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-800">
                      <div
                        className="h-full rounded-full bg-emerald-400/80"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {/* Filtered Trials Table */}
      <section aria-label="Sweep Trials Table" className="rounded-md border border-zinc-800 bg-zinc-900/40">
        <div className="border-b border-zinc-800 bg-zinc-900/60 px-4 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
            Filtered Trials ({filteredTrials.length})
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="border-b border-zinc-800 bg-zinc-900/40">
              <tr>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Trial
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Status
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  {analysis.metric_name}
                </th>
                <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Hyperparameters
                </th>
                {onInspectRun && (
                  <th scope="col" className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                    Action
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {filteredTrials.length === 0 ? (
                <tr>
                  <td colSpan={onInspectRun ? 5 : 4} className="px-4 py-8 text-center text-sm text-zinc-400">
                    No trials match the active axis brushes.
                  </td>
                </tr>
              ) : (
                filteredTrials.map((trial) => {
                  const st = getRunStatusStyle(trial.status);
                  return (
                    <tr
                      key={trial.run_id}
                      onMouseEnter={() => setHoveredRunId(trial.run_id)}
                      onMouseLeave={() => setHoveredRunId(null)}
                      className="hover:bg-zinc-900/50"
                    >
                      <td className="px-3 py-2 font-medium text-zinc-100">
                        {trial.run_name ?? trial.run_id}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] ${st.badge}`}
                        >
                          <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
                          {st.label}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums text-zinc-200">
                        {trial.metric_value !== null && Number.isFinite(trial.metric_value)
                          ? trial.metric_value.toFixed(4)
                          : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-zinc-300">
                        {Object.entries(trial.params)
                          .map(([k, v]) => `${k}=${String(v)}`)
                          .join(" · ")}
                      </td>
                      {onInspectRun && (
                        <td className="px-3 py-2 text-right">
                          <button
                            type="button"
                            onClick={() => onInspectRun(trial.run_id)}
                            className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-[11px] font-medium text-zinc-200 hover:bg-zinc-700"
                          >
                            Inspect Run
                          </button>
                        </td>
                      )}
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
