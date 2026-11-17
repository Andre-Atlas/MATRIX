import { ApiError } from "./api-error";
import type {
  ArtifactDownloadUrlResponse,
  ArtifactRecord,
  CreateModelRequest,
  CreateModelVersionRequest,
  CreateSweepRequest,
  CreateTrainingRunRequest,
  EvaluateModelVersionRequest,
  EvaluateModelVersionResponse,
  IngestMetricsRequest,
  IngestMetricsResponse,
  LineageEdge,
  LineageGraph,
  LineageNode,
  MLOpsSweep,
  ModelAlias,
  ModelVersion,
  PresignArtifactUploadRequest,
  RegisteredModel,
  RegisteredModelDetail,
  SetModelAliasRequest,
  SuggestNextTrialRequest,
  SuggestNextTrialResponse,
  SweepAnalysis,
  SweepDetail,
  TrainingMetricPoint,
  TrainingMetricSeries,
  TrainingRun,
  UpdateModelStageRequest,
  UpdateTrainingRunRequest,
} from "./types";

type Params = Record<string, string | number | undefined>;

interface MockMlopsState {
  runs: TrainingRun[];
  seriesByRun: Record<string, Record<string, TrainingMetricPoint[]>>;
  sweeps: MLOpsSweep[];
  sweepTrials: Record<string, TrainingRun[]>;
  sweepAnalysis: Record<string, SweepAnalysis>;
  artifacts: ArtifactRecord[];
  models: RegisteredModel[];
  versionsByModel: Record<string, ModelVersion[]>;
  aliasesByModel: Record<string, Record<string, number>>;
  runLineage: Record<string, LineageGraph>;
  modelLineage: Record<string, LineageGraph>;
  seq: number;
}

function makeLossSeries(
  steps: number,
  startLoss: number,
  endLoss: number,
  baseTsMs: number,
  options: { includeDpo?: boolean; gpuUtilBase?: number; vramGb?: number } = {},
): Record<string, TrainingMetricPoint[]> {
  const trainLoss: TrainingMetricPoint[] = [];
  const evalLoss: TrainingMetricPoint[] = [];
  const evalPpl: TrainingMetricPoint[] = [];
  const lr: TrainingMetricPoint[] = [];
  const gpuUtil: TrainingMetricPoint[] = [];
  const gpuVram: TrainingMetricPoint[] = [];
  const dpoMargin: TrainingMetricPoint[] = [];

  const count = 20;
  const stepInc = Math.max(1, Math.floor(steps / count));
  for (let i = 0; i <= count; i++) {
    const step = i * stepInc;
    const progress = i / count;
    const decay = Math.exp(-3.1 * progress);
    const wave = Math.sin(i * 1.3) * 0.018 * (1 - progress * 0.5);
    const tVal = Number((endLoss + (startLoss - endLoss) * decay + wave).toFixed(4));
    const eVal = Number((tVal + 0.042 + Math.cos(i * 0.9) * 0.01).toFixed(4));
    const epoch = Number((progress * 3).toFixed(2));
    const recordedAt = new Date(baseTsMs + i * 180_000).toISOString();

    trainLoss.push({ step, value: tVal, epoch, recorded_at: recordedAt });
    if (i % 2 === 0) {
      evalLoss.push({ step, value: eVal, epoch, recorded_at: recordedAt });
      evalPpl.push({ step, value: Number(Math.exp(eVal).toFixed(3)), epoch, recorded_at: recordedAt });
    }
    const warmup = progress < 0.1 ? progress / 0.1 : 1 - (progress - 0.1) / 0.9;
    lr.push({
      step,
      value: Number((2e-4 * Math.max(0.05, warmup)).toExponential(3)),
      epoch,
      recorded_at: recordedAt,
    });
    gpuUtil.push({
      step,
      value: Number(((options.gpuUtilBase ?? 91) + Math.sin(i * 0.8) * 3.5).toFixed(1)),
      epoch,
      recorded_at: recordedAt,
    });
    gpuVram.push({
      step,
      value: Number(((options.vramGb ?? 64.2) + Math.cos(i * 0.6) * 1.1).toFixed(2)),
      epoch,
      recorded_at: recordedAt,
    });
    if (options.includeDpo) {
      dpoMargin.push({
        step,
        value: Number((0.15 + 1.72 * (1 - Math.exp(-2.6 * progress)) + Math.sin(i) * 0.04).toFixed(3)),
        epoch,
        recorded_at: recordedAt,
      });
    }
  }

  const out: Record<string, TrainingMetricPoint[]> = {
    "train/loss": trainLoss,
    "eval/loss": evalLoss,
    "eval/perplexity": evalPpl,
    "train/learning_rate": lr,
    "gpu/utilization": gpuUtil,
    "gpu/vram_gb": gpuVram,
  };
  if (options.includeDpo) {
    out["dpo/reward_margin"] = dpoMargin;
  }
  return out;
}

function buildInitialState(): MockMlopsState {
  const baseMs = Date.parse("2026-11-12T10:00:00.000Z");

  const runs: TrainingRun[] = [
    {
      id: "run-lora-01",
      project_id: "proj-default",
      name: "support-copilot-lora-v3",
      run_type: "lora",
      status: "completed",
      base_model: "meta-llama/Llama-3.1-8B-Instruct",
      dataset_id: "ds-support-sft",
      sweep_id: "sweep-bayes-01",
      parent_run_id: null,
      hyperparams: {
        r: 16,
        lora_alpha: 32,
        lora_dropout: 0.05,
        learning_rate: 2e-4,
        batch_size: 16,
        scheduler: "cosine",
        warmup_ratio: 0.05,
      },
      summary_metrics: {
        "train/loss": 0.412,
        "eval/loss": 0.468,
        "eval/perplexity": 1.597,
        "gpu/utilization": 92.4,
        "gpu/vram_gb": 58.4,
      },
      latest_metrics: {
        "train/loss": 0.412,
        "eval/loss": 0.468,
        "eval/perplexity": 1.597,
        "gpu/utilization": 92.4,
        "gpu/vram_gb": 58.4,
      },
      hardware_info: {
        gpu_name: "NVIDIA A100-SXM4-80GB",
        gpu_count: 2,
        gpu_memory_gb: 80,
        cuda_version: "12.4",
      },
      classic_eval: null,
      tags: ["production-candidate", "support", "lora"],
      total_steps: 1200,
      error: null,
      last_heartbeat_at: "2026-11-12T12:15:00.000Z",
      created_at: "2026-11-12T10:00:00.000Z",
      started_at: "2026-11-12T10:01:00.000Z",
      finished_at: "2026-11-12T12:15:00.000Z",
    },
    {
      id: "run-dpo-02",
      project_id: "proj-default",
      name: "support-copilot-dpo-align",
      run_type: "dpo",
      status: "running",
      base_model: "meta-llama/Llama-3.1-8B-Instruct",
      dataset_id: "ds-dpo-prefs",
      sweep_id: null,
      parent_run_id: "run-lora-01",
      hyperparams: {
        beta: 0.1,
        r: 16,
        lora_alpha: 32,
        learning_rate: 5e-5,
        batch_size: 8,
        scheduler: "cosine",
        max_prompt_length: 1024,
      },
      summary_metrics: {
        "train/loss": 0.318,
        "eval/loss": 0.354,
        "dpo/reward_margin": 1.84,
        "dpo/accuracy": 0.87,
        "gpu/utilization": 94.1,
        "gpu/vram_gb": 69.8,
      },
      latest_metrics: {
        "train/loss": 0.318,
        "eval/loss": 0.354,
        "dpo/reward_margin": 1.84,
        "dpo/accuracy": 0.87,
        "gpu/utilization": 94.1,
        "gpu/vram_gb": 69.8,
      },
      hardware_info: {
        gpu_name: "NVIDIA H100-SXM5-80GB",
        gpu_count: 2,
        gpu_memory_gb: 80,
        cuda_version: "12.4",
      },
      classic_eval: null,
      tags: ["dpo", "alignment", "active"],
      total_steps: 640,
      error: null,
      last_heartbeat_at: "2026-11-13T18:30:00.000Z",
      created_at: "2026-11-13T16:00:00.000Z",
      started_at: "2026-11-13T16:02:00.000Z",
      finished_at: null,
    },
    {
      id: "run-qlora-03",
      project_id: "proj-default",
      name: "mistral-7b-rag-qlora",
      run_type: "qlora",
      status: "completed",
      base_model: "mistralai/Mistral-7B-v0.3",
      dataset_id: "ds-rag-qa",
      sweep_id: "sweep-grid-02",
      parent_run_id: null,
      hyperparams: {
        r: 32,
        lora_alpha: 64,
        quantization: "nf4",
        learning_rate: 1.5e-4,
        batch_size: 16,
        scheduler: "linear",
      },
      summary_metrics: {
        "train/loss": 0.489,
        "eval/loss": 0.521,
        "eval/perplexity": 1.684,
        "gpu/utilization": 88.7,
        "gpu/vram_gb": 22.4,
      },
      latest_metrics: {
        "train/loss": 0.489,
        "eval/loss": 0.521,
        "eval/perplexity": 1.684,
        "gpu/utilization": 88.7,
        "gpu/vram_gb": 22.4,
      },
      hardware_info: {
        gpu_name: "NVIDIA L40S-48GB",
        gpu_count: 1,
        gpu_memory_gb: 48,
        cuda_version: "12.4",
      },
      classic_eval: null,
      tags: ["qlora", "rag", "nf4"],
      total_steps: 1000,
      error: null,
      last_heartbeat_at: "2026-11-11T19:45:00.000Z",
      created_at: "2026-11-11T17:00:00.000Z",
      started_at: "2026-11-11T17:01:00.000Z",
      finished_at: "2026-11-11T19:45:00.000Z",
    },
    {
      id: "run-classic-04",
      project_id: "proj-default",
      name: "churn-risk-xgboost-v1",
      run_type: "classic_ml",
      status: "completed",
      base_model: "xgboost-2.1-tabular",
      dataset_id: "ds-churn-tabular",
      sweep_id: null,
      parent_run_id: null,
      hyperparams: {
        max_depth: 6,
        n_estimators: 420,
        learning_rate: 0.04,
        subsample: 0.85,
        colsample_bytree: 0.8,
        reg_lambda: 1.5,
      },
      summary_metrics: {
        "eval/roc_auc": 0.942,
        "eval/pr_auc": 0.918,
        "eval/f1": 0.884,
        "eval/log_loss": 0.241,
      },
      latest_metrics: {
        "eval/roc_auc": 0.942,
        "eval/pr_auc": 0.918,
        "eval/f1": 0.884,
        "eval/log_loss": 0.241,
      },
      hardware_info: {
        cpu_count: 16,
        ram_gb: 64,
      },
      classic_eval: {
        roc_auc: 0.942,
        pr_auc: 0.918,
        accuracy: 0.895,
        f1: 0.884,
        precision: 0.889,
        recall: 0.869,
        log_loss: 0.241,
        roc_curve: [
          { fpr: 0.0, tpr: 0.0, threshold: 1.0 },
          { fpr: 0.02, tpr: 0.34, threshold: 0.9 },
          { fpr: 0.04, tpr: 0.58, threshold: 0.8 },
          { fpr: 0.07, tpr: 0.74, threshold: 0.7 },
          { fpr: 0.1, tpr: 0.84, threshold: 0.6 },
          { fpr: 0.14, tpr: 0.9, threshold: 0.5 },
          { fpr: 0.21, tpr: 0.94, threshold: 0.4 },
          { fpr: 0.32, tpr: 0.97, threshold: 0.3 },
          { fpr: 0.48, tpr: 0.985, threshold: 0.2 },
          { fpr: 0.72, tpr: 0.996, threshold: 0.1 },
          { fpr: 1.0, tpr: 1.0, threshold: 0.0 },
        ],
        confusion_matrix: {
          labels: ["Retained", "Churned"],
          matrix: [
            [412, 38],
            [46, 304],
          ],
        },
        feature_importance: [
          { feature: "support_tickets_30d", importance: 0.312, direction: "positive" },
          { feature: "days_since_last_login", importance: 0.248, direction: "positive" },
          { feature: "contract_tenure_months", importance: -0.194, direction: "negative" },
          { feature: "nps_last_score", importance: -0.156, direction: "negative" },
          { feature: "seat_utilization_pct", importance: -0.118, direction: "negative" },
          { feature: "failed_payments_90d", importance: 0.095, direction: "positive" },
          { feature: "api_error_rate_7d", importance: 0.072, direction: "positive" },
          { feature: "sso_enabled", importance: -0.049, direction: "negative" },
        ],
      },
      tags: ["tabular", "xgboost", "churn"],
      total_steps: 420,
      error: null,
      last_heartbeat_at: "2026-11-10T14:22:00.000Z",
      created_at: "2026-11-10T14:00:00.000Z",
      started_at: "2026-11-10T14:01:00.000Z",
      finished_at: "2026-11-10T14:22:00.000Z",
    },
    {
      id: "run-failed-05",
      project_id: "proj-default",
      name: "qwen-14b-full-sft-oom",
      run_type: "sft",
      status: "failed",
      base_model: "Qwen/Qwen2.5-14B",
      dataset_id: "ds-support-sft",
      sweep_id: null,
      parent_run_id: null,
      hyperparams: {
        learning_rate: 1e-4,
        batch_size: 32,
        gradient_checkpointing: false,
        bf16: true,
      },
      summary_metrics: {
        "train/loss": 1.842,
        "gpu/utilization": 99.2,
        "gpu/vram_gb": 79.1,
      },
      latest_metrics: {
        "train/loss": 1.842,
        "gpu/utilization": 99.2,
        "gpu/vram_gb": 79.1,
      },
      hardware_info: {
        gpu_name: "NVIDIA A100-SXM4-80GB",
        gpu_count: 1,
        gpu_memory_gb: 80,
        cuda_version: "12.4",
      },
      classic_eval: null,
      tags: ["oom", "sft"],
      total_steps: 140,
      error: "CUDA out of memory. Tried to allocate 2.40 GiB (GPU 0; 79.15 GiB total capacity)",
      last_heartbeat_at: "2026-11-09T09:14:00.000Z",
      created_at: "2026-11-09T09:00:00.000Z",
      started_at: "2026-11-09T09:02:00.000Z",
      finished_at: "2026-11-09T09:14:00.000Z",
    },
  ];

  const seriesByRun: Record<string, Record<string, TrainingMetricPoint[]>> = {
    "run-lora-01": makeLossSeries(1200, 1.92, 0.412, baseMs, { gpuUtilBase: 91, vramGb: 58.4 }),
    "run-dpo-02": makeLossSeries(640, 0.89, 0.318, baseMs + 86_400_000, {
      includeDpo: true,
      gpuUtilBase: 93,
      vramGb: 69.8,
    }),
    "run-qlora-03": makeLossSeries(1000, 2.08, 0.489, baseMs - 86_400_000, { gpuUtilBase: 88, vramGb: 22.4 }),
    "run-classic-04": {
      "eval/log_loss": [
        { step: 50, value: 0.512 },
        { step: 100, value: 0.398 },
        { step: 200, value: 0.312 },
        { step: 300, value: 0.264 },
        { step: 420, value: 0.241 },
      ],
      "eval/roc_auc": [
        { step: 50, value: 0.841 },
        { step: 100, value: 0.889 },
        { step: 200, value: 0.919 },
        { step: 300, value: 0.935 },
        { step: 420, value: 0.942 },
      ],
    },
    "run-failed-05": {
      "train/loss": [
        { step: 20, value: 2.41 },
        { step: 60, value: 2.15 },
        { step: 100, value: 1.98 },
        { step: 140, value: 1.842 },
      ],
      "gpu/utilization": [
        { step: 20, value: 94.0 },
        { step: 60, value: 97.5 },
        { step: 100, value: 98.8 },
        { step: 140, value: 99.2 },
      ],
      "gpu/vram_gb": [
        { step: 20, value: 68.4 },
        { step: 60, value: 74.2 },
        { step: 100, value: 77.9 },
        { step: 140, value: 79.1 },
      ],
    },
  };

  const bayesSweep: MLOpsSweep = {
    id: "sweep-bayes-01",
    project_id: "proj-default",
    name: "llama3-support-lora-bayes",
    description: "Bayesian optimization over LoRA rank, alpha, LR and scheduler with ASHA early stopping.",
    method: "bayes",
    metric_name: "eval/loss",
    metric_goal: "minimize",
    search_space: {
      learning_rate: { type: "float", min: 1e-5, max: 5e-4, log: true },
      r: { type: "categorical", values: [8, 16, 32, 64] },
      lora_dropout: { type: "float", min: 0.0, max: 0.15 },
      scheduler: { type: "categorical", values: ["cosine", "linear", "constant"] },
    },
    early_stopping: {
      strategy: "asha",
      min_step: 150,
      reduction_factor: 3,
      min_resource: 150,
      max_resource: 1200,
    },
    max_trials: 16,
    status: "running",
    best_run_id: "run-lora-01",
    best_metric_value: 0.468,
    created_at: "2026-11-12T08:00:00.000Z",
    stopped_at: null,
  };

  const bayesTrialConfigs: Array<{
    id: string;
    name: string;
    status: TrainingRun["status"];
    lr: number;
    r: number;
    dropout: number;
    scheduler: string;
    loss: number | null;
  }> = [
    { id: "run-sw1-01", name: "bayes-trial-01", status: "completed", lr: 1.2e-4, r: 8, dropout: 0.08, scheduler: "linear", loss: 0.542 },
    { id: "run-sw1-02", name: "bayes-trial-02", status: "stopped_early", lr: 4.5e-4, r: 64, dropout: 0.12, scheduler: "constant", loss: 0.691 },
    { id: "run-sw1-03", name: "bayes-trial-03", status: "completed", lr: 1.8e-4, r: 16, dropout: 0.04, scheduler: "cosine", loss: 0.489 },
    { id: "run-sw1-04", name: "bayes-trial-04", status: "stopped_early", lr: 2.1e-5, r: 8, dropout: 0.14, scheduler: "constant", loss: 0.715 },
    { id: "run-lora-01", name: "support-copilot-lora-v3", status: "completed", lr: 2.0e-4, r: 16, dropout: 0.05, scheduler: "cosine", loss: 0.468 },
    { id: "run-sw1-06", name: "bayes-trial-06", status: "completed", lr: 2.4e-4, r: 32, dropout: 0.03, scheduler: "cosine", loss: 0.476 },
    { id: "run-sw1-07", name: "bayes-trial-07", status: "stopped_early", lr: 4.1e-4, r: 32, dropout: 0.11, scheduler: "linear", loss: 0.638 },
    { id: "run-sw1-08", name: "bayes-trial-08", status: "completed", lr: 1.6e-4, r: 16, dropout: 0.06, scheduler: "cosine", loss: 0.481 },
    { id: "run-sw1-09", name: "bayes-trial-09", status: "completed", lr: 2.9e-4, r: 32, dropout: 0.02, scheduler: "linear", loss: 0.504 },
    { id: "run-sw1-10", name: "bayes-trial-10", status: "running", lr: 1.9e-4, r: 16, dropout: 0.04, scheduler: "cosine", loss: 0.492 },
  ];

  const bayesTrialRuns: TrainingRun[] = bayesTrialConfigs.map((t, idx) => {
    const metrics: Record<string, number> = t.loss !== null ? { "eval/loss": t.loss } : {};
    return {
      id: t.id,
      project_id: "proj-default",
      name: t.name,
      run_type: "lora",
      status: t.status,
      base_model: "meta-llama/Llama-3.1-8B-Instruct",
      dataset_id: "ds-support-sft",
      sweep_id: bayesSweep.id,
      parent_run_id: null,
      hyperparams: {
        learning_rate: t.lr,
        r: t.r,
        lora_dropout: t.dropout,
        scheduler: t.scheduler,
      },
      summary_metrics: metrics,
      latest_metrics: metrics,
      hardware_info: { gpu_name: "NVIDIA A100-SXM4-80GB", gpu_count: 1 },
      classic_eval: null,
      tags: ["sweep", "bayes"],
      total_steps: t.status === "stopped_early" ? 300 : t.status === "running" ? 600 : 1200,
      error: null,
      last_heartbeat_at: new Date(baseMs + idx * 1_800_000).toISOString(),
      created_at: new Date(baseMs + idx * 1_800_000).toISOString(),
      started_at: new Date(baseMs + idx * 1_800_000 + 30_000).toISOString(),
      finished_at: t.status === "running" ? null : new Date(baseMs + idx * 1_800_000 + 1_200_000).toISOString(),
    };
  });

  const gridSweep: MLOpsSweep = {
    id: "sweep-grid-02",
    project_id: "proj-default",
    name: "mistral-qlora-rank-grid",
    description: "Exhaustive grid over LoRA rank and learning rate for Mistral 7B QLoRA.",
    method: "grid",
    metric_name: "eval/loss",
    metric_goal: "minimize",
    search_space: {
      r: { type: "categorical", values: [16, 32, 64] },
      learning_rate: { type: "categorical", values: [1e-4, 1.5e-4] },
    },
    early_stopping: { strategy: "none" },
    max_trials: 6,
    status: "completed",
    best_run_id: "run-qlora-03",
    best_metric_value: 0.521,
    created_at: "2026-11-11T12:00:00.000Z",
    stopped_at: "2026-11-11T20:00:00.000Z",
  };

  const gridTrialConfigs = [
    { id: "run-sw2-01", name: "grid-r16-1e4", r: 16, lr: 1e-4, loss: 0.562 },
    { id: "run-sw2-02", name: "grid-r16-15e5", r: 16, lr: 1.5e-4, loss: 0.548 },
    { id: "run-sw2-03", name: "grid-r32-1e4", r: 32, lr: 1e-4, loss: 0.534 },
    { id: "run-qlora-03", name: "mistral-7b-rag-qlora", r: 32, lr: 1.5e-4, loss: 0.521 },
    { id: "run-sw2-05", name: "grid-r64-1e4", r: 64, lr: 1e-4, loss: 0.541 },
    { id: "run-sw2-06", name: "grid-r64-15e5", r: 64, lr: 1.5e-4, loss: 0.553 },
  ];

  const gridTrialRuns: TrainingRun[] = gridTrialConfigs.map((t, idx) => ({
    id: t.id,
    project_id: "proj-default",
    name: t.name,
    run_type: "qlora",
    status: "completed",
    base_model: "mistralai/Mistral-7B-v0.3",
    dataset_id: "ds-rag-qa",
    sweep_id: gridSweep.id,
    parent_run_id: null,
    hyperparams: { r: t.r, learning_rate: t.lr },
    summary_metrics: { "eval/loss": t.loss },
    latest_metrics: { "eval/loss": t.loss },
    hardware_info: { gpu_name: "NVIDIA L40S-48GB", gpu_count: 1 },
    classic_eval: null,
    tags: ["sweep", "grid"],
    total_steps: 1000,
    error: null,
    last_heartbeat_at: new Date(baseMs - 86_400_000 + idx * 1_500_000).toISOString(),
    created_at: new Date(baseMs - 86_400_000 + idx * 1_500_000).toISOString(),
    started_at: new Date(baseMs - 86_400_000 + idx * 1_500_000 + 20_000).toISOString(),
    finished_at: new Date(baseMs - 86_400_000 + idx * 1_500_000 + 1_100_000).toISOString(),
  }));

  const sweepAnalysis: Record<string, SweepAnalysis> = {
    [bayesSweep.id]: {
      metric_name: "eval/loss",
      metric_goal: "minimize",
      importance: [
        { name: "learning_rate", importance: 0.44, method: "spearman" },
        { name: "scheduler", importance: 0.27, method: "anova_eta2" },
        { name: "r", importance: 0.18, method: "anova_eta2" },
        { name: "lora_dropout", importance: 0.11, method: "spearman" },
      ],
      parallel_coordinates: {
        axes: [
          { name: "learning_rate", type: "float", min: 1e-5, max: 5e-4, log: true },
          { name: "r", type: "categorical", values: [8, 16, 32, 64] },
          { name: "lora_dropout", type: "float", min: 0.0, max: 0.15, log: false },
          { name: "scheduler", type: "categorical", values: ["cosine", "linear", "constant"] },
          { name: "eval/loss", type: "float", min: 0.468, max: 0.715, goal: "minimize" },
        ],
        trials: bayesTrialConfigs.map((t) => ({
          run_id: t.id,
          run_name: t.name,
          status: t.status,
          params: {
            learning_rate: t.lr,
            r: t.r,
            lora_dropout: t.dropout,
            scheduler: t.scheduler,
          },
          metric_value: t.loss,
        })),
      },
    },
    [gridSweep.id]: {
      metric_name: "eval/loss",
      metric_goal: "minimize",
      importance: [
        { name: "r", importance: 0.62, method: "anova_eta2" },
        { name: "learning_rate", importance: 0.38, method: "anova_eta2" },
      ],
      parallel_coordinates: {
        axes: [
          { name: "r", type: "categorical", values: [16, 32, 64] },
          { name: "learning_rate", type: "categorical", values: [1e-4, 1.5e-4] },
          { name: "eval/loss", type: "float", min: 0.521, max: 0.562, goal: "minimize" },
        ],
        trials: gridTrialConfigs.map((t) => ({
          run_id: t.id,
          run_name: t.name,
          status: "completed",
          params: { r: t.r, learning_rate: t.lr },
          metric_value: t.loss,
        })),
      },
    },
  };

  const artifacts: ArtifactRecord[] = [
    {
      id: "art-lora-v3",
      project_id: "proj-default",
      run_id: "run-lora-01",
      name: "support-copilot-lora-adapter",
      kind: "lora_adapter",
      version: 3,
      storage_backend: "s3",
      storage_uri: "s3://matrix-artifacts/proj-default/a3f9c2d1e4b8901234567890abcdef1234567890abcdef1234567890abcdef12",
      sha256: "a3f9c2d1e4b8901234567890abcdef1234567890abcdef1234567890abcdef12",
      size_bytes: 168_427_520,
      content_type: "application/octet-stream",
      status: "committed",
      metadata: { format: "safetensors", files: ["adapter_model.safetensors", "adapter_config.json"] },
      created_at: "2026-11-12T12:14:00.000Z",
      committed_at: "2026-11-12T12:15:00.000Z",
    },
    {
      id: "art-llm-v2",
      project_id: "proj-default",
      run_id: "run-qlora-03",
      name: "matrix-rag-merged-8b-weights",
      kind: "model_weights",
      version: 2,
      storage_backend: "s3",
      storage_uri: "s3://matrix-artifacts/proj-default/b7c8d9e0f1a234567890abcdef1234567890abcdef1234567890abcdef123456",
      sha256: "b7c8d9e0f1a234567890abcdef1234567890abcdef1234567890abcdef123456",
      size_bytes: 15_420_882_944,
      content_type: "application/octet-stream",
      status: "committed",
      metadata: { format: "safetensors", dtype: "bfloat16" },
      created_at: "2026-11-11T19:40:00.000Z",
      committed_at: "2026-11-11T19:44:00.000Z",
    },
    {
      id: "art-cls-v1",
      project_id: "proj-default",
      run_id: "run-classic-04",
      name: "churn-risk-xgboost-model",
      kind: "classic_model",
      version: 1,
      storage_backend: "local",
      storage_uri: "local://proj-default/c9d0e1f2a3b4567890abcdef1234567890abcdef1234567890abcdef12345678",
      sha256: "c9d0e1f2a3b4567890abcdef1234567890abcdef1234567890abcdef12345678",
      size_bytes: 4_823_040,
      content_type: "application/octet-stream",
      status: "committed",
      metadata: { format: "onnx", opset: 17 },
      created_at: "2026-11-10T14:21:00.000Z",
      committed_at: "2026-11-10T14:22:00.000Z",
    },
  ];

  const models: RegisteredModel[] = [
    {
      id: "model-lora-01",
      project_id: "proj-default",
      name: "support-copilot-lora",
      description: "LoRA adapter for customer support triage, refund policy adherence, and tool calling.",
      model_type: "lora",
      base_model: "meta-llama/Llama-3.1-8B-Instruct",
      tags: ["support", "lora", "production"],
      created_at: "2026-11-08T10:00:00.000Z",
      updated_at: "2026-11-12T12:30:00.000Z",
      latest_version: 3,
      aliases: { champion: 3, challenger: 2 },
      stages: { archived: [1], staging: [2], production: [3] },
      latest_metrics: { "eval/loss": 0.468, "eval/perplexity": 1.597, safety_score: 96.2 },
    },
    {
      id: "model-llm-02",
      project_id: "proj-default",
      name: "matrix-rag-merged-8b",
      description: "Merged 8B weights fine-tuned for grounded enterprise RAG synthesis with citations.",
      model_type: "llm",
      base_model: "mistralai/Mistral-7B-v0.3",
      tags: ["rag", "merged", "citations"],
      created_at: "2026-11-09T11:00:00.000Z",
      updated_at: "2026-11-11T20:10:00.000Z",
      latest_version: 2,
      aliases: { challenger: 2 },
      stages: { archived: [1], staging: [2] },
      latest_metrics: { "eval/loss": 0.521, faithfulness: 0.91 },
    },
    {
      id: "model-cls-03",
      project_id: "proj-default",
      name: "churn-risk-xgboost",
      description: "Tabular gradient boosted classifier predicting 30-day enterprise account churn.",
      model_type: "classifier",
      base_model: "xgboost-2.1-tabular",
      tags: ["tabular", "churn", "onnx"],
      created_at: "2026-11-10T14:30:00.000Z",
      updated_at: "2026-11-10T15:00:00.000Z",
      latest_version: 1,
      aliases: { champion: 1 },
      stages: { production: [1] },
      latest_metrics: { roc_auc: 0.942, f1: 0.884, pr_auc: 0.918 },
    },
  ];

  const versionsByModel: Record<string, ModelVersion[]> = {
    "model-lora-01": [
      {
        id: "mv-lora-v3",
        project_id: "proj-default",
        model_id: "model-lora-01",
        version: 3,
        artifact_id: "art-lora-v3",
        run_id: "run-lora-01",
        eval_experiment_id: "exp-support-v3",
        safety_scan_id: "scan-owasp-01",
        stage: "production",
        lora_config: { r: 16, lora_alpha: 32, lora_dropout: 0.05, target_modules: ["q_proj", "v_proj", "k_proj", "o_proj"] },
        signature: {
          inputs: [{ name: "messages", dtype: "chat_messages" }],
          outputs: [{ name: "completion", dtype: "string" }],
        },
        metrics_snapshot: { "eval/loss": 0.468, "eval/perplexity": 1.597, safety_score: 96.2 },
        model_card: "Trained for 1,200 steps on `ds-support-sft` with cosine schedule. Verified against OWASP LLM Top 10.",
        created_by: "admin@matrix.local",
        created_at: "2026-11-12T12:20:00.000Z",
      },
      {
        id: "mv-lora-v2",
        project_id: "proj-default",
        model_id: "model-lora-01",
        version: 2,
        artifact_id: "art-lora-v3",
        run_id: "run-sw1-03",
        eval_experiment_id: "exp-support-v2",
        safety_scan_id: "scan-owasp-01",
        stage: "staging",
        lora_config: { r: 16, lora_alpha: 32, lora_dropout: 0.04, target_modules: ["q_proj", "v_proj"] },
        signature: {
          inputs: [{ name: "messages", dtype: "chat_messages" }],
          outputs: [{ name: "completion", dtype: "string" }],
        },
        metrics_snapshot: { "eval/loss": 0.489, "eval/perplexity": 1.631, safety_score: 94.5 },
        model_card: "Staging candidate from Bayesian sweep trial 3.",
        created_by: "admin@matrix.local",
        created_at: "2026-11-11T16:10:00.000Z",
      },
      {
        id: "mv-lora-v1",
        project_id: "proj-default",
        model_id: "model-lora-01",
        version: 1,
        artifact_id: "art-lora-v3",
        run_id: "run-sw1-01",
        eval_experiment_id: null,
        safety_scan_id: null,
        stage: "archived",
        lora_config: { r: 8, lora_alpha: 16, lora_dropout: 0.08, target_modules: ["q_proj", "v_proj"] },
        signature: {
          inputs: [{ name: "messages", dtype: "chat_messages" }],
          outputs: [{ name: "completion", dtype: "string" }],
        },
        metrics_snapshot: { "eval/loss": 0.542, "eval/perplexity": 1.719 },
        model_card: "Initial baseline LoRA adapter.",
        created_by: "admin@matrix.local",
        created_at: "2026-11-08T10:15:00.000Z",
      },
    ],
    "model-llm-02": [
      {
        id: "mv-llm-v2",
        project_id: "proj-default",
        model_id: "model-llm-02",
        version: 2,
        artifact_id: "art-llm-v2",
        run_id: "run-qlora-03",
        eval_experiment_id: "exp-rag-v2",
        safety_scan_id: "scan-owasp-02",
        stage: "staging",
        lora_config: null,
        signature: {
          inputs: [{ name: "prompt", dtype: "string" }],
          outputs: [{ name: "answer", dtype: "string" }],
        },
        metrics_snapshot: { "eval/loss": 0.521, faithfulness: 0.91 },
        model_card: "Merged Mistral 7B RAG model exported in bfloat16 safetensors.",
        created_by: "admin@matrix.local",
        created_at: "2026-11-11T20:05:00.000Z",
      },
      {
        id: "mv-llm-v1",
        project_id: "proj-default",
        model_id: "model-llm-02",
        version: 1,
        artifact_id: "art-llm-v2",
        run_id: "run-sw2-01",
        eval_experiment_id: null,
        safety_scan_id: null,
        stage: "archived",
        lora_config: null,
        signature: null,
        metrics_snapshot: { "eval/loss": 0.562, faithfulness: 0.84 },
        model_card: "Initial merged checkpoint.",
        created_by: "admin@matrix.local",
        created_at: "2026-11-09T11:30:00.000Z",
      },
    ],
    "model-cls-03": [
      {
        id: "mv-cls-v1",
        project_id: "proj-default",
        model_id: "model-cls-03",
        version: 1,
        artifact_id: "art-cls-v1",
        run_id: "run-classic-04",
        eval_experiment_id: "exp-churn-v1",
        safety_scan_id: null,
        stage: "production",
        lora_config: null,
        signature: {
          inputs: [
            { name: "support_tickets_30d", dtype: "int64", shape: [-1, 1] },
            { name: "days_since_last_login", dtype: "float32", shape: [-1, 1] },
            { name: "contract_tenure_months", dtype: "int64", shape: [-1, 1] },
          ],
          outputs: [{ name: "churn_probability", dtype: "float32", shape: [-1, 2] }],
        },
        metrics_snapshot: { roc_auc: 0.942, f1: 0.884, pr_auc: 0.918 },
        model_card: "XGBoost 2.1 classifier exported to ONNX opset 17.",
        created_by: "admin@matrix.local",
        created_at: "2026-11-10T14:35:00.000Z",
      },
    ],
  };

  const aliasesByModel: Record<string, Record<string, number>> = {
    "model-lora-01": { champion: 3, challenger: 2 },
    "model-llm-02": { challenger: 2 },
    "model-cls-03": { champion: 1 },
  };

  const loraLineageNodes: LineageNode[] = [
    {
      id: "dataset:ds-support-sft",
      node_type: "dataset",
      entity_id: "ds-support-sft",
      label: "ds-support-sft",
      metadata: { description: "Curated customer support SFT dataset (18.4k rows)" },
    },
    {
      id: "run:run-lora-01",
      node_type: "run",
      entity_id: "run-lora-01",
      label: "support-copilot-lora-v3",
      metadata: { run_type: "lora", status: "completed", base_model: "meta-llama/Llama-3.1-8B-Instruct" },
    },
    {
      id: "artifact:art-lora-v3",
      node_type: "artifact",
      entity_id: "art-lora-v3",
      label: "support-copilot-lora-adapter:v3",
      metadata: {
        kind: "lora_adapter",
        sha256: "a3f9c2d1e4b8901234567890abcdef1234567890abcdef1234567890abcdef12",
        size_bytes: 168_427_520,
        status: "committed",
      },
    },
    {
      id: "model_version:mv-lora-v3",
      node_type: "model_version",
      entity_id: "mv-lora-v3",
      label: "support-copilot-lora v3",
      metadata: { model_id: "model-lora-01", version: 3, stage: "production" },
    },
    {
      id: "experiment:exp-support-v3",
      node_type: "experiment",
      entity_id: "exp-support-v3",
      label: "Support Eval Suite v3",
      metadata: { status: "done" },
    },
    {
      id: "safety_scan:scan-owasp-01",
      node_type: "safety_scan",
      entity_id: "scan-owasp-01",
      label: "SafetyScan (completed)",
      metadata: { status: "completed", global_score: 96.2 },
    },
  ];

  const loraLineageEdges: LineageEdge[] = [
    {
      id: "e-lora-1",
      source: "dataset:ds-support-sft",
      target: "run:run-lora-01",
      source_type: "dataset",
      source_id: "ds-support-sft",
      target_type: "run",
      target_id: "run-lora-01",
      relation: "trained_on",
      metadata: {},
    },
    {
      id: "e-lora-2",
      source: "run:run-lora-01",
      target: "artifact:art-lora-v3",
      source_type: "run",
      source_id: "run-lora-01",
      target_type: "artifact",
      target_id: "art-lora-v3",
      relation: "produced",
      metadata: {},
    },
    {
      id: "e-lora-3",
      source: "artifact:art-lora-v3",
      target: "model_version:mv-lora-v3",
      source_type: "artifact",
      source_id: "art-lora-v3",
      target_type: "model_version",
      target_id: "mv-lora-v3",
      relation: "packaged_as",
      metadata: {},
    },
    {
      id: "e-lora-4",
      source: "run:run-lora-01",
      target: "model_version:mv-lora-v3",
      source_type: "run",
      source_id: "run-lora-01",
      target_type: "model_version",
      target_id: "mv-lora-v3",
      relation: "registered_as",
      metadata: {},
    },
    {
      id: "e-lora-5",
      source: "model_version:mv-lora-v3",
      target: "experiment:exp-support-v3",
      source_type: "model_version",
      source_id: "mv-lora-v3",
      target_type: "experiment",
      target_id: "exp-support-v3",
      relation: "evaluated_by",
      metadata: {},
    },
    {
      id: "e-lora-6",
      source: "model_version:mv-lora-v3",
      target: "safety_scan:scan-owasp-01",
      source_type: "model_version",
      source_id: "mv-lora-v3",
      target_type: "safety_scan",
      target_id: "scan-owasp-01",
      relation: "audited_by",
      metadata: {},
    },
  ];

  const llmLineage: LineageGraph = {
    model_id: "model-llm-02",
    nodes: [
      {
        id: "dataset:ds-rag-qa",
        node_type: "dataset",
        entity_id: "ds-rag-qa",
        label: "ds-rag-qa",
        metadata: { description: "Enterprise RAG QA dataset" },
      },
      {
        id: "run:run-qlora-03",
        node_type: "run",
        entity_id: "run-qlora-03",
        label: "mistral-7b-rag-qlora",
        metadata: { run_type: "qlora", status: "completed", base_model: "mistralai/Mistral-7B-v0.3" },
      },
      {
        id: "artifact:art-llm-v2",
        node_type: "artifact",
        entity_id: "art-llm-v2",
        label: "matrix-rag-merged-8b-weights:v2",
        metadata: {
          kind: "model_weights",
          sha256: "b7c8d9e0f1a234567890abcdef1234567890abcdef1234567890abcdef123456",
          size_bytes: 15_420_882_944,
          status: "committed",
        },
      },
      {
        id: "model_version:mv-llm-v2",
        node_type: "model_version",
        entity_id: "mv-llm-v2",
        label: "matrix-rag-merged-8b v2",
        metadata: { model_id: "model-llm-02", version: 2, stage: "staging" },
      },
      {
        id: "experiment:exp-rag-v2",
        node_type: "experiment",
        entity_id: "exp-rag-v2",
        label: "RAG Groundedness Eval",
        metadata: { status: "done" },
      },
      {
        id: "safety_scan:scan-owasp-02",
        node_type: "safety_scan",
        entity_id: "scan-owasp-02",
        label: "SafetyScan (completed)",
        metadata: { status: "completed", global_score: 93.8 },
      },
    ],
    edges: [
      {
        id: "e-llm-1",
        source: "dataset:ds-rag-qa",
        target: "run:run-qlora-03",
        source_type: "dataset",
        source_id: "ds-rag-qa",
        target_type: "run",
        target_id: "run-qlora-03",
        relation: "trained_on",
        metadata: {},
      },
      {
        id: "e-llm-2",
        source: "run:run-qlora-03",
        target: "artifact:art-llm-v2",
        source_type: "run",
        source_id: "run-qlora-03",
        target_type: "artifact",
        target_id: "art-llm-v2",
        relation: "produced",
        metadata: {},
      },
      {
        id: "e-llm-3",
        source: "artifact:art-llm-v2",
        target: "model_version:mv-llm-v2",
        source_type: "artifact",
        source_id: "art-llm-v2",
        target_type: "model_version",
        target_id: "mv-llm-v2",
        relation: "packaged_as",
        metadata: {},
      },
      {
        id: "e-llm-4",
        source: "model_version:mv-llm-v2",
        target: "experiment:exp-rag-v2",
        source_type: "model_version",
        source_id: "mv-llm-v2",
        target_type: "experiment",
        target_id: "exp-rag-v2",
        relation: "evaluated_by",
        metadata: {},
      },
      {
        id: "e-llm-5",
        source: "model_version:mv-llm-v2",
        target: "safety_scan:scan-owasp-02",
        source_type: "model_version",
        source_id: "mv-llm-v2",
        target_type: "safety_scan",
        target_id: "scan-owasp-02",
        relation: "audited_by",
        metadata: {},
      },
    ],
  };

  const clsLineage: LineageGraph = {
    model_id: "model-cls-03",
    nodes: [
      {
        id: "dataset:ds-churn-tabular",
        node_type: "dataset",
        entity_id: "ds-churn-tabular",
        label: "ds-churn-tabular",
        metadata: { description: "Enterprise account churn features (42k rows)" },
      },
      {
        id: "run:run-classic-04",
        node_type: "run",
        entity_id: "run-classic-04",
        label: "churn-risk-xgboost-v1",
        metadata: { run_type: "classic_ml", status: "completed", base_model: "xgboost-2.1-tabular" },
      },
      {
        id: "artifact:art-cls-v1",
        node_type: "artifact",
        entity_id: "art-cls-v1",
        label: "churn-risk-xgboost-model:v1",
        metadata: {
          kind: "classic_model",
          sha256: "c9d0e1f2a3b4567890abcdef1234567890abcdef1234567890abcdef12345678",
          size_bytes: 4_823_040,
          status: "committed",
        },
      },
      {
        id: "model_version:mv-cls-v1",
        node_type: "model_version",
        entity_id: "mv-cls-v1",
        label: "churn-risk-xgboost v1",
        metadata: { model_id: "model-cls-03", version: 1, stage: "production" },
      },
      {
        id: "experiment:exp-churn-v1",
        node_type: "experiment",
        entity_id: "exp-churn-v1",
        label: "Churn Holdout Eval",
        metadata: { status: "done" },
      },
    ],
    edges: [
      {
        id: "e-cls-1",
        source: "dataset:ds-churn-tabular",
        target: "run:run-classic-04",
        source_type: "dataset",
        source_id: "ds-churn-tabular",
        target_type: "run",
        target_id: "run-classic-04",
        relation: "trained_on",
        metadata: {},
      },
      {
        id: "e-cls-2",
        source: "run:run-classic-04",
        target: "artifact:art-cls-v1",
        source_type: "run",
        source_id: "run-classic-04",
        target_type: "artifact",
        target_id: "art-cls-v1",
        relation: "produced",
        metadata: {},
      },
      {
        id: "e-cls-3",
        source: "artifact:art-cls-v1",
        target: "model_version:mv-cls-v1",
        source_type: "artifact",
        source_id: "art-cls-v1",
        target_type: "model_version",
        target_id: "mv-cls-v1",
        relation: "packaged_as",
        metadata: {},
      },
      {
        id: "e-cls-4",
        source: "model_version:mv-cls-v1",
        target: "experiment:exp-churn-v1",
        source_type: "model_version",
        source_id: "mv-cls-v1",
        target_type: "experiment",
        target_id: "exp-churn-v1",
        relation: "evaluated_by",
        metadata: {},
      },
    ],
  };

  return {
    runs,
    seriesByRun,
    sweeps: [bayesSweep, gridSweep],
    sweepTrials: {
      [bayesSweep.id]: bayesTrialRuns,
      [gridSweep.id]: gridTrialRuns,
    },
    sweepAnalysis,
    artifacts,
    models,
    versionsByModel,
    aliasesByModel,
    runLineage: {
      "run-lora-01": { run_id: "run-lora-01", nodes: loraLineageNodes, edges: loraLineageEdges },
      "run-qlora-03": { run_id: "run-qlora-03", nodes: llmLineage.nodes, edges: llmLineage.edges },
      "run-classic-04": { run_id: "run-classic-04", nodes: clsLineage.nodes, edges: clsLineage.edges },
    },
    modelLineage: {
      "model-lora-01": { model_id: "model-lora-01", nodes: loraLineageNodes, edges: loraLineageEdges },
      "model-llm-02": llmLineage,
      "model-cls-03": clsLineage,
    },
    seq: 100,
  };
}

const g = globalThis as unknown as { __matrixMockMlops?: MockMlopsState };

function state(): MockMlopsState {
  if (!g.__matrixMockMlops) {
    g.__matrixMockMlops = buildInitialState();
  }
  return g.__matrixMockMlops;
}

export function resetMockMlops(): void {
  g.__matrixMockMlops = buildInitialState();
}

function nextId(prefix: string): string {
  const s = state();
  s.seq += 1;
  return `${prefix}-${s.seq}`;
}

function syncModelSummary(model: RegisteredModel): void {
  const s = state();
  const versions = s.versionsByModel[model.id] ?? [];
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  s.versionsByModel[model.id] = sorted;

  const latest = sorted[0] ?? null;
  const stages: Record<string, number[]> = {};
  for (const mv of sorted) {
    if (!stages[mv.stage]) stages[mv.stage] = [];
    stages[mv.stage].push(mv.version);
  }
  model.latest_version = latest ? latest.version : null;
  model.aliases = { ...(s.aliasesByModel[model.id] ?? {}) };
  model.stages = stages;
  model.latest_metrics = latest ? { ...latest.metrics_snapshot } : {};
  model.updated_at = new Date().toISOString();
}

export function mlopsGet(path: string, params: Params = {}): unknown {
  const s = state();
  let m: RegExpExecArray | null;

  if (path === "/api/v1/mlops/runs") {
    let items = [...s.runs];
    if (params.status) items = items.filter((r) => r.status === String(params.status));
    if (params.sweep_id) items = items.filter((r) => r.sweep_id === String(params.sweep_id));
    if (params.run_type) items = items.filter((r) => r.run_type === String(params.run_type));
    if (params.base_model) items = items.filter((r) => r.base_model === String(params.base_model));
    const limit = Math.min(500, Math.max(1, Number(params.limit ?? 50)));
    const offset = Number(params.cursor ?? 0) || 0;
    const slice = items.slice(offset, offset + limit);
    return {
      items: slice,
      next_cursor: offset + limit < items.length ? String(offset + limit) : null,
    };
  }

  if ((m = /^\/api\/v1\/mlops\/runs\/([^/]+)\/metrics$/.exec(path))) {
    const runId = m[1];
    const run = s.runs.find((r) => r.id === runId);
    if (!run) throw new ApiError(404, "training run not found");
    const allSeries = s.seriesByRun[runId] ?? {};
    const requestedNames = params.metric_names
      ? String(params.metric_names)
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean)
      : null;
    const minStep = params.min_step !== undefined ? Number(params.min_step) : -Infinity;
    const maxStep = params.max_step !== undefined ? Number(params.max_step) : Infinity;

    const series: Record<string, TrainingMetricPoint[]> = {};
    for (const [k, pts] of Object.entries(allSeries)) {
      if (requestedNames && !requestedNames.includes(k)) continue;
      series[k] = pts.filter((pt) => pt.step >= minStep && pt.step <= maxStep);
    }
    const resp: TrainingMetricSeries = { run_id: runId, series };
    return resp;
  }

  if ((m = /^\/api\/v1\/mlops\/runs\/([^/]+)\/lineage$/.exec(path))) {
    const runId = m[1];
    const run = s.runs.find((r) => r.id === runId);
    if (!run) throw new ApiError(404, "training run not found");
    return (
      s.runLineage[runId] ?? {
        run_id: runId,
        nodes: [
          {
            id: `run:${run.id}`,
            node_type: "run",
            entity_id: run.id,
            label: run.name,
            metadata: { run_type: run.run_type, status: run.status, base_model: run.base_model },
          },
        ],
        edges: [],
      }
    );
  }

  if ((m = /^\/api\/v1\/mlops\/runs\/([^/]+)$/.exec(path))) {
    const runId = m[1];
    const run = s.runs.find((r) => r.id === runId);
    if (!run) throw new ApiError(404, "training run not found");
    const artifacts = s.artifacts.filter((a) => a.run_id === run.id);
    return { ...run, artifacts };
  }

  if (path === "/api/v1/mlops/sweeps") {
    return { items: [...s.sweeps] };
  }

  if ((m = /^\/api\/v1\/mlops\/sweeps\/([^/]+)$/.exec(path))) {
    const sweepId = m[1];
    const sweep = s.sweeps.find((sw) => sw.id === sweepId);
    if (!sweep) throw new ApiError(404, "sweep not found");
    const detail: SweepDetail = {
      ...sweep,
      trials: s.sweepTrials[sweepId] ?? [],
      analysis: s.sweepAnalysis[sweepId] ?? {
        metric_name: sweep.metric_name,
        metric_goal: sweep.metric_goal,
        importance: [],
        parallel_coordinates: { axes: [], trials: [] },
      },
    };
    return detail;
  }

  if (path === "/api/v1/mlops/artifacts") {
    let items = [...s.artifacts];
    if (params.run_id) items = items.filter((a) => a.run_id === String(params.run_id));
    if (params.kind) items = items.filter((a) => a.kind === String(params.kind));
    if (params.status) items = items.filter((a) => a.status === String(params.status));
    if (params.name) items = items.filter((a) => a.name === String(params.name));
    return { items };
  }

  if ((m = /^\/api\/v1\/mlops\/artifacts\/([^/]+)\/download-url$/.exec(path))) {
    const art = s.artifacts.find((a) => a.id === m![1]);
    if (!art) throw new ApiError(404, "artifact not found");
    const resp: ArtifactDownloadUrlResponse = {
      artifact_id: art.id,
      download_url: `https://mock-storage.matrix.local/download/${art.id}?sha256=${art.sha256}`,
      storage_backend: art.storage_backend,
      sha256: art.sha256,
      size_bytes: art.size_bytes,
    };
    return resp;
  }

  if (path === "/api/v1/mlops/models") {
    return { items: s.models.map((mod) => ({ ...mod })) };
  }

  if ((m = /^\/api\/v1\/mlops\/models\/([^/]+)\/lineage$/.exec(path))) {
    const modelId = m[1];
    const mod = s.models.find((x) => x.id === modelId);
    if (!mod) throw new ApiError(404, "registered model not found");
    return s.modelLineage[modelId] ?? { model_id: modelId, nodes: [], edges: [] };
  }

  if ((m = /^\/api\/v1\/mlops\/models\/([^/]+)$/.exec(path))) {
    const modelId = m[1];
    const mod = s.models.find((x) => x.id === modelId);
    if (!mod) throw new ApiError(404, "registered model not found");
    const detail: RegisteredModelDetail = {
      ...mod,
      versions: [...(s.versionsByModel[modelId] ?? [])],
      aliases: { ...(s.aliasesByModel[modelId] ?? {}) },
    };
    return detail;
  }

  return undefined;
}

export function mlopsSend(method: string, path: string, body?: unknown): unknown {
  const s = state();
  let m: RegExpExecArray | null;

  if (method === "POST" && path === "/api/v1/mlops/runs") {
    const req = (body ?? {}) as CreateTrainingRunRequest;
    const now = new Date().toISOString();
    const run: TrainingRun = {
      id: nextId("run"),
      project_id: "proj-default",
      name: req.name || "untitled-run",
      run_type: req.run_type ?? "sft",
      status: req.status ?? "running",
      base_model: req.base_model ?? null,
      dataset_id: req.dataset_id ?? null,
      sweep_id: req.sweep_id ?? null,
      parent_run_id: req.parent_run_id ?? null,
      hyperparams: req.hyperparams ?? {},
      summary_metrics: {},
      latest_metrics: {},
      hardware_info: req.hardware_info ?? null,
      classic_eval: req.classic_eval ?? null,
      tags: req.tags ?? [],
      total_steps: 0,
      error: null,
      last_heartbeat_at: now,
      created_at: now,
      started_at: (req.status ?? "running") === "running" ? now : null,
      finished_at: null,
    };
    s.runs.unshift(run);
    s.seriesByRun[run.id] = {};
    return run;
  }

  if (method === "PATCH" && (m = /^\/api\/v1\/mlops\/runs\/([^/]+)$/.exec(path))) {
    const run = s.runs.find((r) => r.id === m![1]);
    if (!run) throw new ApiError(404, "training run not found");
    const req = (body ?? {}) as UpdateTrainingRunRequest;
    if (req.status !== undefined) run.status = req.status;
    if (req.total_steps !== undefined) run.total_steps = req.total_steps;
    if (req.hardware_info !== undefined) run.hardware_info = req.hardware_info;
    if (req.classic_eval !== undefined) run.classic_eval = req.classic_eval;
    if (req.summary_metrics !== undefined) {
      run.summary_metrics = { ...run.summary_metrics, ...req.summary_metrics };
      run.latest_metrics = { ...run.summary_metrics };
    }
    if (req.error !== undefined) run.error = req.error;
    if (req.finished_at !== undefined) run.finished_at = req.finished_at;
    return run;
  }

  if (method === "POST" && (m = /^\/api\/v1\/mlops\/runs\/([^/]+)\/metrics$/.exec(path))) {
    const run = s.runs.find((r) => r.id === m![1]);
    if (!run) throw new ApiError(404, "training run not found");
    const req = (body ?? { points: [] }) as IngestMetricsRequest;
    const series = (s.seriesByRun[run.id] ??= {});
    let accepted = 0;
    for (const pt of req.points ?? []) {
      const arr = (series[pt.metric_name] ??= []);
      arr.push({
        step: pt.step,
        value: pt.value,
        epoch: pt.epoch ?? null,
        recorded_at: pt.recorded_at ?? new Date().toISOString(),
      });
      run.summary_metrics[pt.metric_name] = pt.value;
      run.latest_metrics[pt.metric_name] = pt.value;
      run.total_steps = Math.max(run.total_steps ?? 0, pt.step);
      accepted += 1;
    }
    run.last_heartbeat_at = new Date().toISOString();
    const resp: IngestMetricsResponse = { accepted, should_stop: false, stop_reason: null };
    return resp;
  }

  if (method === "POST" && path === "/api/v1/mlops/sweeps") {
    const req = (body ?? {}) as CreateSweepRequest;
    const sweep: MLOpsSweep = {
      id: nextId("sweep"),
      project_id: "proj-default",
      name: req.name || "untitled-sweep",
      description: req.description ?? null,
      method: req.method ?? "random",
      metric_name: req.metric_name || "eval/loss",
      metric_goal: req.metric_goal ?? "minimize",
      search_space: req.search_space ?? {},
      early_stopping: req.early_stopping ?? null,
      max_trials: req.max_trials ?? null,
      status: "running",
      best_run_id: null,
      best_metric_value: null,
      created_at: new Date().toISOString(),
      stopped_at: null,
    };
    s.sweeps.unshift(sweep);
    s.sweepTrials[sweep.id] = [];
    s.sweepAnalysis[sweep.id] = {
      metric_name: sweep.metric_name,
      metric_goal: sweep.metric_goal,
      importance: [],
      parallel_coordinates: { axes: [], trials: [] },
    };
    return sweep;
  }

  if (method === "POST" && (m = /^\/api\/v1\/mlops\/sweeps\/([^/]+)\/trials\/next$/.exec(path))) {
    const sweep = s.sweeps.find((sw) => sw.id === m![1]);
    if (!sweep) throw new ApiError(404, "sweep not found");
    const req = (body ?? {}) as SuggestNextTrialRequest;
    const existing = s.sweepTrials[sweep.id] ?? [];
    const idx = existing.length;
    const now = new Date().toISOString();
    const hyperparams = { learning_rate: 1.5e-4, r: 16 };
    const run: TrainingRun = {
      id: nextId("run"),
      project_id: "proj-default",
      name: req.name || `${sweep.name}-trial-${idx + 1}`,
      run_type: req.run_type ?? "lora",
      status: "running",
      base_model: req.base_model ?? "meta-llama/Llama-3.1-8B-Instruct",
      dataset_id: req.dataset_id ?? null,
      sweep_id: sweep.id,
      parent_run_id: null,
      hyperparams,
      summary_metrics: {},
      latest_metrics: {},
      hardware_info: null,
      classic_eval: null,
      tags: req.tags ?? ["sweep"],
      total_steps: 0,
      error: null,
      last_heartbeat_at: now,
      created_at: now,
      started_at: now,
      finished_at: null,
    };
    existing.push(run);
    s.sweepTrials[sweep.id] = existing;
    s.runs.unshift(run);
    const resp: SuggestNextTrialResponse = {
      sweep_id: sweep.id,
      trial_index: idx,
      hyperparams,
      run,
    };
    return resp;
  }

  if (method === "POST" && (m = /^\/api\/v1\/mlops\/sweeps\/([^/]+)\/stop$/.exec(path))) {
    const sweep = s.sweeps.find((sw) => sw.id === m![1]);
    if (!sweep) throw new ApiError(404, "sweep not found");
    sweep.status = "cancelled";
    sweep.stopped_at = new Date().toISOString();
    return { ...sweep, stopped: true };
  }

  if (method === "POST" && path === "/api/v1/mlops/artifacts/presign-upload") {
    const req = (body ?? {}) as PresignArtifactUploadRequest;
    const existing = s.artifacts.find(
      (a) => a.sha256 === req.sha256 && a.status === "committed",
    );
    const now = new Date().toISOString();
    const art: ArtifactRecord = {
      id: nextId("art"),
      project_id: "proj-default",
      run_id: req.run_id ?? null,
      name: req.name,
      kind: req.kind ?? "checkpoint",
      version: s.artifacts.filter((a) => a.name === req.name).length + 1,
      storage_backend: req.external_uri ? "external" : "local",
      storage_uri: req.external_uri ?? existing?.storage_uri ?? `local://proj-default/${req.sha256}`,
      sha256: req.sha256,
      size_bytes: req.size_bytes,
      content_type: req.content_type ?? "application/octet-stream",
      status: existing ? "committed" : "pending_upload",
      metadata: req.metadata ?? {},
      created_at: now,
      committed_at: existing ? now : null,
      deduplicated: Boolean(existing),
      upload_url: existing || req.external_uri ? null : `https://mock-storage.matrix.local/upload/${req.sha256}`,
    };
    s.artifacts.unshift(art);
    return art;
  }

  if (method === "POST" && (m = /^\/api\/v1\/mlops\/artifacts\/([^/]+)\/commit$/.exec(path))) {
    const art = s.artifacts.find((a) => a.id === m![1]);
    if (!art) throw new ApiError(404, "artifact not found");
    art.status = "committed";
    art.committed_at = art.committed_at ?? new Date().toISOString();
    return art;
  }

  if (method === "POST" && path === "/api/v1/mlops/models") {
    const req = (body ?? {}) as CreateModelRequest;
    const name = (req.name ?? "").trim();
    if (!name) throw new ApiError(422, "name must not be blank");
    if (s.models.some((x) => x.name === name)) {
      throw new ApiError(409, "model name already exists");
    }
    const now = new Date().toISOString();
    const mod: RegisteredModel = {
      id: nextId("model"),
      project_id: "proj-default",
      name,
      description: req.description ?? null,
      model_type: req.model_type ?? "llm",
      base_model: req.base_model ?? null,
      tags: req.tags ?? [],
      created_at: now,
      updated_at: now,
      latest_version: null,
      aliases: {},
      stages: {},
      latest_metrics: {},
    };
    s.models.unshift(mod);
    s.versionsByModel[mod.id] = [];
    s.aliasesByModel[mod.id] = {};
    return mod;
  }

  if (method === "DELETE" && (m = /^\/api\/v1\/mlops\/models\/([^/]+)$/.exec(path))) {
    const idx = s.models.findIndex((x) => x.id === m![1]);
    if (idx === -1) throw new ApiError(404, "registered model not found");
    const [removed] = s.models.splice(idx, 1);
    delete s.versionsByModel[removed.id];
    delete s.aliasesByModel[removed.id];
    return { deleted: true, id: removed.id };
  }

  if (method === "POST" && (m = /^\/api\/v1\/mlops\/models\/([^/]+)\/versions$/.exec(path))) {
    const mod = s.models.find((x) => x.id === m![1]);
    if (!mod) throw new ApiError(404, "registered model not found");
    const req = (body ?? {}) as CreateModelVersionRequest;
    const versions = (s.versionsByModel[mod.id] ??= []);
    const nextVer = versions.reduce((mx, v) => Math.max(mx, v.version), 0) + 1;
    const mv: ModelVersion = {
      id: nextId("mv"),
      project_id: "proj-default",
      model_id: mod.id,
      version: nextVer,
      artifact_id: req.artifact_id ?? null,
      run_id: req.run_id ?? null,
      eval_experiment_id: req.eval_experiment_id ?? null,
      safety_scan_id: req.safety_scan_id ?? null,
      stage: req.stage ?? "none",
      lora_config: req.lora_config ?? null,
      signature: req.signature ?? null,
      metrics_snapshot: req.metrics_snapshot ?? {},
      model_card: req.model_card ?? null,
      created_by: "admin@matrix.local",
      created_at: new Date().toISOString(),
    };
    versions.unshift(mv);
    syncModelSummary(mod);
    return mv;
  }

  if (
    method === "PATCH" &&
    (m = /^\/api\/v1\/mlops\/models\/([^/]+)\/versions\/(\d+)\/stage$/.exec(path))
  ) {
    const mod = s.models.find((x) => x.id === m![1]);
    if (!mod) throw new ApiError(404, "registered model not found");
    const verNum = Number(m[2]);
    const mv = (s.versionsByModel[mod.id] ?? []).find((v) => v.version === verNum);
    if (!mv) throw new ApiError(404, "model version not found");
    const req = (body ?? {}) as UpdateModelStageRequest;
    mv.stage = req.stage;
    syncModelSummary(mod);
    return mv;
  }

  if (method === "POST" && (m = /^\/api\/v1\/mlops\/models\/([^/]+)\/aliases$/.exec(path))) {
    const mod = s.models.find((x) => x.id === m![1]);
    if (!mod) throw new ApiError(404, "registered model not found");
    const req = (body ?? {}) as SetModelAliasRequest;
    const alias = (req.alias ?? "").trim().replace(/^@/, "");
    if (!alias) throw new ApiError(422, "alias must not be blank");
    const mv = (s.versionsByModel[mod.id] ?? []).find((v) => v.version === req.version);
    if (!mv) throw new ApiError(404, "model version not found");
    const aliases = (s.aliasesByModel[mod.id] ??= {});
    aliases[alias] = req.version;
    syncModelSummary(mod);
    const resp: ModelAlias = { model_id: mod.id, alias, version: req.version };
    return resp;
  }

  if (
    method === "POST" &&
    (m = /^\/api\/v1\/mlops\/models\/([^/]+)\/versions\/(\d+)\/evaluate$/.exec(path))
  ) {
    const mod = s.models.find((x) => x.id === m![1]);
    if (!mod) throw new ApiError(404, "registered model not found");
    const verNum = Number(m[2]);
    const mv = (s.versionsByModel[mod.id] ?? []).find((v) => v.version === verNum);
    if (!mv) throw new ApiError(404, "model version not found");
    void (body as EvaluateModelVersionRequest | undefined);
    const resp: EvaluateModelVersionResponse = {
      status: "queued",
      job_id: nextId("job"),
      model_id: mod.id,
      version: mv.version,
    };
    return resp;
  }

  return undefined;
}
