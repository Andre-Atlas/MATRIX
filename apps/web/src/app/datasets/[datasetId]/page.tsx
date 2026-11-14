import { notFound } from "next/navigation";
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
    if (isNotFound(e)) notFound();
    throw e;
  }

  let branches: DatasetBranch[] = [];
  try {
    const brRes = await apiGet<{ items: DatasetBranch[] }>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/branches`
    );
    branches = brRes.items || [];
  } catch {
    branches = [
      {
        id: "br-main",
        project_id: "proj-default",
        dataset_id: datasetId,
        name: "main",
        head_commit_id: "c-main-02",
        is_default: true,
        created_by: "system",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ];
  }

  let commits: DatasetCommit[] = [];
  try {
    const comRes = await apiGet<{ items: DatasetCommit[] }>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/commits`
    );
    commits = comRes.items || [];
  } catch {
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
