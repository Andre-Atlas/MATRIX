import { apiGet } from "@/lib/api";
import {
  canonicalRunMetricKeys,
  canonicalRunToTrainingRun,
  canonicalSeriesToTrainingSeries,
  canonicalSweepDetailToSweepDetail,
  canonicalSweepToMLOpsSweep,
} from "@/lib/training-canonical";
import type { MetricKeyStat, Report, Run, SeriesResponse } from "@/lib/runs/types";
import type {
  CanonicalSweep,
  CanonicalSweepDetail,
  ListResponse,
  MLOpsSweep,
  SweepDetail,
  TrainingMetricPoint,
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

  let rawRuns: Run[] = [];
  let sweeps: MLOpsSweep[] = [];
  let initialError: string | null = null;

  try {
    const [runsResp, sweepsResp] = await Promise.all([
      apiGet<ListResponse<Run>>("/api/v1/runs", { limit: 100 }),
      apiGet<ListResponse<CanonicalSweep | MLOpsSweep>>("/api/v1/sweeps"),
    ]);
    rawRuns = Array.isArray(runsResp?.items) ? runsResp.items : [];
    const rawSweeps = Array.isArray(sweepsResp?.items) ? sweepsResp.items : [];
    sweeps = rawSweeps.map((s) => canonicalSweepToMLOpsSweep(s));
  } catch (err) {
    initialError = err instanceof Error ? err.message : "Failed to load training runs and sweeps.";
  }

  // Pre-fetch reports for classic ML evaluation across initial runs
  const reportsByRun: Record<string, Report[]> = {};
  if (rawRuns.length > 0) {
    const reportSlice = rawRuns.slice(0, 12);
    const reportResults = await Promise.allSettled(
      reportSlice.map((r) =>
        apiGet<{ items: Report[] }>(`/api/v1/runs/${encodeURIComponent(r.id)}/reports`),
      ),
    );
    reportResults.forEach((res, idx) => {
      if (res.status === "fulfilled" && Array.isArray(res.value?.items)) {
        reportsByRun[reportSlice[idx].id] = res.value.items;
      }
    });
  }

  const runs: TrainingRun[] = rawRuns.map((r) =>
    canonicalRunToTrainingRun(r, reportsByRun[r.id]),
  );

  // Pre-fetch metric series for the targeted run (if any) + first 2 runs
  const candidateRuns: TrainingRun[] = [];
  if (initialRunId) {
    const target = runs.find((r) => r.id === initialRunId);
    if (target) candidateRuns.push(target);
  }
  for (const r of runs) {
    if (candidateRuns.length >= 3) break;
    if (!candidateRuns.some((c) => c.id === r.id)) {
      candidateRuns.push(r);
    }
  }

  const initialSeriesByRun: Record<string, Record<string, TrainingMetricPoint[]>> = {};
  if (candidateRuns.length > 0) {
    const seriesResults = await Promise.allSettled(
      candidateRuns.map(async (run) => {
        const keyStats = await apiGet<{ keys: MetricKeyStat[] }>(
          `/api/v1/runs/${encodeURIComponent(run.id)}/metric-keys`,
        )
          .then((r) => r.keys)
          .catch(() => undefined);
        const keys = canonicalRunMetricKeys(run, keyStats);
        const seriesResp = await apiGet<SeriesResponse>(
          `/api/v1/runs/${encodeURIComponent(run.id)}/metrics`,
          { keys: keys.join(",") },
        );
        return { runId: run.id, series: canonicalSeriesToTrainingSeries(seriesResp) };
      }),
    );
    for (const res of seriesResults) {
      if (res.status === "fulfilled") {
        initialSeriesByRun[res.value.runId] = res.value.series;
      }
    }
  }

  let initialSweepDetail: SweepDetail | null = null;
  if (sweeps.length > 0) {
    try {
      const rawDetail = await apiGet<CanonicalSweepDetail | SweepDetail>(
        `/api/v1/sweeps/${encodeURIComponent(sweeps[0].id)}`,
      );
      initialSweepDetail = canonicalSweepDetailToSweepDetail(rawDetail);
    } catch {
      initialSweepDetail = null;
    }
  }

  return (
    <TrainingStudioClient
      key={`${initialTab ?? "runs"}:${initialRunId ?? ""}`}
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
