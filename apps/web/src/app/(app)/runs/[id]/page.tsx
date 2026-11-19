import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import { flattenParams, paramText } from "@/lib/runs/params";
import { formatBytes, formatMetric, runDuration, shortCommit } from "@/lib/runs/format";
import { isSystemKey } from "@/lib/runs/keys";
import { formatTimestamp } from "@/lib/format";
import type { MetricKeyStat, Report, Run, RunArtifact } from "@/lib/runs/types";
import { EmptyState, Mono, PageHeader, td, th } from "@/components/ui";
import { StatusPoller } from "@/components/evals/status-poller";
import { ChartsTab } from "@/components/runs/charts-tab";
import { SystemTab } from "@/components/runs/system-tab";
import { ReportsView } from "@/components/runs/reports-view";
import { PackageList, RunActions } from "@/components/runs/overview-parts";
import { FutureLink, RunStatusBadge, TagChips, panel } from "@/components/runs/ui";

export const dynamic = "force-dynamic";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "charts", label: "Charts" },
  { id: "system", label: "System" },
  { id: "artifacts", label: "Artifacts" },
  { id: "reports", label: "Reports" },
] as const;
type Tab = (typeof TABS)[number]["id"];

type Props = { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

async function load<T>(path: string): Promise<T> {
  try {
    return await apiGet<T>(path);
  } catch (e) {
    if (isNotFound(e)) notFound();
    throw e;
  }
}

export default async function RunPage({ params, searchParams }: Props) {
  const { id } = await params;
  const sp = await searchParams;
  const tab: Tab = (TABS.find((t) => t.id === sp.tab)?.id ?? "overview") as Tab;
  const base = `/api/v1/runs/${encodeURIComponent(id)}`;
  const [run, keys, artifacts, reports] = await Promise.all([
    load<Run>(base),
    tab === "charts" || tab === "system" || tab === "overview" ? load<{ keys: MetricKeyStat[] }>(`${base}/metric-keys`) : null,
    tab === "artifacts" || tab === "overview" ? load<{ items: RunArtifact[] }>(`${base}/artifacts`) : null,
    tab === "reports" ? load<{ items: Report[] }>(`${base}/reports`) : null,
  ]);
  const running = run.status === "running";
  // Live data: the poller refreshes this page; the client charts refetch when this key changes.
  const refreshKey = `${run.status}|${run.heartbeat_at ?? ""}|${Object.values(run.summary).join(",")}`;
  const href = (t: Tab) => `/runs/${encodeURIComponent(id)}${t === "overview" ? "" : `?tab=${t}`}`;

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/runs" className="hover:text-zinc-200">Runs</Link>
        {run.group && (
          <>
            {" / "}
            <Link href={`/runs?group=${encodeURIComponent(run.group)}`} className="hover:text-zinc-200">{run.group}</Link>
          </>
        )}
      </nav>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {run.name} <RunStatusBadge status={run.status} />
          </span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-3 text-xs">
            <Mono>{run.id}</Mono>
            <span>started {formatTimestamp(run.started_at)} UTC</span>
            <span>duration <Mono className="text-zinc-300">{runDuration(run.started_at, run.ended_at)}</Mono></span>
            <TagChips tags={run.tags} max={6} />
          </span>
        }
        actions={
          <div className="flex items-center gap-3">
            {running && <StatusPoller ms={5000} />}
            <RunActions runId={run.id} running={running} />
          </div>
        }
      />
      <nav aria-label="Run sections" className="mb-4 flex gap-1 border-b border-zinc-800">
        {TABS.map((t) => (
          <Link
            key={t.id}
            href={href(t.id)}
            scroll={false}
            aria-current={tab === t.id ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-1.5 ${tab === t.id ? "border-emerald-400 text-zinc-50" : "border-transparent text-zinc-400 hover:text-zinc-100"}`}
          >
            {t.label}
          </Link>
        ))}
      </nav>

      {tab === "overview" && <Overview run={run} keys={keys!.keys} artifacts={artifacts!.items} />}
      {tab === "charts" && <ChartsTab key={run.id} runId={run.id} keys={keys!.keys.filter((k) => !isSystemKey(k.key))} refreshKey={refreshKey} />}
      {tab === "system" && <SystemTab key={run.id} runId={run.id} keys={keys!.keys} refreshKey={refreshKey} />}
      {tab === "artifacts" && <Artifacts items={artifacts!.items} />}
      {tab === "reports" && <ReportsView key={run.id} reports={reports!.items} />}
    </div>
  );
}

function Section({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`${panel} p-3 ${className}`} aria-label={title}>
      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{title}</h2>
      {children}
    </section>
  );
}

function Overview({ run, keys, artifacts }: { run: Run; keys: MetricKeyStat[]; artifacts: RunArtifact[] }) {
  const params = Object.entries(flattenParams(run.params));
  const metrics = keys.filter((k) => !isSystemKey(k.key));
  const model = artifacts.find((a) => a.kind === "model");
  return (
    <div className="space-y-3">
      {run.status === "failed" && run.error && (
        <div role="alert" className="rounded-md border border-red-900/60 bg-red-950/30 p-3">
          <h2 className="mb-1 text-sm font-medium text-red-300">Run failed</h2>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs text-red-200/90">{run.error}</pre>
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Section title={`Parameters · ${params.length} (immutable)`} className="xl:col-span-2">
          {params.length ? (
            <dl className="grid max-h-80 grid-cols-1 gap-x-6 overflow-auto font-mono text-xs md:grid-cols-2">
              {params.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3 border-b border-zinc-800/60 py-1">
                  <dt className="truncate text-zinc-400" title={k}>{k}</dt>
                  <dd className="truncate text-right text-zinc-100" title={paramText(v)}>{typeof v === "number" ? formatMetric(v) : paramText(v)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-zinc-500">No parameters logged.</p>
          )}
        </Section>
        <div className="space-y-3">
          <Section title="Git">
            {run.git ? (
              <dl className="space-y-1 text-xs">
                <div className="flex justify-between gap-2"><dt className="text-zinc-500">commit</dt><dd className="font-mono text-zinc-200" title={run.git.commit ?? ""}>{shortCommit(run.git.commit)}</dd></div>
                <div className="flex justify-between gap-2"><dt className="text-zinc-500">branch</dt><dd className="truncate font-mono text-zinc-200">{run.git.branch ?? "—"}</dd></div>
                <div className="flex justify-between gap-2">
                  <dt className="text-zinc-500">working tree</dt>
                  <dd>{run.git.dirty === null ? "—" : run.git.dirty ? <span className="rounded border border-amber-800/60 bg-amber-950/40 px-1.5 text-[11px] text-amber-300">dirty</span> : <span className="text-zinc-300">clean</span>}</dd>
                </div>
                {run.git.remote && <div className="truncate font-mono text-[11px] text-zinc-500" title={run.git.remote}>{run.git.remote}</div>}
              </dl>
            ) : (
              <p className="text-xs text-zinc-500">Not captured.</p>
            )}
          </Section>
          <Section title="Linked">
            <div className="flex flex-col gap-1 text-xs">
              {run.dataset_version_id ? <FutureLink label="dataset" value={run.dataset_version_id} /> : <span className="text-zinc-600">no dataset version</span>}
              {run.sweep_id && <FutureLink label="sweep" value={run.sweep_id} />}
              {run.parent_run_id && (
                <span className="inline-flex gap-1.5"><span className="text-zinc-500">parent</span><Link href={`/runs/${encodeURIComponent(run.parent_run_id)}`} className="font-mono text-emerald-300 hover:underline">{run.parent_run_id}</Link></span>
              )}
              {model && <FutureLink label="model" value={model.name} />}
            </div>
          </Section>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Section title={`Metrics summary · ${metrics.length}`} className="xl:col-span-2">
          {metrics.length ? (
            <div className="max-h-80 overflow-auto">
              <table className="w-full text-xs">
                <thead><tr>{["key", "last", "min", "max", "last step", "points"].map((c) => <th key={c} scope="col" className={th}>{c}</th>)}</tr></thead>
                <tbody className="divide-y divide-zinc-800/60 font-mono tabular-nums">
                  {metrics.map((k) => (
                    <tr key={k.key}>
                      <td className={`${td} text-zinc-300`}>{k.key}</td>
                      <td className={`${td} text-zinc-100`}>{formatMetric(k.last_value)}</td>
                      <td className={`${td} text-zinc-400`}>{formatMetric(k.min)}</td>
                      <td className={`${td} text-zinc-400`}>{formatMetric(k.max)}</td>
                      <td className={`${td} text-zinc-400`}>{k.last_step}</td>
                      <td className={`${td} text-zinc-500`}>{k.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-zinc-500">No metrics logged yet.</p>
          )}
        </Section>
        <Section title="Environment">
          {run.env ? (
            <div className="space-y-2 text-xs">
              <dl className="space-y-1">
                <div className="flex justify-between gap-2"><dt className="text-zinc-500">python</dt><dd className="font-mono text-zinc-200">{run.env.python ?? "—"}</dd></div>
                <div className="flex justify-between gap-2"><dt className="text-zinc-500">host</dt><dd className="font-mono text-zinc-200">{run.env.hostname ?? "—"}</dd></div>
                <div className="truncate font-mono text-[11px] text-zinc-500" title={run.env.platform ?? ""}>{run.env.platform}</div>
              </dl>
              <PackageList packages={run.env.packages} />
            </div>
          ) : (
            <p className="text-xs text-zinc-500">Not captured.</p>
          )}
        </Section>
      </div>
    </div>
  );
}

function Artifacts({ items }: { items: RunArtifact[] }) {
  if (!items.length) return <EmptyState title="No artifacts">Attach files with <code className="font-mono text-zinc-300">run.log_artifact(path)</code>.</EmptyState>;
  return (
    <div className="overflow-x-auto rounded-md border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="border-b border-zinc-800 bg-zinc-900/60">
          <tr>{["Name", "Kind", "Type", "Size", "SHA-256", "Created (UTC)", ""].map((c, i) => <th key={i} scope="col" className={th}>{c || <span className="sr-only">Download</span>}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/70">
          {items.map((a) => (
            <tr key={a.artifact.id}>
              <td className={`${td} max-w-[22rem] truncate font-mono text-xs text-zinc-100`} title={a.name}>{a.name}</td>
              <td className={td}><span className="rounded border border-zinc-700 px-1.5 text-[11px] text-zinc-300">{a.kind}</span></td>
              <td className={`${td} font-mono text-xs text-zinc-400`}>{a.artifact.media_type}</td>
              <td className={`${td} font-mono text-xs tabular-nums text-zinc-300`}>{formatBytes(a.artifact.size)}</td>
              <td className={`${td} font-mono text-xs text-zinc-500`} title={a.artifact.sha256}>{a.artifact.sha256.slice(0, 12)}…</td>
              <td className={`${td} font-mono text-xs text-zinc-400`}>{formatTimestamp(a.artifact.created_at)}</td>
              <td className={td}>
                <a href={`/api/proxy/artifacts/${encodeURIComponent(a.artifact.id)}/download`} download className="text-xs text-emerald-300 hover:underline" aria-label={`Download ${a.name}`}>
                  Download
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
