"use client";

import "@xyflow/react/dist/style.css";
import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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
import { formatDuration } from "@/lib/format";
import {
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  type AgentGraphEdge,
  type AgentGraphNode,
  type AgentNodeData,
} from "@/lib/trace-tree";
import { KIND_STYLE, KindBadge } from "@/components/ui";

type FlowNode = Node<AgentNodeData & { selected: boolean }, "agent">;

function AgentNode({ data }: NodeProps<FlowNode>) {
  const err = data.status === "error";
  const style = KIND_STYLE[data.kind] ?? KIND_STYLE.UNKNOWN;
  return (
    <div
      style={{ width: GRAPH_NODE_WIDTH, height: GRAPH_NODE_HEIGHT }}
      className={`flex cursor-pointer flex-col justify-center gap-1 rounded-md border bg-zinc-900 px-2.5 text-xs shadow-sm ${
        data.selected
          ? "border-emerald-400 ring-1 ring-emerald-400/60"
          : err
            ? "border-red-700"
            : "border-zinc-700 hover:border-zinc-500"
      }`}
    >
      <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-zinc-600" />
      <div className="flex min-w-0 items-center gap-1.5">
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-sm ${style.dot}`} />
        <span className={`truncate font-medium ${err ? "text-red-300" : "text-zinc-100"}`}>{data.label}</span>
        {data.count > 1 && (
          <span className="ml-auto shrink-0 rounded bg-amber-400/15 px-1 font-mono text-[10px] text-amber-300">
            ×{data.count}
          </span>
        )}
      </div>
      <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-zinc-500">
        <KindBadge kind={data.kind} compact />
        <span className="truncate font-mono">{data.model ?? ""}</span>
        <span className="ml-auto shrink-0 font-mono tabular-nums">{formatDuration(data.totalDurationMs)}</span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-zinc-600" />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

export function AgentGraph({
  nodes,
  edges,
  selectedSpanId,
}: {
  nodes: AgentGraphNode[];
  edges: AgentGraphEdge[];
  selectedSpanId: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const flowNodes: FlowNode[] = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        draggable: false,
        connectable: false,
        ariaLabel: `${n.data.kind} ${n.data.label}${n.data.count > 1 ? `, ${n.data.count} calls` : ""}${
          n.data.status === "error" ? ", error" : ""
        }`,
        data: { ...n.data, selected: !!selectedSpanId && n.data.spanIds.includes(selectedSpanId) },
      })),
    [nodes, selectedSpanId],
  );
  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((e) => ({
        ...e,
        type: "default",
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "#71717a" },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 3,
      })),
    [edges],
  );

  // Clicking a merged node cycles through its spans (e.g. each iteration of a reflection loop).
  const activate = useCallback(
    (ids: string[]) => {
      const i = selectedSpanId ? ids.indexOf(selectedSpanId) : -1;
      const next = ids[(i + 1) % ids.length];
      const params = new URLSearchParams(searchParams.toString());
      params.set("tab", "graph");
      params.set("span", next);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [router, pathname, searchParams, selectedSpanId],
  );
  const onNodeClick = useCallback((_: unknown, node: FlowNode) => activate(node.data.spanIds), [activate]);
  // React Flow's own Enter/Space handling only toggles selection; route it to the span panel too.
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const id = (e.target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id;
      const node = nodes.find((n) => n.id === id);
      if (node) activate(node.data.spanIds);
    },
    [nodes, activate],
  );

  if (!nodes.length) {
    return (
      <p className="rounded-md border border-dashed border-zinc-800 p-6 text-center text-zinc-500">
        No agent, LLM, tool or retrieval spans to draw.
      </p>
    );
  }

  return (
    <div className="h-[560px] overflow-hidden rounded-md border border-zinc-800" onKeyDown={onKeyDown}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        colorMode="dark"
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1.1 }}
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={20} size={1} color="#27272a" />
        <Controls showInteractive={false} />
      </ReactFlow>
      <p className="sr-only">Click a node to show its span. Merged nodes cycle through their calls on each click.</p>
    </div>
  );
}
