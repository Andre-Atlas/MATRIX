"use client";

import { useMemo, useState } from "react";
import { confusionCells, curveLegend, histogramBars, prepareRoc, preparePr, prevalence, regressionScatter } from "@/lib/runs/reports";
import { formatMetric } from "@/lib/runs/format";
import { confusionTooltip, importanceTooltip, scatterTooltip } from "@/lib/runs/tooltips";
import type { ClassificationReport, ImportanceReport, RegressionReport, Report, TableReport } from "@/lib/runs/types";
import { Chart, type EChartOption } from "@/components/charts";
import { GRID, seriesColor } from "@/components/charts/theme";
import { EmptyState, Kpi, td, th } from "@/components/ui";
import { toggleBtn } from "./ui";

const f3 = (v: number) => (Number.isFinite(v) ? v.toFixed(3) : "—");
const unitAxis = (name: string) => ({ type: "value", min: 0, max: 1, name, nameLocation: "middle", nameGap: 26, axisLabel: { formatter: (v: number) => v.toFixed(1) } });

export function ReportsView({ reports }: { reports: Report[] }) {
  if (!reports.length) {
    return (
      <EmptyState title="No reports">
        Log one with <code className="font-mono text-zinc-300">matrix_sdk.evaluation.log_classification_report(run, y_true, y_score)</code> or{" "}
        <code className="font-mono text-zinc-300">run.log_report(kind, name, data)</code>.
      </EmptyState>
    );
  }
  return (
    <div className="space-y-6">
      <nav aria-label="Reports" className="flex flex-wrap gap-2 text-xs">
        {reports.map((r) => (
          <a key={r.id} href={`#report-${r.id}`} className="rounded border border-zinc-800 px-2 py-0.5 text-zinc-400 hover:text-zinc-100">
            <span className="text-zinc-500">{r.kind}</span> · {r.name}
          </a>
        ))}
      </nav>
      {reports.map((r) => (
        <section key={r.id} id={`report-${r.id}`} aria-labelledby={`h-${r.id}`} className="scroll-mt-4">
          <h2 id={`h-${r.id}`} className="mb-2 flex items-baseline gap-2 text-sm font-medium text-zinc-100">
            {r.name}
            <span className="rounded border border-zinc-700 px-1.5 font-mono text-[10px] font-normal text-zinc-400">{r.kind}</span>
          </h2>
          {r.kind === "classification" ? (
            <ClassificationView data={r.data} />
          ) : r.kind === "regression" ? (
            <RegressionView data={r.data} />
          ) : r.kind === "importance" ? (
            <ImportanceView data={r.data} />
          ) : r.kind === "table" ? (
            <TableView data={r.data} />
          ) : (
            <pre className="max-h-96 overflow-auto rounded-md border border-zinc-800 bg-zinc-900/60 p-3 font-mono text-xs text-zinc-300">{JSON.stringify(r.data, null, 2)}</pre>
          )}
        </section>
      ))}
    </div>
  );
}

// ── classification ───────────────────────────────────────────────────────────────────────────

function ClassificationView({ data }: { data: ClassificationReport }) {
  const [normalized, setNormalized] = useState(true);
  const roc = useMemo<EChartOption>(
    () => ({
      grid: { ...GRID, left: 44, bottom: 40, right: 12, top: 12 },
      legend: { orient: "vertical", right: 20, bottom: 52, backgroundColor: "rgba(9,9,11,0.85)", padding: 6, itemHeight: 8, data: data.roc.map((c) => curveLegend(c.label, "AUC", c.auc)) },
      tooltip: { trigger: "item", valueFormatter: (v: unknown) => (typeof v === "number" ? f3(v) : String(v)) },
      xAxis: unitAxis("False positive rate"),
      yAxis: unitAxis("True positive rate"),
      series: [
        ...data.roc.map((c, i) => ({ name: curveLegend(c.label, "AUC", c.auc), type: "line", showSymbol: false, data: prepareRoc(c.fpr, c.tpr), lineStyle: { width: 1.6 }, color: seriesColor(i) })),
        { name: "chance", type: "line", data: [[0, 0], [1, 1]], showSymbol: false, silent: true, lineStyle: { type: "dashed", color: "#52525b", width: 1 }, color: "#52525b" },
      ],
    }),
    [data.roc],
  );
  const pr = useMemo<EChartOption>(
    () => ({
      grid: { ...GRID, left: 44, bottom: 40, right: 12, top: 12 },
      legend: { orient: "vertical", left: 54, bottom: 52, backgroundColor: "rgba(9,9,11,0.85)", padding: 6, itemHeight: 8 },
      tooltip: { trigger: "item" },
      xAxis: unitAxis("Recall"),
      yAxis: unitAxis("Precision"),
      series: data.pr.map((c, i) => {
        const base = prevalence(data.confusion_matrix, data.labels.indexOf(c.label));
        return {
          name: curveLegend(c.label, "AP", c.ap),
          type: "line",
          step: "end",
          showSymbol: false,
          data: preparePr(c.precision, c.recall),
          lineStyle: { width: 1.6 },
          color: seriesColor(i),
          markLine: base === null ? undefined : { silent: true, symbol: "none", label: { show: false }, lineStyle: { type: "dotted", color: seriesColor(i), opacity: 0.5 }, data: [{ yAxis: base }] },
        };
      }),
    }),
    [data.pr, data.confusion_matrix, data.labels],
  );
  const cm = useMemo<EChartOption>(() => {
    const cells = confusionCells(data.confusion_matrix, normalized);
    const n = data.labels.length;
    const max = normalized ? 1 : Math.max(1, ...cells.map((c) => c[2]));
    return {
      grid: { left: 90, right: 70, top: 10, bottom: 50 },
      tooltip: {
        formatter: (p: { value: [number, number, number] }) =>
          confusionTooltip(data.labels[n - 1 - p.value[1]], data.labels[p.value[0]], p.value[2], normalized),
      },
      xAxis: { type: "category", data: data.labels, name: "Predicted", nameLocation: "middle", nameGap: 30, splitArea: { show: false } },
      yAxis: { type: "category", data: [...data.labels].reverse(), name: "True", nameLocation: "middle", nameGap: 70 },
      visualMap: { min: 0, max, calculable: false, orient: "vertical", right: 0, top: "middle", itemHeight: 120, textStyle: { color: "#a1a1aa", fontSize: 10 }, inRange: { color: ["#18181b", "#0c4a6e", "#0284c7", "#7dd3fc"] }, formatter: (v: number) => (normalized ? v.toFixed(2) : String(Math.round(v))) },
      series: [
        {
          type: "heatmap",
          data: cells,
          label: { show: true, color: "#f4f4f5", fontSize: 11, fontFamily: "ui-monospace, monospace", formatter: (p: { value: [number, number, number] }) => (normalized ? p.value[2].toFixed(2) : String(p.value[2])) },
          itemStyle: { borderColor: "#09090b", borderWidth: 1 },
          emphasis: { itemStyle: { borderColor: "#e4e4e7" } },
        },
      ],
    };
  }, [data.confusion_matrix, data.labels, normalized]);

  const keyMetrics = Object.entries(data.metrics);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        {keyMetrics.map(([k, v]) => (
          <Kpi key={k} label={k.replace(/_/g, " ")} value={f3(v)} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Chart
          title="ROC (one-vs-rest)"
          description={`ROC curves for ${data.roc.length} classes with the chance diagonal. ${data.roc.map((c) => curveLegend(c.label, "AUC", c.auc)).join("; ")}.`}
          option={roc}
          height={320}
          table={{ columns: ["class", "AUC", "points"], rows: data.roc.map((c) => [c.label, f3(c.auc), c.fpr.length]) }}
        />
        <Chart
          title="Precision–recall"
          description={`Precision-recall curves; dotted lines are each class's prevalence. ${data.pr.map((c) => curveLegend(c.label, "AP", c.ap)).join("; ")}.`}
          option={pr}
          height={320}
          table={{ columns: ["class", "AP", "points"], rows: data.pr.map((c) => [c.label, f3(c.ap), c.precision.length]) }}
        />
        <Chart
          title={`Confusion matrix${normalized ? " (row-normalized)" : " (counts)"}`}
          description={`Heatmap of true (rows) versus predicted (columns) labels for ${data.labels.join(", ")}.`}
          option={cm}
          height={320}
          actions={
            <button type="button" aria-pressed={normalized} onClick={() => setNormalized(!normalized)} className={`${toggleBtn(normalized)} h-6`}>
              Normalize rows
            </button>
          }
          table={{ columns: ["true \\ predicted", ...data.labels], rows: data.confusion_matrix.map((row, i) => [data.labels[i], ...row]) }}
        />
      </div>
      <div className="overflow-x-auto rounded-md border border-zinc-800">
        <table className="w-full text-xs">
          <caption className="sr-only">Per-class metrics</caption>
          <thead className="bg-zinc-900/60">
            <tr>
              {["class", "precision", "recall", "f1", "support"].map((c) => (
                <th key={c} scope="col" className={th}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70 font-mono tabular-nums">
            {data.per_class.map((c) => (
              <tr key={c.label}>
                <td className={`${td} font-sans text-zinc-200`}>{c.label}</td>
                <td className={td}>{f3(c.precision)}</td>
                <td className={td}>{f3(c.recall)}</td>
                <td className={td}>{f3(c.f1)}</td>
                <td className={td}>{c.support}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── regression ───────────────────────────────────────────────────────────────────────────────

function RegressionView({ data }: { data: RegressionReport }) {
  const bars = useMemo(() => histogramBars(data.residual_hist.edges, data.residual_hist.counts), [data.residual_hist]);
  const scatter = useMemo(() => regressionScatter(data.sample.y_true, data.sample.y_pred), [data.sample]);
  const hist: EChartOption = {
    grid: { ...GRID, bottom: 40 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: bars.map((b) => b.label), name: "Residual (pred − true)", nameLocation: "middle", nameGap: 26, axisLabel: { interval: Math.max(0, Math.floor(bars.length / 6) - 1), formatter: (v: string) => v.split(" – ")[0] } },
    yAxis: { type: "value", name: "count" },
    series: [{ type: "bar", data: bars.map((b) => b.count), barCategoryGap: "4%", itemStyle: { color: seriesColor(1) } }],
  };
  const sc: EChartOption = {
    grid: { ...GRID, left: 56, bottom: 40 },
    tooltip: { trigger: "item", formatter: (p: { value: [number, number] }) => scatterTooltip(p.value[0], p.value[1]) },
    xAxis: { type: "value", scale: true, name: "y_true", nameLocation: "middle", nameGap: 26 },
    yAxis: { type: "value", scale: true, name: "y_pred" },
    series: [
      { type: "scatter", data: scatter.points, symbolSize: 3, itemStyle: { color: seriesColor(0), opacity: 0.55 } },
      ...(scatter.diagonal ? [{ type: "line", data: scatter.diagonal, showSymbol: false, silent: true, lineStyle: { type: "dashed", color: "#a1a1aa", width: 1 } }] : []),
    ],
  };
  const m = data.metrics;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Kpi label="RMSE" value={formatMetric(m.rmse)} />
        <Kpi label="MAE" value={formatMetric(m.mae)} />
        <Kpi label="R²" value={f3(m.r2)} />
        {m.mape !== undefined && <Kpi label="MAPE" value={`${(m.mape * 100).toFixed(1)}%`} />}
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Chart title="Residual histogram" description={`Histogram of residuals in ${bars.length} bins.`} option={hist} height={300} table={{ columns: ["bin", "count"], rows: bars.map((b) => [b.label, b.count]) }} />
        <Chart
          title="Predicted vs true"
          description={`Scatter of ${scatter.points.length} sampled predictions against true values with the y = x diagonal.`}
          option={sc}
          height={300}
          table={{ columns: ["y_true", "y_pred"], rows: scatter.points.map((p) => [p[0], p[1]]) }}
        />
      </div>
    </div>
  );
}

// ── importance ───────────────────────────────────────────────────────────────────────────────

function ImportanceView({ data }: { data: ImportanceReport }) {
  const feats = useMemo(() => [...data.features].reverse(), [data.features]); // largest at the top
  const hasStd = feats.some((f) => f.std !== undefined);
  const option: EChartOption = {
    grid: { left: 150, right: 24, top: 10, bottom: 28 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (ps: { dataIndex: number }[]) => { const f = feats[ps[0].dataIndex]; return importanceTooltip(f.name, f.importance, f.std); } },
    xAxis: { type: "value", name: data.method, nameLocation: "middle", nameGap: 22 },
    yAxis: { type: "category", data: feats.map((f) => f.name), axisLabel: { width: 140, overflow: "truncate" } },
    series: [
      { type: "bar", data: feats.map((f) => f.importance), barCategoryGap: "30%", itemStyle: { color: seriesColor(0) } },
      ...(hasStd
        ? [
            {
              type: "custom",
              silent: true,
              data: feats.map((f, i) => [i, f.importance - (f.std ?? 0), f.importance + (f.std ?? 0)]),
              renderItem: (_p: unknown, api: { value: (d: number) => number; coord: (v: [number, number]) => [number, number]; size: (v: [number, number]) => [number, number] }) => {
                const lo = api.coord([api.value(1), api.value(0)]);
                const hi = api.coord([api.value(2), api.value(0)]);
                const h = api.size([0, 1])[1] * 0.25;
                const style = { stroke: "#e4e4e7", lineWidth: 1 };
                return {
                  type: "group",
                  children: [
                    { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style },
                    { type: "line", shape: { x1: lo[0], y1: lo[1] - h, x2: lo[0], y2: lo[1] + h }, style },
                    { type: "line", shape: { x1: hi[0], y1: hi[1] - h, x2: hi[0], y2: hi[1] + h }, style },
                  ],
                };
              },
              encode: { x: [1, 2], y: 0 },
              z: 10,
            },
          ]
        : []),
    ],
  };
  return (
    <Chart
      title={`Feature importance · method: ${data.method}${hasStd ? " · whiskers = ±1 std" : ""}`}
      description={`Horizontal bar chart of ${feats.length} features by ${data.method} importance; top: ${data.features.slice(0, 3).map((f) => f.name).join(", ")}.`}
      option={option}
      height={Math.max(200, feats.length * 22 + 40)}
      table={{ columns: ["feature", "importance", "std"], rows: data.features.map((f) => [f.name, formatMetric(f.importance), f.std === undefined ? null : formatMetric(f.std)]) }}
    />
  );
}

// ── table ────────────────────────────────────────────────────────────────────────────────────

const CELL_MAX = 160;

function TableView({ data }: { data: TableReport }) {
  const cell = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));
  return (
    <div>
      <p className="mb-1 text-xs text-zinc-500">
        {data.rows.length} rows × {data.columns.length} columns
        {data.truncated && (
          <span className="ml-2 rounded border border-amber-800/60 bg-amber-950/40 px-1.5 text-[11px] text-amber-300">truncated: the full table is an artifact</span>
        )}
      </p>
      <div className="max-h-96 overflow-auto rounded-md border border-zinc-800">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-900">
            <tr>{data.columns.map((c) => <th key={c} scope="col" className={th}>{c}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70 font-mono">
            {data.rows.map((r, i) => (
              <tr key={i}>
                {r.map((v, j) => {
                  const s = cell(v);
                  const cut = s.length > CELL_MAX;
                  return (
                    <td key={j} className="max-w-[28rem] px-3 py-1 align-top text-zinc-300" title={cut ? s : undefined}>
                      {cut ? `${s.slice(0, CELL_MAX)}…` : s}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
