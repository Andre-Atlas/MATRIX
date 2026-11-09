import type { MatrixSummary } from "@/lib/eval-matrix";
import { variantColor } from "./ui";

const ABBREV: Record<string, string> = {
  answer_relevancy: "relevancy",
  context_precision: "ctx_precision",
  faithfulness: "faithfulness",
};
function short(m: string, max: number): string {
  const s = m.length > max ? (ABBREV[m] ?? m) : m;
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** Grouped bars of variant means per metric (0..1), plain SVG. */
export function VariantChart({ summary }: { summary: MatrixSummary }) {
  const metrics = summary.metrics.filter((m) => summary.variants.some((v) => summary.cells[v][m].mean !== null));
  if (!metrics.length) return null;
  const nv = summary.variants.length;
  const W = 576;
  const H = 196;
  const pad = { l: 28, r: 6, t: 8, b: 34 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;
  const groupW = plotW / metrics.length;
  const barW = Math.max(3, Math.min(14, (groupW - 10) / nv - 2));
  const y = (v: number) => pad.t + plotH * (1 - v);

  return (
    <figure className="min-w-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Mean score per metric for each variant"
        className="h-auto w-full"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke={t === 0 ? "#52525b" : "#27272a"} strokeWidth={1} />
            <text x={pad.l - 5} y={y(t) + 3} textAnchor="end" fontSize={9} fill="#71717a" fontFamily="ui-monospace, monospace">
              {t.toFixed(t === 0 || t === 1 ? 0 : 2)}
            </text>
          </g>
        ))}
        {metrics.map((m, gi) => {
          const gx = pad.l + gi * groupW;
          const total = nv * (barW + 2) - 2;
          const x0 = gx + (groupW - total) / 2;
          return (
            <g key={m}>
              {summary.variants.map((v, vi) => {
                const mean = summary.cells[v][m].mean;
                if (mean === null) return null;
                const best = summary.best[m]?.includes(v);
                return (
                  <rect
                    key={v}
                    x={x0 + vi * (barW + 2)}
                    y={y(Math.max(0, Math.min(1, mean)))}
                    width={barW}
                    height={Math.max(1, plotH * Math.max(0, Math.min(1, mean)))}
                    rx={1.5}
                    fill={variantColor(vi)}
                    opacity={best ? 1 : 0.6}
                  >
                    <title>{`${v} · ${m}: ${mean.toFixed(2)}`}</title>
                  </rect>
                );
              })}
              <text
                x={gx + groupW / 2}
                y={H - pad.b + 13}
                textAnchor="middle"
                fontSize={9.5}
                fill="#a1a1aa"
                fontFamily="ui-monospace, monospace"
              >
                {short(m, Math.max(4, Math.floor(groupW / 6.2)))}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-400">
        {summary.variants.map((v, i) => (
          <span key={v} className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: variantColor(i) }} />
            <span className="font-mono">{v}</span>
          </span>
        ))}
        <span className="text-zinc-600">solid = best in column</span>
      </figcaption>
    </figure>
  );
}
