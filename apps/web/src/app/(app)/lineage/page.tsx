import { apiGet } from "@/lib/api";
import { adaptLineageGraph } from "@/lib/mlops";
import type { LineageGraph } from "@/lib/types";
import { LineageExplorerClient } from "@/components/lineage/lineage-explorer-client";

export const dynamic = "force-dynamic";

export default async function LineagePage({
  searchParams,
}: {
  searchParams?: Promise<{
    node?: string;
    direction?: "up" | "down" | "both";
    depth?: string;
  }>;
}) {
  const sp = searchParams ? await searchParams : {};
  const node = sp.node?.trim() || "";
  const direction: "up" | "down" | "both" =
    sp.direction === "up" || sp.direction === "down" ? sp.direction : "both";
  const parsedDepth = Number(sp.depth);
  const depth = Number.isInteger(parsedDepth) && parsedDepth >= 0 && parsedDepth <= 10 ? parsedDepth : 3;

  let initialGraph: LineageGraph | null = null;
  let initialError: string | null = null;

  if (node) {
    try {
      const raw = await apiGet<LineageGraph>("/api/v1/lineage", {
        node,
        direction,
        depth,
      });
      initialGraph = adaptLineageGraph(raw);
    } catch (err) {
      initialError = err instanceof Error ? err.message : "Failed to load lineage graph.";
    }
  }

  return (
    <LineageExplorerClient
      initialNode={node}
      initialDirection={direction}
      initialDepth={depth}
      initialGraph={initialGraph}
      initialError={initialError}
    />
  );
}
