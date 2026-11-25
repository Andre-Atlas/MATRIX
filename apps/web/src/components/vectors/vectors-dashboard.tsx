"use client";

import { useState } from "react";
import type { VectorDriftResponse, VectorProjectionResponse } from "@/lib/types";
import { VectorProjector } from "./vector-projector";
import { DriftMonitor } from "./drift-monitor";
import { ClusterGapList } from "./cluster-gap-list";
import { Kpi, SimulatedBadge } from "@/components/ui";

interface VectorsDashboardProps {
  projection: VectorProjectionResponse;
  drift: VectorDriftResponse;
}

export function VectorsDashboard({ projection, drift }: VectorsDashboardProps) {
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);

  const selectedCluster = selectedClusterId
    ? projection.clusters.find((c) => c.id === selectedClusterId)
    : null;

  const isSimulated =
    projection.simulated === true ||
    drift.simulated === true ||
    projection.engine === "pca-lite" ||
    drift.mode === "demo";

  return (
    <div className="space-y-6">
      {isSimulated && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-700/60 bg-amber-950/30 px-3.5 py-2.5 text-xs text-amber-200"
        >
          <SimulatedBadge reason={projection.engine || "pca-lite / demo"} />
          <span>
            Vector projection ({projection.engine || "pca-lite"} ·{" "}
            {projection.embedding || "hash-3gram (demo)"}) and drift metrics are running in
            simulated demo mode.
          </span>
        </div>
      )}

      {/* Top KPI Cards Row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi
          label="Total Vectors"
          value={projection.total_points}
          sub={`${projection.points.filter((p) => p.type === "query").length} queries, ${
            projection.points.filter((p) => p.type === "document").length
          } docs`}
        />
        <Kpi
          label="Clusters"
          value={projection.clusters.length}
          sub={
            selectedCluster
              ? `Filtered: ${selectedCluster.name.slice(0, 18)}…`
              : "All clusters active"
          }
        />
        <Kpi
          label="Knowledge Gaps"
          value={projection.gaps.length}
          sub="Uncovered query topics"
          tone={projection.gaps.length > 0 ? "warn" : "default"}
        />
        <Kpi
          label="Drift Status"
          value={drift.status.toUpperCase()}
          sub={`JS: ${drift.metrics.jensen_shannon.value.toFixed(3)} | PSI: ${drift.metrics.psi.value.toFixed(3)}`}
          tone={drift.status === "critical" ? "error" : drift.status === "warning" ? "warn" : "default"}
        />
      </div>

      {/* Main Vector Space Canvas */}
      <section aria-label="Vector Space Projection">
        <VectorProjector
          points={projection.points}
          clusters={projection.clusters}
          selectedClusterId={selectedClusterId}
          onClusterSelect={setSelectedClusterId}
        />
      </section>

      {/* Drift Observatory Panel */}
      <section aria-label="Drift Observatory">
        <DriftMonitor driftData={drift} />
      </section>

      {/* Clusters and Knowledge Gaps List */}
      <section aria-label="Clusters and Knowledge Gaps">
        <ClusterGapList
          clusters={projection.clusters}
          gaps={projection.gaps}
          selectedClusterId={selectedClusterId}
          onSelectCluster={setSelectedClusterId}
        />
      </section>
    </div>
  );
}
