import { ModelsRegistryClient } from "@/components/models/models-registry-client";
import { apiGet } from "@/lib/api";
import { adaptLineageGraph } from "@/lib/mlops";
import type {
  ArtifactRecord,
  LineageGraph,
  ModelStageHistoryItem,
  RegisteredModel,
  RegisteredModelDetail,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ModelsPage({
  searchParams,
}: {
  searchParams?: Promise<{
    model?: string;
    tab?: "versions" | "history" | "webhooks" | "lineage" | "card";
  }>;
}) {
  const sp = searchParams ? await searchParams : {};
  let initialModels: RegisteredModel[] = [];
  let initialArtifacts: ArtifactRecord[] = [];
  let initialError: string | null = null;

  try {
    const [modelsResp, artifactsResp] = await Promise.all([
      apiGet<{ items: RegisteredModel[] }>("/api/v1/mlops/models"),
      apiGet<{ items: ArtifactRecord[] }>("/api/v1/artifacts").catch(() => ({
        items: [] as ArtifactRecord[],
      })),
    ]);
    initialModels = modelsResp.items ?? [];
    initialArtifacts = artifactsResp.items ?? [];
  } catch (err) {
    initialError =
      err instanceof Error ? err.message : "Could not load registered models.";
  }

  const requestedId = sp?.model?.trim();
  const initialSelectedId =
    requestedId && initialModels.some((m) => m.id === requestedId)
      ? requestedId
      : initialModels[0]?.id ?? null;

  let initialDetail: RegisteredModelDetail | null = null;
  let initialLineage: LineageGraph | null = null;
  let initialHistory: ModelStageHistoryItem[] = [];

  if (initialSelectedId) {
    const [detailResult, historyResult] = await Promise.all([
      apiGet<RegisteredModelDetail>(
        `/api/v1/mlops/models/${encodeURIComponent(initialSelectedId)}`,
      ).catch(() => null),
      apiGet<{ items: ModelStageHistoryItem[] }>(
        `/api/v1/models/${encodeURIComponent(initialSelectedId)}/history`,
      )
        .then((r) => r.items ?? [])
        .catch(() => [] as ModelStageHistoryItem[]),
    ]);
    initialDetail = detailResult;
    initialHistory = historyResult;

    const latestVersion = detailResult?.versions?.[0];
    if (latestVersion?.id) {
      initialLineage = await apiGet<LineageGraph>("/api/v1/lineage", {
        node: `model_version:${latestVersion.id}`,
        direction: "both",
        depth: 3,
      })
        .then((g) => adaptLineageGraph(g))
        .catch(() => null);
    }
    if (!initialLineage) {
      initialLineage = await apiGet<LineageGraph>(
        `/api/v1/mlops/models/${encodeURIComponent(initialSelectedId)}/lineage`,
      )
        .then((g) => adaptLineageGraph(g))
        .catch(() => null);
    }
  }

  return (
    <ModelsRegistryClient
      initialModels={initialModels}
      initialDetail={initialDetail}
      initialLineage={initialLineage}
      initialHistory={initialHistory}
      initialArtifacts={initialArtifacts}
      initialSelectedId={initialSelectedId}
      initialError={initialError}
      initialTab={sp.tab}
    />
  );
}
