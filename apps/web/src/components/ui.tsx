import Link from "next/link";
import type { SpanKind } from "@/lib/types";
import { formatScore } from "@/lib/format";

// Kind palette: muted hues so kinds are distinguishable without competing with status colours.
export const KIND_STYLE: Record<SpanKind, { text: string; bar: string; dot: string; short: string }> = {
  AGENT: { text: "text-violet-300", bar: "bg-violet-400/70", dot: "bg-violet-400", short: "AGT" },
  LLM: { text: "text-sky-300", bar: "bg-sky-400/70", dot: "bg-sky-400", short: "LLM" },
  TOOL: { text: "text-orange-300", bar: "bg-orange-300/70", dot: "bg-orange-300", short: "TOOL" },
  RETRIEVER: { text: "text-cyan-300", bar: "bg-cyan-400/70", dot: "bg-cyan-400", short: "RET" },
  RERANKER: { text: "text-teal-300", bar: "bg-teal-400/70", dot: "bg-teal-400", short: "RRK" },
  GUARDRAIL: { text: "text-fuchsia-300", bar: "bg-fuchsia-400/70", dot: "bg-fuchsia-400", short: "GRD" },
  CHAIN: { text: "text-zinc-300", bar: "bg-zinc-400/70", dot: "bg-zinc-400", short: "CHN" },
  EMBEDDING: { text: "text-indigo-300", bar: "bg-indigo-400/70", dot: "bg-indigo-400", short: "EMB" },
  EVALUATOR: { text: "text-lime-300", bar: "bg-lime-400/70", dot: "bg-lime-400", short: "EVAL" },
  UNKNOWN: { text: "text-zinc-400", bar: "bg-zinc-600", dot: "bg-zinc-600", short: "UNK" },
};

export function KindBadge({ kind, compact = false }: { kind: SpanKind; compact?: boolean }) {
  const s = KIND_STYLE[kind] ?? KIND_STYLE.UNKNOWN;
  return (
    <span
      title={kind}
      className={`inline-flex shrink-0 items-center rounded border border-zinc-700/80 bg-zinc-900 px-1.5 font-mono text-[10px] font-medium leading-4 tracking-wide ${s.text}`}
    >
      {compact ? s.short : kind}
    </span>
  );
}

export function StatusBadge({ status }: { status: "ok" | "error" | "unset" }) {
  const cls =
    status === "error"
      ? "border-red-800/70 bg-red-950/50 text-red-300"
      : status === "ok"
        ? "border-emerald-800/60 bg-emerald-950/40 text-emerald-300"
        : "border-zinc-700 bg-zinc-900 text-zinc-400";
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 text-[11px] leading-4 ${cls}`}>
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${status === "error" ? "bg-red-400" : status === "ok" ? "bg-emerald-400" : "bg-zinc-500"}`}
      />
      {status}
    </span>
  );
}

/**
 * Visible, accessible badge for simulated results (local_sim benchmarks, fake sandboxes,
 * mock gateway, simulated_outputs safety scans, pca-lite/demo vectors).
 * Never color-only: always renders an icon + explicit "Simulated" text label.
 */
export function SimulatedBadge({
  reason,
  label = "Simulated",
  className = "",
}: {
  reason?: string;
  label?: string;
  className?: string;
}) {
  const fullLabel = reason ? `${label} (${reason})` : label;
  return (
    <span
      aria-label={fullLabel}
      title={fullLabel}
      className={`inline-flex shrink-0 items-center gap-1 rounded border border-amber-500/70 bg-amber-950/70 px-1.5 py-0.5 text-[11px] font-semibold leading-4 text-amber-200 ${className}`}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 16 16"
        fill="currentColor"
        className="h-3 w-3 shrink-0 text-amber-300"
      >
        <path d="M6 2a1 1 0 0 0 0 2h.5v2.764l-3.31 5.516A1.5 1.5 0 0 0 4.476 14.5h7.048a1.5 1.5 0 0 0 1.286-2.22L9.5 6.764V4h.5a1 1 0 1 0 0-2H6Zm2 5.236 2.758 4.597a.25.25 0 0 1-.214.37H5.456a.25.25 0 0 1-.214-.37L8 7.236Z" />
      </svg>
      <span>{label}</span>
    </span>
  );
}

/** Visible, accessible badge for scans or evaluations that were not evaluated (no target). */
export function NotEvaluatedBadge({
  label = "Not evaluated",
  className = "",
}: {
  label?: string;
  className?: string;
}) {
  return (
    <span
      aria-label={label}
      className={`inline-flex shrink-0 items-center gap-1 rounded border border-zinc-600 bg-zinc-900 px-2 py-0.5 text-[11px] font-semibold leading-4 text-zinc-200 ${className}`}
    >
      <span aria-hidden="true" className="font-mono text-zinc-400">
        ⊘
      </span>
      <span>{label}</span>
    </span>
  );
}

/** Scores are 0..1 by convention: < 0.5 red, < 0.8 amber, otherwise neutral-good. */
export function scoreTone(v: number): string {
  if (v < 0.5) return "text-red-300";
  if (v < 0.8) return "text-amber-300";
  return "text-emerald-300";
}

export function ScoreChips({
  scores,
  max = 3,
  labelWidth = "max-w-[9rem]",
}: {
  scores: Record<string, number>;
  max?: number;
  labelWidth?: string;
}) {
  const entries = Object.entries(scores);
  if (!entries.length) return <span className="text-zinc-600">—</span>;
  return (
    <span className="flex flex-nowrap gap-1">
      {entries.slice(0, max).map(([k, v]) => (
        <span
          key={k}
          title={`${k}: ${v}`}
          className="inline-flex items-center gap-1 rounded bg-zinc-800/80 px-1.5 text-[11px] leading-5"
        >
          <span className={`${labelWidth} truncate text-zinc-400`}>{k}</span>
          <span className={`font-mono tabular-nums ${scoreTone(v)}`}>{formatScore(v)}</span>
        </span>
      ))}
      {entries.length > max && <span className="text-[11px] text-zinc-400">+{entries.length - max}</span>}
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="truncate text-base font-semibold text-zinc-50">{title}</h1>
        {subtitle && <div className="mt-0.5 text-zinc-400">{subtitle}</div>}
      </div>
      {actions}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-zinc-800 px-6 py-10 text-center">
      <p className="font-medium text-zinc-300">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-zinc-400">{children}</div>}
    </div>
  );
}

export function Mono({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <span className={`font-mono tabular-nums ${className}`}>{children}</span>;
}

export function Kpi({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "error" | "warn";
}) {
  const color = tone === "error" ? "text-red-300" : tone === "warn" ? "text-amber-300" : "text-zinc-50";
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-wider text-zinc-400">{label}</div>
      <div className={`mt-1 font-mono text-xl tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[11px] tabular-nums text-zinc-400">{sub}</div>}
    </div>
  );
}

export function Segmented({
  label,
  options,
}: {
  label: string;
  options: { href: string; label: string; active: boolean }[];
}) {
  return (
    <nav aria-label={label} className="inline-flex rounded-md border border-zinc-800 bg-zinc-900 p-0.5">
      {options.map((o) => (
        <Link
          key={o.href}
          href={o.href}
          aria-current={o.active ? "true" : undefined}
          className={`rounded px-2.5 py-0.5 font-mono text-xs ${
            o.active ? "bg-zinc-700 text-zinc-50" : "text-zinc-400 hover:text-zinc-100"
          }`}
        >
          {o.label}
        </Link>
      ))}
    </nav>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-zinc-800/60 ${className}`} />;
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-2">
      <Skeleton className="h-8 w-full" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-7 w-full opacity-70" />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export const th = "whitespace-nowrap px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wider text-zinc-400";
export const td = "whitespace-nowrap px-3 py-1.5 align-middle";
