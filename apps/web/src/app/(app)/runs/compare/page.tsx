import Link from "next/link";
import { apiGet, isNotFound } from "@/lib/api";
import { parseIds } from "@/lib/runs/query";
import { isSystemKey } from "@/lib/runs/keys";
import type { MetricKeyStat, Run } from "@/lib/runs/types";
import { EmptyState, PageHeader } from "@/components/ui";
import { CompareView } from "@/components/runs/compare-view";

export const dynamic = "force-dynamic";

type SP = Promise<Record<string, string | string[] | undefined>>;

async function orNull<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch (e) {
    if (isNotFound(e)) return null;
    throw e;
  }
}

export default async function ComparePage({ searchParams }: { searchParams: SP }) {
  const { ids, invalid, truncated } = parseIds((await searchParams).ids);
  const found = await Promise.all(
    ids.map(async (id) => {
      const base = `/api/v1/runs/${encodeURIComponent(id)}`;
      const [run, keys] = await Promise.all([orNull(apiGet<Run>(base)), orNull(apiGet<{ keys: MetricKeyStat[] }>(`${base}/metric-keys`))]);
      return run ? { run, keys: keys?.keys ?? [] } : null;
    }),
  );
  const runs = found.filter((x): x is { run: Run; keys: MetricKeyStat[] } => x !== null);
  const missing = ids.filter((id) => !runs.some((r) => r.run.id === id));
  // Keys shared by most runs first.
  const count = new Map<string, number>();
  for (const r of runs) for (const k of r.keys) if (!isSystemKey(k.key)) count.set(k.key, (count.get(k.key) ?? 0) + 1);
  const keys = [...count.entries()].sort(([a, x], [b, y]) => y - x || a.localeCompare(b)).map(([k]) => k);

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500">
        <Link href="/runs" className="hover:text-zinc-200">Runs</Link>
      </nav>
      <PageHeader title="Compare runs" subtitle={`${runs.length} runs · overlaid curves, parameter diff and final metrics`} />
      {(missing.length > 0 || invalid.length > 0 || truncated) && (
        <p role="status" className="mb-3 rounded border border-amber-800/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
          {missing.length > 0 && <>Not found: <span className="font-mono">{missing.join(", ")}</span>. </>}
          {invalid.length > 0 && <>Ignored invalid ids: <span className="font-mono">{invalid.join(", ")}</span>. </>}
          {truncated && <>Only the first 20 runs are compared.</>}
        </p>
      )}
      {runs.length < 2 ? (
        <EmptyState title="Pick at least two runs">
          Select 2–20 runs in the{" "}
          <Link href="/runs" className="text-emerald-300 hover:underline">runs table</Link> and press Compare.
        </EmptyState>
      ) : (
        <CompareView key={runs.map((r) => r.run.id).join(",")} runs={runs.map((r) => r.run)} keys={keys} />
      )}
    </div>
  );
}
