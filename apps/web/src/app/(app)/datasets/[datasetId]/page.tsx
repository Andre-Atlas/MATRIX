import { notFound, unstable_rethrow } from "next/navigation";
import { apiGet, isNotFound } from "@/lib/api";
import type { DatasetBranch, DatasetCommit, DatasetDetail } from "@/lib/types";
import { DatasetStudioClient } from "@/components/datasets/dataset-studio-client";

export const dynamic = "force-dynamic";

export default async function DatasetPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  let detail: DatasetDetail;
  try {
    detail = await apiGet<DatasetDetail>(`/api/v1/datasets/${encodeURIComponent(datasetId)}`);
  } catch (e) {
    unstable_rethrow(e);
    if (isNotFound(e)) notFound();
    throw e;
  }

  let branches: DatasetBranch[] = [];
  try {
    const brRes = await apiGet<{ items: DatasetBranch[] }>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/branches`
    );
    branches = brRes.items || [];
  } catch (e) {
    unstable_rethrow(e);
    branches = [];
  }

  let commits: DatasetCommit[] = [];
  try {
    const comRes = await apiGet<{ items: DatasetCommit[] }>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/commits`
    );
    commits = comRes.items || [];
  } catch (e) {
    unstable_rethrow(e);
    commits = [];
  }

  return (
    <DatasetStudioClient
      dataset={detail.dataset}
      initialItems={detail.items}
      initialBranches={branches}
      initialCommits={commits}
    />
  );
}
