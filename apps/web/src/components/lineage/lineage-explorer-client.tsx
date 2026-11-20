"use client";

import React, { useMemo, useState } from "react";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { LineageDagTab } from "@/components/models/lineage-dag-tab";
import { adaptLineageGraph } from "@/lib/mlops";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { explain, proxy } from "@/lib/proxy-client";
import type { LineageGraph } from "@/lib/types";

export interface LineageExplorerClientProps {
  initialNode: string;
  initialDirection: "up" | "down" | "both";
  initialDepth: number;
  initialGraph: LineageGraph | null;
  initialError?: string | null;
}

const NODE_KIND_HINTS = [
  "run:<id>",
  "model_version:<id>",
  "dataset_commit:<id>",
  "sweep:<id>",
  "experiment:<id>",
  "benchmark:<id>",
];

export function LineageExplorerClient({
  initialNode,
  initialDirection,
  initialDepth,
  initialGraph,
  initialError = null,
}: LineageExplorerClientProps) {
  const [nodeInput, setNodeInput] = useState(initialNode);
  const [direction, setDirection] = useState<"up" | "down" | "both">(initialDirection);
  const [depth, setDepth] = useState<number>(initialDepth);
  const [graph, setGraph] = useState<LineageGraph | null>(initialGraph);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [kindFilter, setKindFilter] = useState<string>("all");

  const availableKinds = useMemo(() => {
    const set = new Set<string>();
    for (const n of graph?.nodes ?? []) {
      set.add(n.node_type);
    }
    return [...set].sort();
  }, [graph]);

  const filteredGraph = useMemo<LineageGraph>(() => {
    if (!graph) return { nodes: [], edges: [] };
    if (kindFilter === "all") return graph;
    const nodes = graph.nodes.filter((n) => n.node_type === kindFilter);
    const keep = new Set(nodes.map((n) => n.id));
    const edges = graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target));
    return { ...graph, nodes, edges };
  }, [graph, kindFilter]);

  async function runQuery(targetNode: string, dir: "up" | "down" | "both", d: number) {
    const clean = targetNode.trim();
    if (!clean) {
      setError("Enter a node reference in <kind>:<id> format (e.g. run:run_123).");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        node: clean,
        direction: dir,
        depth: String(d),
      });
      const raw = await proxy<LineageGraph>("GET", `lineage?${qs.toString()}`);
      setGraph(adaptLineageGraph(raw));
      setKindFilter("all");
    } catch (err) {
      setError(explain(err));
      setGraph(null);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    void runQuery(nodeInput, direction, depth);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Lineage Explorer"
        subtitle={
          <span className="text-zinc-400">
            Bounded per-hop DAG across datasets, runs, sweeps, model versions, experiments, and benchmarks (max 500 nodes)
          </span>
        }
      />

      {/* Query Bar */}
      <form
        onSubmit={handleSubmit}
        className="flex flex-wrap items-end gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 text-xs"
      >
        <div className="min-w-[260px] flex-1">
          <label htmlFor="lineage-node-input" className="block font-medium text-zinc-300">
            Root Node (<code className="font-mono text-zinc-200">&lt;kind&gt;:&lt;id&gt;</code>)
          </label>
          <input
            id="lineage-node-input"
            type="text"
            value={nodeInput}
            onChange={(e) => setNodeInput(e.target.value)}
            placeholder="e.g. model_version:mv_01 or run:run_01"
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-400 focus:border-emerald-500 focus:outline-none"
          />
        </div>

        <div>
          <label htmlFor="lineage-direction" className="block font-medium text-zinc-300">
            Direction
          </label>
          <select
            id="lineage-direction"
            value={direction}
            onChange={(e) => setDirection(e.target.value as "up" | "down" | "both")}
            className="mt-1 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            <option value="both">Both (upstream &amp; downstream)</option>
            <option value="up">Upstream only</option>
            <option value="down">Downstream only</option>
          </select>
        </div>

        <div>
          <label htmlFor="lineage-depth" className="block font-medium text-zinc-300">
            Max Depth
          </label>
          <select
            id="lineage-depth"
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            className="mt-1 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>
                {d} hop{d > 1 ? "s" : ""}
              </option>
            ))}
          </select>
        </div>

        <button
          type="submit"
          disabled={loading}
          className={`rounded px-4 py-1.5 text-xs font-semibold disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
        >
          {loading ? "Loading…" : "Explore"}
        </button>
      </form>

      {/* Truncated Warning Banner (WCAG 2.2 AA) */}
      {graph?.truncated && (
        <div
          role="alert"
          data-testid="lineage-truncated-banner"
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-600/80 bg-amber-950/50 px-4 py-3 text-xs text-amber-100"
        >
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="font-bold text-amber-300">
              ⚠
            </span>
            <span>
              <strong>Lineage graph truncated:</strong> Traversal hit the 500-node safety bound.
              Reduce depth or switch direction to upstream/downstream to inspect a focused subgraph.
            </span>
          </div>
          <span className="font-mono text-[11px] text-amber-200">
            {graph.nodes.length} nodes · {graph.edges.length} edges
          </span>
        </div>
      )}

      {/* Error State */}
      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-800/70 bg-red-950/40 p-4 text-xs text-red-200"
        >
          <span>{error}</span>
          {nodeInput.trim() && (
            <button
              type="button"
              onClick={() => void runQuery(nodeInput, direction, depth)}
              className="rounded border border-red-700 bg-red-900/50 px-3 py-1 font-medium text-red-100"
            >
              Retry
            </button>
          )}
        </div>
      )}

      {/* Kind Filter Chips */}
      {graph && availableKinds.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="Filter nodes by kind">
          <span className="text-zinc-400 mr-1">Filter kind:</span>
          <button
            type="button"
            aria-pressed={kindFilter === "all"}
            onClick={() => setKindFilter("all")}
            className={`rounded border px-2 py-0.5 font-mono text-[11px] ${
              kindFilter === "all"
                ? "border-emerald-600 bg-emerald-950/50 text-emerald-200"
                : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
            }`}
          >
            All ({graph.nodes.length})
          </button>
          {availableKinds.map((kind) => (
            <button
              key={kind}
              type="button"
              aria-pressed={kindFilter === kind}
              onClick={() => setKindFilter(kind)}
              className={`rounded border px-2 py-0.5 font-mono text-[11px] ${
                kindFilter === kind
                  ? "border-emerald-600 bg-emerald-950/50 text-emerald-200"
                  : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
              }`}
            >
              {kind}
            </button>
          ))}
        </div>
      )}

      {/* Loading / Empty / Graph */}
      {loading ? (
        <div role="status" aria-label="Loading lineage graph" className="space-y-3">
          <Skeleton className="h-[420px] w-full" />
          <span className="sr-only">Loading lineage graph…</span>
        </div>
      ) : !graph ? (
        <EmptyState title="Enter a node reference to inspect its lineage">
          <p className="mt-1 text-xs text-zinc-400">
            Supported references: {NODE_KIND_HINTS.join(", ")}.
          </p>
        </EmptyState>
      ) : (
        <LineageDagTab lineage={filteredGraph} loading={false} hideTruncatedBanner />
      )}
    </div>
  );
}
