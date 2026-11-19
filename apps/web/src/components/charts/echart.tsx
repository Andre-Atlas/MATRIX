"use client";

// Thin ECharts wrapper (canvas). Only the chart types and components used by the runs screens are
// registered, via `echarts/core`, to keep the bundle small. Loaded with next/dynamic (ssr: false)
// from ./index — import `EChart` from there, never this file directly.
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { THEME } from "./theme";

echarts.use([
  LineChart,
  BarChart,
  HeatmapChart,
  ScatterChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  ToolboxComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export type EChartOption = echarts.EChartsCoreOption;

export type EChartProps = {
  option: EChartOption;
  height?: number;
  /** Charts sharing a group get synchronized tooltips/axis pointers and zoom. */
  group?: string;
  /** Called with the instance after each option update (e.g. to enable box zoom). */
  onReady?: (chart: echarts.ECharts) => void;
};

export default function EChartCanvas({ option, height = 240, group, onReady }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const c = echarts.init(ref.current, THEME, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    c.setOption(option, { notMerge: true, lazyUpdate: true });
    onReady?.(c);
  }, [option, onReady]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !group) return;
    c.group = group;
    echarts.connect(group);
  }, [group]);

  // The canvas itself is hidden from assistive tech: the figure around it carries the label and
  // the data table alternative.
  return <div ref={ref} aria-hidden style={{ height, width: "100%" }} />;
}
