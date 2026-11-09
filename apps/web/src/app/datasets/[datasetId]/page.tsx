import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import { inputPreview } from "@/lib/eval-matrix";
import { formatTimestamp } from "@/lib/format";
import type { DatasetDetail } from "@/lib/types";
import { EmptyState, Mono, td, th } from "@/components/ui";
import { AddItemForm } from "@/components/evals/add-item-form";
import { DeleteItemButton } from "@/components/evals/delete-item-button";
import { primaryBtn, secondaryBtn } from "@/components/evals/ui";

export const dynamic = "force-dynamic";

export default async function DatasetPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  let detail: DatasetDetail;
  try {
    detail = await apiGet<DatasetDetail>(`/api/v1/datasets/${encodeURIComponent(datasetId)}`);
  } catch (e) {
    if (isNotFound(e)) notFound();
    throw e;
  }
  const { dataset, items } = detail;
  const id = encodeURIComponent(dataset.id);

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/datasets" className="hover:text-zinc-200">
          Datasets
        </Link>{" "}
        / <Mono>{dataset.name}</Mono>
      </nav>
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3 border-b border-zinc-800 pb-3">
        <div className="min-w-0">
          <h1 className="truncate font-mono text-base font-semibold text-zinc-50">{dataset.name}</h1>
          {dataset.description && <p className="mt-0.5 text-zinc-400">{dataset.description}</p>}
          <dl className="mt-1 flex gap-4 text-xs">
            <div className="flex gap-1.5">
              <dt className="text-zinc-500">Items</dt>
              <dd className="font-mono tabular-nums">{items.length}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-zinc-500">Created</dt>
              <dd className="font-mono tabular-nums">{formatTimestamp(dataset.created_at)}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-zinc-500">ID</dt>
              <dd className="font-mono text-zinc-400">{dataset.id}</dd>
            </div>
          </dl>
        </div>
        <div className="flex items-center gap-2">
          <a href={`/api/proxy/datasets/${id}/export.jsonl`} download={`${dataset.name}.jsonl`} className={`${secondaryBtn} leading-7`}>
            Export JSONL
          </a>
          <Link href={`/experiments/new?dataset=${id}`} className={`${primaryBtn} leading-7`}>
            New experiment
          </Link>
        </div>
      </header>

      <div className="mb-3">
        <AddItemForm datasetId={dataset.id} />
      </div>

      {items.length === 0 ? (
        <EmptyState title="This dataset has no items">
          Add one above, or open a span in a trace and use “Add to dataset”.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-md border border-zinc-800">
          <table className="w-full min-w-[1080px] table-fixed">
            <colgroup>
              <col className="w-10" />
              <col className="w-[32%]" />
              <col />
              <col className="w-20" />
              <col className="w-40" />
              <col className="w-40" />
              <col className="w-28" />
            </colgroup>
            <thead className="border-b border-zinc-800 bg-zinc-900/60">
              <tr>
                <th className={`${th} text-right`}>#</th>
                <th className={th}>Input</th>
                <th className={th}>Expected output</th>
                <th className={`${th} text-right`}>Context</th>
                <th className={th}>Source</th>
                <th className={th}>Created (UTC)</th>
                <th className={th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {items.map((it, i) => (
                <tr key={it.id} className="align-top hover:bg-zinc-900/50">
                  <td className={`${td} text-right align-top`}>
                    <Mono className="text-zinc-500">{i + 1}</Mono>
                  </td>
                  <td className="px-3 py-1.5">
                    <pre
                      className="line-clamp-3 whitespace-pre-wrap break-words font-mono text-xs text-emerald-100/90"
                      title={JSON.stringify(it.input, null, 2)}
                    >
                      {inputPreview(it.input, 400)}
                    </pre>
                  </td>
                  <td className="px-3 py-1.5">
                    {it.expected_output ? (
                      <p className="line-clamp-3 break-words text-xs text-zinc-300" title={it.expected_output}>
                        {it.expected_output}
                      </p>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className={`${td} text-right align-top`}>
                    <Mono className={it.context.length ? "text-zinc-200" : "text-zinc-600"} >{it.context.length}</Mono>
                  </td>
                  <td className={`${td} align-top`}>
                    {it.source_trace_id ? (
                      <Link
                        href={`/traces/${encodeURIComponent(it.source_trace_id)}${it.source_span_id ? `?span=${encodeURIComponent(it.source_span_id)}` : ""}`}
                        className="font-mono text-xs text-emerald-300 hover:underline"
                        title={`trace ${it.source_trace_id}${it.source_span_id ? ` · span ${it.source_span_id}` : ""}`}
                      >
                        {it.source_trace_id.slice(0, 8)}
                        {it.source_span_id && <span className="text-zinc-500">/{it.source_span_id.slice(0, 6)}</span>} ↗
                      </Link>
                    ) : (
                      <span className="text-xs text-zinc-600">manual</span>
                    )}
                  </td>
                  <td className={`${td} align-top`}>
                    <Mono className="text-xs text-zinc-400">{formatTimestamp(it.created_at)}</Mono>
                  </td>
                  <td className={`${td} text-right align-top`}>
                    <DeleteItemButton datasetId={dataset.id} itemId={it.id} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
