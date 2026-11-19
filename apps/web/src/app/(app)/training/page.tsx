import { apiGet } from "@/lib/api";
import type {
  ListResponse,
  MLOpsSweep,
  SweepDetail,
  TrainingMetricSeries,
  TrainingRun,
} from "@/lib/types";
import { TrainingStudioClient } from "@/components/training/training-studio-client";

export const dynamic = "force-dynamic";

type TrainingPageProps = {
  searchParams: Promise<{ run?: string; tab?: string }>;
};

export default async function TrainingPage({ searchParams }: TrainingPageProps) {
  const sp = await searchParams;
  const initialRunId = typeof sp.run === "string" ? sp.run : undefined;
  const initialTab =
    sp.tab === "sweeps" || sp.tab === "classic" || sp.tab === "runs" ? sp.tab : "runs";

  let runs: TrainingRun[] = [];
  let sweeps: MLOpsSweep[] = [];
  let initialError: string | null = null;

  try {
    const [runsResp, sweepsResp] = await Promise.all([
      apiGet<ListResponse<TrainingRun>>("/api/v1/mlops/runs", { limit: 100 }),
      apiGet<ListResponse<MLOpsSweep>>("/api/v1/mlops/sweeps"),
    ]);
    runs = Array.isArray(runsResp?.items) ? runsResp.items : [];
    sweeps = Array.isArray(sweepsResp?.items) ? sweepsResp.items : [];
  } catch (err) {
    initialError = err instanceof Error ? err.message : "Failed to load training runs and sweeps.";
  }

  // Pre-fetch metric series for the targeted run (if any) + first 2 runs
  const candidateRunIds: string[] = [];
  if (initialRunId && runs.some((r) => r.id === initialRunId)) {
    candidateRunIds.push(initialRunId);
  }
  for (const r of runs) {
    if (candidateRunIds.length >= 3) break;
    if (!candidateRunIds.includes(r.id)) {
      candidateRunIds.push(r.id);
    }
  }

  const initialSeriesByRun: Record<string, TrainingMetricSeries["series"]> = {};
  if (candidateRunIds.length > 0) {
    const seriesResults = await Promise.allSettled(
      candidateRunIds.map((id) =>
        apiGet<TrainingMetricSeries>(`/api/v1/mlops/runs/${encodeURIComponent(id)}/metrics`),
      ),
    );
    for (const res of seriesResults) {
      if (res.status === "fulfilled" && res.value?.run_id) {
        initialSeriesByRun[res.value.run_id] = res.value.series ?? {};
      }
    }
  }

  let initialSweepDetail: SweepDetail | null = null;
  if (sweeps.length > 0) {
    try {
      initialSweepDetail = await apiGet<SweepDetail>(
        `/api/v1/mlops/sweeps/${encodeURIComponent(sweeps[0].id)}`,
      );
    } catch {
      initialSweepDetail = null;
    }
  }

  return (
    <TrainingStudioClient
      initialRuns={runs}
      initialSweeps={sweeps}
      initialSeriesByRun={initialSeriesByRun}
      initialSweepDetail={initialSweepDetail}
      initialRunId={initialRunId}
      initialTab={initialTab}
      initialError={initialError}
    />
  );
}
