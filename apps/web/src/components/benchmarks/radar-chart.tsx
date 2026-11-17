"use client";

import React, { useId, useState } from "react";
import {
  calculateRadarPolygon,
  formatPercent,
  getRadarAxisPoints,
  getTaskResultFromRun,
  getVariantColor,
  polarToCartesian,
} from "@/lib/benchmarks";
import type { BenchmarkRun } from "@/lib/types";
import { Mono, scoreTone } from "@/components/ui";

interface RadarChartProps {
  selectedRuns: BenchmarkRun[];
  tasks: { key: string; label: string; desc: string }[];
  onSelectTask?: (run: BenchmarkRun, taskKey: string) => void;
}

export function BenchmarkVisualComparison({
  selectedRuns,
  tasks,
  onSelectTask,
}: RadarChartProps) {
  const chartTitleId = useId();
  const chartDescId = useId();
  const [viewMode, setViewMode] = useState<"radar" | "bar" | "table">("radar");
  const [activeTooltip, setActiveTooltip] = useState<{
    x: number;
    y: number;
    model: string;
    task: string;
    score: number;
  } | null>(null);

  if (selectedRuns.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center">
        <p className="text-sm font-medium text-zinc-300">
          No models selected for comparison
        </p>
        <p className="mt-1 text-xs text-zinc-400">
          Check up to 6 models in the matrix below to generate comparative Radar and Bar charts.
        </p>
      </div>
    );
  }

  // Radar geometry constants
  const size = 360;
  const center = size / 2;
  const maxRadius = 120;
  const rings = [0.2, 0.4, 0.6, 0.8, 1.0];

  const axisPoints = getRadarAxisPoints(tasks, center, center, maxRadius);

  // Grouped Bar constants
  const barChartWidth = 600;
  const barChartHeight = 260;
  const barPaddingTop = 24;
  const barPaddingBottom = 48;
  const barPaddingLeft = 48;
  const barPaddingRight = 20;
  const usableWidth = barChartWidth - barPaddingLeft - barPaddingRight;
  const usableHeight = barChartHeight - barPaddingTop - barPaddingBottom;
  const taskGroupWidth = usableWidth / Math.max(1, tasks.length);
  const barWidth = Math.min(20, (taskGroupWidth - 12) / Math.max(1, selectedRuns.length));

  // Compute description text for WCAG
  const modelNames = selectedRuns.map((r) => r.target_model).join(", ");
  const taskList = tasks.map((t) => t.label).join(", ");

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
      {/* Header and Toggle Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">
            Comparative Benchmark Visualizer
          </h2>
          <p className="text-xs text-zinc-400">
            Comparing {selectedRuns.length} model{selectedRuns.length > 1 ? "s" : ""} across {tasks.length} benchmark tasks
          </p>
        </div>

        {/* View Mode Toggle */}
        <div
          role="group"
          aria-label="Chart display options"
          className="inline-flex rounded-md border border-zinc-800 bg-zinc-950 p-0.5 text-xs"
        >
          <button
            type="button"
            aria-pressed={viewMode === "radar"}
            onClick={() => setViewMode("radar")}
            className={`rounded px-2.5 py-1 font-medium transition ${
              viewMode === "radar"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Radar Chart
          </button>
          <button
            type="button"
            aria-pressed={viewMode === "bar"}
            onClick={() => setViewMode("bar")}
            className={`rounded px-2.5 py-1 font-medium transition ${
              viewMode === "bar"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Grouped Bar
          </button>
          <button
            type="button"
            aria-pressed={viewMode === "table"}
            onClick={() => setViewMode("table")}
            className={`rounded px-2.5 py-1 font-medium transition ${
              viewMode === "table"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Data Table
          </button>
        </div>
      </div>

      {/* Model Legend Swatches */}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
        {selectedRuns.map((run, idx) => {
          const color = getVariantColor(idx);
          return (
            <div key={run.id} className="inline-flex items-center gap-1.5 font-mono">
              <span
                aria-hidden="true"
                className="h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: color }}
              />
              <span className="text-zinc-300 font-medium truncate max-w-[200px]" title={run.target_model}>
                {run.target_model.split("/").pop() || run.target_model}
              </span>
            </div>
          );
        })}
      </div>

      {/* Main View Area */}
      <div className="mt-4 flex min-h-[300px] items-center justify-center">
        {/* VIEW 1: Pure SVG Radar Chart */}
        {viewMode === "radar" && (
          <div className="relative flex flex-col items-center">
            <svg
              role="img"
              aria-labelledby={`${chartTitleId} ${chartDescId}`}
              width={size}
              height={size}
              viewBox={`0 0 ${size} ${size}`}
              className="overflow-visible"
            >
              <title id={chartTitleId}>
                Benchmark Radar Chart: {modelNames}
              </title>
              <desc id={chartDescId}>
                Radar polygon comparison for {selectedRuns.length} models across {tasks.length} tasks: {taskList}.
              </desc>

              {/* Concentric Grid Rings */}
              {rings.map((ringPct) => {
                const r = ringPct * maxRadius;
                const ringPoints = tasks
                  .map((_, i) => {
                    const angle = i * ((2 * Math.PI) / tasks.length);
                    const { x, y } = polarToCartesian(center, center, r, angle);
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(" ");

                return (
                  <g key={ringPct}>
                    <polygon
                      points={ringPoints}
                      fill="none"
                      stroke="#27272a"
                      strokeWidth={1}
                      strokeDasharray={ringPct === 1.0 ? "none" : "2,2"}
                    />
                    <text
                      x={center + 4}
                      y={center - r + 3}
                      fill="#71717a"
                      fontSize="9"
                      fontFamily="monospace"
                    >
                      {Math.round(ringPct * 100)}%
                    </text>
                  </g>
                );
              })}

              {/* Spoke Axes & Labels */}
              {axisPoints.map((axis) => {
                // Offset label slightly outward
                const labelRadius = maxRadius + 22;
                const labelPos = polarToCartesian(center, center, labelRadius, axis.angle);
                return (
                  <g key={axis.key}>
                    <line
                      x1={center}
                      y1={center}
                      x2={axis.x}
                      y2={axis.y}
                      stroke="#27272a"
                      strokeWidth={1}
                    />
                    <text
                      x={labelPos.x}
                      y={labelPos.y + 3}
                      fill="#d4d4d8"
                      fontSize="11"
                      textAnchor="middle"
                      className="font-medium uppercase tracking-tight select-none"
                    >
                      {axis.label}
                    </text>
                  </g>
                );
              })}

              {/* Model Polygons */}
              {selectedRuns.map((run, idx) => {
                const color = getVariantColor(idx);
                const { pointsString, points } = calculateRadarPolygon(
                  tasks,
                  run,
                  center,
                  center,
                  maxRadius,
                );

                return (
                  <g key={run.id}>
                    <polygon
                      points={pointsString}
                      fill={color}
                      fillOpacity={0.16}
                      stroke={color}
                      strokeWidth={2}
                      className="transition-all duration-200 motion-reduce:animate-none"
                    />
                    {/* Interactive vertices */}
                    {points.map((pt, pIdx) => {
                      const taskKey = tasks[pIdx].key;
                      return (
                        <circle
                          key={pIdx}
                          cx={pt.x}
                          cy={pt.y}
                          r={4.5}
                          fill={color}
                          stroke="#09090b"
                          strokeWidth={1.5}
                          tabIndex={0}
                          role="graphics-symbol"
                          aria-label={`${run.target_model} on ${pt.task}: ${formatPercent(pt.score)}`}
                          className="cursor-pointer transition-transform hover:scale-125 focus:scale-125 focus:outline-none"
                          onMouseEnter={() =>
                            setActiveTooltip({
                              x: pt.x,
                              y: pt.y - 10,
                              model: run.target_model,
                              task: pt.task,
                              score: pt.score,
                            })
                          }
                          onMouseLeave={() => setActiveTooltip(null)}
                          onClick={() => onSelectTask?.(run, taskKey)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onSelectTask?.(run, taskKey);
                            }
                          }}
                        />
                      );
                    })}
                  </g>
                );
              })}
            </svg>

            {/* Hover Tooltip Overlay */}
            {activeTooltip && (
              <div
                className="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-full rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-xs shadow-lg"
                style={{ left: `${activeTooltip.x}px`, top: `${activeTooltip.y}px` }}
              >
                <div className="font-semibold text-zinc-200">{activeTooltip.task}</div>
                <div className="font-mono text-[11px] text-zinc-400">
                  {activeTooltip.model.split("/").pop()}
                </div>
                <div className="mt-0.5 font-mono font-bold tabular-nums text-emerald-400">
                  {formatPercent(activeTooltip.score)}
                </div>
              </div>
            )}
          </div>
        )}

        {/* VIEW 2: Pure SVG Grouped Bar Chart */}
        {viewMode === "bar" && (
          <div className="relative w-full max-w-2xl overflow-x-auto">
            <svg
              role="img"
              aria-labelledby={`${chartTitleId} ${chartDescId}`}
              width="100%"
              height={barChartHeight}
              viewBox={`0 0 ${barChartWidth} ${barChartHeight}`}
              className="overflow-visible"
            >
              <title id={chartTitleId}>
                Grouped Bar Chart Benchmark Comparison: {modelNames}
              </title>
              <desc id={chartDescId}>
                Grouped bar chart comparing {selectedRuns.length} models across {tasks.length} benchmark tasks.
              </desc>

              {/* Y-Axis Horizontal Grid Lines */}
              {[0, 0.25, 0.5, 0.75, 1.0].map((yTick) => {
                const yPos =
                  barPaddingTop + usableHeight - yTick * usableHeight;
                return (
                  <g key={yTick}>
                    <line
                      x1={barPaddingLeft}
                      y1={yPos}
                      x2={barChartWidth - barPaddingRight}
                      y2={yPos}
                      stroke="#27272a"
                      strokeWidth={1}
                      strokeDasharray={yTick === 0 ? "none" : "2,2"}
                    />
                    <text
                      x={barPaddingLeft - 6}
                      y={yPos + 3}
                      fill="#71717a"
                      fontSize="10"
                      fontFamily="monospace"
                      textAnchor="end"
                    >
                      {Math.round(yTick * 100)}%
                    </text>
                  </g>
                );
              })}

              {/* Baseline Reference Line at 70% */}
              <line
                x1={barPaddingLeft}
                y1={barPaddingTop + usableHeight - 0.7 * usableHeight}
                x2={barChartWidth - barPaddingRight}
                y2={barPaddingTop + usableHeight - 0.7 * usableHeight}
                stroke="#34d399"
                strokeWidth={1.5}
                strokeDasharray="4,4"
              />
              <text
                x={barChartWidth - barPaddingRight}
                y={barPaddingTop + usableHeight - 0.7 * usableHeight - 4}
                fill="#34d399"
                fontSize="9"
                fontFamily="monospace"
                textAnchor="end"
              >
                Baseline: 70%
              </text>

              {/* Bars per Task Group */}
              {tasks.map((task, tIdx) => {
                const groupX = barPaddingLeft + tIdx * taskGroupWidth;
                const groupCenterX = groupX + taskGroupWidth / 2;

                return (
                  <g key={task.key}>
                    {/* Task Axis Label */}
                    <text
                      x={groupCenterX}
                      y={barChartHeight - 16}
                      fill="#d4d4d8"
                      fontSize="11"
                      textAnchor="middle"
                      className="font-medium uppercase select-none"
                    >
                      {task.label}
                    </text>

                    {/* Bars for Each Selected Run */}
                    {selectedRuns.map((run, rIdx) => {
                      const color = getVariantColor(rIdx);
                      const tr = getTaskResultFromRun(run, task.key);
                      const score = tr ? tr.primary_score : 0;
                      const barHeight = Math.max(2, score * usableHeight);
                      const barX =
                        groupX +
                        (taskGroupWidth - selectedRuns.length * barWidth) / 2 +
                        rIdx * barWidth;
                      const barY =
                        barPaddingTop + usableHeight - barHeight;

                      return (
                        <rect
                          key={run.id}
                          x={barX}
                          y={barY}
                          width={barWidth - 2}
                          height={barHeight}
                          fill={color}
                          rx={1.5}
                          tabIndex={0}
                          role="graphics-symbol"
                          aria-label={`${run.target_model} on ${task.label}: ${formatPercent(score)}`}
                          className="cursor-pointer transition-all hover:brightness-125 focus:brightness-125 focus:outline-none"
                          onClick={() => onSelectTask?.(run, task.key)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onSelectTask?.(run, task.key);
                            }
                          }}
                        />
                      );
                    })}
                  </g>
                );
              })}
            </svg>
          </div>
        )}

        {/* VIEW 3: Semantic Data Table (<table role="grid">) */}
        {viewMode === "table" && (
          <div className="w-full overflow-x-auto">
            <table
              role="grid"
              aria-label="Benchmark Comparison Table"
              className="w-full border-collapse text-left text-xs"
            >
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-400">
                  <th scope="col" className="px-3 py-2 font-medium uppercase tracking-wider">
                    Model / Checkpoint
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium uppercase tracking-wider">
                    Suite
                  </th>
                  {tasks.map((task) => (
                    <th
                      key={task.key}
                      scope="col"
                      className="px-3 py-2 font-medium uppercase tracking-wider text-right"
                    >
                      {task.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {selectedRuns.map((run, idx) => {
                  const color = getVariantColor(idx);
                  return (
                    <tr
                      key={run.id}
                      className="hover:bg-zinc-800/30 transition-colors"
                    >
                      <th scope="row" className="px-3 py-2.5 font-normal text-zinc-200">
                        <div className="flex items-center gap-2">
                          <span
                            aria-hidden="true"
                            className="h-2 w-2 rounded-sm shrink-0"
                            style={{ backgroundColor: color }}
                          />
                          <span className="font-mono font-medium truncate max-w-xs">
                            {run.target_model}
                          </span>
                        </div>
                      </th>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-zinc-400 uppercase">
                        {run.suite}
                      </td>
                      {tasks.map((task) => {
                        const tr = getTaskResultFromRun(run, task.key);
                        const score = tr ? tr.primary_score : null;
                        return (
                          <td
                            key={task.key}
                            className="px-3 py-2.5 text-right font-mono tabular-nums"
                          >
                            {score !== null ? (
                              <button
                                type="button"
                                onClick={() => onSelectTask?.(run, task.key)}
                                className={`font-semibold hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400 rounded px-1 ${scoreTone(
                                  score,
                                )}`}
                                title={`Click to open breakdown for ${task.label}`}
                              >
                                {formatPercent(score)}
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
  );
}
