import { diffStats, diffText, sideBySide, type DiffOp } from "@/lib/line-diff";

const cell = "whitespace-pre-wrap break-words px-2 py-px font-mono text-xs leading-relaxed";

function Gutter({ n }: { n?: number }) {
  return (
    <td className="w-9 select-none border-r border-zinc-800/80 px-1.5 text-right align-top font-mono text-[10px] leading-relaxed tabular-nums text-zinc-600">
      {n ?? ""}
    </td>
  );
}

export function DiffSummary({ ops }: { ops: DiffOp[] }) {
  const { added, removed, changed } = diffStats(ops);
  if (!changed) return <span className="text-zinc-500">no changes</span>;
  return (
    <span className="font-mono tabular-nums">
      <span className="text-emerald-300">+{added}</span> <span className="text-red-300">−{removed}</span>
    </span>
  );
}

/** Side-by-side line diff (old on the left). Pure render, safe in server components. */
export function SideBySideDiff({
  before,
  after,
  beforeLabel,
  afterLabel,
}: {
  before: string;
  after: string;
  beforeLabel: string;
  afterLabel: string;
}) {
  const ops = diffText(before, after);
  const rows = sideBySide(ops);
  if (!diffStats(ops).changed) {
    return <p className="rounded-md border border-zinc-800 px-3 py-4 text-center text-zinc-500">Identical content.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-md border border-zinc-800">
      <table className="w-full table-fixed border-collapse" aria-label={`Diff ${beforeLabel} to ${afterLabel}`}>
        <colgroup>
          <col className="w-9" />
          <col />
          <col className="w-9" />
          <col />
        </colgroup>
        <thead className="border-b border-zinc-800 bg-zinc-900/60 text-[11px] uppercase tracking-wider text-zinc-500">
          <tr>
            <th colSpan={2} className="px-2 py-1.5 text-left font-medium">
              {beforeLabel}
            </th>
            <th colSpan={2} className="border-l border-zinc-800 px-2 py-1.5 text-left font-medium">
              {afterLabel} <span className="ml-2 normal-case tracking-normal"><DiffSummary ops={ops} /></span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <Gutter n={r.left?.n} />
              <td
                className={`${cell} ${r.left && r.kind !== "equal" ? "bg-red-950/40 text-red-100" : r.left ? "text-zinc-300" : "bg-zinc-900/40"}`}
              >
                {r.left ? <>{r.kind !== "equal" && <span aria-hidden className="mr-1 text-red-400">−</span>}{r.left.text || " "}</> : null}
              </td>
              <td className="w-9 select-none border-x border-zinc-800/80 px-1.5 text-right align-top font-mono text-[10px] leading-relaxed tabular-nums text-zinc-600">
                {r.right?.n ?? ""}
              </td>
              <td
                className={`${cell} ${r.right && r.kind !== "equal" ? "bg-emerald-950/40 text-emerald-100" : r.right ? "text-zinc-300" : "bg-zinc-900/40"}`}
              >
                {r.right ? <>{r.kind !== "equal" && <span aria-hidden className="mr-1 text-emerald-400">+</span>}{r.right.text || " "}</> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Compact unified diff for narrow columns (annotation correction vs original). */
export function UnifiedDiff({ before, after }: { before: string; after: string }) {
  const ops = diffText(before, after);
  if (!diffStats(ops).changed) return <p className="text-xs text-zinc-500">No changes yet.</p>;
  return (
    <div className="max-h-56 overflow-auto rounded border border-zinc-800 bg-zinc-950" role="region" aria-label="Correction diff">
      {ops.map((o, i) => (
        <div
          key={i}
          className={`${cell} ${
            o.type === "add" ? "bg-emerald-950/40 text-emerald-100" : o.type === "del" ? "bg-red-950/40 text-red-100" : "text-zinc-400"
          }`}
        >
          <span aria-hidden className={`mr-1.5 inline-block w-2 ${o.type === "add" ? "text-emerald-400" : o.type === "del" ? "text-red-400" : "text-zinc-700"}`}>
            {o.type === "add" ? "+" : o.type === "del" ? "−" : " "}
          </span>
          {o.text || " "}
        </div>
      ))}
    </div>
  );
}
