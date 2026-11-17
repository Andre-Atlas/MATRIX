import { BenchmarksClient } from "@/components/benchmarks/benchmarks-client";
import { apiGet } from "@/lib/api";
import type {
  BenchmarkCatalogResponse,
  BenchmarkCatalogTask,
  BenchmarkRun,
  ListResponse,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function BenchmarksPage() {
  let initialRuns: BenchmarkRun[] = [];
  let initialCatalog: BenchmarkCatalogTask[] = [];
  let initialError: string | null = null;

  try {
    const [runsResp, catalogResp] = await Promise.all([
      apiGet<ListResponse<BenchmarkRun>>("/api/v1/benchmarks/runs"),
      apiGet<BenchmarkCatalogResponse>("/api/v1/benchmarks/catalog").catch(() => ({
        tasks: [] as BenchmarkCatalogTask[],
      })),
    ]);

    initialRuns = runsResp.items ?? [];
    initialCatalog = catalogResp.tasks ?? [];
  } catch (err) {
    initialError =
      err instanceof Error ? err.message : "Could not load benchmark runs.";
  }

  return (
    <BenchmarksClient
      initialRuns={initialRuns}
      initialCatalog={initialCatalog}
      initialError={initialError}
    />
  );
}
