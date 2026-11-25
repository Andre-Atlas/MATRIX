import { unstable_rethrow } from "next/navigation";
import { ModelsRegistryClient } from "@/components/models/models-registry-client";
import { apiGet, isNotFound } from "@/lib/api";
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
      apiGet<{ items: ArtifactRecord[] }>("/api/v1/artifacts").catch((err) => {
        unstable_rethrow(err);
        return {
          items: [] as ArtifactRecord[],
        };
      }),
    ]);
    initialModels = modelsResp.items ?? [];
    initialArtifacts = artifactsResp.items ?? [];
  } catch (err) {
    unstable_rethrow(err);
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
  let initialLineageError: string | null = null;
  let initialHistory: ModelStageHistoryItem[] = [];

  if (initialSelectedId) {
    const [detailResult, historyResult] = await Promise.all([
      apiGet<RegisteredModelDetail>(
        `/api/v1/mlops/models/${encodeURIComponent(initialSelectedId)}`,
      ).catch((err) => {
        unstable_rethrow(err);
        return null;
      }),
      apiGet<{ items: ModelStageHistoryItem[] }>(
        `/api/v1/models/${encodeURIComponent(initialSelectedId)}/history`,
      )
        .then((r) => r.items ?? [])
        .catch((err) => {
          unstable_rethrow(err);
          return [] as ModelStageHistoryItem[];
        }),
    ]);
    initialDetail = detailResult;
    initialHistory = historyResult;

    const latestVersion = detailResult?.versions?.[0];
    if (latestVersion?.id) {
      try {
        const rawLineage = await apiGet<LineageGraph>("/api/v1/lineage", {
          node: `model_version:${latestVersion.id}`,
          direction: "both",
          depth: 3,
        });
        initialLineage = adaptLineageGraph(rawLineage);
      } catch (err) {
        unstable_rethrow(err);
        if (!isNotFound(err)) {
          initialLineageError =
            err instanceof Error ? err.message : "Could not load model lineage.";
        }
      }
    }
    if (!initialLineage && !initialLineageError) {
      try {
        const rawModelLineage = await apiGet<LineageGraph>(
          `/api/v1/mlops/models/${encodeURIComponent(initialSelectedId)}/lineage`,
        );
        initialLineage = adaptLineageGraph(rawModelLineage);
      } catch (err) {
        unstable_rethrow(err);
        initialLineageError =
          err instanceof Error ? err.message : "Could not load model lineage.";
      }
    }
  }

  return (
    <ModelsRegistryClient
      key={`${sp.tab ?? "versions"}:${initialSelectedId ?? ""}`}
      initialModels={initialModels}
      initialDetail={initialDetail}
      initialLineage={initialLineage}
      initialLineageError={initialLineageError}
      initialHistory={initialHistory}
      initialArtifacts={initialArtifacts}
      initialSelectedId={initialSelectedId}
      initialError={initialError}
      initialTab={sp.tab}
    />
  );
}
