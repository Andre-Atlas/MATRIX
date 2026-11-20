import { adaptRunDetail } from "./mlops";
import { normalizeImportance } from "./sweeps";
import type {
  CanonicalSweep,
  CanonicalSweepDetail,
  ClassicMlEval,
  EarlyStoppingConfig,
  HardwareInfo,
  MLOpsSweep,
  ParallelAxis,
  SearchSpaceParam,
  SweepDetail,
  SweepTrial,
  TrainingMetricPoint,
  TrainingMetricSeries,
  TrainingRun,
  TrainingRunStatus,
  TrainingRunType,
} from "./types";
import type {
  ClassificationReport,
  ImportanceReport,
  MetricKeyStat,
  Report,
  Run,
  SeriesResponse,
} from "./runs/types";

const DEFAULT_TRAINING_METRIC_KEYS = [
  "train/loss",
  "eval/loss",
  "eval/perplexity",
  "train/learning_rate",
  "dpo/reward_margin",
  "rewards/margins",
  "gpu/utilization",
  "gpu/vram_gb",
  "sys/gpu.0.utilization",
  "sys/gpu.0.memory_used_mb",
];

function mapCanonicalRunStatus(status: string): TrainingRunStatus {
  if (status === "finished") return "completed";
  if (status === "killed") return "cancelled";
  if (status === "running" || status === "completed" || status === "failed" || status === "cancelled" || status === "stopped" || status === "queued") {
    return status;
  }
  return "running";
}

function inferRunType(
  raw: Run & { run_type?: string | null },
  reports?: Report[],
): TrainingRunType {
  if (raw.run_type) return raw.run_type;
  const tagSet = new Set((raw.tags ?? []).map((t) => t.toLowerCase()));
  for (const cand of ["classic_ml", "qlora", "lora", "dpo", "grpo", "ppo", "sft"] as const) {
    if (tagSet.has(cand)) return cand;
  }
  if (reports?.some((r) => r.kind === "classification" || r.kind === "regression")) {
    return "classic_ml";
  }
  const lower = raw.name.toLowerCase();
  if (lower.includes("qlora")) return "qlora";
  if (lower.includes("lora")) return "lora";
  if (lower.includes("dpo")) return "dpo";
  if (lower.includes("churn") || lower.includes("rf") || lower.includes("gbm") || lower.includes("xgb")) {
    return "classic_ml";
  }
  return "sft";
}

export function reportsToClassicEval(reports?: Report[] | null): ClassicMlEval | null {
  if (!Array.isArray(reports) || reports.length === 0) return null;

  const clsReport =
    reports.find((r) => r.kind === "classification" && r.name === "classic_eval") ??
    reports.find((r) => r.kind === "classification");
  const impReport =
    reports.find((r) => r.kind === "importance" && r.name === "classic_eval") ??
    reports.find((r) => r.kind === "importance");
  const customReport = reports.find((r) => r.kind === "custom" && r.name === "classic_eval");

  if (!clsReport && !impReport && !customReport) return null;

  const out: ClassicMlEval = {};
  if (clsReport && clsReport.kind === "classification") {
    const data = clsReport.data as ClassificationReport;
    const m = data.metrics ?? {};
    out.roc_auc = m.roc_auc ?? data.roc?.[0]?.auc ?? null;
    out.pr_auc = m.pr_auc ?? data.pr?.[0]?.ap ?? null;
    out.accuracy = m.accuracy ?? null;
    out.f1 = m.f1 ?? m.f1_macro ?? m.f1_weighted ?? null;
    out.precision = m.precision ?? m.precision_macro ?? m.precision_weighted ?? null;
    out.recall = m.recall ?? m.recall_macro ?? m.recall_weighted ?? null;
    out.log_loss = m.log_loss ?? null;
    if (Array.isArray(data.labels) && Array.isArray(data.confusion_matrix)) {
      out.confusion_matrix = { labels: data.labels, matrix: data.confusion_matrix };
    }
    const firstRoc = data.roc?.[0];
    if (firstRoc && Array.isArray(firstRoc.fpr) && Array.isArray(firstRoc.tpr)) {
      out.roc_curve = firstRoc.fpr.map((fpr, idx) => ({
        fpr,
        tpr: firstRoc.tpr[idx] ?? 0,
      }));
    }
  }

  if (impReport && impReport.kind === "importance") {
    const data = impReport.data as ImportanceReport;
    if (Array.isArray(data.features)) {
      out.feature_importance = data.features.map((f) => ({
        feature: f.name,
        importance: f.importance,
      }));
    }
  }

  if (customReport && customReport.data && typeof customReport.data === "object") {
    const custom = { ...(customReport.data as Record<string, unknown>) };
    const directions = custom.feature_importance_direction as Record<string, "positive" | "negative"> | undefined;
    delete custom.feature_importance_direction;
    Object.assign(out, custom);
    if (directions && Array.isArray(out.feature_importance)) {
      out.feature_importance = out.feature_importance.map((item) =>
        directions[item.feature] ? { ...item, direction: directions[item.feature] } : item,
      );
    }
  }

  return out;
}

export function canonicalRunToTrainingRun(
  raw: Run | TrainingRun,
  reports?: Report[],
): TrainingRun {
  if ("hyperparams" in raw && "summary_metrics" in raw) {
    return adaptRunDetail(raw);
  }

  const run = raw as Run & {
    run_type?: string | null;
    base_model?: string | null;
    env?: (Run["env"] & { hardware?: HardwareInfo | null }) | null;
  };
  const summaryMetrics: Record<string, number> = {};
  for (const [k, v] of Object.entries(run.summary ?? {})) {
    if (typeof v === "number" && Number.isFinite(v)) {
      summaryMetrics[k] = v;
    }
  }
  if (summaryMetrics["dpo/reward_margin"] === undefined && typeof summaryMetrics["rewards/margins"] === "number") {
    summaryMetrics["dpo/reward_margin"] = summaryMetrics["rewards/margins"];
  }
  if (summaryMetrics["gpu/utilization"] === undefined && typeof summaryMetrics["sys/gpu.0.utilization"] === "number") {
    summaryMetrics["gpu/utilization"] = summaryMetrics["sys/gpu.0.utilization"];
  }
  if (summaryMetrics["gpu/vram_gb"] === undefined && typeof summaryMetrics["sys/gpu.0.memory_used_mb"] === "number") {
    summaryMetrics["gpu/vram_gb"] = Number((summaryMetrics["sys/gpu.0.memory_used_mb"] / 1024).toFixed(2));
  }

  const hardware = run.env?.hardware ?? null;
  const baseModel =
    run.base_model ??
    (typeof run.params?.base_model === "string" ? run.params.base_model : null);

  return {
    id: run.id,
    name: run.name,
    run_type: inferRunType(run, reports),
    status: mapCanonicalRunStatus(run.status),
    base_model: baseModel,
    dataset_id: run.dataset_version_id ?? null,
    sweep_id: run.sweep_id ?? null,
    parent_run_id: run.parent_run_id ?? null,
    hyperparams: run.params ?? {},
    summary_metrics: summaryMetrics,
    latest_metrics: summaryMetrics,
    hardware_info: hardware,
    classic_eval: reportsToClassicEval(reports),
    tags: Array.isArray(run.tags) ? run.tags : [],
    total_steps: null,
    error: run.error ?? null,
    last_heartbeat_at: run.heartbeat_at ?? null,
    created_at: run.started_at ?? "",
    started_at: run.started_at ?? null,
    finished_at: run.ended_at ?? null,
  };
}

export function canonicalRunMetricKeys(
  run: Pick<TrainingRun, "summary_metrics"> | Pick<Run, "summary">,
  keyStats?: MetricKeyStat[],
): string[] {
  const keys = new Set<string>();
  for (const stat of keyStats ?? []) {
    if (stat.key) keys.add(stat.key);
  }
  const summary = "summary_metrics" in run ? run.summary_metrics : run.summary;
  for (const k of Object.keys(summary ?? {})) {
    keys.add(k);
  }
  for (const k of DEFAULT_TRAINING_METRIC_KEYS) {
    keys.add(k);
  }
  return [...keys].slice(0, 30);
}

export function canonicalSeriesToTrainingSeries(
  res: SeriesResponse | TrainingMetricSeries | null | undefined,
): Record<string, TrainingMetricPoint[]> {
  if (!res || !res.series) return {};
  const out: Record<string, TrainingMetricPoint[]> = {};

  for (const [key, rawSeries] of Object.entries(res.series)) {
    if (Array.isArray(rawSeries)) {
      out[key] = rawSeries;
      continue;
    }
    const { steps = [], values = [], ts = [] } = rawSeries;
    const pts: TrainingMetricPoint[] = [];
    for (let i = 0; i < steps.length; i++) {
      const val = values[i];
      if (typeof val !== "number" || !Number.isFinite(val)) continue;
      pts.push({
        step: steps[i],
        value: val,
        recorded_at: ts[i] ?? null,
      });
    }
    out[key] = pts;
  }

  if (!(out["dpo/reward_margin"]?.length) && out["rewards/margins"]?.length) {
    out["dpo/reward_margin"] = out["rewards/margins"];
  }
  if (!(out["gpu/utilization"]?.length) && out["sys/gpu.0.utilization"]?.length) {
    out["gpu/utilization"] = out["sys/gpu.0.utilization"];
  }
  if (!(out["gpu/vram_gb"]?.length) && out["sys/gpu.0.memory_used_mb"]?.length) {
    out["gpu/vram_gb"] = out["sys/gpu.0.memory_used_mb"].map((pt) => ({
      ...pt,
      value: Number((pt.value / 1024).toFixed(2)),
    }));
  }

  return out;
}

export function canonicalSweepToMLOpsSweep(raw: CanonicalSweep | MLOpsSweep): MLOpsSweep {
  if ("search_space" in raw && "metric_name" in raw) {
    return raw;
  }
  const sweep = raw as CanonicalSweep;
  const searchSpace: Record<string, SearchSpaceParam> = {};
  for (const [name, p] of Object.entries(sweep.config?.parameters ?? {})) {
    if (p.type === "choice") {
      searchSpace[name] = {
        type: "categorical",
        values: p.values.filter((v): v is string | number | boolean => v !== null),
      };
    } else if (p.type === "constant") {
      const val = p.value;
      searchSpace[name] = {
        type: "categorical",
        values:
          typeof val === "string" || typeof val === "number" || typeof val === "boolean"
            ? [val]
            : [String(val)],
      };
    } else {
      searchSpace[name] = {
        type: p.type,
        min: p.min,
        max: p.max,
        ...(p.type === "int" && p.step !== undefined ? { step: p.step } : {}),
        ...(p.log !== undefined ? { log: p.log } : {}),
      };
    }
  }

  const earlyStopping: EarlyStoppingConfig | null = sweep.config?.early_stop
    ? {
        strategy: sweep.config.early_stop.type === "asha" ? "asha" : "median",
        min_step: sweep.config.early_stop.min_iter,
        reduction_factor: sweep.config.early_stop.eta,
      }
    : null;

  return {
    id: sweep.id,
    name: sweep.name,
    description: sweep.description ?? null,
    method: sweep.config?.method === "tpe" ? "bayes" : (sweep.config?.method ?? "random"),
    metric_name: sweep.config?.metric?.key ?? "eval/loss",
    metric_goal: sweep.config?.metric?.goal ?? "minimize",
    search_space: searchSpace,
    early_stopping: earlyStopping,
    max_trials: sweep.config?.max_runs ?? null,
    status:
      sweep.status === "finished"
        ? "completed"
        : sweep.status,
    best_run_id: sweep.best_run_id ?? null,
    best_metric_value: sweep.best_value ?? null,
    created_at: sweep.created_at,
    stopped_at: null,
  };
}

export function canonicalSweepDetailToSweepDetail(
  raw: CanonicalSweepDetail | SweepDetail,
): SweepDetail {
  if ("analysis" in raw && "trials" in raw) {
    return raw;
  }
  const detail = raw as CanonicalSweepDetail;
  const mapped = canonicalSweepToMLOpsSweep(detail.sweep);
  const metricName = mapped.metric_name;
  const metricGoal = mapped.metric_goal;

  const trials: TrainingRun[] = (detail.runs ?? []).map((r) => ({
    id: r.run_id,
    name: r.run_id,
    run_type: "lora",
    status: mapCanonicalRunStatus(r.status),
    base_model: null,
    dataset_id: null,
    sweep_id: mapped.id,
    parent_run_id: null,
    hyperparams: r.params ?? {},
    summary_metrics: r.best_value !== null ? { [metricName]: r.best_value } : {},
    latest_metrics: r.last_value !== null ? { [metricName]: r.last_value } : {},
    hardware_info: null,
    classic_eval: null,
    tags: r.pruned ? ["pruned"] : [],
    total_steps: r.step ?? null,
    error: null,
    last_heartbeat_at: null,
    created_at: mapped.created_at,
    started_at: mapped.created_at,
    finished_at: null,
  }));

  const pcTrials: SweepTrial[] = (detail.runs ?? []).map((r) => ({
    run_id: r.run_id,
    run_name: r.run_id,
    status: mapCanonicalRunStatus(r.status),
    params: r.params ?? {},
    metric_value: r.best_value ?? r.last_value ?? null,
  }));

  const axes: ParallelAxis[] = [];
  for (const [name, p] of Object.entries(detail.sweep?.config?.parameters ?? {})) {
    if (p.type === "choice") {
      axes.push({
        name,
        type: "categorical",
        values: p.values.filter((v): v is string | number | boolean => v !== null),
      });
    } else if (p.type === "constant") {
      axes.push({ name, type: "categorical", values: [String(p.value)] });
    } else {
      axes.push({
        name,
        type: p.type,
        min: p.min,
        max: p.max,
        log: p.log,
      });
    }
  }
  axes.push({ name: metricName, type: "float", goal: metricGoal });

  const importance = normalizeImportance(detail.importance).map((entry) => ({
    name: entry.param,
    importance: entry.score,
    method: "random_forest",
  }));

  return {
    ...mapped,
    trials,
    analysis: {
      metric_name: metricName,
      metric_goal: metricGoal,
      importance,
      parallel_coordinates: {
        axes,
        trials: pcTrials,
      },
    },
  };
}
