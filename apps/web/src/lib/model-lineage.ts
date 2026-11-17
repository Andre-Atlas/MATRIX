import type { ArtifactRecord, LineageEdge, LineageNode, ModelVersion } from "./types";

const NODE_TYPE_COLUMN: Record<string, number> = {
  dataset: 0,
  run: 1,
  artifact: 2,
  model_version: 3,
  experiment: 4,
  safety_scan: 4,
};

export type PositionedLineageNode = LineageNode & {
  position: { x: number; y: number };
  column: number;
};

/**
 * Computes deterministic left-to-right coordinates for lineage DAG nodes
 * grouped by semantic stage (Dataset -> Run -> Artifact -> ModelVersion -> Eval/Safety).
 */
export function layoutLineageNodes(
  nodes: LineageNode[],
  edges: LineageEdge[],
): PositionedLineageNode[] {
  if (!nodes.length) return [];

  const colMap = new Map<string, number>();
  for (const n of nodes) {
    colMap.set(n.id, NODE_TYPE_COLUMN[n.node_type] ?? 2);
  }

  // Ensure edges always flow left-to-right even for custom node types
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of edges) {
      const srcCol = colMap.get(e.source);
      const tgtCol = colMap.get(e.target);
      if (srcCol !== undefined && tgtCol !== undefined && tgtCol <= srcCol) {
        colMap.set(e.target, srcCol + 1);
        changed = true;
      }
    }
    if (!changed) break;
  }

  const byCol = new Map<number, LineageNode[]>();
  for (const n of nodes) {
    const col = colMap.get(n.id) ?? 0;
    const arr = byCol.get(col) ?? [];
    arr.push(n);
    byCol.set(col, arr);
  }

  const COL_GAP = 270;
  const ROW_GAP = 120;
  const PADDING_X = 24;
  const PADDING_Y = 24;

  const positioned: PositionedLineageNode[] = [];
  const sortedCols = [...byCol.keys()].sort((a, b) => a - b);
  const maxRows = Math.max(...[...byCol.values()].map((arr) => arr.length), 1);

  for (const col of sortedCols) {
    const group = byCol.get(col) ?? [];
    const totalHeight = (group.length - 1) * ROW_GAP;
    const maxTotalHeight = (maxRows - 1) * ROW_GAP;
    const offsetY = Math.max(0, (maxTotalHeight - totalHeight) / 2);

    group.forEach((node, idx) => {
      positioned.push({
        ...node,
        column: col,
        position: {
          x: PADDING_X + col * COL_GAP,
          y: PADDING_Y + offsetY + idx * ROW_GAP,
        },
      });
    });
  }

  return positioned;
}

/**
 * Computes all upstream ancestor and downstream descendant node IDs and edge IDs
 * connected to `selectedNodeId` for path highlighting.
 */
export function computeConnectedPath(
  edges: LineageEdge[],
  selectedNodeId: string | null,
): { nodeIds: Set<string>; edgeIds: Set<string> } {
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  if (!selectedNodeId) return { nodeIds, edgeIds };

  nodeIds.add(selectedNodeId);

  // Upstream BFS
  const upQueue = [selectedNodeId];
  const upVisited = new Set<string>([selectedNodeId]);
  while (upQueue.length > 0) {
    const curr = upQueue.shift()!;
    for (const e of edges) {
      if (e.target === curr) {
        edgeIds.add(e.id);
        nodeIds.add(e.source);
        if (!upVisited.has(e.source)) {
          upVisited.add(e.source);
          upQueue.push(e.source);
        }
      }
    }
  }

  // Downstream BFS
  const downQueue = [selectedNodeId];
  const downVisited = new Set<string>([selectedNodeId]);
  while (downQueue.length > 0) {
    const curr = downQueue.shift()!;
    for (const e of edges) {
      if (e.source === curr) {
        edgeIds.add(e.id);
        nodeIds.add(e.target);
        if (!downVisited.has(e.target)) {
          downVisited.add(e.target);
          downQueue.push(e.target);
        }
      }
    }
  }

  return { nodeIds, edgeIds };
}

/**
 * Infers artifact serialization format (`safetensors`, `onnx`, `pkl`, etc.)
 * from metadata, file name, storage URI, or content type.
 */
export function inferArtifactFormat(
  artifact: Partial<ArtifactRecord> | null | undefined,
): string {
  if (!artifact) return "unknown";
  const metaFmt = artifact.metadata?.format;
  if (typeof metaFmt === "string" && metaFmt.trim()) {
    return metaFmt.trim().toLowerCase();
  }
  const probe = `${artifact.name ?? ""} ${artifact.storage_uri ?? ""} ${artifact.content_type ?? ""}`.toLowerCase();
  if (probe.includes("safetensors")) return "safetensors";
  if (probe.includes("onnx")) return "onnx";
  if (probe.includes("pkl") || probe.includes("pickle")) return "pkl";
  if (probe.includes("gguf")) return "gguf";
  if (probe.includes("pt") || probe.includes("pytorch")) return "pt";
  if (artifact.kind === "lora_adapter" || artifact.kind === "model_weights") return "safetensors";
  if (artifact.kind === "classic_model") return "onnx";
  return "bin";
}

export type VersionMetricDelta = {
  metric: string;
  valueA: number | null;
  valueB: number | null;
  delta: number | null;
};

/**
 * Computes side-by-side metric snapshot deltas between two model versions (B - A).
 */
export function computeVersionMetricsDelta(
  versionA: ModelVersion,
  versionB: ModelVersion,
): VersionMetricDelta[] {
  const keys = new Set<string>([
    ...Object.keys(versionA.metrics_snapshot ?? {}),
    ...Object.keys(versionB.metrics_snapshot ?? {}),
  ]);
  return [...keys].sort().map((metric) => {
    const rawA = versionA.metrics_snapshot?.[metric];
    const rawB = versionB.metrics_snapshot?.[metric];
    const valueA = typeof rawA === "number" && Number.isFinite(rawA) ? rawA : null;
    const valueB = typeof rawB === "number" && Number.isFinite(rawB) ? rawB : null;
    const delta = valueA !== null && valueB !== null ? Number((valueB - valueA).toFixed(4)) : null;
    return { metric, valueA, valueB, delta };
  });
}
