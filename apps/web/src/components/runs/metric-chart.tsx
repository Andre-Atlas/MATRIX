"use client";

import { useEffect, useMemo, useState } from "react";
import { proxy, explain } from "@/lib/proxy-client";
import { capPoints, ema, logSafe, relativeSeconds } from "@/lib/runs/series";
import { formatMetric, formatSeconds } from "@/lib/runs/format";
import { MAX_SERIES_KEYS, type SeriesData, type SeriesResponse, type XAxisMode } from "@/lib/runs/types";
import { Chart, type EChartOption } from "@/components/charts";
import { GRID, seriesColor } from "@/components/charts/theme";

export type LineInput = { name: string; data: SeriesData; color?: string };
export type ChartSettings = { smoothing: number; log: boolean; x: XAxisMode };

/** x values of a series on the chosen axis (time is relative wall time in seconds). */
const xsOf = (s: SeriesData, x: XAxisMode) => (x === "time" ? relativeSeconds(s.ts) : s.steps);

export function lineOption(lines: LineInput[], settings: ChartSettings, opts: { valueFormat?: (v: number) => string; showLegend?: boolean } = {}): EChartOption {
  const fmtY = opts.valueFormat ?? formatMetric;
  const fmtX = (v: number) => (settings.x === "time" ? formatSeconds(v) : String(Math.round(v)));
  const series: object[] = [];
  lines.forEach((l, i) => {
    const color = l.color ?? seriesColor(i);
    const xs = xsOf(l.data, settings.x);
    let ys = l.data.values;
    if (settings.log) ys = logSafe(ys);
    const pairs = (vals: (number | null)[]) => capPoints(xs.map((x, j) => [x, vals[j]] as [number, number | null]));
    const smooth = settings.smoothing > 0;
    series.push({
      name: smooth ? `${l.name} (raw)` : l.name,
      type: "line",
      data: pairs(ys),
      showSymbol: false,
      connectNulls: false, // NaN/Inf arrive as null: drawn as gaps
      lineStyle: { width: smooth ? 1 : 1.5, color, opacity: smooth ? 0.18 : 1 },
      itemStyle: { color },
      emphasis: { disabled: true },
      silent: smooth,
      tooltip: smooth ? { show: false } : undefined,
    });
    if (smooth) {
      series.push({ name: l.name, type: "line", data: pairs(ema(ys, settings.smoothing)), showSymbol: false, connectNulls: false, lineStyle: { width: 1.6, color }, itemStyle: { color }, emphasis: { disabled: true } });
    }
  });
  return {
    animation: false,
    grid: { ...GRID, top: opts.showLegend ? 34 : GRID.top },
    legend: opts.showLegend ? { top: 0, type: "scroll", data: lines.map((l) => l.name) } : undefined,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line" },
      valueFormatter: (v: unknown) => (typeof v === "number" ? fmtY(v) : "—"),
    },
    toolbox: { right: 4, top: 0, itemSize: 12, feature: { dataZoom: { yAxisIndex: "none", title: { zoom: "Box zoom", back: "Undo zoom" } }, restore: { title: "Reset" } } },
    dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none" }],
    xAxis: { type: "value", scale: true, axisLabel: { formatter: fmtX }, name: settings.x === "time" ? "time" : "step", nameGap: 4, nameLocation: "end" },
    yAxis: { type: settings.log ? "log" : "value", scale: !settings.log, axisLabel: { formatter: (v: number) => fmtY(v) } },
    series,
  };
}

/** Long-format table for the chart's screen-reader alternative. */
export function lineTable(lines: LineInput[], x: XAxisMode) {
  const rows: (string | number | null)[][] = [];
  for (const l of lines) {
    const xs = xsOf(l.data, x);
    l.data.values.forEach((v, j) => rows.push([l.name, x === "time" ? formatSeconds(xs[j]) : xs[j], v === null ? null : formatMetric(v)]));
  }
  return { columns: ["series", x, "value"], rows, note: "Empty values are NaN/Inf or missing points." };
}

/**
 * Fetches series of one run through the proxy (M4-reduced by the API, ≤ 1000 points per series),
 * in batches of 100 keys. Refetches when keys, axis or `refreshKey` change; keeps the previous data
 * while refetching so live updates do not flash.
 */
export function useRunSeries(runId: string, keys: string[], x: XAxisMode, refreshKey: string) {
  const [state, setState] = useState<{ data: Record<string, SeriesData> | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const keySig = keys.join("\u0000");
  useEffect(() => {
    let alive = true;
    const ks = keySig ? keySig.split("\u0000") : [];
    if (!ks.length) {
      setState({ data: {}, error: null, loading: false });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    const batches: string[][] = [];
    for (let i = 0; i < ks.length; i += MAX_SERIES_KEYS) batches.push(ks.slice(i, i + MAX_SERIES_KEYS));
    Promise.all(
      batches.map((b) =>
        proxy<SeriesResponse>("GET", `runs/${encodeURIComponent(runId)}/metrics?keys=${encodeURIComponent(b.join(","))}&max_points=1000&x=${x}`),
      ),
    )
      .then((res) => {
        if (!alive) return;
        setState({ data: Object.assign({}, ...res.map((r) => r.series)), error: null, loading: false });
      })
      .catch((e) => alive && setState((s) => ({ ...s, error: explain(e), loading: false })));
    return () => {
      alive = false;
    };
  }, [runId, keySig, x, refreshKey]);
  return state;
}

export function useStoredState<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(initial);
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw) setV({ ...initial, ...JSON.parse(raw) });
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set = (next: T) => {
    setV(next);
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      /* storage unavailable */
    }
  };
  return [v, set];
}

export function MetricLineChart({
  title,
  lines,
  settings,
  group,
  height = 220,
  valueFormat,
}: {
  title: string;
  lines: LineInput[];
  settings: ChartSettings;
  group?: string;
  height?: number;
  valueFormat?: (v: number) => string;
}) {
  const option = useMemo(() => lineOption(lines, settings, { valueFormat, showLegend: lines.length > 1 }), [lines, settings, valueFormat]);
  const table = useMemo(() => lineTable(lines, settings.x), [lines, settings.x]);
  const points = lines.reduce((a, l) => a + l.data.steps.length, 0);
  const empty = points === 0;
  return (
    <Chart
      title={title}
      description={`Line chart of ${lines.map((l) => l.name).join(", ")} by ${settings.x}${settings.log ? ", log scale" : ""}${settings.smoothing > 0 ? `, EMA smoothing ${settings.smoothing}` : ""}; ${points} points.${empty ? " No data." : ""}`}
      option={option}
      group={group}
      height={height}
      table={table}
    />
  );
}
