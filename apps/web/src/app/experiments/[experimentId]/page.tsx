import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import {
  inputPreview,
  matrixRows,
  regressionMap,
  summaryMeta,
  usesHeuristicJudge,
  summarizeMatrix,
  type MatrixCell,
  type MatrixSummary,
} from "@/lib/eval-matrix";
import { formatCost, formatDuration, formatPercent, formatTimestamp, formatTokens } from "@/lib/format";
import {
  metricKey,
  type DatasetDetail,
  type DatasetItem,
  type Experiment,
  type ExperimentDetail,
  type ExperimentListItem,
  type FaithfulnessStatement,
  type ListResponse,
  type Regression,
  type Variant,
} from "@/lib/types";
import { EmptyState, Mono, td, th } from "@/components/ui";
import { Payload } from "@/components/trace/span-panel";
import { CloseOnEscape } from "@/components/evals/close-on-escape";
import { StatusPoller } from "@/components/evals/status-poller";
import { VariantChart } from "@/components/evals/variant-chart";
import {
  ExperimentStatusBadge,
  HeuristicJudgeBadge,
  PartialBadge,
  MetricChip,
  VariantSwatch,
  formatMetricValue,
  primaryBtn,
} from "@/components/evals/ui";

export const dynamic = "force-dynamic";

type SP = { failures?: string; regressions?: string; metric?: string; item?: string; variant?: string };

function promptLabel(v: Variant): string {
  return "name" in v.prompt ? `${v.prompt.name} v${v.prompt.version}` : `inline ${v.prompt.type}`;
}

function regressionText(r: Regression): string {
  const text = `${r.metric} ${formatMetricValue(r.baseline)} → ${formatMetricValue(r.current)}`;
  return r.errored ? `${text} (erro na célula)` : text;
}

function SummaryTable({ summary, experiment }: { summary: MatrixSummary; experiment: Experiment }) {
  const byLabel = new Map(experiment.variants.map((v) => [v.label, v]));
  return (
    <div className="min-w-0 overflow-x-auto rounded-md border border-zinc-800">
      <table className="w-full">
        <thead className="border-b border-zinc-800 bg-zinc-900/60">
          <tr>
            <th className={th}>Variant</th>
            {summary.metrics.map((m) => (
              <th key={m} className={`${th} text-right`}>
                {m}
              </th>
            ))}
            <th className={`${th} text-right`}>Avg latency</th>
            <th className={`${th} text-right`}>Total cost</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/70">
          {summary.variants.map((v, i) => {
            const meta = byLabel.get(v);
            const perf = summary.perf[v];
            return (
              <tr key={v}>
                <td className={`${td} py-1`}>
                  <div className="flex items-center gap-1.5">
                    <VariantSwatch index={i} />
                    <span className="font-mono text-xs text-zinc-100">{v}</span>
                  </div>
                  {meta && (
                    <div className="pl-3.5 text-[11px] text-zinc-500">
                      {promptLabel(meta)} · {meta.model.provider}/{meta.model.name}
                    </div>
                  )}
                </td>
                {summary.metrics.map((m) => {
                  const c = summary.cells[v][m];
                  const best = summary.best[m]?.includes(v);
                  return (
                    <td
                      key={m}
                      className={`${td} py-1 text-right ${best ? "bg-emerald-950/50" : ""}`}
                      title={best ? "Best mean in this column" : undefined}
                    >
                      <div className={`font-mono text-sm tabular-nums ${best ? "text-emerald-200" : "text-zinc-200"}`}>
                        {formatMetricValue(c.mean)}
                        {best && <span className="sr-only"> (best)</span>}
                      </div>
                      <div
                        className={`font-mono text-[11px] tabular-nums ${
                          c.pass_rate === null ? "text-zinc-600" : c.pass_rate < 0.5 ? "text-red-300" : c.pass_rate < 1 ? "text-amber-300" : "text-emerald-300/80"
                        }`}
                      >
                        {c.pass_rate === null ? "—" : `${formatPercent(c.pass_rate, 0)} pass`}
                      </div>
                    </td>
                  );
                })}
                <td className={`${td} py-1 text-right`}>
                  <Mono className="text-xs">{perf.latency_mean !== null ? formatDuration(perf.latency_mean) : "—"}</Mono>
                  {perf.errors > 0 && <div className="font-mono text-[11px] text-red-300">{perf.errors} err</div>}
                </td>
                <td className={`${td} py-1 text-right`}>
                  <Mono className="text-xs">{formatCost(perf.cost_total)}</Mono>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Cell({
  cell,
  metrics,
  focusMetric,
  href,
  selected,
  pending,
}: {
  cell: MatrixCell;
  metrics: string[];
  focusMetric: string | null;
  href: string;
  selected: boolean;
  pending: boolean;
}) {
  const r = cell.result;
  const regressed = cell.regressions.length > 0;
  return (
    <td
      className={`relative min-w-[13rem] border-l px-2.5 py-2 align-top ${
        regressed ? "border-l-2 border-l-red-500 bg-red-950/10" : "border-l-zinc-800/70"
      } ${selected ? "bg-zinc-800/50 outline outline-1 -outline-offset-1 outline-emerald-500/70" : ""}`}
    >
      {!r ? (
        <span className="text-xs text-zinc-600">{pending ? "pending…" : "—"}</span>
      ) : (
        <div className="space-y-1.5">
          {regressed && (
            <div className="rounded border border-red-800/70 bg-red-950/50 px-1.5 py-0.5 text-[10px] leading-4 text-red-200">
              <span className="font-medium uppercase tracking-wider">regressed</span>
              <ul className="font-mono">
                {cell.regressions.map((x) => (
                  <li key={x.metric}>{regressionText(x)}</li>
                ))}
              </ul>
            </div>
          )}
          {r.error ? (
            <p className="rounded border border-red-900/70 bg-red-950/40 px-1.5 py-1 font-mono text-[11px] text-red-200">{r.error}</p>
          ) : r.output ? (
            <details className="group">
              <summary className="cursor-pointer list-none text-xs leading-relaxed text-zinc-200 [&::-webkit-details-marker]:hidden">
                <span className="line-clamp-3 group-open:hidden">{r.output}</span>
                <span className="text-[10px] text-zinc-500 group-open:hidden">expand</span>
                <span className="hidden text-[10px] text-zinc-500 group-open:inline">collapse</span>
              </summary>
              <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-200">{r.output}</p>
            </details>
          ) : (
            <span className="text-xs text-zinc-600">(empty output)</span>
          )}
          <div className="flex gap-2 font-mono text-[10px] tabular-nums text-zinc-500">
            <span>{r.latency_ms !== null ? formatDuration(r.latency_ms) : "—"}</span>
            <span>{r.cost_usd !== null ? formatCost(r.cost_usd) : "—"}</span>
            {r.output_tokens !== null && <span>{formatTokens(r.output_tokens)} tok</span>}
          </div>
          <div className="flex flex-wrap gap-1">
            {metrics.map((m) => (
              <MetricChip key={m} name={m} score={r.scores[m]} dim={!!focusMetric && focusMetric !== m} />
            ))}
          </div>
          <Link
            href={href}
            scroll={false}
            replace
            aria-label={`Inspect ${cell.variant}`}
            className="inline-block text-[11px] text-emerald-300 hover:underline"
          >
            Inspect ›
          </Link>
        </div>
      )}
    </td>
  );
}

function Statements({ statements }: { statements: FaithfulnessStatement[] }) {
  if (!statements.length) return <p className="text-xs text-zinc-500">No statements extracted.</p>;
  return (
    <ol className="mt-1.5 space-y-1.5">
      {statements.map((s, i) => (
        <li
          key={i}
          className={`rounded-r border-l-2 px-2 py-1 text-xs leading-relaxed ${
            s.supported ? "border-emerald-600/60 text-zinc-200" : "border-red-500 bg-red-950/30 text-red-200"
          }`}
        >
          <span className="sr-only">{s.supported ? "Supported: " : "Unsupported: "}</span>
          {s.statement}
          {!s.supported && <div className="mt-0.5 text-[11px] text-red-300/90">✗ {s.reason}</div>}
        </li>
      ))}
    </ol>
  );
}

function CellPanel({
  experiment,
  item,
  itemIndex,
  cell,
  variantIndex,
  closeHref,
}: {
  experiment: Experiment;
  item: DatasetItem | null;
  itemIndex: number;
  cell: MatrixCell;
  variantIndex: number;
  closeHref: string;
}) {
  const r = cell.result;
  const sectionTitle = "mb-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-400";
  return (
    <>
      <CloseOnEscape href={closeHref} />
      <Link href={closeHref} scroll={false} replace aria-hidden tabIndex={-1} className="fixed inset-0 z-30 bg-black/40" />
      <aside
        aria-label="Cell detail"
        className="fixed inset-y-0 right-0 z-40 w-[min(600px,100vw)] overflow-y-auto border-l border-zinc-700 bg-zinc-900 shadow-2xl shadow-black/60"
      >
        <header className="sticky top-0 z-10 flex items-start gap-2 border-b border-zinc-800 bg-zinc-900 px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <VariantSwatch index={variantIndex} />
              <h2 className="truncate font-mono text-sm font-semibold text-zinc-50">{cell.variant}</h2>
            </div>
            <p className="mt-0.5 text-xs text-zinc-500">
              Item #{itemIndex + 1} <Mono className="text-zinc-600">{cell.result?.item_id}</Mono>
            </p>
          </div>
          <Link href={closeHref} scroll={false} replace className="rounded px-2 py-0.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">
            Close <span className="text-zinc-600">esc</span>
          </Link>
        </header>

        {cell.regressions.length > 0 && (
          <section className="border-b border-zinc-800 px-4 py-3">
            <h3 className={`${sectionTitle} text-red-300`}>Regressed vs baseline</h3>
            <ul className="space-y-0.5 font-mono text-xs text-red-200">
              {cell.regressions.map((x) => (
                <li key={x.metric}>{regressionText(x)}</li>
              ))}
            </ul>
          </section>
        )}

        {r && (
          <section className="border-b border-zinc-800 px-4 py-3">
            <h3 className={sectionTitle}>Metrics</h3>
            <ul className="space-y-2.5">
              {experiment.metrics.map((spec) => {
                const name = metricKey(spec);
                const s = r.scores[name];
                const statements = Array.isArray(s?.details?.statements)
                  ? (s.details!.statements as FaithfulnessStatement[])
                  : null;
                return (
                  <li key={name}>
                    <div className="flex items-center gap-2">
                      <MetricChip name={name} score={s} />
                      <span className="text-[11px] text-zinc-500">
                        {s?.passed === true ? "pass" : s?.passed === false ? "fail" : "n/a"}
                        {typeof spec.threshold === "number" && ` · threshold ${spec.threshold}`}
                      </span>
                    </div>
                    {s?.reason && <p className="mt-0.5 text-xs text-zinc-300">{s.reason}</p>}
                    {statements && spec.type === "faithfulness" && <Statements statements={statements} />}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        <section className="border-b border-zinc-800 px-4 py-3">
          <h3 className={sectionTitle}>Output</h3>
          {r?.error && (
            <p className="mb-2 rounded border border-red-900/70 bg-red-950/40 px-2 py-1 font-mono text-xs text-red-200">{r.error}</p>
          )}
          {r?.output ? <Payload text={r.output} label="output" /> : <p className="text-xs text-zinc-500">No output.</p>}
          {r && (
            <dl className="mt-2 grid grid-cols-4 gap-2 text-xs">
              {[
                ["Latency", r.latency_ms !== null ? formatDuration(r.latency_ms) : "—"],
                ["Cost", r.cost_usd !== null ? formatCost(r.cost_usd) : "—"],
                ["Tokens in", r.input_tokens !== null ? formatTokens(r.input_tokens) : "—"],
                ["Tokens out", r.output_tokens !== null ? formatTokens(r.output_tokens) : "—"],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[10px] uppercase tracking-wider text-zinc-500">{k}</dt>
                  <dd className="font-mono tabular-nums text-zinc-200">{v}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>

        {item ? (
          <>
            <section className="border-b border-zinc-800 px-4 py-3">
              <h3 className={sectionTitle}>Input</h3>
              <Payload text={JSON.stringify(item.input)} mime="application/json" label="input" />
            </section>
            {item.expected_output && (
              <section className="border-b border-zinc-800 px-4 py-3">
                <h3 className={sectionTitle}>Expected output</h3>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">{item.expected_output}</p>
              </section>
            )}
            {item.context.length > 0 && (
              <section className="px-4 py-3">
                <h3 className={sectionTitle}>
                  Context <span className="font-mono text-zinc-600">{item.context.length}</span>
                </h3>
                <ol className="space-y-1.5">
                  {item.context.map((c, i) => (
                    <li key={i} className="rounded border border-zinc-800 bg-zinc-950/60 p-2 text-xs leading-relaxed text-zinc-300">
                      <Mono className="mr-1.5 text-zinc-500">#{i + 1}</Mono>
                      {c}
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </>
        ) : (
          <p className="px-4 py-3 text-xs text-zinc-500">This item is no longer in the dataset.</p>
        )}
      </aside>
    </>
  );
}

export default async function ExperimentPage({
  params,
  searchParams,
}: {
  params: Promise<{ experimentId: string }>;
  searchParams: Promise<SP>;
}) {
  const { experimentId } = await params;
  const sp = await searchParams;
  let detail: ExperimentDetail;
  try {
    detail = await apiGet<ExperimentDetail>(`/api/v1/experiments/${encodeURIComponent(experimentId)}`);
  } catch (e) {
    if (isNotFound(e)) notFound();
    throw e;
  }
  const { experiment, results, regressions } = detail;
  const [dataset, list] = await Promise.all([
    apiGet<DatasetDetail>(`/api/v1/datasets/${encodeURIComponent(experiment.dataset_id)}`).catch((e) => {
      if (isNotFound(e)) return null;
      throw e;
    }),
    experiment.baseline_experiment_id
      ? apiGet<ListResponse<ExperimentListItem>>("/api/v1/experiments")
      : Promise.resolve(null),
  ]);
  const baseline = list?.items.find((e) => e.id === experiment.baseline_experiment_id) ?? null;
  const active = experiment.status === "queued" || experiment.status === "running";
  // A finished run covers the items that existed when it ran; items added later are not part of it.
  const ran = new Set(results.map((r) => r.item_id));
  const items = (dataset?.items ?? []).filter((it) => active || ran.has(it.id));

  const variants = experiment.variants.map((v) => v.label);
  const metricNames = experiment.metrics.map(metricKey);
  const summary = summarizeMatrix(results, experiment.metrics, variants);
  const regMap = regressionMap(regressions);
  const focusMetric = sp.metric && metricNames.includes(sp.metric) ? sp.metric : null;
  const filters = { onlyFailures: sp.failures === "1", onlyRegressions: sp.regressions === "1", metric: focusMetric };
  const allRows = matrixRows(items, results, variants, {}, regMap);
  const rows = matrixRows(items, results, variants, filters, regMap);
  const total = active ? items.length * variants.length : results.length;

  const base = `/experiments/${encodeURIComponent(experiment.id)}`;
  const href = (patch: Partial<SP>) => {
    const next: SP = {
      failures: sp.failures,
      regressions: sp.regressions,
      metric: focusMetric ?? undefined,
      item: sp.item,
      variant: sp.variant,
      ...patch,
    };
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(next)) if (v) p.set(k, v);
    const s = p.toString();
    return s ? `${base}?${s}` : base;
  };

  const failingRows = matrixRows(items, results, variants, { onlyFailures: true, metric: focusMetric }, regMap).length;
  const regressedRows = matrixRows(items, results, variants, { onlyRegressions: true, metric: focusMetric }, regMap).length;

  const openRowIndex = sp.item ? allRows.findIndex((r) => r.item_id === sp.item) : -1;
  const openVariantIndex = sp.variant ? variants.indexOf(sp.variant) : -1;
  const openRow = openRowIndex >= 0 ? allRows[openRowIndex] : null;
  const openCell = openRow && openVariantIndex >= 0 ? openRow.cells[openVariantIndex] : null;
  const itemIndex = new Map(items.map((it, i) => [it.id, i]));

  const toggle = (on: boolean, label: string, count: number, patch: Partial<SP>) => (
    <Link
      href={href({ ...patch, item: undefined, variant: undefined })}
      scroll={false}
      replace
      role="switch"
      aria-checked={on}
      className={`inline-flex h-7 items-center gap-1.5 rounded border px-2.5 text-xs ${
        on ? "border-red-800/70 bg-red-950/50 text-red-200" : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:text-zinc-50"
      }`}
    >
      <span aria-hidden className={`h-2.5 w-2.5 rounded-sm border ${on ? "border-red-400 bg-red-400" : "border-zinc-500"}`} />
      {label}
      <Mono className="text-zinc-500">{count}</Mono>
    </Link>
  );

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/experiments" className="hover:text-zinc-200">
          Experiments
        </Link>{" "}
        / <Mono>{experiment.id}</Mono>
      </nav>
      <header className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-zinc-800 pb-3">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate text-base font-semibold text-zinc-50">{experiment.name}</h1>
          <ExperimentStatusBadge status={experiment.status} />
          {usesHeuristicJudge(experiment.summary, results) && <HeuristicJudgeBadge />}
          {summaryMeta(experiment.summary)?.partial && <PartialBadge meta={summaryMeta(experiment.summary)!} />}
          {active && <StatusPoller />}
        </div>
        <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <div className="flex items-baseline gap-1.5">
            <dt className="text-zinc-500">Dataset</dt>
            <dd>
              <Link href={`/datasets/${encodeURIComponent(experiment.dataset_id)}`} className="font-mono text-emerald-300 hover:underline">
                {dataset?.dataset.name ?? experiment.dataset_id}
              </Link>
            </dd>
          </div>
          {experiment.baseline_experiment_id && (
            <div className="flex items-baseline gap-1.5">
              <dt className="text-zinc-500">Baseline</dt>
              <dd>
                <Link
                  href={`/experiments/${encodeURIComponent(experiment.baseline_experiment_id)}`}
                  className="text-emerald-300 hover:underline"
                >
                  {baseline?.name ?? experiment.baseline_experiment_id}
                </Link>
              </dd>
            </div>
          )}
          <div className="flex items-baseline gap-1.5">
            <dt className="text-zinc-500">Cells</dt>
            <dd className="font-mono tabular-nums">
              {results.length}/{total}
            </dd>
          </div>
          <div className="flex items-baseline gap-1.5">
            <dt className="text-zinc-500">Created</dt>
            <dd className="font-mono tabular-nums">{formatTimestamp(experiment.created_at)}</dd>
          </div>
          {experiment.finished_at && (
            <div className="flex items-baseline gap-1.5">
              <dt className="text-zinc-500">Finished</dt>
              <dd className="font-mono tabular-nums">
                {formatTimestamp(experiment.finished_at)}
                {experiment.started_at && (
                  <span className="text-zinc-500">
                    {" "}
                    ({formatDuration(Date.parse(experiment.finished_at) - Date.parse(experiment.started_at))})
                  </span>
                )}
              </dd>
            </div>
          )}
          {experiment.baseline_experiment_id && (
            <div className="flex items-baseline gap-1.5">
              <dt className="text-zinc-500">Regressions</dt>
              <dd className={`font-mono tabular-nums ${regressions.length ? "text-red-300" : "text-emerald-300"}`}>
                {regressions.length}
              </dd>
            </div>
          )}
        </dl>
        <Link href={`/experiments/new?dataset=${encodeURIComponent(experiment.dataset_id)}`} className={`${primaryBtn} ml-auto leading-7`}>
          New experiment
        </Link>
      </header>

      {experiment.status === "failed" && experiment.error && (
        <div role="alert" className="mb-4 rounded border border-red-900/70 bg-red-950/40 px-3 py-2 text-xs">
          <span className="font-medium text-red-300">Experiment failed</span>
          <p className="mt-0.5 font-mono text-sm font-semibold text-red-200">{experiment.error}</p>
        </div>
      )}

      {results.length === 0 ? (
        <EmptyState
          title={
            experiment.status === "queued"
              ? "Queued — waiting for a worker"
              : experiment.status === "running"
                ? "Running — first results are on their way"
                : "No results"
          }
        >
          {active
            ? "This page refreshes every 3 seconds."
            : experiment.status === "failed"
              ? "The run stopped before producing any result."
              : "The experiment finished without results (is the dataset empty?)."}
        </EmptyState>
      ) : (
        <>
          <section aria-label="Summary" className="mb-5 grid gap-4 min-[1800px]:grid-cols-[minmax(0,1fr)_600px]">
            <SummaryTable summary={summary} experiment={experiment} />
            <div className="w-full max-w-[600px] rounded-md border border-zinc-800 bg-zinc-900/40 p-3">
              <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wider text-zinc-400">Variant means</h2>
              <VariantChart summary={summary} />
            </div>
          </section>

          <section aria-label="Eval matrix">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h2 className="mr-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">Matrix</h2>
              {toggle(filters.onlyFailures, "Only failures", failingRows, { failures: filters.onlyFailures ? undefined : "1" })}
              {experiment.baseline_experiment_id &&
                toggle(filters.onlyRegressions, "Only regressions", regressedRows, {
                  regressions: filters.onlyRegressions ? undefined : "1",
                })}
              <nav aria-label="Metric" className="inline-flex flex-wrap rounded-md border border-zinc-800 bg-zinc-900 p-0.5">
                {[null, ...metricNames].map((m) => (
                  <Link
                    key={m ?? "all"}
                    href={href({ metric: m ?? undefined, item: undefined, variant: undefined })}
                    scroll={false}
                    replace
                    aria-current={focusMetric === m ? "true" : undefined}
                    className={`rounded px-2 py-0.5 font-mono text-xs ${
                      focusMetric === m ? "bg-zinc-700 text-zinc-50" : "text-zinc-400 hover:text-zinc-100"
                    }`}
                  >
                    {m ?? "all metrics"}
                  </Link>
                ))}
              </nav>
              <span className="ml-auto font-mono text-xs text-zinc-500">
                {rows.length}/{allRows.length} items
              </span>
            </div>

            {rows.length === 0 ? (
              <EmptyState title="No items match these filters">
                <Link href={href({ failures: undefined, regressions: undefined, metric: undefined })} className="text-emerald-300 hover:underline">
                  Clear filters
                </Link>
              </EmptyState>
            ) : (
              <div className="overflow-x-auto rounded-md border border-zinc-800">
                <table className="w-full min-w-[900px] table-fixed">
                  <colgroup>
                    <col className="w-64" />
                    {variants.map((v) => (
                      <col key={v} />
                    ))}
                  </colgroup>
                  <thead className="border-b border-zinc-800 bg-zinc-900/60">
                    <tr>
                      <th className={`${th} sticky left-0 z-10 bg-zinc-900`}>Item</th>
                      {variants.map((v, i) => (
                        <th key={v} className={`${th} border-l border-zinc-800/70 normal-case tracking-normal`}>
                          <span className="flex items-center gap-1.5">
                            <VariantSwatch index={i} />
                            <span className="truncate font-mono text-xs text-zinc-200">{v}</span>
                          </span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {rows.map((row) => {
                      const idx = itemIndex.get(row.item_id);
                      return (
                        <tr key={row.item_id}>
                          <th scope="row" className="sticky left-0 z-10 bg-zinc-950 px-3 py-2 text-left align-top font-normal">
                            <Mono className="text-[11px] text-zinc-500">#{idx !== undefined ? idx + 1 : "–"}</Mono>
                            {row.item ? (
                              <>
                                <p className="line-clamp-3 whitespace-pre-wrap break-words text-xs text-zinc-100" title={inputPreview(row.item.input, 2000)}>
                                  {inputPreview(row.item.input, 240).replace(/^question: /, "")}
                                </p>
                                {row.item.expected_output && (
                                  <p className="mt-1 line-clamp-2 text-[11px] text-zinc-500" title={row.item.expected_output}>
                                    <span className="text-zinc-600">expected </span>
                                    {row.item.expected_output}
                                  </p>
                                )}
                                <Mono className="mt-1 block text-[10px] text-zinc-600">{row.item.context.length} ctx</Mono>
                              </>
                            ) : (
                              <p className="text-xs text-zinc-600">(item deleted)</p>
                            )}
                          </th>
                          {row.cells.map((cell) => (
                            <Cell
                              key={cell.variant}
                              cell={cell}
                              metrics={metricNames}
                              focusMetric={focusMetric}
                              href={href({ item: row.item_id, variant: cell.variant })}
                              selected={sp.item === row.item_id && sp.variant === cell.variant}
                              pending={active}
                            />
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {openRow && openCell?.result && (
        <CellPanel
          experiment={experiment}
          item={openRow.item}
          itemIndex={itemIndex.get(openRow.item_id) ?? openRowIndex}
          cell={openCell}
          variantIndex={openVariantIndex}
          closeHref={href({ item: undefined, variant: undefined })}
        />
      )}
    </div>
  );
}
