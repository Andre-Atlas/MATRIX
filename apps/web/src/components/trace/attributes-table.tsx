"use client";

import { useId, useMemo, useState } from "react";
import { formatValue } from "@/lib/format";

export function AttributesTable({ attributes }: { attributes: Record<string, unknown> }) {
  const [q, setQ] = useState("");
  const inputId = useId();
  const entries = useMemo(
    () =>
      Object.entries(attributes)
        .map(([k, v]) => [k, formatValue(v)] as const)
        .sort(([a], [b]) => a.localeCompare(b)),
    [attributes],
  );
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? entries.filter(([k, v]) => k.toLowerCase().includes(needle) || v.toLowerCase().includes(needle))
    : entries;

  if (!entries.length) return <p className="text-zinc-500">No attributes.</p>;
  return (
    <div>
      <label htmlFor={inputId} className="sr-only">
        Search attributes
      </label>
      <input
        id={inputId}
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={`Search ${entries.length} attributes`}
        className="mb-2 h-7 w-full rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-100 placeholder:text-zinc-600"
      />
      {shown.length === 0 ? (
        <p className="text-xs text-zinc-500">No attribute matches “{q}”.</p>
      ) : (
        <table className="w-full table-fixed text-xs">
          <tbody className="divide-y divide-zinc-800/70">
            {shown.map(([k, v]) => (
              <tr key={k}>
                <th scope="row" className="w-2/5 break-all py-1 pr-2 text-left align-top font-mono font-normal text-zinc-400">
                  {k}
                </th>
                <td className="whitespace-pre-wrap break-all py-1 align-top font-mono text-zinc-200">
                  {v === "" ? <span className="text-zinc-600">""</span> : v}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
