"use client";

// Accessible chart frame: a <figure> with an ARIA label and description, the ECharts canvas
// (client-only, lazy) and a collapsible data table alternative for screen readers and copy/paste.
import dynamic from "next/dynamic";
import { useId } from "react";
import type { EChartProps } from "./echart";

export type { EChartOption } from "./echart";

const Canvas = dynamic(() => import("./echart"), {
  ssr: false,
  loading: () => <div className="h-full min-h-[120px] w-full animate-pulse rounded bg-zinc-800/40" />,
});

export type DataTable = { columns: string[]; rows: (string | number | null)[][]; note?: string };

const MAX_TABLE_ROWS = 200;

/** Evenly samples rows so the alternative table stays readable. */
function sampleRows<T>(rows: T[], max = MAX_TABLE_ROWS): T[] {
  if (rows.length <= max) return rows;
  return Array.from({ length: max }, (_, i) => rows[Math.round((i * (rows.length - 1)) / (max - 1))]);
}

export function Chart({
  title,
  description,
  table,
  height = 240,
  actions,
  className = "",
  ...rest
}: EChartProps & {
  title: string;
  description: string;
  table?: DataTable;
  actions?: React.ReactNode;
  className?: string;
}) {
  const id = useId();
  const rows = table ? sampleRows(table.rows) : [];
  return (
    <figure
      role="group"
      aria-labelledby={`${id}-t`}
      aria-describedby={`${id}-d`}
      className={`min-w-0 rounded-md border border-zinc-800 bg-zinc-900/40 p-2 ${className}`}
    >
      <figcaption className="flex items-center justify-between gap-2 px-1">
        <span id={`${id}-t`} className="truncate font-mono text-xs text-zinc-200" title={title}>
          {title}
        </span>
        {actions}
      </figcaption>
      <p id={`${id}-d`} className="sr-only">
        {description}
      </p>
      <div style={{ height }}>
        <Canvas height={height} {...rest} />
      </div>
      {table && (
        <details className="mt-1 px-1 text-[11px] text-zinc-500">
          <summary className="cursor-pointer select-none hover:text-zinc-300">Data table</summary>
          <div className="mt-1 max-h-56 overflow-auto">
            {(table.note || rows.length < table.rows.length) && (
              <p className="mb-1">
                {table.note ?? ""}
                {rows.length < table.rows.length ? ` Showing ${rows.length} of ${table.rows.length} rows, evenly sampled.` : ""}
              </p>
            )}
            <table className="w-full font-mono tabular-nums">
              <caption className="sr-only">{title}</caption>
              <thead>
                <tr>
                  {table.columns.map((c) => (
                    <th key={c} scope="col" className="sticky top-0 bg-zinc-900 px-2 py-0.5 text-left font-medium text-zinc-400">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-t border-zinc-800/60">
                    {r.map((v, j) => (
                      <td key={j} className="px-2 py-0.5 text-zinc-300">
                        {v === null ? "—" : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </figure>
  );
}
