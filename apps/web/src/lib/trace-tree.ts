import type { Span, SpanKind } from "./types";

// Pure helpers that turn a flat list of spans into the shapes the trace views need.

export type SpanNode = { span: Span; children: SpanNode[] };

export type WaterfallRow = {
  span: Span;
  depth: number;
  /** Bar start, as a percentage of the trace duration. */
  offsetPct: number;
  /** Bar width, as a percentage of the trace duration (never below MIN_WIDTH_PCT). */
  widthPct: number;
  /** TTFT tick position in trace coordinates (same axis as offsetPct), or null. */
  ttftPct: number | null;
};

export const MIN_WIDTH_PCT = 0.5;

/** Parses an ISO 8601 timestamp to epoch milliseconds, keeping sub-millisecond digits. */
export function parseTimeMs(iso: string): number {
  const m = /^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/.exec(iso);
  if (!m) return Date.parse(iso);
  const base = Date.parse(`${m[1]}${m[3] ?? "Z"}`);
  const frac = m[2] ? Number(`0${m[2]}`) * 1000 : 0;
  return base + frac;
}

function spanStart(s: Span): number {
  return parseTimeMs(s.start_time);
}

function spanEnd(s: Span): number {
  const end = parseTimeMs(s.end_time);
  return Number.isFinite(end) ? end : spanStart(s) + s.duration_ms;
}

function compareSpans(a: Span, b: Span): number {
  return spanStart(a) - spanStart(b) || a.name.localeCompare(b.name) || a.span_id.localeCompare(b.span_id);
}

/**
 * Builds the span tree. Children (and roots) are sorted by start time. Spans whose parent is
 * missing from the list become roots; parent cycles are broken by promoting the earliest span.
 */
export function buildTree(spans: Span[]): SpanNode[] {
  const nodes = new Map<string, SpanNode>();
  for (const span of spans) {
    if (!nodes.has(span.span_id)) nodes.set(span.span_id, { span, children: [] });
  }
  const roots: SpanNode[] = [];
  for (const node of nodes.values()) {
    const pid = node.span.parent_span_id;
    const parent = pid && pid !== node.span.span_id ? nodes.get(pid) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  // Break cycles: any node not reachable from a root gets detached and promoted to root.
  const reached = new Set<string>();
  const mark = (n: SpanNode) => {
    const stack = [n];
    while (stack.length) {
      const cur = stack.pop()!;
      if (reached.has(cur.span.span_id)) continue;
      reached.add(cur.span.span_id);
      stack.push(...cur.children);
    }
  };
  roots.forEach(mark);
  if (reached.size < nodes.size) {
    const pending = [...nodes.values()]
      .filter((n) => !reached.has(n.span.span_id))
      .sort((a, b) => compareSpans(a.span, b.span));
    for (const n of pending) {
      if (reached.has(n.span.span_id)) continue;
      const parent = nodes.get(n.span.parent_span_id ?? "");
      if (parent) parent.children = parent.children.filter((c) => c !== n);
      roots.push(n);
      mark(n);
    }
  }

  const sortRec = (list: SpanNode[]) => {
    list.sort((a, b) => compareSpans(a.span, b.span));
    for (const n of list) sortRec(n.children);
  };
  sortRec(roots);
  return roots;
}

/** Depth-first walk over the tree, yielding each node with its depth. */
export function walkTree(roots: SpanNode[], visit: (node: SpanNode, depth: number) => void): void {
  const stack: [SpanNode, number][] = roots.map((r) => [r, 0] as [SpanNode, number]).reverse();
  while (stack.length) {
    const [node, depth] = stack.pop()!;
    visit(node, depth);
    for (let i = node.children.length - 1; i >= 0; i--) stack.push([node.children[i], depth + 1]);
  }
}

/** Trace window [start, end] in epoch ms over all spans. */
export function traceBounds(spans: Span[]): { start: number; end: number } {
  let start = Infinity;
  let end = -Infinity;
  for (const s of spans) {
    start = Math.min(start, spanStart(s));
    end = Math.max(end, spanEnd(s));
  }
  if (!Number.isFinite(start)) return { start: 0, end: 0 };
  return { start, end: Math.max(start, end) };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Rows for the waterfall, depth-first, positioned relative to the trace window.
 * A zero-duration trace puts every bar at offset 0 with the minimum width.
 */
export function waterfallRows(spans: Span[]): WaterfallRow[] {
  const { start, end } = traceBounds(spans);
  const total = end - start;
  const rows: WaterfallRow[] = [];
  walkTree(buildTree(spans), (node, depth) => {
    const s = node.span;
    if (total <= 0) {
      rows.push({ span: s, depth, offsetPct: 0, widthPct: MIN_WIDTH_PCT, ttftPct: null });
      return;
    }
    const a = spanStart(s);
    const b = Math.max(a, spanEnd(s));
    const widthPct = clamp(((b - a) / total) * 100, MIN_WIDTH_PCT, 100);
    const offsetPct = clamp(((a - start) / total) * 100, 0, 100 - widthPct);
    let ttftPct: number | null = null;
    if (s.ttft_ms !== null && s.ttft_ms !== undefined) {
      ttftPct = clamp(((a + s.ttft_ms - start) / total) * 100, offsetPct, offsetPct + widthPct);
    }
    rows.push({ span: s, depth, offsetPct, widthPct, ttftPct });
  });
  return rows;
}

// ---------------------------------------------------------------------------
// Agent graph

export const GRAPH_KINDS: ReadonlySet<SpanKind> = new Set<SpanKind>([
  "AGENT",
  "TOOL",
  "LLM",
  "RETRIEVER",
  "RERANKER",
  "GUARDRAIL",
  "CHAIN",
]);

export const GRAPH_NODE_WIDTH = 184;
export const GRAPH_NODE_HEIGHT = 56;
export const GRAPH_GAP_X = 32;
export const GRAPH_GAP_Y = 64;
/** Vertical gap between wrapped sub-rows of the same layer. */
export const GRAPH_GAP_ROW = 28;
export const GRAPH_MAX_PER_ROW = 4;

export type AgentNodeData = {
  label: string;
  kind: SpanKind;
  /** Spans merged into this node, in start order. */
  spanIds: string[];
  count: number;
  status: "ok" | "error" | "unset";
  model: string | null;
  totalDurationMs: number;
  depth: number;
};

export type AgentGraphNode = {
  id: string;
  type: "agent";
  position: { x: number; y: number };
  data: AgentNodeData;
};

export type AgentGraphEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
  data: { count: number };
};

/**
 * Agent graph for React Flow. One node per span of a GRAPH_KINDS kind; other kinds (UNKNOWN,
 * EMBEDDING, EVALUATOR) are collapsed into their nearest visible ancestor. Repeated children
 * with the same name and kind under the same node are merged into one node with a `count`,
 * and the edge into it is labelled "×count". Layout is top-down layered: y by depth, x by
 * order of first appearance within the depth, each layer centred on x = 0. Layers wider than
 * GRAPH_MAX_PER_ROW wrap into staggered sub-rows below each other.
 */
export function agentGraph(spans: Span[]): { nodes: AgentGraphNode[]; edges: AgentGraphEdge[] } {
  const nodes: AgentGraphNode[] = [];
  const byKey = new Map<string, AgentGraphNode>();
  const edges = new Map<string, AgentGraphEdge>();

  const visit = (node: SpanNode, parentId: string | null, depth: number) => {
    const s = node.span;
    let here = parentId;
    let nextDepth = depth;
    if (GRAPH_KINDS.has(s.kind)) {
      const key = `${parentId ?? ""}\u0000${s.kind}\u0000${s.name}`;
      let g = byKey.get(key);
      if (!g) {
        g = {
          id: `n${nodes.length}`,
          type: "agent",
          position: { x: 0, y: 0 },
          data: {
            label: s.name,
            kind: s.kind,
            spanIds: [],
            count: 0,
            status: s.status,
            model: s.model,
            totalDurationMs: 0,
            depth,
          },
        };
        byKey.set(key, g);
        nodes.push(g);
      }
      g.data.spanIds.push(s.span_id);
      g.data.count += 1;
      g.data.totalDurationMs += s.duration_ms;
      if (s.status === "error") g.data.status = "error";
      else if (g.data.status === "unset" && s.status === "ok") g.data.status = "ok";
      if (parentId) {
        const eid = `${parentId}->${g.id}`;
        const e = edges.get(eid) ?? { id: eid, source: parentId, target: g.id, data: { count: 0 } };
        e.data.count += 1;
        e.label = e.data.count > 1 ? `×${e.data.count}` : undefined;
        if (!e.label) delete e.label;
        edges.set(eid, e);
      }
      here = g.id;
      nextDepth = depth + 1;
    }
    for (const c of node.children) visit(c, here, nextDepth);
  };
  for (const r of buildTree(spans)) visit(r, null, 0);

  const layers = new Map<number, AgentGraphNode[]>();
  for (const n of nodes) {
    const layer = layers.get(n.data.depth) ?? [];
    layer.push(n);
    layers.set(n.data.depth, layer);
  }
  // Wide layers wrap into staggered sub-rows so the graph stays readable when fitted to a panel.
  const stepX = GRAPH_NODE_WIDTH + GRAPH_GAP_X;
  let y = 0;
  const depths = [...layers.keys()].sort((a, b) => a - b);
  for (const depth of depths) {
    const layer = layers.get(depth)!;
    const rows = Math.ceil(layer.length / GRAPH_MAX_PER_ROW);
    for (let r = 0; r < rows; r++) {
      const row = layer.slice(r * GRAPH_MAX_PER_ROW, (r + 1) * GRAPH_MAX_PER_ROW);
      // Odd sub-rows sit in the gaps of the first sub-row so edges can pass between nodes.
      const sameParity = row.length % 2 === Math.min(layer.length, GRAPH_MAX_PER_ROW) % 2;
      const stagger = r % 2 === 1 && sameParity ? stepX / 2 : r % 2 === 0 && !sameParity ? stepX / 2 : 0;
      row.forEach((n, i) => {
        n.position = { x: (i - (row.length - 1) / 2) * stepX + stagger, y };
      });
      y += GRAPH_NODE_HEIGHT + (r < rows - 1 ? GRAPH_GAP_ROW : GRAPH_GAP_Y);
    }
  }
  return { nodes, edges: [...edges.values()] };
}
