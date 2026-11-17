import { describe, expect, it } from "vitest";
import {
  calculateClusterMetrics,
  evaluateOverallDrift,
  filterKnowledgeGaps,
  filterVectorPoints,
  formatDriftMetric,
  getDriftStatus,
  normalizeCoordinates,
  project3Dto2D,
} from "./vectors";
import { mockVectorDrift, mockVectorProjection } from "./mock-data";
import type { KnowledgeGap, VectorPoint } from "./types";

const TEST_POINTS: VectorPoint[] = [
  {
    id: "p1",
    label: "User Query on Stripe Webhook",
    type: "query",
    x: 10,
    y: 20,
    z: 5,
    cluster_id: "c-billing",
    cluster_name: "Billing",
    score: 0.92,
    preview: "How to handle Stripe webhook signature validation?",
  },
  {
    id: "p2",
    label: "Doc: Stripe Integration",
    type: "document",
    x: 12,
    y: 22,
    z: 6,
    cluster_id: "c-billing",
    cluster_name: "Billing",
    score: 0.98,
    preview: "Reference guide for verifying Stripe signatures.",
  },
  {
    id: "p3",
    label: "Gap: SAML SSO",
    type: "gap",
    x: 18,
    y: 30,
    z: 10,
    cluster_id: "c-billing",
    cluster_name: "Billing",
    score: 0.45,
    preview: "No documentation available for Okta SAML 2.0 configuration.",
  },
  {
    id: "p4",
    label: "User Query on Agent Tools",
    type: "query",
    x: -30,
    y: -15,
    z: -10,
    cluster_id: "c-agent",
    cluster_name: "Agent Tools",
    score: 0.85,
    preview: "How to handle timeouts in function calling tools?",
  },
  {
    id: "p5",
    label: "Doc: Function Calling Schema",
    type: "document",
    x: -28,
    y: -12,
    z: -8,
    cluster_id: "c-agent",
    cluster_name: "Agent Tools",
    score: 0.95,
    preview: "Specifying JSON schemas for agent tool calls.",
  },
];

const TEST_GAPS: KnowledgeGap[] = [
  {
    id: "g1",
    cluster_id: "c-billing",
    cluster_name: "Billing",
    query_text: "How to configure SAML SSO with Okta?",
    missing_topic: "Enterprise SAML 2.0",
    frequency: 34,
    severity: "critical",
    suggested_action: "Publish SAML setup guide.",
    last_detected: "2026-11-10T12:00:00Z",
  },
  {
    id: "g2",
    cluster_id: "c-agent",
    cluster_name: "Agent Tools",
    query_text: "How to stream partial JSON tool arguments?",
    missing_topic: "Streaming Partial JSON",
    frequency: 21,
    severity: "warning",
    suggested_action: "Add parser guide.",
    last_detected: "2026-11-10T10:00:00Z",
  },
  {
    id: "g3",
    cluster_id: "c-agent",
    cluster_name: "Agent Tools",
    query_text: "Custom headers on subagents?",
    missing_topic: "Subagent Header Forwarding",
    frequency: 8,
    severity: "low",
    suggested_action: "Add small FAQ entry.",
    last_detected: "2026-11-09T18:00:00Z",
  },
];

describe("Vector filtering and projection helpers", () => {
  it("filters points by cluster", () => {
    const billingPoints = filterVectorPoints(TEST_POINTS, { clusterId: "c-billing" });
    expect(billingPoints).toHaveLength(3);
    expect(billingPoints.every((p) => p.cluster_id === "c-billing")).toBe(true);

    const agentPoints = filterVectorPoints(TEST_POINTS, { clusterId: "c-agent" });
    expect(agentPoints).toHaveLength(2);

    const allPoints = filterVectorPoints(TEST_POINTS, { clusterId: "all" });
    expect(allPoints).toHaveLength(5);
  });

  it("filters points by type", () => {
    const queries = filterVectorPoints(TEST_POINTS, { type: "query" });
    expect(queries).toHaveLength(2);
    expect(queries.map((p) => p.id)).toEqual(["p1", "p4"]);

    const docs = filterVectorPoints(TEST_POINTS, { type: "document" });
    expect(docs).toHaveLength(2);

    const gaps = filterVectorPoints(TEST_POINTS, { type: "gap" });
    expect(gaps).toHaveLength(1);
    expect(gaps[0].id).toBe("p3");
  });

  it("filters points by search text across label and preview", () => {
    const stripeSearch = filterVectorPoints(TEST_POINTS, { search: "stripe" });
    expect(stripeSearch).toHaveLength(2);

    const timeoutSearch = filterVectorPoints(TEST_POINTS, { search: "timeout" });
    expect(timeoutSearch).toHaveLength(1);
    expect(timeoutSearch[0].id).toBe("p4");

    const nonExistent = filterVectorPoints(TEST_POINTS, { search: "non-existent-xyz" });
    expect(nonExistent).toHaveLength(0);
  });

  it("filters points by minimum score", () => {
    const highScores = filterVectorPoints(TEST_POINTS, { minScore: 0.95 });
    expect(highScores).toHaveLength(2);
    expect(highScores.map((p) => p.id)).toEqual(["p2", "p5"]);
  });

  it("combines multiple filters correctly", () => {
    const result = filterVectorPoints(TEST_POINTS, {
      clusterId: "c-billing",
      type: "query",
      search: "signature",
    });
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("p1");
  });

  it("calculates cluster metrics correctly", () => {
    const billingStats = calculateClusterMetrics(TEST_POINTS, "c-billing");
    expect(billingStats.total).toBe(3);
    expect(billingStats.queries).toBe(1);
    expect(billingStats.docs).toBe(1);
    expect(billingStats.gaps).toBe(1);
    expect(billingStats.avgScore).toBeCloseTo((0.92 + 0.98 + 0.45) / 3, 2);
  });

  it("projects 3D coordinates onto 2D plane", () => {
    const origin = project3Dto2D({ x: 0, y: 0, z: 0 });
    expect(origin.x).toBeCloseTo(0);
    expect(origin.y).toBeCloseTo(0);

    const projected = project3Dto2D({ x: 10, y: 20, z: 5 }, 45, 30);
    expect(typeof projected.x).toBe("number");
    expect(typeof projected.y).toBe("number");
    expect(Number.isNaN(projected.x)).toBe(false);
    expect(Number.isNaN(projected.y)).toBe(false);
  });

  it("normalizes coordinates into bounding box with padding", () => {
    const coords = [
      { x: 0, y: 0 },
      { x: 100, y: 200 },
    ];
    const normalized = normalizeCoordinates(coords, { width: 500, height: 300, padding: 50 });
    expect(normalized).toHaveLength(2);
    expect(normalized[0].x).toBe(50);
    expect(normalized[0].y).toBe(50);
    expect(normalized[1].x).toBe(450);
    expect(normalized[1].y).toBe(250);

    expect(normalizeCoordinates([], { width: 100, height: 100 })).toEqual([]);
  });
});

describe("Drift metric formatting and status evaluation", () => {
  it("formats drift metric values as decimal", () => {
    expect(formatDriftMetric(0.1424)).toBe("0.142");
    expect(formatDriftMetric(0.08, { decimals: 2 })).toBe("0.08");
    expect(formatDriftMetric(0)).toBe("0.000");
    expect(formatDriftMetric(NaN)).toBe("—");
  });

  it("formats drift metric values as percentage", () => {
    expect(formatDriftMetric(0.142, { format: "percentage" })).toBe("14.2%");
    expect(formatDriftMetric(0.085, { format: "percentage" })).toBe("8.5%");
  });

  it("evaluates drift status based on thresholds", () => {
    expect(getDriftStatus(0.05, 0.1, 0.2)).toBe("healthy");
    expect(getDriftStatus(0.12, 0.1, 0.2)).toBe("warning");
    expect(getDriftStatus(0.25, 0.1, 0.2)).toBe("critical");
  });

  it("evaluates inverted drift status (lower is worse)", () => {
    expect(getDriftStatus(0.95, 0.85, 0.7, false)).toBe("healthy");
    expect(getDriftStatus(0.8, 0.85, 0.7, false)).toBe("warning");
    expect(getDriftStatus(0.65, 0.85, 0.7, false)).toBe("critical");
  });

  it("evaluates overall drift health from individual components", () => {
    expect(
      evaluateOverallDrift({
        jensen_shannon: { name: "JS", value: 0.05, threshold_warning: 0.1, threshold_critical: 0.2, status: "healthy" },
        psi: { name: "PSI", value: 0.06, threshold_warning: 0.1, threshold_critical: 0.25, status: "healthy" },
        centroid_cosine: { name: "Cos", value: 0.04, threshold_warning: 0.08, threshold_critical: 0.15, status: "healthy" },
      }),
    ).toBe("healthy");

    expect(
      evaluateOverallDrift({
        jensen_shannon: { name: "JS", value: 0.12, threshold_warning: 0.1, threshold_critical: 0.2, status: "warning" },
        psi: { name: "PSI", value: 0.06, threshold_warning: 0.1, threshold_critical: 0.25, status: "healthy" },
        centroid_cosine: { name: "Cos", value: 0.04, threshold_warning: 0.08, threshold_critical: 0.15, status: "healthy" },
      }),
    ).toBe("warning");

    expect(
      evaluateOverallDrift({
        jensen_shannon: { name: "JS", value: 0.22, threshold_warning: 0.1, threshold_critical: 0.2, status: "critical" },
        psi: { name: "PSI", value: 0.12, threshold_warning: 0.1, threshold_critical: 0.25, status: "warning" },
        centroid_cosine: { name: "Cos", value: 0.04, threshold_warning: 0.08, threshold_critical: 0.15, status: "healthy" },
      }),
    ).toBe("critical");
  });
});

describe("Knowledge gap filtering", () => {
  it("filters gaps by severity", () => {
    const critical = filterKnowledgeGaps(TEST_GAPS, { severity: "critical" });
    expect(critical).toHaveLength(1);
    expect(critical[0].id).toBe("g1");

    const warning = filterKnowledgeGaps(TEST_GAPS, { severity: "warning" });
    expect(warning).toHaveLength(1);
    expect(warning[0].id).toBe("g2");
  });

  it("filters gaps by cluster", () => {
    const agentGaps = filterKnowledgeGaps(TEST_GAPS, { clusterId: "c-agent" });
    expect(agentGaps).toHaveLength(2);
  });

  it("filters gaps by search query", () => {
    const searchResult = filterKnowledgeGaps(TEST_GAPS, { search: "streaming" });
    expect(searchResult).toHaveLength(1);
    expect(searchResult[0].missing_topic).toBe("Streaming Partial JSON");
  });
});

describe("Vector Mock API responses", () => {
  it("returns projection fixture with points, clusters, and gaps", () => {
    const res = mockVectorProjection();
    expect(res.points.length).toBeGreaterThan(0);
    expect(res.clusters.length).toBeGreaterThan(0);
    expect(res.gaps.length).toBeGreaterThan(0);
    expect(res.reduction_method).toBe("umap");
  });

  it("filters projection by cluster parameter", () => {
    const res = mockVectorProjection({ cluster: "cluster-auth" });
    expect(res.points.every((p) => p.cluster_id === "cluster-auth")).toBe(true);
  });

  it("filters projection by type parameter", () => {
    const res = mockVectorProjection({ type: "gap" });
    expect(res.points.every((p) => p.type === "gap")).toBe(true);
  });

  it("returns drift fixture with valid statistical metrics", () => {
    const drift = mockVectorDrift();
    expect(drift.metrics.jensen_shannon.value).toBeGreaterThan(0);
    expect(drift.metrics.psi.value).toBeGreaterThan(0);
    expect(drift.metrics.centroid_cosine.value).toBeGreaterThan(0);
    expect(drift.timeline.length).toBeGreaterThanOrEqual(5);
    expect(["healthy", "warning", "critical"]).toContain(drift.status);
  });
});
