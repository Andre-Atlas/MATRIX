"use client";

import { useState } from "react";
import type { VectorDriftResponse, VectorProjectionResponse } from "@/lib/types";
import { VectorProjector } from "./vector-projector";
import { DriftMonitor } from "./drift-monitor";
import { ClusterGapList } from "./cluster-gap-list";
import { Kpi } from "@/components/ui";

interface VectorsDashboardProps {
  projection: VectorProjectionResponse;
  drift: VectorDriftResponse;
}

export function VectorsDashboard({ projection, drift }: VectorsDashboardProps) {
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);

  const selectedCluster = selectedClusterId
    ? projection.clusters.find((c) => c.id === selectedClusterId)
    : null;

  return (
    <div className="space-y-6">
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
