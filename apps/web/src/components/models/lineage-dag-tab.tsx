"use client";

import { useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { computeConnectedPath, layoutLineageNodes } from "@/lib/model-lineage";
import { getDagNodeStyle } from "@/lib/mlops-tokens";
import type { LineageGraph, LineageNode } from "@/lib/types";

type LineageFlowNodeData = {
  lineageNode: LineageNode;
  highlighted: boolean;
  dimmed: boolean;
  selected: boolean;
};

type LineageFlowNode = Node<LineageFlowNodeData, "lineageNode">;

function LineageNodeCard({ data }: NodeProps<LineageFlowNode>) {
  const { lineageNode, highlighted, dimmed, selected } = data;
  const style = getDagNodeStyle(lineageNode.node_type);

  return (
    <div
      className={`min-w-[205px] max-w-[230px] rounded-md border px-3 py-2 shadow-md transition ${style.border} ${style.bg} ${
        selected
          ? "ring-2 ring-emerald-400 ring-offset-2 ring-offset-zinc-950"
          : highlighted
            ? "ring-1 ring-emerald-400/70"
            : ""
      } ${dimmed ? "opacity-35" : "opacity-100"}`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !border-zinc-700 !bg-zinc-400"
      />
      <div className="flex items-center gap-1.5">
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} />
        <span className={`text-[10px] font-semibold uppercase tracking-wider ${style.text}`}>
          {style.label}
        </span>
      </div>
      <div className="mt-1 truncate font-mono text-xs font-medium text-zinc-100" title={lineageNode.label}>
        {lineageNode.label}
      </div>
      <div className="mt-0.5 truncate font-mono text-[10px] text-zinc-400" title={lineageNode.entity_id}>
        {lineageNode.entity_id}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !border-zinc-700 !bg-zinc-400"
      />
    </div>
  );
}

const NODE_TYPES = {
  lineageNode: LineageNodeCard,
};

export interface LineageDagTabProps {
  lineage: LineageGraph;
  loading?: boolean;
}

export function LineageDagTab({ lineage, loading = false }: LineageDagTabProps) {
  const [viewMode, setViewMode] = useState<"graph" | "tree">("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const positionedNodes = useMemo(
    () => layoutLineageNodes(lineage.nodes ?? [], lineage.edges ?? []),
    [lineage.nodes, lineage.edges],
  );

  const connectedPath = useMemo(
    () => computeConnectedPath(lineage.edges ?? [], selectedNodeId),
    [lineage.edges, selectedNodeId],
  );

  const rfNodes: LineageFlowNode[] = useMemo(() => {
    const hasSelection = Boolean(selectedNodeId);
    return positionedNodes.map((n) => {
      const inPath = connectedPath.nodeIds.has(n.id);
      return {
        id: n.id,
        type: "lineageNode",
        position: n.position,
        data: {
          lineageNode: n,
          highlighted: hasSelection && inPath,
          dimmed: hasSelection && !inPath,
          selected: selectedNodeId === n.id,
        },
      };
    });
  }, [positionedNodes, connectedPath, selectedNodeId]);

  const rfEdges: Edge[] = useMemo(() => {
    const hasSelection = Boolean(selectedNodeId);
    return (lineage.edges ?? []).map((e) => {
      const inPath = connectedPath.edgeIds.has(e.id);
      const stroke = inPath ? "#34d399" : "#52525b";
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.relation,
        animated: hasSelection && inPath,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: stroke,
        },
        style: {
          stroke,
          strokeWidth: inPath ? 2 : 1.25,
          opacity: hasSelection && !inPath ? 0.25 : 1,
        },
        labelStyle: {
          fill: inPath ? "#34d399" : "#fbbf24",
          fontSize: 11,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        },
        labelBgStyle: {
          fill: "#18181b",
          fillOpacity: 0.9,
        },
      };
    });
  }, [lineage.edges, connectedPath, selectedNodeId]);

  const selectedNode = useMemo(
    () => (lineage.nodes ?? []).find((n) => n.id === selectedNodeId) ?? null,
    [lineage.nodes, selectedNodeId],
  );

  const incomingFor = (nodeId: string) =>
    (lineage.edges ?? []).filter((e) => e.target === nodeId);
  const outgoingFor = (nodeId: string) =>
    (lineage.edges ?? []).filter((e) => e.source === nodeId);

  if (loading) {
    return (
      <div
        role="status"
        aria-label="Loading lineage graph"
        className="flex h-[440px] items-center justify-center rounded-md border border-zinc-800 bg-zinc-950"
      >
        <div className="space-y-2 text-center">
          <div className="mx-auto h-4 w-40 animate-pulse rounded bg-zinc-800 motion-reduce:animate-none" />
          <p className="text-xs text-zinc-400">Loading lineage DAG…</p>
        </div>
      </div>
    );
  }

  if (!lineage.nodes || lineage.nodes.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-zinc-800 px-6 py-10 text-center">
        <p className="text-sm font-medium text-zinc-200">No lineage data recorded</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-zinc-400">
          Lineage edges are populated automatically when a model version links to a training run,
          artifact, evaluation experiment, or safety scan.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
          <span>
            Click or focus any node to highlight its upstream & downstream lineage path.
          </span>
          {selectedNodeId && (
            <button
              type="button"
              onClick={() => setSelectedNodeId(null)}
              className="inline-flex h-7 items-center rounded border border-zinc-700 bg-zinc-900 px-2 text-[11px] font-medium text-zinc-200 hover:bg-zinc-800"
            >
              Clear Highlight
            </button>
          )}
        </div>

        <div
          role="group"
          aria-label="Lineage view mode"
          className="inline-flex rounded-md border border-zinc-800 bg-zinc-900 p-0.5"
        >
          <button
            type="button"
            aria-pressed={viewMode === "graph"}
            onClick={() => setViewMode("graph")}
            className={`h-7 rounded px-2.5 text-xs font-medium transition ${
              viewMode === "graph"
                ? "bg-zinc-800 text-zinc-50"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Graph
          </button>
          <button
            type="button"
            aria-pressed={viewMode === "tree"}
            onClick={() => setViewMode("tree")}
            className={`h-7 rounded px-2.5 text-xs font-medium transition ${
              viewMode === "tree"
                ? "bg-zinc-800 text-zinc-50"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Accessible Tree List
          </button>
        </div>
      </div>

      {viewMode === "graph" ? (
        <div
          role="region"
          aria-label="Model lineage directed acyclic graph"
          className="h-[440px] w-full overflow-hidden rounded-md border border-zinc-800 bg-zinc-950"
        >
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={NODE_TYPES}
            onNodeClick={(_, node) =>
              setSelectedNodeId((prev) => (prev === node.id ? null : node.id))
            }
            onPaneClick={() => setSelectedNodeId(null)}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            minZoom={0.4}
            maxZoom={1.6}
            nodesDraggable={false}
            nodesConnectable={false}
          >
            <Background color="#27272a" gap={20} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      ) : (
        <div
          role="region"
          aria-label="Accessible lineage tree list"
          className="rounded-md border border-zinc-800 bg-zinc-950 p-3"
        >
          <ol className="space-y-2">
            {positionedNodes.map((node) => {
              const style = getDagNodeStyle(node.node_type);
              const isSelected = selectedNodeId === node.id;
              const isInPath = connectedPath.nodeIds.has(node.id);
              const dimmed = Boolean(selectedNodeId) && !isInPath;
              const incoming = incomingFor(node.id);
              const outgoing = outgoingFor(node.id);

              return (
                <li
                  key={node.id}
                  style={{ marginLeft: `${Math.min(node.column, 4) * 16}px` }}
                  className={`rounded-md border p-3 transition ${
                    isSelected
                      ? "border-emerald-500 bg-emerald-950/20"
                      : isInPath
                        ? "border-emerald-800/70 bg-zinc-900/80"
                        : "border-zinc-800 bg-zinc-900/50"
                  } ${dimmed ? "opacity-50" : "opacity-100"}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedNodeId((prev) => (prev === node.id ? null : node.id))
                      }
                      aria-pressed={isSelected}
                      className="flex min-h-[28px] items-center gap-2 text-left"
                    >
                      <span
                        className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-medium ${style.border} ${style.bg} ${style.text}`}
                      >
                        <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                        {style.label}
                      </span>
                      <span className="font-mono text-xs font-semibold text-zinc-100">
                        {node.label}
                      </span>
                      <span className="font-mono text-[11px] text-zinc-400">
                        ({node.entity_id})
                      </span>
                    </button>
                  </div>

                  {(incoming.length > 0 || outgoing.length > 0) && (
                    <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-zinc-400">
                      {incoming.map((e) => (
                        <span key={e.id} className="font-mono">
                          ← <span className="text-amber-300">{e.relation}</span> from{" "}
                          <span className="text-zinc-200">{e.source_id || e.source}</span>
                        </span>
                      ))}
                      {outgoing.map((e) => (
                        <span key={e.id} className="font-mono">
                          → <span className="text-emerald-300">{e.relation}</span> to{" "}
                          <span className="text-zinc-200">{e.target_id || e.target}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {selectedNode && (
        <div
          aria-live="polite"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 p-3 text-xs"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-zinc-100">Selected Node:</span>
              <span className="font-mono text-emerald-300">{selectedNode.label}</span>
              <span className="font-mono text-[11px] text-zinc-400">
                [{selectedNode.node_type} · {selectedNode.entity_id}]
              </span>
            </div>
            <span className="font-mono text-[11px] text-zinc-400">
              {connectedPath.nodeIds.size} connected nodes · {connectedPath.edgeIds.size} edges
            </span>
          </div>
          {Object.keys(selectedNode.metadata ?? {}).length > 0 && (
            <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(selectedNode.metadata).map(([k, v]) => (
                <div key={k} className="flex items-baseline gap-1.5 truncate">
                  <dt className="font-mono text-[11px] text-zinc-400">{k}:</dt>
                  <dd className="truncate font-mono text-[11px] text-zinc-200">
                    {typeof v === "string" || typeof v === "number" || typeof v === "boolean"
                      ? String(v)
                      : JSON.stringify(v)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
    </div>
  );
}
