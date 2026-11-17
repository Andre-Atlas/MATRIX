import type {
  LineageEdge,
  LineageGraph,
  LineageNode,
  RegisteredModel,
  SweepAnalysis,
  TrainingMetricPoint,
  TrainingRun,
} from "./types";

/**
 * Exponential Moving Average (EMA) smoothing for SVG metric curves.
 * `alpha` in (0, 1]: 1.0 = no smoothing, smaller values = smoother curve.
 */
export function smoothEma(points: TrainingMetricPoint[], alpha: number): TrainingMetricPoint[] {
  if (!Array.isArray(points) || points.length === 0) return [];
  const a = Number.isFinite(alpha) ? Math.min(1, Math.max(0.01, alpha)) : 1;
  if (points.length === 1 || a >= 1) {
    return points.map((p) => ({ ...p }));
  }

  const out: TrainingMetricPoint[] = [];
  let prev = points[0].value;
  out.push({ ...points[0], value: prev });

  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    const next = a * p.value + (1 - a) * prev;
    prev = next;
    out.push({ ...p, value: next });
  }
  return out;
}

/**
 * Normalizes a confusion matrix to raw counts or row-wise percentages (0..100).
 */
export function normalizeClassicConfusion(
  matrix: number[][],
  mode: "counts" | "percent",
): number[][] {
  if (!Array.isArray(matrix) || matrix.length === 0) return [];
  if (mode === "counts") {
    return matrix.map((row) => (Array.isArray(row) ? row.map((v) => (Number.isFinite(v) ? v : 0)) : []));
  }
  return matrix.map((row) => {
    if (!Array.isArray(row) || row.length === 0) return [];
    const safeRow = row.map((v) => (Number.isFinite(v) && v > 0 ? v : 0));
    const sum = safeRow.reduce((acc, v) => acc + v, 0);
    if (sum <= 0) return safeRow.map(() => 0);
    return safeRow.map((v) => Number(((v / sum) * 100).toFixed(2)));
  });
}

/**
 * Formats byte sizes into human-readable units (B, KB, MB, GB, TB).
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let val = bytes;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  if (idx === 0) return `${Math.round(val)} B`;
  const decimals = val >= 100 ? 0 : val >= 10 ? 1 : 2;
  return `${val.toFixed(decimals)} ${units[idx]}`;
}

/**
 * Truncates a SHA-256 hash to a short prefix for display.
 */
export function shortSha(sha: string | null | undefined, length = 8): string {
  if (!sha) return "—";
  const clean = sha.trim();
  if (!clean) return "—";
  return clean.slice(0, Math.max(4, length));
}

export type RunFilters = {
  search?: string;
  status?: string;
  run_type?: string;
  sweep_id?: string;
};

export function filterRuns(runs: TrainingRun[], filters: RunFilters): TrainingRun[] {
  const q = filters.search?.trim().toLowerCase() ?? "";
  const status = filters.status?.trim() || "";
  const runType = filters.run_type?.trim() || "";
  const sweepId = filters.sweep_id?.trim() || "";

  return runs.filter((r) => {
    if (status && status !== "all" && r.status !== status) return false;
    if (runType && runType !== "all" && r.run_type !== runType) return false;
    if (sweepId && r.sweep_id !== sweepId) return false;
    if (q) {
      const hay = [r.id, r.name, r.base_model ?? "", r.run_type, ...(r.tags ?? [])]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export type ModelFilters = {
  search?: string;
  model_type?: string;
  stage?: string;
  alias?: string;
};

export function filterModels(models: RegisteredModel[], filters: ModelFilters): RegisteredModel[] {
  const q = filters.search?.trim().toLowerCase() ?? "";
  const modelType = filters.model_type?.trim() || "";
  const stage = filters.stage?.trim() || "";
  const alias = filters.alias?.trim().replace(/^@/, "").toLowerCase() || "";

  return models.filter((m) => {
    if (modelType && modelType !== "all" && m.model_type !== modelType) return false;
    if (stage && stage !== "all") {
      const stages = m.stages ?? {};
      const versionsInStage = stages[stage] ?? [];
      if (versionsInStage.length === 0) return false;
    }
    if (alias && alias !== "all") {
      const modelAliases = Object.keys(m.aliases ?? {}).map((a) => a.toLowerCase());
      if (!modelAliases.includes(alias)) return false;
    }
    if (q) {
      const hay = [m.id, m.name, m.description ?? "", m.base_model ?? "", m.model_type, ...(m.tags ?? [])]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export type HyperparamDiffRow = {
  key: string;
  values: Record<string, unknown>;
  hasDiff: boolean;
};

export type HyperparamDiffResult = {
  keys: string[];
  differs: Record<string, boolean>;
  rows: HyperparamDiffRow[];
};

function stableSerialize(val: unknown): string {
  if (val === undefined) return "__undefined__";
  if (val === null || typeof val !== "object") return JSON.stringify(val);
  if (Array.isArray(val)) return `[${val.map(stableSerialize).join(",")}]`;
  const obj = val as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableSerialize(obj[k])}`).join(",")}}`;
}

/**
 * Computes hyperparameter differences across selected training runs for the Comparison Drawer.
 */
export function computeHyperparamDiff(runs: TrainingRun[]): HyperparamDiffResult {
  if (!Array.isArray(runs) || runs.length === 0) {
    return { keys: [], differs: {}, rows: [] };
  }

  const keySet = new Set<string>();
  for (const r of runs) {
    const hp = r.hyperparams ?? {};
    for (const k of Object.keys(hp)) {
      keySet.add(k);
    }
  }
  const keys = [...keySet].sort();
  const differs: Record<string, boolean> = {};
  const rows: HyperparamDiffRow[] = [];

  for (const key of keys) {
    const values: Record<string, unknown> = {};
    const serialized = new Set<string>();
    for (const r of runs) {
      const val = r.hyperparams ? r.hyperparams[key] : undefined;
      values[r.id] = val;
      serialized.add(stableSerialize(val));
    }
    const hasDiff = serialized.size > 1;
    differs[key] = hasDiff;
    rows.push({ key, values, hasDiff });
  }

  return { keys, differs, rows };
}

/**
 * Safe adapter for TrainingRun detail payloads with optional/nullable fields.
 */
export function adaptRunDetail(raw: Partial<TrainingRun> | null | undefined): TrainingRun {
  const r = raw ?? {};
  const summaryMetrics = r.summary_metrics ?? r.latest_metrics ?? {};
  const latestMetrics = r.latest_metrics ?? r.summary_metrics ?? {};
  return {
    id: r.id ?? "",
    project_id: r.project_id,
    name: r.name ?? "Unnamed Run",
    run_type: r.run_type ?? "sft",
    status: r.status ?? "queued",
    base_model: r.base_model ?? null,
    dataset_id: r.dataset_id ?? null,
    sweep_id: r.sweep_id ?? null,
    parent_run_id: r.parent_run_id ?? null,
    hyperparams: r.hyperparams ?? {},
    summary_metrics: summaryMetrics,
    latest_metrics: latestMetrics,
    hardware_info: r.hardware_info ?? null,
    classic_eval: r.classic_eval ?? null,
    tags: Array.isArray(r.tags) ? r.tags : [],
    total_steps: r.total_steps ?? null,
    error: r.error ?? null,
    last_heartbeat_at: r.last_heartbeat_at ?? null,
    created_at: r.created_at ?? "",
    started_at: r.started_at ?? null,
    finished_at: r.finished_at ?? null,
    artifacts: Array.isArray(r.artifacts) ? r.artifacts : [],
  };
}

/**
 * Safe adapter for SweepAnalysis payloads with optional/nullable fields.
 */
export function adaptSweepAnalysis(raw: Partial<SweepAnalysis> | null | undefined): SweepAnalysis {
  const a = raw ?? {};
  const pc = a.parallel_coordinates;
  return {
    metric_name: a.metric_name ?? "eval/loss",
    metric_goal: a.metric_goal === "maximize" ? "maximize" : "minimize",
    importance: Array.isArray(a.importance) ? a.importance : [],
    parallel_coordinates: {
      axes: Array.isArray(pc?.axes) ? pc.axes : [],
      trials: Array.isArray(pc?.trials) ? pc.trials : [],
    },
  };
}

/**
 * Safe adapter for LineageGraph payloads with optional/nullable fields.
 */
export function adaptLineageGraph(raw: Partial<LineageGraph> | null | undefined): LineageGraph {
  const g = raw ?? {};
  const nodes: LineageNode[] = Array.isArray(g.nodes)
    ? g.nodes.map((n) => ({
        id: n.id,
        node_type: n.node_type ?? "run",
        entity_id: n.entity_id ?? n.id,
        label: n.label ?? n.id,
        metadata: n.metadata ?? {},
      }))
    : [];
  const edges: LineageEdge[] = Array.isArray(g.edges)
    ? g.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        source_type: e.source_type ?? "",
        source_id: e.source_id ?? "",
        target_type: e.target_type ?? "",
        target_id: e.target_id ?? "",
        relation: e.relation ?? "related_to",
        metadata: e.metadata ?? {},
      }))
    : [];

  return {
    run_id: g.run_id,
    model_id: g.model_id,
    nodes,
    edges,
  };
}
