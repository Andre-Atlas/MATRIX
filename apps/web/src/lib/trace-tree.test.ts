import { describe, expect, it } from "vitest";
import {
  GRAPH_MAX_PER_ROW,
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  MIN_WIDTH_PCT,
  agentGraph,
  buildTree,
  parseTimeMs,
  traceBounds,
  waterfallRows,
  walkTree,
} from "./trace-tree";
import type { Span, SpanKind } from "./types";

const T0 = Date.parse("2026-11-09T10:00:00.000Z");
const iso = (ms: number) => new Date(T0 + ms).toISOString();

function span(
  id: string,
  parent: string | null,
  startMs: number,
  durMs: number,
  extra: Partial<Span> & { kind?: SpanKind } = {},
): Span {
  return {
    span_id: id,
    trace_id: "t1",
    parent_span_id: parent,
    name: id,
    kind: "CHAIN",
    start_time: iso(startMs),
    end_time: iso(startMs + durMs),
    duration_ms: durMs,
    status: "ok",
    status_message: null,
    model: null,
    provider: null,
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_usd: 0,
    ttft_ms: null,
    input: null,
    output: null,
    input_messages: [],
    output_messages: [],
    documents: [],
    tool: null,
    media: [],
    invocation_parameters: {},
    attributes: {},
    events: [],
    ...extra,
  };
}

const ids = (spans: { span: Span }[]) => spans.map((n) => n.span.span_id);

describe("parseTimeMs", () => {
  it("keeps sub-millisecond precision and handles offsets", () => {
    expect(parseTimeMs("2026-11-09T10:00:00.000500Z") - T0).toBeCloseTo(0.5, 6);
    expect(parseTimeMs("2026-11-09T10:00:00Z")).toBe(T0);
    expect(parseTimeMs("2026-11-09T12:00:00.250+02:00")).toBe(T0 + 250);
    expect(parseTimeMs("2026-11-09T10:00:00.25")).toBe(T0 + 250);
  });
});

describe("buildTree", () => {
  it("returns an empty forest for no spans", () => {
    expect(buildTree([])).toEqual([]);
  });

  it("handles a single span", () => {
    const roots = buildTree([span("a", null, 0, 10)]);
    expect(ids(roots)).toEqual(["a"]);
    expect(roots[0].children).toEqual([]);
  });

  it("sorts children by start time regardless of input order", () => {
    const roots = buildTree([
      span("c3", "root", 30, 5),
      span("root", null, 0, 100),
      span("c1", "root", 10, 5),
      span("c2", "root", 20, 5),
      span("g1", "c1", 12, 1),
    ]);
    expect(ids(roots)).toEqual(["root"]);
    expect(ids(roots[0].children)).toEqual(["c1", "c2", "c3"]);
    expect(ids(roots[0].children[0].children)).toEqual(["g1"]);
  });

  it("breaks start-time ties deterministically by name then id", () => {
    const roots = buildTree([
      span("root", null, 0, 10),
      span("b", "root", 5, 1),
      span("a", "root", 5, 1),
    ]);
    expect(ids(roots[0].children)).toEqual(["a", "b"]);
  });

  it("promotes orphans (missing parent) to roots, sorted by start", () => {
    const roots = buildTree([
      span("root", null, 5, 100),
      span("orphan", "missing", 0, 10),
      span("child", "orphan", 1, 2),
    ]);
    expect(ids(roots)).toEqual(["orphan", "root"]);
    expect(ids(roots[0].children)).toEqual(["child"]);
  });

  it("treats a self-parented span as a root", () => {
    expect(ids(buildTree([span("a", "a", 0, 1)]))).toEqual(["a"]);
  });

  it("breaks parent cycles instead of losing or looping on spans", () => {
    const roots = buildTree([span("a", "b", 0, 10), span("b", "a", 1, 5), span("c", "b", 2, 1)]);
    const seen: string[] = [];
    walkTree(roots, (n) => seen.push(n.span.span_id));
    expect(seen.sort()).toEqual(["a", "b", "c"]);
    expect(ids(roots)).toEqual(["a"]);
  });

  it("ignores duplicate span ids", () => {
    const roots = buildTree([span("a", null, 0, 1), span("a", null, 0, 1)]);
    expect(roots).toHaveLength(1);
  });
});

describe("traceBounds", () => {
  it("spans min start to max end", () => {
    expect(traceBounds([span("a", null, 10, 5), span("b", null, 0, 30)])).toEqual({ start: T0, end: T0 + 30 });
  });
  it("is zero for no spans", () => {
    expect(traceBounds([])).toEqual({ start: 0, end: 0 });
  });
});

describe("waterfallRows", () => {
  const spans = [
    span("root", null, 0, 1000),
    span("b", "root", 500, 500),
    span("a", "root", 100, 200, { ttft_ms: 50 }),
    span("a1", "a", 150, 100),
  ];

  it("emits rows depth-first with depths", () => {
    const rows = waterfallRows(spans);
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["root", 0],
      ["a", 1],
      ["a1", 2],
      ["b", 1],
    ]);
  });

  it("positions bars relative to the trace window", () => {
    const byId = Object.fromEntries(waterfallRows(spans).map((r) => [r.span.span_id, r]));
    expect(byId.root).toMatchObject({ offsetPct: 0, widthPct: 100, ttftPct: null });
    expect(byId.a.offsetPct).toBeCloseTo(10);
    expect(byId.a.widthPct).toBeCloseTo(20);
    expect(byId.a.ttftPct).toBeCloseTo(15);
    expect(byId.b.offsetPct).toBeCloseTo(50);
    expect(byId.b.widthPct).toBeCloseTo(50);
  });

  it("uses the trace window even when the first span does not start at zero", () => {
    const rows = waterfallRows([span("x", null, 1000, 100), span("y", "x", 1050, 50)]);
    expect(rows[1].offsetPct).toBeCloseTo(50);
    expect(rows[1].widthPct).toBeCloseTo(50);
  });

  it("enforces the minimum width and keeps the bar inside the track", () => {
    const rows = waterfallRows([span("root", null, 0, 10_000), span("tiny", "root", 10_000, 0)]);
    const tiny = rows[1];
    expect(tiny.widthPct).toBe(MIN_WIDTH_PCT);
    expect(tiny.offsetPct + tiny.widthPct).toBeLessThanOrEqual(100);
  });

  it("clamps TTFT longer than the span to the bar end", () => {
    const rows = waterfallRows([span("root", null, 0, 100, { ttft_ms: 500 })]);
    expect(rows[0].ttftPct).toBe(100);
  });

  it("handles a single span", () => {
    expect(waterfallRows([span("only", null, 0, 42)])).toEqual([
      expect.objectContaining({ depth: 0, offsetPct: 0, widthPct: 100, ttftPct: null }),
    ]);
  });

  it("handles a zero-duration trace without NaN", () => {
    const rows = waterfallRows([span("r", null, 0, 0, { ttft_ms: 0 }), span("c", "r", 0, 0)]);
    for (const r of rows) {
      expect(r.offsetPct).toBe(0);
      expect(r.widthPct).toBe(MIN_WIDTH_PCT);
      expect(r.ttftPct).toBeNull();
    }
  });

  it("returns no rows for no spans", () => {
    expect(waterfallRows([])).toEqual([]);
  });
});

describe("agentGraph", () => {
  it("creates one node per visible span and parent→child edges", () => {
    const { nodes, edges } = agentGraph([
      span("sup", null, 0, 100, { kind: "AGENT", name: "supervisor" }),
      span("plan", "sup", 1, 10, { kind: "LLM", name: "planner", model: "gpt-4o" }),
      span("search", "sup", 20, 10, { kind: "TOOL", name: "search" }),
    ]);
    expect(nodes.map((n) => [n.data.label, n.data.kind, n.data.count])).toEqual([
      ["supervisor", "AGENT", 1],
      ["planner", "LLM", 1],
      ["search", "TOOL", 1],
    ]);
    expect(nodes[1].data.model).toBe("gpt-4o");
    expect(edges.map((e) => [e.source, e.target, e.label])).toEqual([
      ["n0", "n1", undefined],
      ["n0", "n2", undefined],
    ]);
    expect(edges.every((e) => !("label" in e))).toBe(true);
  });

  it("merges repeated child names under the same parent and labels the edge with the count", () => {
    const { nodes, edges } = agentGraph([
      span("sup", null, 0, 100, { kind: "AGENT", name: "supervisor" }),
      span("c1", "sup", 10, 5, { kind: "LLM", name: "critic" }),
      span("c2", "sup", 20, 5, { kind: "LLM", name: "critic", status: "error" }),
      span("c3", "sup", 30, 7, { kind: "LLM", name: "critic" }),
    ]);
    expect(nodes).toHaveLength(2);
    const critic = nodes[1];
    expect(critic.data).toMatchObject({
      label: "critic",
      count: 3,
      spanIds: ["c1", "c2", "c3"],
      status: "error",
      totalDurationMs: 17,
    });
    expect(edges).toEqual([{ id: "n0->n1", source: "n0", target: "n1", label: "×3", data: { count: 3 } }]);
  });

  it("does not merge same-named spans under different parents or of different kinds", () => {
    const { nodes } = agentGraph([
      span("root", null, 0, 100, { kind: "AGENT", name: "root" }),
      span("a", "root", 1, 10, { kind: "AGENT", name: "a" }),
      span("b", "root", 20, 10, { kind: "AGENT", name: "b" }),
      span("a-x", "a", 2, 1, { kind: "TOOL", name: "x" }),
      span("b-x", "b", 21, 1, { kind: "TOOL", name: "x" }),
      span("b-x-llm", "b", 22, 1, { kind: "LLM", name: "x" }),
    ]);
    expect(nodes.filter((n) => n.data.label === "x")).toHaveLength(3);
  });

  it("merges grandchildren of merged nodes (loop bodies)", () => {
    const { nodes, edges } = agentGraph([
      span("sup", null, 0, 100, { kind: "AGENT", name: "sup" }),
      span("i1", "sup", 10, 5, { kind: "CHAIN", name: "iteration" }),
      span("i1t", "i1", 11, 1, { kind: "TOOL", name: "lookup" }),
      span("i2", "sup", 20, 5, { kind: "CHAIN", name: "iteration" }),
      span("i2t", "i2", 21, 1, { kind: "TOOL", name: "lookup" }),
    ]);
    expect(nodes.map((n) => [n.data.label, n.data.count])).toEqual([
      ["sup", 1],
      ["iteration", 2],
      ["lookup", 2],
    ]);
    expect(edges.map((e) => e.label)).toEqual(["×2", "×2"]);
  });

  it("collapses UNKNOWN (and other non-graph kinds) into the nearest visible ancestor", () => {
    const { nodes, edges } = agentGraph([
      span("agent", null, 0, 100, { kind: "AGENT", name: "agent" }),
      span("wrap", "agent", 1, 50, { kind: "UNKNOWN", name: "wrapper" }),
      span("emb", "wrap", 2, 5, { kind: "EMBEDDING", name: "embed" }),
      span("tool", "emb", 3, 1, { kind: "TOOL", name: "tool" }),
    ]);
    expect(nodes.map((n) => n.data.label)).toEqual(["agent", "tool"]);
    expect(nodes[1].data.depth).toBe(1);
    expect(edges.map((e) => [e.source, e.target])).toEqual([["n0", "n1"]]);
  });

  it("lifts children of an UNKNOWN root to graph roots", () => {
    const { nodes, edges } = agentGraph([
      span("u", null, 0, 100, { kind: "UNKNOWN", name: "u" }),
      span("a", "u", 1, 10, { kind: "LLM", name: "a" }),
      span("b", "u", 20, 10, { kind: "LLM", name: "b" }),
    ]);
    expect(nodes.map((n) => n.data.depth)).toEqual([0, 0]);
    expect(edges).toEqual([]);
  });

  it("lays out top-down by depth with non-overlapping, centred layers", () => {
    const { nodes } = agentGraph([
      span("sup", null, 0, 100, { kind: "AGENT", name: "sup" }),
      span("a", "sup", 1, 1, { kind: "LLM", name: "a" }),
      span("b", "sup", 2, 1, { kind: "TOOL", name: "b" }),
      span("c", "sup", 3, 1, { kind: "RETRIEVER", name: "c" }),
    ]);
    const [sup, a, b, c] = nodes;
    expect(sup.position).toEqual({ x: 0, y: 0 });
    expect(a.position.y).toBeGreaterThan(sup.position.y + GRAPH_NODE_HEIGHT);
    expect([a, b, c].every((n) => n.position.y === a.position.y)).toBe(true);
    expect(b.position.x).toBe(0);
    expect(b.position.x - a.position.x).toBeGreaterThan(GRAPH_NODE_WIDTH);
    expect(c.position.x - b.position.x).toBeGreaterThan(GRAPH_NODE_WIDTH);
  });

  it("wraps wide layers into rows without overlapping nodes and keeps depths ordered", () => {
    const kids = Array.from({ length: 9 }, (_, i) =>
      span(`k${i}`, "sup", i + 1, 1, { kind: "TOOL", name: `tool${i}` }),
    );
    const grand = span("g", "k8", 9.5, 0.2, { kind: "LLM", name: "grand" });
    const { nodes } = agentGraph([span("sup", null, 0, 100, { kind: "AGENT", name: "sup" }), ...kids, grand]);
    const layer1 = nodes.filter((n) => n.data.depth === 1);
    expect(new Set(layer1.map((n) => n.position.y)).size).toBe(Math.ceil(9 / GRAPH_MAX_PER_ROW));
    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b) continue;
        const overlapX = Math.abs(a.position.x - b.position.x) < GRAPH_NODE_WIDTH;
        const overlapY = Math.abs(a.position.y - b.position.y) < GRAPH_NODE_HEIGHT;
        expect(overlapX && overlapY).toBe(false);
      }
    }
    const maxY1 = Math.max(...layer1.map((n) => n.position.y));
    expect(nodes.find((n) => n.data.label === "grand")!.position.y).toBeGreaterThan(maxY1 + GRAPH_NODE_HEIGHT);
    expect(nodes[0].position.y).toBeLessThan(Math.min(...layer1.map((n) => n.position.y)));
  });

  it("handles a single span and no spans", () => {
    expect(agentGraph([span("x", null, 0, 1, { kind: "LLM" })]).nodes).toHaveLength(1);
    expect(agentGraph([])).toEqual({ nodes: [], edges: [] });
    expect(agentGraph([span("x", null, 0, 1, { kind: "UNKNOWN" })])).toEqual({ nodes: [], edges: [] });
  });
});
