import type { ExperimentStatus, MetricScore } from "@/lib/types";

export const field =
  "h-7 rounded border border-zinc-700 bg-zinc-900 px-2 text-zinc-100 placeholder:text-zinc-600 hover:border-zinc-600 disabled:opacity-50";
export const area =
  "w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 font-mono text-xs leading-relaxed text-zinc-100 placeholder:text-zinc-600 hover:border-zinc-600";
export const label = "flex flex-col gap-1 text-[11px] uppercase tracking-wider text-zinc-500";
export const primaryBtn =
  "h-7 rounded border border-emerald-700/70 bg-emerald-900/40 px-3 text-emerald-200 hover:bg-emerald-900/70 disabled:cursor-not-allowed disabled:opacity-50";
export const secondaryBtn =
  "h-7 rounded border border-zinc-700 bg-zinc-800/60 px-3 text-zinc-200 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50";

/** Categorical palette for variants; avoids the emerald/red/amber status hues. */
export const VARIANT_COLORS = ["#38bdf8", "#a78bfa", "#fb923c", "#f472b6", "#22d3ee", "#facc15", "#94a3b8", "#c084fc"];
export const variantColor = (i: number) => VARIANT_COLORS[i % VARIANT_COLORS.length];

export function VariantSwatch({ index }: { index: number }) {
  return <span aria-hidden className="inline-block h-2 w-2 shrink-0 rounded-sm" style={{ background: variantColor(index) }} />;
}

const STATUS_STYLE: Record<ExperimentStatus, { cls: string; dot: string }> = {
  queued: { cls: "border-zinc-700 bg-zinc-900 text-zinc-300", dot: "bg-zinc-500" },
  running: { cls: "border-sky-800/70 bg-sky-950/40 text-sky-300", dot: "bg-sky-400 animate-pulse" },
  done: { cls: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300", dot: "bg-emerald-400" },
  failed: { cls: "border-red-800/70 bg-red-950/50 text-red-300", dot: "bg-red-400" },
};

export function ExperimentStatusBadge({ status }: { status: ExperimentStatus }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.queued;
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 text-[11px] leading-4 ${s.cls}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {status}
    </span>
  );
}

export function formatMetricValue(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : "n/a";
}

/** Green pass / red fail / grey n/a. Shows the value with 2 decimals. */
export function MetricChip({ name, score, dim = false }: { name: string; score: MetricScore | undefined; dim?: boolean }) {
  const passed = score?.passed;
  const cls =
    passed === true
      ? "border-emerald-800/60 bg-emerald-950/40 text-emerald-300"
      : passed === false
        ? "border-red-800/70 bg-red-950/50 text-red-300"
        : "border-zinc-700/80 bg-zinc-800/50 text-zinc-500";
  const verdict = passed === true ? "pass" : passed === false ? "fail" : "n/a";
  return (
    <span
      title={`${name}: ${formatMetricValue(score?.value)} (${verdict})${score?.reason ? ` — ${score.reason}` : ""}`}
      className={`inline-flex items-center gap-1 rounded border px-1 text-[10px] leading-4 ${cls} ${dim ? "opacity-40" : ""}`}
    >
      <span className="max-w-[7rem] truncate">{name}</span>
      <span className="font-mono tabular-nums">{formatMetricValue(score?.value)}</span>
    </span>
  );
}
