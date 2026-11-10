import type { DriftMetricStatus, DriftMetrics, KnowledgeGap, VectorCluster, VectorPoint, VectorPointType } from "./types";

export type VectorFilterOptions = {
  clusterId?: string | "all";
  type?: VectorPointType | "all";
  search?: string;
  minScore?: number;
};

/** Filters vector points by cluster, type, keyword search and score threshold. */
export function filterVectorPoints(
  points: VectorPoint[],
  filters: VectorFilterOptions = {},
): VectorPoint[] {
  const { clusterId, type, search, minScore } = filters;
  const q = search?.trim().toLowerCase();

  return points.filter((pt) => {
    if (clusterId && clusterId !== "all" && pt.cluster_id !== clusterId) {
      return false;
    }
    if (type && type !== "all" && pt.type !== type) {
      return false;
    }
    if (minScore !== undefined && (pt.score ?? 0) < minScore) {
      return false;
    }
    if (q) {
      const matchLabel = pt.label.toLowerCase().includes(q);
      const matchPreview = pt.preview ? pt.preview.toLowerCase().includes(q) : false;
      const matchCluster = pt.cluster_name ? pt.cluster_name.toLowerCase().includes(q) : false;
      if (!matchLabel && !matchPreview && !matchCluster) return false;
    }
    return true;
  });
}

/** Formats a drift metric value as string with decimals or percentage. */
export function formatDriftMetric(
  value: number,
  options?: { format?: "decimal" | "percentage" | "score"; decimals?: number },
): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  if (options?.format === "percentage") {
    const decimals = options?.decimals ?? 1;
    return `${(value * 100).toFixed(decimals)}%`;
  }
  const decimals = options?.decimals ?? 3;
  return value.toFixed(decimals);
}

/** Determines drift status based on metric thresholds. */
export function getDriftStatus(
  value: number,
  thresholdWarning: number,
  thresholdCritical: number,
  higherIsWorse = true,
): DriftMetricStatus {
  if (higherIsWorse) {
    if (value >= thresholdCritical) return "critical";
    if (value >= thresholdWarning) return "warning";
    return "healthy";
  }
  // If lower is worse (e.g. similarity)
  if (value <= thresholdCritical) return "critical";
  if (value <= thresholdWarning) return "warning";
  return "healthy";
}

/** Project 3D vector coordinates onto a 2D isometric/camera plane with rotation. */
export function project3Dto2D(
  coord: { x: number; y: number; z?: number },
  rotationDegrees: number = 30,
  pitchDegrees: number = 20,
): { x: number; y: number } {
  const radYaw = (rotationDegrees * Math.PI) / 180;
  const radPitch = (pitchDegrees * Math.PI) / 180;

  const z = coord.z ?? 0;
  // Rotate around Y axis (yaw)
  const xRot = coord.x * Math.cos(radYaw) - z * Math.sin(radYaw);
  const zRot = coord.x * Math.sin(radYaw) + z * Math.cos(radYaw);

  // Rotate around X axis (pitch)
  const yRot = coord.y * Math.cos(radPitch) - zRot * Math.sin(radPitch);

  return { x: xRot, y: yRot };
}

/** Normalizes coordinates of points to fit SVG/Canvas viewport while preserving aspect ratio. */
export function normalizeCoordinates(
  points: { x: number; y: number }[],
  bounds: { width: number; height: number; padding?: number },
): { x: number; y: number }[] {
  if (!points.length) return [];
  const padding = bounds.padding ?? 32;
  const w = Math.max(1, bounds.width - padding * 2);
  const h = Math.max(1, bounds.height - padding * 2);

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }

  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;

  return points.map((p) => ({
    x: padding + ((p.x - minX) / rangeX) * w,
    y: padding + ((p.y - minY) / rangeY) * h,
  }));
}

/** Aggregates point metrics for a given cluster. */
export function calculateClusterMetrics(points: VectorPoint[], clusterId: string) {
  const clusterPoints = points.filter((p) => p.cluster_id === clusterId);
  const total = clusterPoints.length;
  const queries = clusterPoints.filter((p) => p.type === "query").length;
  const docs = clusterPoints.filter((p) => p.type === "document").length;
  const gaps = clusterPoints.filter((p) => p.type === "gap").length;

  const scores = clusterPoints.filter((p) => p.score !== undefined).map((p) => p.score as number);
  const avgScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;

  return {
    total,
    queries,
    docs,
    gaps,
    avgScore,
  };
}

/** Evaluates overall system health from individual drift metrics. */
export function evaluateOverallDrift(
  metrics: Pick<DriftMetrics, "jensen_shannon" | "psi" | "centroid_cosine">,
): DriftMetricStatus {
  const statuses = [metrics.jensen_shannon.status, metrics.psi.status, metrics.centroid_cosine.status];
  if (statuses.includes("critical")) return "critical";
  if (statuses.includes("warning")) return "warning";
  return "healthy";
}

/** Filters knowledge gaps by search query or severity. */
export function filterKnowledgeGaps(
  gaps: KnowledgeGap[],
  filter: { clusterId?: string; severity?: string; search?: string } = {},
): KnowledgeGap[] {
  const q = filter.search?.trim().toLowerCase();
  return gaps.filter((gap) => {
    if (filter.clusterId && filter.clusterId !== "all" && gap.cluster_id !== filter.clusterId) {
      return false;
    }
    if (filter.severity && filter.severity !== "all" && gap.severity !== filter.severity) {
      return false;
    }
    if (q) {
      const matchTopic = gap.missing_topic.toLowerCase().includes(q);
      const matchQuery = gap.query_text.toLowerCase().includes(q);
      const matchCluster = gap.cluster_name.toLowerCase().includes(q);
      if (!matchTopic && !matchQuery && !matchCluster) return false;
    }
    return true;
  });
}

/** Adapts raw backend API responses to the frontend's VectorProjectionResponse & VectorDriftResponse structures. */
export function adaptVectorsData(rawProj: any, rawDrift: any): { projection: any; drift: any } {
  const points = (rawProj?.points || []).map((p: any) => ({
    id: String(p.id),
    label: p.label || (p.text_preview ? p.text_preview.slice(0, 32) : p.id),
    type: p.type || p.kind || "query",
    x: typeof p.x === "number" ? p.x : 0,
    y: typeof p.y === "number" ? p.y : 0,
    z: typeof p.z === "number" ? p.z : 0,
    cluster_id: String(p.cluster_id ?? 0),
    cluster_name: p.cluster_name || `Cluster ${p.cluster_id ?? 0}`,
    score: p.score ?? undefined,
    preview: p.preview || p.text_preview || "",
    trace_id: p.trace_id || undefined,
    document_id: p.document_id || undefined,
  }));

  const clusters = (rawProj?.clusters || []).map((c: any) => ({
    id: String(c.id ?? c.cluster_id ?? 0),
    name: c.name || c.label || `Cluster ${c.cluster_id ?? c.id ?? 0}`,
    confidence_rate: c.confidence_rate ?? 0.88,
    point_count: c.count ?? c.point_count ?? points.filter((p: any) => p.cluster_id === String(c.id ?? c.cluster_id)).length,
    document_count: c.document_count ?? points.filter((p: any) => p.cluster_id === String(c.id ?? c.cluster_id) && p.type === "document").length,
    query_count: c.query_count ?? points.filter((p: any) => p.cluster_id === String(c.id ?? c.cluster_id) && p.type === "query").length,
    gap_count: c.gap_count ?? points.filter((p: any) => p.cluster_id === String(c.id ?? c.cluster_id) && p.type === "gap").length,
    centroid: c.centroid || [0, 0, 0],
    health_status: c.health_status || "healthy",
    top_keywords: c.top_keywords || ["query", "document"],
  }));

  const gaps = (rawProj?.gaps || []).length > 0
    ? rawProj.gaps
    : points.filter((p: any) => p.type === "gap").map((p: any, idx: number) => ({
        id: `gap-${idx}`,
        cluster_id: p.cluster_id,
        cluster_name: p.cluster_name,
        missing_topic: p.label || "Tópico sem cobertura documental",
        query_text: p.preview || "Consulta de usuário com baixa proximidade aos documentos",
        severity: "warning",
        unanswered_query_count: 1,
        recommended_action: "Indexar documentação adicional para este tópico",
        created_at: new Date().toISOString(),
      }));

  const projAdapted = {
    points,
    clusters,
    gaps,
    dimensions: rawProj?.dimensions || 3,
    total_points: rawProj?.total_points || rawProj?.metadata?.total_points || points.length,
    reduction_method: rawProj?.reduction_method || "umap",
  };

  const jsdVal = typeof rawDrift?.metrics?.jsd === "number" ? rawDrift.metrics.jsd : (rawDrift?.metrics?.jensen_shannon?.value ?? 0.05);
  const psiVal = typeof rawDrift?.metrics?.psi === "number" ? rawDrift.metrics.psi : (rawDrift?.metrics?.psi?.value ?? 0.04);
  const cosVal = typeof rawDrift?.metrics?.centroid_drift === "number" ? rawDrift.metrics.centroid_drift : (rawDrift?.metrics?.centroid_cosine?.value ?? 0.02);

  const driftStatus = rawDrift?.status || (jsdVal >= 0.25 ? "critical" : jsdVal >= 0.1 ? "warning" : "healthy");

  const driftAdapted = {
    status: driftStatus,
    clusters_drifting: rawDrift?.clusters_drifting ?? 0,
    top_drifting_clusters: rawDrift?.top_drifting_clusters || [],
    metrics: {
      overall_status: rawDrift?.metrics?.overall_status || driftStatus,
      baseline_period: rawDrift?.metrics?.baseline_period || "Last 7 days",
      target_period: rawDrift?.metrics?.target_period || "Current session",
      last_calculated: rawDrift?.metrics?.last_calculated || new Date().toISOString(),
      total_queries_analyzed: rawDrift?.metrics?.total_queries_analyzed ?? points.filter((p: any) => p.type === "query").length,
      jensen_shannon: {
        value: jsdVal,
        threshold_warning: 0.1,
        threshold_critical: 0.25,
        status: jsdVal >= 0.25 ? "critical" : jsdVal >= 0.1 ? "warning" : "healthy",
      },
      psi: {
        value: psiVal,
        threshold_warning: 0.1,
        threshold_critical: 0.25,
        status: psiVal >= 0.25 ? "critical" : psiVal >= 0.1 ? "warning" : "healthy",
      },
      centroid_cosine: {
        value: cosVal,
        threshold_warning: 0.15,
        threshold_critical: 0.3,
        status: cosVal >= 0.3 ? "critical" : cosVal >= 0.15 ? "warning" : "healthy",
      },
      wasserstein: {
        value: 0.042,
        threshold_warning: 0.2,
        threshold_critical: 0.4,
        status: "healthy",
      },
    },
    timeline: (rawDrift?.history || rawDrift?.timeline || []).map((h: any) => ({
      timestamp: h.timestamp || new Date().toISOString(),
      jensen_shannon: h.jsd ?? h.jensen_shannon ?? 0.01,
      psi: h.psi ?? 0.01,
      centroid_cosine: h.centroid_drift ?? h.centroid_cosine ?? 0.005,
      status: h.drift_level || h.status || "healthy",
    })),
  };

  return { projection: projAdapted, drift: driftAdapted };
}
