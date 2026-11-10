import Link from "next/link";
import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { promptPath, templateToText } from "@/lib/prompt-template";
import type { PromptVersion, PromptVersionList } from "@/lib/types";
import { Mono, PageHeader, th } from "@/components/ui";
import { DiffSummary, SideBySideDiff } from "@/components/prompts/diff-view";
import { LabelMover } from "@/components/prompts/label-mover";
import { Playground } from "@/components/prompts/playground";
import { LabelChip, NewLink, TemplateView, VariableChips, promptHref } from "@/components/prompts/ui";
import { diffText } from "@/lib/line-diff";
import { field, secondaryBtn } from "@/components/evals/ui";

export const dynamic = "force-dynamic";

const json = (v: unknown) => JSON.stringify(v, null, 2);

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">{title}</h3>
      {children}
    </section>
  );
}

export default async function PromptPage({
  params,
  searchParams,
}: {
  params: Promise<{ name: string[] }>;
  searchParams: Promise<{ v?: string; base?: string }>;
}) {
  const { name: segments } = await params;
  const sp = await searchParams;
  const name = segments.map(decodeURIComponent).join("/");
  let versions: PromptVersion[];
  try {
    versions = (await apiGet<PromptVersionList>(`/api/v1/prompts/${promptPath(name)}/versions`)).items;
  } catch (e) {
    if (isNotFound(e)) notFound();
    throw e;
  }
  versions = [...versions].sort((a, b) => b.version - a.version);
  const byNumber = (n: string | undefined) => versions.find((v) => String(v.version) === n);
  const production = versions.find((v) => v.labels.includes("production"));
  const selected = byNumber(sp.v) ?? production ?? versions[0];
  const baseV = byNumber(sp.base);
  const labelVersions: Record<string, number> = {};
  for (const v of versions) for (const l of v.labels) labelVersions[l] = v.version;
  const prev = versions.find((v) => v.version === selected.version - 1);

  const diffParts = baseV
    ? [
        { title: "Template", a: templateToText(baseV.template), b: templateToText(selected.template) },
        { title: "Config", a: json(baseV.config), b: json(selected.config) },
        { title: "Response schema", a: baseV.response_schema == null ? "" : json(baseV.response_schema), b: selected.response_schema == null ? "" : json(selected.response_schema) },
      ].map((p) => ({ ...p, ops: diffText(p.a, p.b) }))
    : [];

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/prompts" className="hover:text-zinc-200">
          Prompts
        </Link>{" "}
        / <Mono>{name}</Mono>
      </nav>
      <PageHeader
        title={<span className="font-mono">{name}</span>}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono tabular-nums">{versions.length} {versions.length === 1 ? "version" : "versions"}</span>
            <span className="flex flex-wrap gap-1">
              {Object.entries(labelVersions).map(([l, n]) => (
                <LabelChip key={l} label={l} version={n} />
              ))}
            </span>
          </span>
        }
        actions={<NewLink href={`/prompts/new?from=${encodeURIComponent(name)}&version=${selected.version}`}>New version from v{selected.version}</NewLink>}
      />

      <div className="grid items-start gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
        <nav aria-label="Versions" className="overflow-hidden rounded-md border border-zinc-800">
          <div className={`${th} border-b border-zinc-800 bg-zinc-900/60`}>Versions</div>
          <ol className="divide-y divide-zinc-800/70">
            {versions.map((v) => {
              const active = v.version === selected.version;
              return (
                <li key={v.version} className={active ? "bg-zinc-800/60" : "hover:bg-zinc-900/70"}>
                  <Link
                    href={promptHref(name, { v: v.version, base: baseV?.version })}
                    aria-current={active ? "page" : undefined}
                    className="block px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Mono className="font-medium text-zinc-100">v{v.version}</Mono>
                      {v.labels.map((l) => (
                        <LabelChip key={l} label={l} />
                      ))}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-zinc-400" title={v.commit_message ?? undefined}>
                      {v.commit_message ?? <span className="text-zinc-600">no commit message</span>}
                    </p>
                    <Mono className="text-[11px] text-zinc-600">{formatTimestamp(v.created_at)}</Mono>
                  </Link>
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="min-w-0 space-y-4">
          <section aria-label={`Version ${selected.version}`} className="space-y-3 rounded-md border border-zinc-800 p-4">
            <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <h2 className="font-mono text-sm font-semibold text-zinc-50">v{selected.version}</h2>
              <span className="rounded border border-zinc-700 px-1.5 font-mono text-[11px] leading-4 text-zinc-400">{selected.type}</span>
              <Mono className="text-xs text-zinc-500">{formatTimestamp(selected.created_at)}</Mono>
              {selected.commit_message && <span className="text-zinc-300">{selected.commit_message}</span>}
            </header>
            <LabelMover key={selected.version} name={name} version={selected.version} labelVersions={labelVersions} />
            <Block title="Template">
              <TemplateView type={selected.type} template={selected.template} />
            </Block>
            <div className="grid gap-4 md:grid-cols-2">
              <Block title="Variables">
                <VariableChips variables={selected.variables} />
              </Block>
              <Block title="Config">
                <pre className="overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-xs text-zinc-300">{json(selected.config)}</pre>
              </Block>
            </div>
            {selected.response_schema != null && (
              <Block title="Response schema">
                <pre className="max-h-64 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-xs text-emerald-100/90">{json(selected.response_schema)}</pre>
              </Block>
            )}
          </section>

          <section aria-label="Compare versions" className="space-y-2 rounded-md border border-zinc-800 p-4">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <h2 className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">Compare versions</h2>
              {versions.length > 1 && (
              <form method="get" aria-label="Choose versions to compare" className="flex flex-wrap items-end gap-2">
                <input type="hidden" name="v" value={selected.version} />
                <label className="flex items-center gap-1.5 text-xs text-zinc-500">
                  From
                  <select name="base" defaultValue={baseV?.version ?? prev?.version ?? ""} className={`${field} font-mono`}>
                    {versions.filter((v) => v.version !== selected.version).map((v) => (
                      <option key={v.version} value={v.version}>
                        v{v.version}
                      </option>
                    ))}
                  </select>
                </label>
                <span className="pb-1 text-xs text-zinc-500">
                  to <span className="font-mono text-zinc-200">v{selected.version}</span>
                </span>
                <button type="submit" className={secondaryBtn}>
                  Compare
                </button>
                {baseV && (
                  <Link href={promptHref(name, { v: selected.version })} className="pb-1 text-xs text-zinc-400 hover:text-zinc-100">
                    Clear
                  </Link>
                )}
              </form>
              )}
            </div>
            {!baseV ? (
              <p className="text-zinc-500">
                {versions.length < 2 ? "Only one version so far." : "Pick an earlier version to see a line diff against the selected one."}
              </p>
            ) : (
              <div className="space-y-4">
                {diffParts
                  .filter((p, i) => i === 0 || p.ops.some((o) => o.type !== "equal"))
                  .map((p) => (
                    <div key={p.title} className="space-y-1">
                      <h3 className="flex items-baseline gap-2 text-xs font-medium text-zinc-300">
                        {p.title} <DiffSummary ops={p.ops} />
                      </h3>
                      <SideBySideDiff before={p.a} after={p.b} beforeLabel={`v${baseV.version}`} afterLabel={`v${selected.version}`} />
                    </div>
                  ))}
              </div>
            )}
          </section>

          <Playground key={selected.version} type={selected.type} template={selected.template} />
        </div>
      </div>
    </div>
  );
}
