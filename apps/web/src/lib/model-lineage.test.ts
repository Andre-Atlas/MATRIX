import { describe, expect, it } from "vitest";
import {
  computeConnectedPath,
  computeVersionMetricsDelta,
  inferArtifactFormat,
  layoutLineageNodes,
} from "./model-lineage";
import type { LineageEdge, LineageNode, ModelVersion } from "./types";

describe("layoutLineageNodes", () => {
  it("positions nodes left-to-right by semantic column without overlapping", () => {
    const nodes: LineageNode[] = [
      { id: "dataset:1", node_type: "dataset", entity_id: "1", label: "ds", metadata: {} },
      { id: "run:1", node_type: "run", entity_id: "1", label: "run", metadata: {} },
      { id: "artifact:1", node_type: "artifact", entity_id: "1", label: "art", metadata: {} },
      { id: "mv:1", node_type: "model_version", entity_id: "1", label: "v1", metadata: {} },
      { id: "exp:1", node_type: "experiment", entity_id: "1", label: "exp", metadata: {} },
      { id: "scan:1", node_type: "safety_scan", entity_id: "1", label: "scan", metadata: {} },
    ];
    const edges: LineageEdge[] = [
      {
        id: "e1",
        source: "dataset:1",
        target: "run:1",
        source_type: "dataset",
        source_id: "1",
        target_type: "run",
        target_id: "1",
        relation: "trained_on",
        metadata: {},
      },
      {
        id: "e2",
        source: "run:1",
        target: "artifact:1",
        source_type: "run",
        source_id: "1",
        target_type: "artifact",
        target_id: "1",
        relation: "produced",
        metadata: {},
      },
    ];

    const positioned = layoutLineageNodes(nodes, edges);
    expect(positioned).toHaveLength(6);

    const byId = Object.fromEntries(positioned.map((n) => [n.id, n]));
    expect(byId["dataset:1"].position.x).toBeLessThan(byId["run:1"].position.x);
    expect(byId["run:1"].position.x).toBeLessThan(byId["artifact:1"].position.x);
    expect(byId["artifact:1"].position.x).toBeLessThan(byId["mv:1"].position.x);
    expect(byId["exp:1"].position.x).toBe(byId["scan:1"].position.x);
    expect(byId["exp:1"].position.y).not.toBe(byId["scan:1"].position.y);
  });
});

describe("computeConnectedPath", () => {
  const edges: LineageEdge[] = [
    {
      id: "e1",
      source: "ds:1",
      target: "run:1",
      source_type: "dataset",
      source_id: "1",
      target_type: "run",
      target_id: "1",
      relation: "trained_on",
      metadata: {},
    },
    {
      id: "e2",
      source: "run:1",
      target: "mv:1",
      source_type: "run",
      source_id: "1",
      target_type: "model_version",
      target_id: "1",
      relation: "registered_as",
      metadata: {},
    },
    {
      id: "e3",
      source: "mv:1",
      target: "exp:1",
      source_type: "model_version",
      source_id: "1",
      target_type: "experiment",
      target_id: "1",
      relation: "evaluated_by",
      metadata: {},
    },
    {
      id: "e4",
      source: "ds:2",
      target: "run:2",
      source_type: "dataset",
      source_id: "2",
      target_type: "run",
      target_id: "2",
      relation: "trained_on",
      metadata: {},
    },
  ];

  it("collects all upstream and downstream nodes and edges for selected node", () => {
    const path = computeConnectedPath(edges, "run:1");
    expect(path.nodeIds).toEqual(new Set(["ds:1", "run:1", "mv:1", "exp:1"]));
    expect(path.edgeIds).toEqual(new Set(["e1", "e2", "e3"]));
  });

  it("returns empty sets when nothing is selected", () => {
    const path = computeConnectedPath(edges, null);
    expect(path.nodeIds.size).toBe(0);
    expect(path.edgeIds.size).toBe(0);
  });
});

describe("inferArtifactFormat", () => {
  it("detects safetensors, onnx, pkl, and metadata format", () => {
    expect(inferArtifactFormat({ name: "adapter.safetensors" })).toBe("safetensors");
    expect(inferArtifactFormat({ storage_uri: "s3://bucket/model.onnx" })).toBe("onnx");
    expect(inferArtifactFormat({ name: "clf.pkl" })).toBe("pkl");
    expect(inferArtifactFormat({ metadata: { format: "GGUF" } })).toBe("gguf");
    expect(inferArtifactFormat({ kind: "lora_adapter" })).toBe("safetensors");
  });
});

describe("computeVersionMetricsDelta", () => {
  it("computes sorted metric deltas between two versions", () => {
    const v1: ModelVersion = {
      id: "mv-1",
      model_id: "m-1",
      version: 1,
      artifact_id: null,
      run_id: null,
      eval_experiment_id: null,
      safety_scan_id: null,
      stage: "archived",
      lora_config: null,
      signature: null,
      metrics_snapshot: { "eval/loss": 0.55, accuracy: 0.85 },
      model_card: null,
      created_by: null,
      created_at: "2026-11-10T00:00:00Z",
    };
    const v2: ModelVersion = {
      ...v1,
      id: "mv-2",
      version: 2,
      stage: "production",
      metrics_snapshot: { "eval/loss": 0.42, accuracy: 0.91, f1: 0.89 },
    };

    const deltas = computeVersionMetricsDelta(v1, v2);
    expect(deltas).toEqual([
      { metric: "accuracy", valueA: 0.85, valueB: 0.91, delta: 0.06 },
      { metric: "eval/loss", valueA: 0.55, valueB: 0.42, delta: -0.13 },
      { metric: "f1", valueA: null, valueB: 0.89, delta: null },
    ]);
  });
});
