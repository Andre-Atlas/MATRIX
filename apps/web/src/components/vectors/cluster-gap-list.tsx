"use client";

import { useState } from "react";
import type { KnowledgeGap, VectorCluster } from "@/lib/types";
import { filterKnowledgeGaps } from "@/lib/vectors";

interface ClusterGapListProps {
  clusters: VectorCluster[];
  gaps: KnowledgeGap[];
  selectedClusterId?: string | null;
  onSelectCluster?: (clusterId: string | null) => void;
}

const SEVERITY_STYLE: Record<KnowledgeGap["severity"], { badge: string; dot: string; label: string }> = {
  critical: {
    badge: "border-red-800/80 bg-red-950/50 text-red-300",
    dot: "bg-red-400",
    label: "Critical Gap",
  },
  warning: {
    badge: "border-amber-800/70 bg-amber-950/40 text-amber-300",
    dot: "bg-amber-400",
    label: "Warning Gap",
  },
  low: {
    badge: "border-zinc-700 bg-zinc-900 text-zinc-400",
    dot: "bg-zinc-500",
    label: "Low Priority",
  },
};

const CLUSTER_HEALTH_STYLE: Record<VectorCluster["health_status"], { badge: string; text: string }> = {
  healthy: { badge: "border-emerald-800/60 bg-emerald-950/40 text-emerald-300", text: "Healthy" },
  warning: { badge: "border-amber-800/60 bg-amber-950/40 text-amber-300", text: "Warning" },
  critical: { badge: "border-red-800/70 bg-red-950/50 text-red-300", text: "Critical" },
};

export function ClusterGapList({
  clusters,
  gaps,
  selectedClusterId,
  onSelectCluster,
}: ClusterGapListProps) {
  const [activeTab, setActiveTab] = useState<"clusters" | "gaps">("clusters");
  const [gapSeverityFilter, setGapSeverityFilter] = useState<string>("all");
  const [searchGaps, setSearchGaps] = useState("");

  const filteredGaps = filterKnowledgeGaps(gaps, {
    clusterId: selectedClusterId ?? undefined,
    severity: gapSeverityFilter,
    search: searchGaps,
  });

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 shadow-lg">
      {/* Tab Navigation Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-zinc-700/80 bg-zinc-950 p-0.5">
            <button
              type="button"
              onClick={() => setActiveTab("clusters")}
              className={`rounded px-3 py-1 font-mono text-xs font-medium transition-colors ${
                activeTab === "clusters"
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Identified Clusters ({clusters.length})
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("gaps")}
              className={`rounded px-3 py-1 font-mono text-xs font-medium transition-colors ${
                activeTab === "gaps"
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Knowledge Gaps ({gaps.length})
            </button>
          </div>
          {selectedClusterId && (
            <button
              type="button"
              onClick={() => onSelectCluster && onSelectCluster(null)}
              className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300 hover:text-zinc-100"
            >
              <span>Clear cluster filter</span>
              <span className="text-zinc-500">✕</span>
            </button>
          )}
        </div>

        {activeTab === "gaps" && (
          <div className="flex items-center gap-2">
            <select
              value={gapSeverityFilter}
              onChange={(e) => setGapSeverityFilter(e.target.value)}
              aria-label="Filter gap severity"
              className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 focus:border-zinc-500 focus:outline-none"
            >
              <option value="all">All Severities</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="low">Low</option>
            </select>
            <input
              type="search"
              placeholder="Search gaps..."
              value={searchGaps}
              onChange={(e) => setSearchGaps(e.target.value)}
              className="w-36 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:border-zinc-500 focus:outline-none"
            />
          </div>
        )}
      </div>

      {/* Tab 1: Clusters Table */}
      {activeTab === "clusters" && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-zinc-800 bg-zinc-950/40 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3">Cluster</th>
                <th className="px-3 py-3">Assertividade</th>
                <th className="px-3 py-3">Pontos</th>
                <th className="px-3 py-3">Gaps</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Keywords</th>
                <th className="px-4 py-3 text-right">Ação</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {clusters.map((cluster) => {
                const isSelected = selectedClusterId === cluster.id;
                const assertivenessPercent = Math.round(cluster.confidence_rate * 100);
                const health = CLUSTER_HEALTH_STYLE[cluster.health_status];

                return (
                  <tr
                    key={cluster.id}
                    className={`transition-colors hover:bg-zinc-800/40 ${
                      isSelected ? "bg-zinc-800/60" : ""
                    }`}
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-zinc-200">{cluster.name}</div>
                      {cluster.description && (
                        <div className="mt-0.5 line-clamp-1 max-w-sm text-[11px] text-zinc-400">
                          {cluster.description}
                        </div>
                      )}
                    </td>

                    {/* Taxa de Assertividade com barra de progresso */}
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-semibold tabular-nums text-zinc-200">
                          {assertivenessPercent}%
                        </span>
                        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-zinc-800">
                          <div
                            className={`h-full ${
                              assertivenessPercent >= 85
                                ? "bg-emerald-400"
                                : assertivenessPercent >= 70
                                  ? "bg-amber-400"
                                  : "bg-red-400"
                            }`}
                            style={{ width: `${assertivenessPercent}%` }}
                          />
                        </div>
                      </div>
                    </td>

                    <td className="px-3 py-3 font-mono text-zinc-300">
                      <span className="text-sky-300">{cluster.query_count}q</span>
                      {" / "}
                      <span className="text-emerald-300">{cluster.document_count}d</span>
                    </td>

                    <td className="px-3 py-3">
                      {cluster.gap_count > 0 ? (
                        <span className="inline-flex items-center gap-1 font-mono font-medium text-red-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-red-400 animate-pulse" />
                          {cluster.gap_count} gaps
                        </span>
                      ) : (
                        <span className="font-mono text-zinc-500">0</span>
                      )}
                    </td>

                    <td className="px-3 py-3">
                      <span
                        className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${health.badge}`}
                      >
                        {health.text}
                      </span>
                    </td>

                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-1 max-w-xs">
                        {cluster.keywords?.slice(0, 3).map((kw) => (
                          <span
                            key={kw}
                            className="rounded bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400"
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
                    </td>

                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() =>
                          onSelectCluster &&
                          onSelectCluster(isSelected ? null : cluster.id)
                        }
                        className={`rounded px-2.5 py-1 font-mono text-xs transition-colors ${
                          isSelected
                            ? "bg-sky-500/20 text-sky-300 border border-sky-500/40"
                            : "border border-zinc-700 bg-zinc-800 text-zinc-300 hover:text-zinc-100 hover:border-zinc-600"
                        }`}
                      >
                        {isSelected ? "Selected" : "Focar"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Tab 2: Knowledge Gaps List */}
      {activeTab === "gaps" && (
        <div className="divide-y divide-zinc-800/60">
          {filteredGaps.length === 0 ? (
            <div className="p-8 text-center text-xs text-zinc-500">
              Nenhum gap de conhecimento encontrado com os filtros atuais.
            </div>
          ) : (
            filteredGaps.map((gap) => {
              const sev = SEVERITY_STYLE[gap.severity] ?? SEVERITY_STYLE.low;
              const formattedDate = new Date(gap.last_detected).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              });

              return (
                <div
                  key={gap.id}
                  className="p-4 transition-colors hover:bg-zinc-800/30 flex flex-col md:flex-row md:items-start justify-between gap-4"
                >
                  <div className="space-y-1.5 max-w-2xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${sev.badge}`}
                      >
                        <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${sev.dot}`} />
                        {sev.label}
                      </span>
                      <span className="font-semibold text-zinc-100 text-sm">
                        {gap.missing_topic}
                      </span>
                      <span className="text-zinc-600">•</span>
                      <span className="font-mono text-xs text-zinc-400">{gap.cluster_name}</span>
                    </div>

                    <div className="rounded-md border border-zinc-800 bg-zinc-950/70 p-2 text-xs">
                      <span className="font-mono text-zinc-500">Query frequente: </span>
                      <span className="italic text-zinc-300">&ldquo;{gap.query_text}&rdquo;</span>
                    </div>

                    <div className="flex items-center gap-1.5 text-xs text-amber-300/90 pt-1">
                      <span className="font-semibold">Ação recomendada:</span>
                      <span>{gap.suggested_action}</span>
                    </div>
                  </div>

                  <div className="flex flex-col items-end justify-between shrink-0 gap-3 text-right">
                    <div className="text-xs">
                      <div className="font-mono font-medium text-red-300">
                        {gap.frequency} queries sem resposta
                      </div>
                      <div className="font-mono text-[11px] text-zinc-500">
                        Detectado: {formattedDate}
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() =>
                        onSelectCluster && onSelectCluster(gap.cluster_id)
                      }
                      className="rounded border border-zinc-700 bg-zinc-800 px-3 py-1 font-mono text-xs text-zinc-200 hover:border-zinc-500 hover:text-white transition-colors"
                    >
                      Ver no Projetor →
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
