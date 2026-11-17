"use client";

import React, { useMemo, useState } from "react";
import { normalizeClassicConfusion } from "@/lib/mlops";
import type { ClassicMlEval, RocPoint, TrainingRun } from "@/lib/types";

type ClassicMlTabProps = {
  runs: TrainingRun[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
};

function findYoudenOptimal(rocCurve: RocPoint[]): RocPoint | null {
  if (!rocCurve.length) return null;
  let best = rocCurve[0];
  let bestJ = best.tpr - best.fpr;
  for (const pt of rocCurve) {
    const j = pt.tpr - pt.fpr;
    if (j > bestJ) {
      bestJ = j;
      best = pt;
    }
  }
  return best;
}

export function ClassicMlTab({ runs, selectedRunId, onSelectRun }: ClassicMlTabProps) {
  const [rocShowTable, setRocShowTable] = useState(false);
  const [cmMode, setCmMode] = useState<"counts" | "percent">("counts");
  const [fiShowTable, setFiShowTable] = useState(false);

  const defaultRun = useMemo(
    () =>
      (selectedRunId ? runs.find((r) => r.id === selectedRunId) : undefined) ??
      runs.find((r) => Boolean(r.classic_eval)) ??
      runs[0] ??
      null,
    [runs, selectedRunId],
  );

  const evalData: ClassicMlEval | null = defaultRun?.classic_eval ?? null;

  const rocCurve = useMemo(() => evalData?.roc_curve ?? [], [evalData]);
  const youden = useMemo(() => findYoudenOptimal(rocCurve), [rocCurve]);

  const confusion = evalData?.confusion_matrix;
  const normalizedCm = useMemo(
    () => (confusion ? normalizeClassicConfusion(confusion.matrix, cmMode) : []),
    [confusion, cmMode],
  );

  const features = useMemo(() => evalData?.feature_importance ?? [], [evalData]);
  const maxAbsImportance = useMemo(
    () =>
      features.length
        ? Math.max(...features.map((f) => Math.abs(f.importance)), 0.001)
        : 1,
    [features],
  );

  if (runs.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-zinc-800 px-6 py-12 text-center">
        <p className="font-medium text-zinc-200">No training runs available</p>
        <p className="mt-1 text-xs text-zinc-400">
          Log a <code className="font-mono text-zinc-300">classic_ml</code> run with{" "}
          <code className="font-mono text-zinc-300">classic_eval</code> metrics to inspect ROC-AUC,
          confusion matrix, and SHAP/feature importance.
        </p>
      </div>
    );
  }

  // ROC SVG geometry
  const rocW = 420;
  const rocH = 280;
  const rocPad = { top: 18, right: 18, bottom: 36, left: 46 };
  const rocPlotW = rocW - rocPad.left - rocPad.right;
  const rocPlotH = rocH - rocPad.top - rocPad.bottom;
  const rx = (fpr: number) => rocPad.left + Math.min(1, Math.max(0, fpr)) * rocPlotW;
  const ry = (tpr: number) => rocPad.top + rocPlotH - Math.min(1, Math.max(0, tpr)) * rocPlotH;

  // Feature importance SVG geometry
  const fiW = 820;
  const rowH = 26;
  const fiPad = { top: 20, right: 32, bottom: 26, left: 190 };
  const fiH = Math.max(140, fiPad.top + fiPad.bottom + features.length * rowH);
  const fiPlotW = fiW - fiPad.left - fiPad.right;
  const fiZeroX = fiPad.left + fiPlotW / 2;

  return (
    <div className="space-y-5">
      {/* Run selector */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-zinc-800 bg-zinc-900/60 p-4">
        <label className="flex items-center gap-2 text-xs font-medium text-zinc-300">
          <span>Evaluation Run</span>
          <select
            aria-label="Select run for classic ML evaluation"
            value={defaultRun?.id ?? ""}
            onChange={(e) => onSelectRun(e.target.value)}
            className="h-8 rounded border border-zinc-700 bg-zinc-950 px-2.5 text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} ({r.run_type}) {r.classic_eval ? "· has eval" : ""}
              </option>
            ))}
          </select>
        </label>

        {defaultRun && (
          <div className="font-mono text-xs text-zinc-400">
            base: <span className="text-zinc-200">{defaultRun.base_model ?? "—"}</span>
          </div>
        )}
      </div>

      {!evalData ? (
        <div className="rounded-md border border-dashed border-zinc-800 px-6 py-12 text-center">
          <p className="font-medium text-zinc-200">
            No classic evaluation metrics for <span className="font-mono">{defaultRun?.name}</span>
          </p>
          <p className="mt-1 text-xs text-zinc-400">
            This run ({defaultRun?.run_type}) does not include <code className="font-mono">classic_eval</code>{" "}
            payloads. Select a <code className="font-mono">classic_ml</code> run above to view ROC-AUC,
            confusion matrix, and feature importance.
          </p>
        </div>
      ) : (
        <>
          {/* Headline Classic ML Metrics */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
            {[
              { label: "ROC-AUC", value: evalData.roc_auc },
              { label: "PR-AUC", value: evalData.pr_auc },
              { label: "Accuracy", value: evalData.accuracy },
              { label: "F1 Score", value: evalData.f1 },
              { label: "Precision", value: evalData.precision },
              { label: "Recall", value: evalData.recall },
              { label: "Log Loss", value: evalData.log_loss },
            ].map((m) => (
              <div key={m.label} className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
                <div className="text-[11px] uppercase tracking-wider text-zinc-400">{m.label}</div>
                <div className="mt-1 font-mono text-lg tabular-nums text-zinc-100">
                  {typeof m.value === "number" && Number.isFinite(m.value) ? m.value.toFixed(3) : "—"}
                </div>
              </div>
            ))}
          </div>

          {/* ROC-AUC + Confusion Matrix */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* ROC-AUC Curve */}
            <section
              aria-label="ROC-AUC Curve"
              className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4"
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
                    ROC-AUC Curve
                  </h3>
                  <p className="text-[11px] text-zinc-400">
                    {youden
                      ? `Youden's J optimal threshold: ${youden.threshold?.toFixed(2) ?? "—"} (TPR ${youden.tpr.toFixed(2)}, FPR ${youden.fpr.toFixed(2)})`
                      : "True Positive Rate vs False Positive Rate"}
                  </p>
                </div>
                <button
                  type="button"
                  aria-pressed={rocShowTable}
                  onClick={() => setRocShowTable((v) => !v)}
                  className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-[11px] font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                >
                  {rocShowTable ? "Chart View" : "Data Table"}
                </button>
              </div>

              {rocCurve.length === 0 ? (
                <div className="flex h-64 items-center justify-center rounded border border-dashed border-zinc-800 text-xs text-zinc-400">
                  No ROC curve points recorded.
                </div>
              ) : rocShowTable ? (
                <div className="max-h-64 overflow-auto rounded border border-zinc-800">
                  <table className="w-full text-left text-xs">
                    <thead className="border-b border-zinc-800 bg-zinc-950">
                      <tr>
                        <th scope="col" className="px-3 py-1.5 font-mono text-zinc-400">
                          Threshold
                        </th>
                        <th scope="col" className="px-3 py-1.5 font-mono text-zinc-400">
                          FPR
                        </th>
                        <th scope="col" className="px-3 py-1.5 font-mono text-zinc-400">
                          TPR
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/70">
                      {rocCurve.map((pt, idx) => (
                        <tr key={idx}>
                          <td className="px-3 py-1 font-mono tabular-nums text-zinc-300">
                            {pt.threshold !== undefined ? pt.threshold.toFixed(2) : "—"}
                          </td>
                          <td className="px-3 py-1 font-mono tabular-nums text-zinc-200">
                            {pt.fpr.toFixed(3)}
                          </td>
                          <td className="px-3 py-1 font-mono tabular-nums text-emerald-300">
                            {pt.tpr.toFixed(3)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <svg
                  viewBox={`0 0 ${rocW} ${rocH}`}
                  role="img"
                  aria-labelledby="roc-chart-title"
                  aria-describedby="roc-chart-desc"
                  className="h-64 w-full"
                >
                  <title id="roc-chart-title">ROC-AUC curve for {defaultRun?.name}</title>
                  <desc id="roc-chart-desc">
                    Receiver Operating Characteristic curve with AUC{" "}
                    {evalData.roc_auc?.toFixed(3) ?? "unknown"}, 0.50 random baseline diagonal, and
                    Youden&apos;s J optimal operating point.
                  </desc>

                  {/* Grid */}
                  {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                    <g key={t}>
                      <line
                        x1={rocPad.left}
                        y1={ry(t)}
                        x2={rocW - rocPad.right}
                        y2={ry(t)}
                        stroke="#27272a"
                        strokeWidth={1}
                      />
                      <line
                        x1={rx(t)}
                        y1={rocPad.top}
                        x2={rx(t)}
                        y2={rocH - rocPad.bottom}
                        stroke="#27272a"
                        strokeWidth={1}
                      />
                      <text
                        x={rocPad.left - 6}
                        y={ry(t) + 3}
                        textAnchor="end"
                        className="fill-zinc-400 font-mono text-[10px]"
                      >
                        {t.toFixed(2)}
                      </text>
                      <text
                        x={rx(t)}
                        y={rocH - 18}
                        textAnchor="middle"
                        className="fill-zinc-400 font-mono text-[10px]"
                      >
                        {t.toFixed(2)}
                      </text>
                    </g>
                  ))}

                  {/* 0.50 Random baseline */}
                  <line
                    x1={rx(0)}
                    y1={ry(0)}
                    x2={rx(1)}
                    y2={ry(1)}
                    stroke="#71717a"
                    strokeWidth={1.2}
                    strokeDasharray="4 4"
                  />

                  {/* ROC Curve */}
                  <path
                    d={rocCurve
                      .map((p, i) => `${i === 0 ? "M" : "L"} ${rx(p.fpr).toFixed(1)} ${ry(p.tpr).toFixed(1)}`)
                      .join(" ")}
                    fill="none"
                    stroke="#38bdf8"
                    strokeWidth={2.2}
                    strokeLinejoin="round"
                  />

                  {/* Youden's J marker */}
                  {youden && (
                    <g>
                      <circle
                        cx={rx(youden.fpr)}
                        cy={ry(youden.tpr)}
                        r={5}
                        fill="#34d399"
                        stroke="#09090b"
                        strokeWidth={1.5}
                      />
                      <text
                        x={Math.min(rocW - 95, rx(youden.fpr) + 8)}
                        y={Math.max(rocPad.top + 12, ry(youden.tpr) - 8)}
                        className="fill-emerald-300 font-mono text-[10px]"
                      >
                        J* ({youden.threshold?.toFixed(2) ?? "opt"})
                      </text>
                    </g>
                  )}

                  <text
                    x={rocPad.left + rocPlotW / 2}
                    y={rocH - 4}
                    textAnchor="middle"
                    className="fill-zinc-400 font-mono text-[10px]"
                  >
                    False Positive Rate (FPR)
                  </text>
                </svg>
              )}
            </section>

            {/* Confusion Matrix */}
            <section
              aria-label="Confusion Matrix"
              className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4"
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
                    Confusion Matrix
                  </h3>
                  <p className="text-[11px] text-zinc-400">Rows: Actual · Columns: Predicted</p>
                </div>
                <div className="inline-flex rounded border border-zinc-700 bg-zinc-950 p-0.5">
                  <button
                    type="button"
                    aria-pressed={cmMode === "counts"}
                    onClick={() => setCmMode("counts")}
                    className={`h-7 rounded px-2.5 text-[11px] font-medium ${
                      cmMode === "counts" ? "bg-zinc-700 text-zinc-50" : "text-zinc-300 hover:text-zinc-100"
                    }`}
                  >
                    Counts
                  </button>
                  <button
                    type="button"
                    aria-pressed={cmMode === "percent"}
                    onClick={() => setCmMode("percent")}
                    className={`h-7 rounded px-2.5 text-[11px] font-medium ${
                      cmMode === "percent" ? "bg-zinc-700 text-zinc-50" : "text-zinc-300 hover:text-zinc-100"
                    }`}
                  >
                    Row %
                  </button>
                </div>
              </div>

              {!confusion || confusion.labels.length === 0 ? (
                <div className="flex h-64 items-center justify-center rounded border border-dashed border-zinc-800 text-xs text-zinc-400">
                  No confusion matrix recorded.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table role="grid" className="w-full border-collapse text-center text-xs">
                    <thead>
                      <tr>
                        <th scope="col" className="p-2 text-left font-mono text-[11px] text-zinc-400">
                          Actual \ Pred
                        </th>
                        {confusion.labels.map((lbl) => (
                          <th
                            key={lbl}
                            scope="col"
                            className="border border-zinc-800 bg-zinc-950/70 p-2 font-mono text-zinc-200"
                          >
                            {lbl}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {confusion.labels.map((rowLbl, rIdx) => (
                        <tr key={rowLbl}>
                          <th
                            scope="row"
                            className="border border-zinc-800 bg-zinc-950/70 p-2 text-left font-mono text-zinc-200"
                          >
                            {rowLbl}
                          </th>
                          {confusion.labels.map((_, cIdx) => {
                            const val = normalizedCm[rIdx]?.[cIdx] ?? 0;
                            const isDiag = rIdx === cIdx;
                            return (
                              <td
                                key={cIdx}
                                className={`border border-zinc-800 p-4 font-mono text-sm tabular-nums ${
                                  isDiag
                                    ? "bg-emerald-950/40 font-semibold text-emerald-200"
                                    : "bg-zinc-900/40 text-zinc-300"
                                }`}
                              >
                                {cmMode === "percent" ? `${val.toFixed(1)}%` : val}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>

          {/* Feature Importance / SHAP Diverging Bars */}
          <section
            aria-label="Feature Importance and SHAP"
            className="rounded-md border border-zinc-800 bg-zinc-900/60 p-4"
          >
            <div className="mb-3 flex items-center justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
                  Feature Importance / SHAP Impact
                </h3>
                <p className="text-[11px] text-zinc-400">
                  Positive impact pushes prediction higher; negative impact pushes prediction lower.
                </p>
              </div>
              <button
                type="button"
                aria-pressed={fiShowTable}
                onClick={() => setFiShowTable((v) => !v)}
                className="h-7 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 text-[11px] font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
              >
                {fiShowTable ? "Chart View" : "Data Table"}
              </button>
            </div>

            {features.length === 0 ? (
              <div className="flex h-36 items-center justify-center rounded border border-dashed border-zinc-800 text-xs text-zinc-400">
                No feature importance data recorded.
              </div>
            ) : fiShowTable ? (
              <div className="overflow-x-auto rounded border border-zinc-800">
                <table className="w-full text-left text-xs">
                  <thead className="border-b border-zinc-800 bg-zinc-950">
                    <tr>
                      <th scope="col" className="px-3 py-2 text-zinc-400">
                        Feature
                      </th>
                      <th scope="col" className="px-3 py-2 text-zinc-400">
                        Importance / SHAP
                      </th>
                      <th scope="col" className="px-3 py-2 text-zinc-400">
                        Direction
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {features.map((f) => (
                      <tr key={f.feature}>
                        <td className="px-3 py-1.5 font-mono text-zinc-200">{f.feature}</td>
                        <td className="px-3 py-1.5 font-mono tabular-nums text-zinc-200">
                          {f.importance.toFixed(4)}
                        </td>
                        <td className="px-3 py-1.5 text-zinc-300">
                          {f.direction ?? (f.importance >= 0 ? "positive" : "negative")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <svg
                viewBox={`0 0 ${fiW} ${fiH}`}
                role="img"
                aria-labelledby="shap-chart-title"
                aria-describedby="shap-chart-desc"
                className="w-full"
              >
                <title id="shap-chart-title">Feature importance and SHAP diverging bar chart</title>
                <desc id="shap-chart-desc">
                  Horizontal diverging bars for {features.length} features showing positive and negative
                  model impact.
                </desc>

                {/* Zero center line */}
                <line
                  x1={fiZeroX}
                  y1={fiPad.top - 4}
                  x2={fiZeroX}
                  y2={fiH - fiPad.bottom + 4}
                  stroke="#52525b"
                  strokeWidth={1}
                />

                {features.map((f, idx) => {
                  const y = fiPad.top + idx * rowH;
                  const ratio = f.importance / maxAbsImportance;
                  const barW = Math.abs(ratio) * (fiPlotW / 2 - 12);
                  const isPos = f.importance >= 0;
                  const barX = isPos ? fiZeroX : fiZeroX - barW;
                  return (
                    <g key={f.feature}>
                      <text
                        x={fiPad.left - 10}
                        y={y + 15}
                        textAnchor="end"
                        className="fill-zinc-200 font-mono text-[11px]"
                      >
                        {f.feature}
                      </text>
                      <rect
                        x={barX}
                        y={y + 5}
                        width={Math.max(2, barW)}
                        height={14}
                        rx={2}
                        fill={isPos ? "#34d399" : "#fb923c"}
                        fillOpacity={0.85}
                      />
                      <text
                        x={isPos ? fiZeroX + barW + 6 : fiZeroX - barW - 6}
                        y={y + 15}
                        textAnchor={isPos ? "start" : "end"}
                        className="fill-zinc-300 font-mono text-[10px]"
                      >
                        {f.importance >= 0 ? "+" : ""}
                        {f.importance.toFixed(3)}
                      </text>
                    </g>
                  );
                })}
              </svg>
            )}
          </section>
        </>
      )}
    </div>
  );
}
