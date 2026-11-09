import Link from "next/link";
import { formatDuration } from "@/lib/format";
import type { WaterfallRow } from "@/lib/trace-tree";
import { KIND_STYLE, KindBadge } from "@/components/ui";

const TICKS = [0, 25, 50, 75, 100];

export function Waterfall({
  rows,
  totalMs,
  selectedId,
  hrefFor,
}: {
  rows: WaterfallRow[];
  totalMs: number;
  selectedId: string | null;
  hrefFor: (spanId: string) => string;
}) {
  return (
    <div className="text-xs">
      <div className="grid grid-cols-[minmax(180px,38%)_1fr_64px] items-end border-b border-zinc-800 pb-1 text-[10px] text-zinc-500">
        <div className="pl-2 uppercase tracking-wider">Span</div>
        <div className="relative h-4" aria-hidden>
          {TICKS.map((t) => (
            <span
              key={t}
              className="absolute bottom-0 whitespace-nowrap font-mono tabular-nums"
              style={{
                left: `${t}%`,
                transform: t === 0 ? undefined : t === 100 ? "translateX(-100%)" : "translateX(-50%)",
              }}
            >
              {formatDuration((totalMs * t) / 100)}
            </span>
          ))}
        </div>
        <div className="pr-2 text-right uppercase tracking-wider">Dur.</div>
      </div>
      <ol aria-label="Spans" className="mt-1">
        {rows.map((r) => {
          const s = r.span;
          const selected = s.span_id === selectedId;
          const err = s.status === "error";
          return (
            <li key={s.span_id}>
              <Link
                href={hrefFor(s.span_id)}
                scroll={false}
                replace
                aria-current={selected ? "true" : undefined}
                className={`grid grid-cols-[minmax(180px,38%)_1fr_64px] items-center rounded-sm border-l-2 py-[3px] ${
                  selected
                    ? "border-emerald-400 bg-zinc-800/80"
                    : "border-transparent hover:bg-zinc-900"
                }`}
              >
                <span className="flex min-w-0 items-center gap-1.5 pr-2" style={{ paddingLeft: 6 + r.depth * 14 }}>
                  <KindBadge kind={s.kind} compact />
                  <span className={`truncate ${err ? "text-red-300" : "text-zinc-200"}`} title={s.name}>
                    {s.name}
                  </span>
                  {err && (
                    <span className="sr-only">(error)</span>
                  )}
                </span>
                <span className="relative h-4 border-l border-zinc-800/80">
                  {TICKS.slice(1, -1).map((t) => (
                    <span key={t} aria-hidden className="absolute inset-y-0 w-px bg-zinc-800/60" style={{ left: `${t}%` }} />
                  ))}
                  <span
                    aria-hidden
                    className={`absolute top-1 h-2 rounded-[2px] ${err ? "bg-red-500/80" : (KIND_STYLE[s.kind] ?? KIND_STYLE.UNKNOWN).bar}`}
                    style={{ left: `${r.offsetPct}%`, width: `${r.widthPct}%` }}
                  />
                  {r.ttftPct !== null && (
                    <span
                      title={`TTFT ${formatDuration(s.ttft_ms ?? 0)}`}
                      className="absolute top-0 h-4 w-0.5 -translate-x-1/2 rounded bg-amber-300"
                      style={{ left: `${r.ttftPct}%` }}
                    />
                  )}
                </span>
                <span className="pr-2 text-right font-mono tabular-nums text-zinc-400">
                  {formatDuration(s.duration_ms)}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
      <p className="mt-2 flex items-center gap-1.5 text-[11px] text-zinc-500">
        <span aria-hidden className="inline-block h-3 w-0.5 rounded bg-amber-300" /> time to first token
      </p>
    </div>
  );
}
