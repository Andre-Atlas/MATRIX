import { notFound } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import type { CanonicalSweepDetail } from "@/lib/types";
import { SweepDetailClient } from "@/components/sweeps/sweep-detail-client";

export const dynamic = "force-dynamic";

export default async function SweepDetailPage({
  params,
}: {
  params: Promise<{ sweepId: string }>;
}) {
  const { sweepId } = await params;
  let detail: CanonicalSweepDetail | null = null;
  let initialError: string | null = null;

  try {
    detail = await apiGet<CanonicalSweepDetail>(
      `/api/v1/sweeps/${encodeURIComponent(sweepId)}`,
    );
  } catch (err) {
    if (isNotFound(err)) notFound();
    initialError = err instanceof Error ? err.message : "Failed to load sweep detail.";
  }

  return (
    <SweepDetailClient
      sweepId={sweepId}
      initialDetail={detail}
      initialError={initialError}
    />
  );
}
