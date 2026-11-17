// Dark ECharts theme matching the app (zinc surfaces, muted grid, mono numbers).

/** Categorical palette; avoids the emerald/red/amber status hues. Same order as VARIANT_COLORS. */
export const SERIES_COLORS = ["#38bdf8", "#a78bfa", "#fb923c", "#f472b6", "#22d3ee", "#facc15", "#94a3b8", "#c084fc", "#4ade80", "#f87171"];
export const seriesColor = (i: number) => SERIES_COLORS[((i % SERIES_COLORS.length) + SERIES_COLORS.length) % SERIES_COLORS.length];

const axis = {
  axisLine: { lineStyle: { color: "#3f3f46" } },
  axisTick: { lineStyle: { color: "#3f3f46" } },
  axisLabel: { color: "#a1a1aa", fontSize: 10, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  splitLine: { lineStyle: { color: "#27272a" } },
  nameTextStyle: { color: "#71717a", fontSize: 10 },
};

export const THEME = {
  color: SERIES_COLORS,
  backgroundColor: "transparent",
  textStyle: { color: "#d4d4d8", fontFamily: "ui-sans-serif, system-ui, sans-serif" },
  categoryAxis: axis,
  valueAxis: axis,
  logAxis: axis,
  timeAxis: axis,
  legend: { textStyle: { color: "#a1a1aa", fontSize: 11 }, inactiveColor: "#52525b" },
  tooltip: {
    backgroundColor: "#18181b",
    borderColor: "#3f3f46",
    textStyle: { color: "#e4e4e7", fontSize: 11 },
    axisPointer: { lineStyle: { color: "#52525b" }, crossStyle: { color: "#52525b" } },
  },
  dataZoom: { textStyle: { color: "#a1a1aa" }, borderColor: "#3f3f46", fillerColor: "rgba(52,211,153,0.12)" },
  toolbox: { iconStyle: { borderColor: "#71717a" }, emphasis: { iconStyle: { borderColor: "#e4e4e7" } } },
};

/** Grid with room for mono axis labels. */
export const GRID = { left: 56, right: 16, top: 28, bottom: 32, containLabel: false };
