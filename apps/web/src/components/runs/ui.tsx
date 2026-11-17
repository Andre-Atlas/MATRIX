import type { RunStatus } from "@/lib/runs/types";

const STATUS_STYLE: Record<RunStatus, { cls: string; dot: string }> = {
  running: { cls: "border-sky-800/70 bg-sky-950/40 text-sky-300", dot: "bg-sky-400 motion-safe:animate-pulse" },
  finished: { cls: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300", dot: "bg-emerald-400" },
  failed: { cls: "border-red-800/70 bg-red-950/50 text-red-300", dot: "bg-red-400" },
  killed: { cls: "border-zinc-700 bg-zinc-900 text-zinc-400", dot: "bg-zinc-500" },
};

/** Run status; `running` pulses softly (off under prefers-reduced-motion). */
export function RunStatusBadge({ status }: { status: RunStatus }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.killed;
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 text-[11px] leading-4 ${s.cls}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {status}
    </span>
  );
}

export function TagChips({ tags, max = 3 }: { tags: string[]; max?: number }) {
  if (!tags.length) return <span className="text-zinc-600">—</span>;
  return (
    <span className="flex flex-nowrap gap-1">
      {tags.slice(0, max).map((t) => (
        <span key={t} className="rounded bg-zinc-800/80 px-1.5 font-mono text-[10px] leading-4 text-zinc-300">
          {t}
        </span>
      ))}
      {tags.length > max && <span className="text-[11px] text-zinc-500" title={tags.slice(max).join(", ")}>+{tags.length - max}</span>}
    </span>
  );
}

/** A link to a G2/G3 screen that does not exist yet: shown, but disabled. */
export function FutureLink({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-zinc-500">{label}</span>
      <span
        role="link"
        aria-disabled="true"
        title="Available in a later release"
        className="cursor-not-allowed font-mono text-xs text-zinc-400 underline decoration-dotted decoration-zinc-600 underline-offset-2"
      >
        {value}
      </span>
    </span>
  );
}

export const panel = "rounded-md border border-zinc-800 bg-zinc-900/40";
export const toggleBtn = (on: boolean) =>
  `h-7 rounded border px-2.5 text-xs ${on ? "border-emerald-700/70 bg-emerald-900/30 text-emerald-200" : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-zinc-600"}`;
