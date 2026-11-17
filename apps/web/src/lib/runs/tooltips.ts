// Pure builders for the HTML tooltips of the report charts. Every data-derived string is escaped
// with escapeHtml before being placed in markup (ECharts assigns the result to innerHTML).
import { escapeHtml } from "@/lib/escape-html";
import { formatMetric } from "./format";

export function confusionTooltip(trueLabel: string, predLabel: string, value: number, normalized: boolean): string {
  const shown = normalized ? `${(value * 100).toFixed(1)}% of row` : String(value);
  return `true <b>${escapeHtml(trueLabel)}</b> → predicted <b>${escapeHtml(predLabel)}</b><br/>${escapeHtml(shown)}`;
}

export function scatterTooltip(trueValue: number, predValue: number): string {
  return `true ${escapeHtml(formatMetric(trueValue))}<br/>pred ${escapeHtml(formatMetric(predValue))}`;
}

export function importanceTooltip(name: string, importance: number, std?: number): string {
  const spread = std !== undefined ? ` ± ${escapeHtml(formatMetric(std))}` : "";
  return `${escapeHtml(name)}<br/>${escapeHtml(formatMetric(importance))}${spread}`;
}
