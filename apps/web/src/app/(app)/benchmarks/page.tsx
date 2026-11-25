import { unstable_rethrow } from "next/navigation";
import { BenchmarksClient } from "@/components/benchmarks/benchmarks-client";
import { apiGet } from "@/lib/api";
import type {
  BenchmarkCatalogResponse,
  BenchmarkCatalogTask,
  BenchmarkRun,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function BenchmarksPage() {
  let initialRuns: BenchmarkRun[] = [];
  let initialCatalog: BenchmarkCatalogTask[] = [];
  let initialError: string | null = null;

  try {
    const [runsResp, catalogResp] = await Promise.all([
      apiGet<{ runs?: BenchmarkRun[]; items?: BenchmarkRun[] }>(
        "/api/v1/benchmarks/runs",
      ),
      apiGet<BenchmarkCatalogResponse>("/api/v1/benchmarks/catalog").catch((err) => {
        unstable_rethrow(err);
        return {
          tasks: [] as BenchmarkCatalogTask[],
        };
      }),
    ]);

    initialRuns = runsResp.runs ?? runsResp.items ?? [];
    initialCatalog = catalogResp.tasks ?? [];
  } catch (err) {
    unstable_rethrow(err);
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
