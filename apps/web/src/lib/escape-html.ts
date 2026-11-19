// Single HTML-escaping helper. ECharts writes a tooltip `formatter` string with innerHTML, so any
// text that comes from data (class labels, feature names, metric/run names...) MUST go through here.
// Same mapping as echarts.format.encodeHTML.
const MAP: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (c) => MAP[c]);
}
