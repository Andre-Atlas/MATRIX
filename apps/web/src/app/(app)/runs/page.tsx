import Form from "next/form";
import Link from "next/link";
import { apiGet } from "@/lib/api";
import { runFilters, runListParams, runsHref } from "@/lib/runs/query";
import { RUN_STATUSES, type RunPage } from "@/lib/runs/types";
import { EmptyState, PageHeader, Segmented } from "@/components/ui";
import { field, secondaryBtn } from "@/components/evals/ui";
import { StatusPoller } from "@/components/evals/status-poller";
import { RunsTable } from "@/components/runs/runs-table";

export const dynamic = "force-dynamic";

type SP = Promise<Record<string, string | string[] | undefined>>;

export default async function RunsPage({ searchParams }: { searchParams: SP }) {
  const f = runFilters(await searchParams);
  const page = await apiGet<RunPage>("/api/v1/runs", runListParams(f));
  const running = page.items.some((r) => r.status === "running");
  const filtered = Boolean(f.status || f.group || f.tag || f.q);
  const groups = [...new Set(page.items.map((r) => r.group).filter((g): g is string => !!g))].sort();
  const tags = [...new Set(page.items.flatMap((r) => r.tags))].sort();
  const dir = (asc: string, desc: string) => (f.sort === asc ? "ascending" : f.sort === desc ? "descending" : "none");
  const sortHeaders = {
    name: runsHref(f, { sort: f.sort === "name" ? "-name" : "name" }),
    started: runsHref(f, { sort: f.sort === "-started_at" ? "started_at" : "-started_at" }),
    nameDir: dir("name", "-name"),
    startedDir: dir("started_at", "-started_at"),
  };

  return (
    <div>
      <PageHeader
        title="Runs"
        subtitle="Training runs: parameters, metrics, system telemetry and evaluation reports"
        actions={running ? <StatusPoller ms={5000} /> : undefined}
      />
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Segmented
          label="Status"
          options={[
            { href: runsHref(f, { status: null }), label: "all", active: !f.status },
            ...RUN_STATUSES.map((s) => ({ href: runsHref(f, { status: s }), label: s, active: f.status === s })),
          ]}
        />
        {/* next/form: client-side navigation to /runs?… without a full reload. */}
        <Form action="/runs" className="flex flex-wrap items-center gap-2" role="search" aria-label="Filter runs">
          {f.status && <input type="hidden" name="status" value={f.status} />}
          {f.sort !== "-started_at" && <input type="hidden" name="sort" value={f.sort} />}
          <input name="q" defaultValue={f.q ?? ""} placeholder="Search name, group, tag" aria-label="Search" className={`${field} w-56`} />
          <input name="group" defaultValue={f.group ?? ""} placeholder="Group" aria-label="Group" list="run-groups" className={`${field} w-44`} />
          <datalist id="run-groups">{groups.map((g) => <option key={g} value={g} />)}</datalist>
          <input name="tag" defaultValue={f.tag ?? ""} placeholder="Tag" aria-label="Tag" list="run-tags" className={`${field} w-32`} />
          <datalist id="run-tags">{tags.map((t) => <option key={t} value={t} />)}</datalist>
          <button type="submit" className={secondaryBtn}>
            Apply
          </button>
          {filtered && (
            <Link href="/runs" className="text-xs text-zinc-400 hover:text-zinc-100">
              Clear filters
            </Link>
          )}
        </Form>
      </div>

      {page.items.length === 0 ? (
        filtered ? (
          <EmptyState title="No runs match these filters">
            <Link href="/runs" className="text-emerald-300 hover:underline">
              Clear filters
            </Link>
          </EmptyState>
        ) : (
          <EmptyState title="No runs yet">
            Start one from Python with <code className="font-mono text-zinc-300">matrix_sdk.start_run()</code> and it shows up here with
            its metrics and telemetry.
          </EmptyState>
        )
      ) : (
        <>
          <RunsTable key={JSON.stringify(f)} runs={page.items} now={Date.now()} sortHeaders={sortHeaders} />
          <nav aria-label="Pagination" className="mt-3 flex items-center justify-end gap-3 text-xs">
            {f.cursor && (
              <Link href={runsHref(f, {})} className="text-zinc-400 hover:text-zinc-100">
                ← First page
              </Link>
            )}
            {page.next_cursor ? (
              <Link href={runsHref(f, { cursor: page.next_cursor })} className="text-zinc-300 hover:text-emerald-300">
                Next page →
              </Link>
            ) : (
              <span className="text-zinc-600">End of list</span>
            )}
          </nav>
        </>
      )}
    </div>
  );
}
