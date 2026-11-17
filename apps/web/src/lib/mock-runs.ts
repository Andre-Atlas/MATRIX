// In-memory training runs for MATRIX_MOCK=1 (contract G1; mirrors server/src/matrix_server/api/runs.py).
//
// 12 seeded runs: LLM fine-tuning (DPO/SFT), a sklearn classifier with 3-class reports and
// importances, a regression, a ResNet LR sweep, failures and kills. Long series have 20 000 points
// and are reduced with a real M4 (`max_points`). Running runs grow with wall time (simulated
// heartbeat) and one of them finishes ~15 min after the server starts. State lives on globalThis so
// route handlers and Server Components share it in one process.
import { ApiError } from "./api-error";
import { NO_CONTENT } from "./mock-auth";
import { lastFinite, m4, type M4Point } from "./runs/series";
import type {
  ArtifactInfo,
  ClassificationReport,
  CompareResponse,
  ImportanceReport,
  MetricKeyStat,
  RegressionReport,
  Report,
  Run,
  RunArtifact,
  RunPage,
  RunStatus,
  SeriesData,
  SeriesResponse,
  TableReport,
} from "./runs/types";

type Params = Record<string, string | number | undefined>;

// ── deterministic randomness ─────────────────────────────────────────────────────────────────

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
type Rand = () => number;
const gauss = (r: Rand) => Math.sqrt(-2 * Math.log(r() || 1e-12)) * Math.cos(2 * Math.PI * r());
const hashStr = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
};
const hex = (r: Rand, n: number) => Array.from({ length: n }, () => Math.floor(r() * 16).toString(16)).join("");

// ── run specs ────────────────────────────────────────────────────────────────────────────────

/** A metric: `every` steps apart, `gen(step, rand)`; NaN/±Inf allowed (read back as null). */
type KeySpec = { key: string; every?: number; gen: (s: number, r: Rand) => number };

type Spec = {
  id: string;
  name: string;
  group: string | null;
  tags: string[];
  params: Record<string, unknown>;
  /** Seconds before server start when the run began. */
  startedAgo: number;
  /** Total planned steps and seconds per step. */
  steps: number;
  secPerStep: number;
  /** Final status once all steps are done (or `stopAt` is reached). */
  outcome: RunStatus;
  /** Step where a failed/killed run stopped. */
  stopAt?: number;
  /** Runs that are still alive at server start (they grow with time). */
  live?: boolean;
  error?: string;
  keys: KeySpec[];
  gpus: number | null; // null = no telemetry at all
  sysEvery: number; // seconds between telemetry samples
  git: Run["git"];
  env: Run["env"];
  dataset_version_id?: string;
  sweep_id?: string;
  parent_run_id?: string;
  created_by: string;
  reports?: () => Omit<Report, "id" | "run_id" | "created_at">[];
  artifacts?: { name: string; kind: RunArtifact["kind"]; media_type: string; size: number }[];
};

const decay = (s: number, from: number, to: number, tau: number) => to + (from - to) * Math.exp(-s / tau);
const cosineLr = (peak: number, warmup: number, total: number) => (s: number) =>
  s < warmup ? (peak * s) / warmup : peak * 0.5 * (1 + Math.cos((Math.PI * (s - warmup)) / Math.max(1, total - warmup)));

function llmKeys(total: number, dpo: boolean, seedLoss: number, peakLr: number): KeySpec[] {
  const loss = dpo
    ? (s: number, r: Rand) => decay(s, 0.693, 0.22 + seedLoss, 5000) + 0.035 * gauss(r)
    : (s: number, r: Rand) => decay(s, 2.3, 0.92 + seedLoss, 3500) + 0.06 * gauss(r);
  const keys: KeySpec[] = [
    { key: "train/loss", gen: loss },
    { key: "train/perplexity", gen: (s, r) => Math.exp(loss(s, r)) },
    { key: "train/learning_rate", gen: cosineLr(peakLr, 500, total) },
    {
      key: "train/grad_norm",
      gen: (s, r) => ([4211, 9876, 15_002].includes(s) ? Infinity : s === 12_040 ? NaN : decay(s, 3.2, 0.7, 6000) * Math.exp(0.25 * gauss(r))),
    },
    { key: "train/epoch", every: 10, gen: (s) => s / (total / 3) },
    { key: "eval/loss", every: 500, gen: (s, r) => (dpo ? decay(s, 0.69, 0.3 + seedLoss, 6000) : decay(s, 2.25, 1.05 + seedLoss, 4000)) + 0.01 * gauss(r) },
    { key: "eval/perplexity", every: 500, gen: (s, r) => Math.exp(dpo ? decay(s, 0.69, 0.3 + seedLoss, 6000) : decay(s, 2.25, 1.05 + seedLoss, 4000)) + 0.02 * gauss(r) },
  ];
  if (dpo) {
    const chosen = (s: number) => 1.4 * (1 - Math.exp(-s / 6000)) - 0.1;
    const rejected = (s: number) => -2.6 * (1 - Math.exp(-s / 5000));
    keys.push(
      { key: "rewards/chosen", gen: (s, r) => chosen(s) + 0.25 * gauss(r) },
      { key: "rewards/rejected", gen: (s, r) => rejected(s) + 0.25 * gauss(r) },
      { key: "rewards/margins", gen: (s, r) => chosen(s) - rejected(s) + 0.3 * gauss(r) },
      { key: "rewards/accuracies", gen: (s, r) => Math.min(1, Math.max(0, decay(s, 0.5, 0.93, 4000) + 0.04 * gauss(r))) },
    );
  }
  return keys;
}

function visionKeys(lr: number, diverge?: number): KeySpec[] {
  const q = Math.min(1, Math.abs(Math.log10(lr / 3e-4)) / 1.2); // 0 = best lr
  const nan = (s: number, v: number) => (diverge !== undefined && s >= diverge ? NaN : diverge !== undefined && s > diverge - 400 ? v * Math.exp((s - diverge + 400) / 60) : v);
  return [
    { key: "train/loss", gen: (s, r) => nan(s, decay(s, 6.9, 0.55 + 0.6 * q, 2500) + 0.08 * gauss(r)) },
    { key: "train/acc", gen: (s, r) => Math.min(1, Math.max(0, decay(s, 0.001, 0.86 - 0.12 * q, 3000) + 0.02 * gauss(r))) },
    { key: "train/lr", gen: (s) => lr * Math.pow(0.1, Math.floor(s / 7000)) },
    { key: "eval/loss", every: 1000, gen: (s, r) => nan(s, decay(s, 6.8, 0.82 + 0.55 * q, 2800) + 0.02 * gauss(r)) },
    { key: "eval/acc", every: 1000, gen: (s, r) => Math.min(1, decay(s, 0.001, 0.781 - 0.11 * q, 3200) + 0.005 * gauss(r)) },
    { key: "eval/top5_acc", every: 1000, gen: (s, r) => Math.min(1, decay(s, 0.005, 0.937 - 0.06 * q, 3000) + 0.003 * gauss(r)) },
  ];
}

const PKGS_BASE = [
  "numpy==2.1.3", "pandas==2.2.3", "scipy==1.14.1", "scikit-learn==1.5.2", "matplotlib==3.9.2", "tqdm==4.67.0",
  "requests==2.32.3", "pyyaml==6.0.2", "psutil==6.1.0", "pynvml==11.5.3", "matrix-sdk==0.4.0", "packaging==24.2",
  "filelock==3.16.1", "fsspec==2024.10.0", "jinja2==3.1.4", "markupsafe==3.0.2", "networkx==3.4.2", "sympy==1.13.1",
  "typing-extensions==4.12.2", "urllib3==2.2.3", "certifi==2024.8.30", "idna==3.10", "charset-normalizer==3.4.0",
  "six==1.16.0", "python-dateutil==2.9.0", "pytz==2024.2", "tzdata==2024.2", "pyarrow==18.0.0", "rich==13.9.4",
  "pygments==2.18.0", "click==8.1.7", "attrs==24.2.0", "protobuf==5.28.3", "grpcio==1.68.0", "joblib==1.4.2",
  "threadpoolctl==3.5.0", "pillow==11.0.0", "regex==2024.11.6", "safetensors==0.4.5", "huggingface-hub==0.26.2",
];
const PKGS_TORCH = ["torch==2.5.1", "torchvision==0.20.1", "triton==3.1.0", "nvidia-cudnn-cu12==9.1.0.70", "nvidia-nccl-cu12==2.21.5", "nvidia-cublas-cu12==12.4.5.8"];
const PKGS_LLM = ["transformers==4.46.3", "trl==0.12.1", "peft==0.13.2", "accelerate==1.1.1", "datasets==3.1.0", "tokenizers==0.20.3", "bitsandbytes==0.44.1", "flash-attn==2.7.0.post2", "deepspeed==0.15.4", "sentencepiece==0.2.0", "evaluate==0.4.3", "wandb==0.18.7"];
const PKGS_TAB = ["xgboost==2.1.2", "lightgbm==4.5.0", "shap==0.46.0", "optuna==4.1.0", "category-encoders==2.6.4", "polars==1.14.0"];
const pkgs = (...lists: string[][]) => [...new Set(lists.flat())].sort();

const gitOf = (r: Rand, branch: string, dirty: boolean): Run["git"] => ({
  commit: hex(r, 40),
  branch,
  dirty,
  remote: "git@github.com:acme/ml-training.git",
});
const envOf = (host: string, packages: string[]): Run["env"] => ({
  python: "3.11.10",
  platform: "Linux-5.15.0-1063-aws-x86_64-with-glibc2.35",
  hostname: host,
  packages,
});

const H = 3600;

function specs(): Spec[] {
  const r = rng(20261110);
  const llmPkgs = pkgs(PKGS_BASE, PKGS_TORCH, PKGS_LLM);
  const visPkgs = pkgs(PKGS_BASE, PKGS_TORCH, ["timm==1.0.11", "albumentations==1.4.21"]);
  const tabPkgs = pkgs(PKGS_BASE, PKGS_TAB);
  const vision = (id: string, lr: number, opts: Partial<Spec>): Spec => ({
    id,
    name: `resnet50-lr-${lr.toExponential(0).replace("+", "")}`,
    group: "resnet50-imagenet-sweep",
    tags: ["vision", "sweep", "a10g"],
    params: { model: "resnet50", lr, batch_size: 256, optimizer: "sgd", momentum: 0.9, weight_decay: 1e-4, epochs: 20, seed: 7, augment: { mixup: 0.2, randaugment: "rand-m9-mstd0.5" } },
    startedAgo: 9 * H,
    steps: 20_000,
    secPerStep: 1.2,
    outcome: "finished",
    keys: visionKeys(lr),
    gpus: 1,
    sysEvery: 5,
    git: gitOf(r, "sweep/resnet-lr", false),
    env: envOf("gpu-a10g-03", visPkgs),
    sweep_id: "swp_resnet_lr",
    dataset_version_id: "dsv_imagenet1k_v2",
    created_by: "u_bruno",
    artifacts: [
      { name: "checkpoints/best.pt", kind: "model", media_type: "application/octet-stream", size: 102_543_871 },
      { name: "config.yaml", kind: "file", media_type: "application/yaml", size: 1_874 },
    ],
    ...opts,
  });

  return [
    {
      id: "run_llama3_dpo",
      name: "llama-3-8b-dpo-ultrafeedback",
      group: "llm-finetune",
      tags: ["dpo", "llama-3", "lora", "2xa100"],
      params: {
        model_name_or_path: "meta-llama/Meta-Llama-3-8B-Instruct",
        learning_rate: 5e-6,
        beta: 0.1,
        per_device_train_batch_size: 4,
        gradient_accumulation_steps: 8,
        max_length: 2048,
        lora: { r: 16, alpha: 32, dropout: 0.05, target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"] },
        num_train_epochs: 3,
        warmup_steps: 500,
        lr_scheduler_type: "cosine",
        bf16: true,
        seed: 42,
      },
      startedAgo: 3.3 * H,
      steps: 30_000,
      secPerStep: 0.6,
      outcome: "finished",
      live: true,
      keys: llmKeys(30_000, true, 0, 5e-6),
      gpus: 2,
      sysEvery: 2,
      git: gitOf(r, "feat/dpo-ultrafeedback", true),
      env: envOf("gpu-a100-01", llmPkgs),
      dataset_version_id: "dsv_ultrafeedback_v3",
      parent_run_id: "run_llama3_sft",
      created_by: "u_ana",
      reports: () => [{ kind: "table", name: "generation samples", data: generationSamples() }],
      artifacts: [
        { name: "adapter_model.safetensors", kind: "model", media_type: "application/octet-stream", size: 167_832_240 },
        { name: "trainer_state.json", kind: "file", media_type: "application/json", size: 48_211 },
      ],
    },
    {
      id: "run_llama3_sft",
      name: "llama-3-8b-sft-openhermes",
      group: "llm-finetune",
      tags: ["sft", "llama-3", "lora", "2xa100"],
      params: { model_name_or_path: "meta-llama/Meta-Llama-3-8B", learning_rate: 2e-4, per_device_train_batch_size: 8, gradient_accumulation_steps: 4, max_seq_length: 4096, lora: { r: 64, alpha: 16, dropout: 0.1 }, num_train_epochs: 3, warmup_steps: 500, lr_scheduler_type: "cosine", bf16: true, seed: 42 },
      startedAgo: 30 * H,
      steps: 20_000,
      secPerStep: 0.9,
      outcome: "finished",
      keys: llmKeys(20_000, false, 0, 2e-4),
      gpus: 2,
      sysEvery: 5,
      git: gitOf(r, "main", false),
      env: envOf("gpu-a100-01", llmPkgs),
      dataset_version_id: "dsv_openhermes_v1",
      created_by: "u_ana",
      artifacts: [
        { name: "adapter_model.safetensors", kind: "model", media_type: "application/octet-stream", size: 671_088_640 },
        { name: "merged/model-00001-of-00004.safetensors", kind: "model", media_type: "application/octet-stream", size: 4_976_698_672 },
        { name: "trainer_state.json", kind: "file", media_type: "application/json", size: 61_002 },
      ],
    },
    {
      id: "run_qwen_dpo",
      name: "qwen2.5-7b-dpo-beta0.3",
      group: "llm-finetune",
      tags: ["dpo", "qwen", "2xa100"],
      params: { model_name_or_path: "Qwen/Qwen2.5-7B-Instruct", learning_rate: 1e-5, beta: 0.3, per_device_train_batch_size: 4, gradient_accumulation_steps: 8, max_length: 2048, num_train_epochs: 3, warmup_steps: 500, lr_scheduler_type: "cosine", bf16: true, seed: 42 },
      startedAgo: 14 * H,
      steps: 30_000,
      secPerStep: 0.6,
      outcome: "killed",
      stopAt: 8_400,
      keys: llmKeys(30_000, true, 0.05, 1e-5),
      gpus: 2,
      sysEvery: 5,
      git: gitOf(r, "feat/dpo-ultrafeedback", false),
      env: envOf("gpu-a100-02", llmPkgs),
      dataset_version_id: "dsv_ultrafeedback_v3",
      created_by: "u_ana",
    },
    {
      id: "run_churn_gbm",
      name: "churn-xgb-3class",
      group: "tabular-churn",
      tags: ["sklearn", "xgboost", "classification"],
      params: { estimator: "XGBClassifier", n_estimators: 1500, max_depth: 6, learning_rate: 0.05, subsample: 0.8, colsample_bytree: 0.7, objective: "multi:softprob", num_class: 3, early_stopping_rounds: 50, seed: 13 },
      startedAgo: 5 * H,
      steps: 1500,
      secPerStep: 0.4,
      outcome: "finished",
      keys: [
        { key: "train/mlogloss", gen: (s, rr) => decay(s, 1.09, 0.21, 260) + 0.002 * gauss(rr) },
        { key: "eval/mlogloss", gen: (s, rr) => decay(s, 1.09, 0.33, 240) + 0.0001 * (s - 600) * (s > 600 ? 0.05 : 0) + 0.003 * gauss(rr) },
        { key: "eval/accuracy", every: 10, gen: (s, rr) => decay(s, 0.6, 0.874, 220) + 0.002 * gauss(rr) },
        { key: "fit_time_s", every: 1500, gen: () => 612.4 },
      ],
      gpus: 0,
      sysEvery: 5,
      git: gitOf(r, "main", false),
      env: envOf("cpu-c6i-08", tabPkgs),
      dataset_version_id: "dsv_churn_2026q3",
      created_by: "u_carla",
      reports: () => {
        const cls = classificationReport(rng(77));
        return [
          { kind: "classification", name: "eval", data: cls },
          { kind: "importance", name: "permutation importance", data: importances(rng(78), "permutation", CHURN_FEATURES) },
          { kind: "importance", name: "shap", data: importances(rng(79), "shap", CHURN_FEATURES) },
          { kind: "table", name: "worst predictions", data: worstPredictions(rng(80)) },
          { kind: "custom", name: "threshold sweep", data: { positive_class: "churn", thresholds: [0.3, 0.4, 0.5, 0.6], precision: [0.61, 0.68, 0.74, 0.8], recall: [0.83, 0.77, 0.69, 0.58], chosen: 0.45, note: "picked for recall ≥ 0.75" } },
        ];
      },
      artifacts: [
        { name: "model.ubj", kind: "model", media_type: "application/octet-stream", size: 18_221_904 },
        { name: "predictions.parquet", kind: "table", media_type: "application/vnd.apache.parquet", size: 3_544_018 },
        { name: "confusion_matrix.png", kind: "plot", media_type: "image/png", size: 41_772 },
      ],
    },
    {
      id: "run_house_rf",
      name: "house-prices-rf",
      group: "tabular-pricing",
      tags: ["sklearn", "regression"],
      params: { estimator: "RandomForestRegressor", n_estimators: 500, max_depth: null, min_samples_leaf: 2, max_features: 0.33, n_jobs: -1, random_state: 0 },
      startedAgo: 7 * H,
      steps: 500,
      secPerStep: 0.8,
      outcome: "finished",
      keys: [
        { key: "eval/rmse", gen: (s, rr) => decay(s, 61.0, 27.4, 40) + 0.2 * gauss(rr) },
        { key: "eval/r2", gen: (s, rr) => decay(s, 0.62, 0.891, 45) + 0.001 * gauss(rr) },
        { key: "oob_score", every: 50, gen: (s) => decay(s, 0.7, 0.884, 60) },
      ],
      gpus: 0,
      sysEvery: 5,
      git: gitOf(r, "main", true),
      env: envOf("cpu-c6i-04", tabPkgs),
      dataset_version_id: "dsv_house_prices_v5",
      created_by: "u_carla",
      reports: () => [
        { kind: "regression", name: "holdout", data: regressionReport(rng(91)) },
        { kind: "importance", name: "impurity importance", data: importances(rng(92), "model", HOUSE_FEATURES) },
      ],
      artifacts: [{ name: "model.joblib", kind: "model", media_type: "application/octet-stream", size: 412_334_080 }],
    },
    vision("run_resnet_1e3", 1e-3, { startedAgo: 12 * H }),
    vision("run_resnet_3e4", 3e-4, { startedAgo: 11 * H }),
    // Still running at server start; finishes ~15 minutes later (simulated finish).
    vision("run_resnet_1e4", 1e-4, { startedAgo: 20_000 * 1.2 - 15 * 60, live: true, git: gitOf(r, "sweep/resnet-lr", true) }),
    vision("run_resnet_3e3", 3e-3, {
      startedAgo: 10 * H,
      outcome: "failed",
      stopAt: 4_310,
      keys: visionKeys(3e-3, 4_250),
      error:
        "Traceback (most recent call last):\n  File \"train.py\", line 214, in <module>\n    main()\n  File \"train.py\", line 188, in train_one_epoch\n    loss.backward()\nRuntimeError: Function 'CudnnConvolutionBackward0' returned nan values in its 0th output.",
      artifacts: [{ name: "config.yaml", kind: "file", media_type: "application/yaml", size: 1_874 }],
    }),
    {
      id: "run_vit_base",
      name: "vit-b16-baseline",
      group: "vision-baselines",
      tags: ["vision", "vit", "a10g"],
      params: { model: "vit_base_patch16_224", lr: 1e-3, optimizer: "adamw", weight_decay: 0.05, batch_size: 512, epochs: 20, warmup_epochs: 2, label_smoothing: 0.1, seed: 7 },
      startedAgo: 40 * H,
      steps: 20_000,
      secPerStep: 1.5,
      outcome: "finished",
      keys: visionKeys(4e-4),
      gpus: 1,
      sysEvery: 5,
      git: gitOf(r, "main", false),
      env: envOf("gpu-a10g-01", visPkgs),
      dataset_version_id: "dsv_imagenet1k_v2",
      created_by: "u_bruno",
    },
    {
      id: "run_bert_ner",
      name: "bert-base-ner-conll",
      group: null,
      tags: ["nlp", "ner"],
      params: { model_name_or_path: "bert-base-cased", learning_rate: 3e-5, per_device_train_batch_size: 32, num_train_epochs: 5, max_seq_length: 256, seed: 1 },
      startedAgo: 20 * H,
      steps: 9_000,
      secPerStep: 0.5,
      outcome: "failed",
      stopAt: 6_020,
      error: "lost heartbeat",
      keys: [
        { key: "train/loss", gen: (s, rr) => decay(s, 2.1, 0.06, 900) + 0.01 * gauss(rr) },
        { key: "train/learning_rate", gen: cosineLr(3e-5, 300, 9000) },
        { key: "eval/f1", every: 500, gen: (s, rr) => decay(s, 0.2, 0.912, 1200) + 0.003 * gauss(rr) },
      ],
      gpus: 1,
      sysEvery: 5,
      git: gitOf(r, "main", false),
      env: envOf("gpu-t4-02", pkgs(PKGS_BASE, PKGS_TORCH, ["transformers==4.46.3", "datasets==3.1.0", "seqeval==1.2.2"])),
      created_by: "u_dario",
    },
    {
      id: "run_whisper_ft",
      name: "whisper-small-pt-br",
      group: null,
      tags: ["asr", "whisper"],
      params: { model_name_or_path: "openai/whisper-small", learning_rate: 1e-5, per_device_train_batch_size: 16, max_steps: 5000, language: "portuguese", seed: 3 },
      startedAgo: 50 * H,
      steps: 5_000,
      secPerStep: 1.1,
      outcome: "killed",
      stopAt: 2_960,
      keys: [
        { key: "train/loss", gen: (s, rr) => decay(s, 1.4, 0.31, 700) + 0.02 * gauss(rr) },
        { key: "eval/wer", every: 250, gen: (s, rr) => decay(s, 0.42, 0.137, 900) + 0.002 * gauss(rr) },
      ],
      gpus: null,
      sysEvery: 5,
      git: null,
      env: null,
      created_by: "u_dario",
    },
  ];
}

// ── reports ──────────────────────────────────────────────────────────────────────────────────

const CHURN_FEATURES = ["tenure_months", "monthly_charges", "support_tickets_90d", "contract_type", "payment_method", "num_products", "last_login_days", "discount_pct", "region", "nps_score", "avg_session_min", "plan_changes_12m"];
const HOUSE_FEATURES = ["overall_qual", "gr_liv_area", "garage_cars", "total_bsmt_sf", "year_built", "full_bath", "lot_area", "neighborhood", "fireplaces", "kitchen_qual", "year_remod", "mas_vnr_area", "open_porch_sf", "pool_area"];

/** Down-samples a curve to at most `n` points, keeping both ends. */
function thin<T>(xs: T[], n: number): T[] {
  if (xs.length <= n) return xs;
  return Array.from({ length: n }, (_, i) => xs[Math.round((i * (xs.length - 1)) / (n - 1))]);
}

function classificationReport(r: Rand): ClassificationReport {
  const labels = ["stay", "downgrade", "churn"];
  const prior = [0.62, 0.23, 0.15];
  const sep = [2.4, 1.5, 2.0];
  const n = 2400;
  const y: number[] = [];
  const p: number[][] = [];
  for (let i = 0; i < n; i++) {
    const u = r();
    const c = u < prior[0] ? 0 : u < prior[0] + prior[1] ? 1 : 2;
    const logits = [0, 1, 2].map((k) => Math.log(prior[k]) + (k === c ? sep[c] : 0) + 0.9 * gauss(r));
    const mx = Math.max(...logits);
    const e = logits.map((l) => Math.exp(l - mx));
    const z = e.reduce((a, b) => a + b, 0);
    y.push(c);
    p.push(e.map((v) => v / z));
  }
  const cm = labels.map(() => labels.map(() => 0));
  let logLoss = 0;
  for (let i = 0; i < n; i++) {
    const pred = p[i].indexOf(Math.max(...p[i]));
    cm[y[i]][pred] += 1;
    logLoss -= Math.log(Math.max(1e-15, p[i][y[i]]));
  }
  const per_class = labels.map((label, k) => {
    const tp = cm[k][k];
    const support = cm[k].reduce((a, b) => a + b, 0);
    const predicted = cm.reduce((a, row) => a + row[k], 0);
    const precision = predicted ? tp / predicted : 0;
    const recall = support ? tp / support : 0;
    const f1 = precision + recall ? (2 * precision * recall) / (precision + recall) : 0;
    return { label, precision, recall, f1, support };
  });
  const roc: ClassificationReport["roc"] = [];
  const pr: ClassificationReport["pr"] = [];
  labels.forEach((label, k) => {
    const order = p.map((row, i) => [row[k], y[i] === k ? 1 : 0] as const).sort((a, b) => b[0] - a[0]);
    const P = order.reduce((a, [, t]) => a + t, 0);
    const N = n - P;
    let tp = 0;
    let fp = 0;
    let auc = 0;
    let ap = 0;
    let prevFpr = 0;
    let prevTpr = 0;
    let prevRecall = 0;
    const rocPts: [number, number][] = [[0, 0]];
    const prPts: [number, number][] = [];
    for (let i = 0; i < order.length; i++) {
      if (order[i][1]) tp += 1;
      else fp += 1;
      if (i < order.length - 1 && order[i + 1][0] === order[i][0]) continue;
      const fpr = fp / N;
      const tpr = tp / P;
      auc += ((fpr - prevFpr) * (tpr + prevTpr)) / 2;
      const precision = tp / (tp + fp);
      ap += (tpr - prevRecall) * precision;
      prevFpr = fpr;
      prevTpr = tpr;
      prevRecall = tpr;
      rocPts.push([fpr, tpr]);
      prPts.push([tpr, precision]);
    }
    const rocThin = thin(rocPts, 200);
    const prThin = thin(prPts, 200);
    roc.push({ label, fpr: rocThin.map((q) => q[0]), tpr: rocThin.map((q) => q[1]), auc });
    pr.push({ label, recall: prThin.map((q) => q[0]), precision: prThin.map((q) => q[1]), ap });
  });
  const macro = (f: "precision" | "recall" | "f1") => per_class.reduce((a, c) => a + c[f], 0) / labels.length;
  const weighted = (f: "precision" | "recall" | "f1") => per_class.reduce((a, c) => a + c[f] * c.support, 0) / n;
  const accuracy = cm.reduce((a, row, k) => a + row[k], 0) / n;
  return {
    labels,
    metrics: {
      accuracy,
      precision_macro: macro("precision"),
      recall_macro: macro("recall"),
      f1_macro: macro("f1"),
      precision_weighted: weighted("precision"),
      recall_weighted: weighted("recall"),
      f1_weighted: weighted("f1"),
      log_loss: logLoss / n,
    },
    per_class,
    confusion_matrix: cm,
    roc,
    pr,
  };
}

function regressionReport(r: Rand): RegressionReport {
  const n = 1000;
  const yt: number[] = [];
  const yp: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = Math.exp(Math.log(180) + 0.42 * gauss(r));
    const pred = t * (1 + 0.11 * gauss(r)) + (t > 450 ? -0.08 * t : 0); // under-predicts expensive houses
    yt.push(Number(t.toFixed(1)));
    yp.push(Number(pred.toFixed(1)));
  }
  const res = yt.map((t, i) => yp[i] - t);
  const mean = yt.reduce((a, b) => a + b, 0) / n;
  const ssRes = res.reduce((a, e) => a + e * e, 0);
  const ssTot = yt.reduce((a, t) => a + (t - mean) ** 2, 0);
  const lo = Math.min(...res);
  const hi = Math.max(...res);
  const bins = 30;
  const w = (hi - lo) / bins;
  const edges = Array.from({ length: bins + 1 }, (_, i) => Number((lo + i * w).toFixed(2)));
  const counts = new Array(bins).fill(0);
  for (const e of res) counts[Math.min(bins - 1, Math.floor((e - lo) / w))] += 1;
  return {
    metrics: {
      rmse: Math.sqrt(ssRes / n),
      mae: res.reduce((a, e) => a + Math.abs(e), 0) / n,
      r2: 1 - ssRes / ssTot,
      mape: res.reduce((a, e, i) => a + Math.abs(e / yt[i]), 0) / n,
    },
    residual_hist: { edges, counts },
    sample: { y_true: yt, y_pred: yp },
  };
}

function importances(r: Rand, method: ImportanceReport["method"], names: string[]): ImportanceReport {
  const features = names
    .map((name, i) => {
      const importance = Math.max(0.002, 0.32 * Math.exp(-i / 2.6) * (0.8 + 0.4 * r()));
      return method === "permutation" ? { name, importance, std: importance * (0.08 + 0.2 * r()) } : { name, importance };
    })
    .sort((a, b) => b.importance - a.importance);
  return { method, features };
}

function worstPredictions(r: Rand): TableReport {
  const labels = ["stay", "downgrade", "churn"];
  const rows = Array.from({ length: 40 }, (_, i) => {
    const t = Math.floor(r() * 3);
    const pred = (t + 1 + Math.floor(r() * 2)) % 3;
    return [`cus_${(48_210 + i * 37).toString(36)}`, labels[t], labels[pred], Number((0.55 + 0.44 * r()).toFixed(3)), i % 7 === 0 ? "Long free-text note from support: " + "customer reported repeated billing issues and asked about contract terms. ".repeat(6) : ""];
  });
  return { columns: ["customer_id", "y_true", "y_pred", "confidence", "support_note"], rows, truncated: true };
}

function generationSamples(): TableReport {
  return {
    columns: ["prompt", "chosen", "rejected", "reward_margin"],
    rows: [
      ["Explain gradient clipping in one paragraph.", "Gradient clipping rescales gradients whose global norm exceeds a threshold, preventing a single bad batch from blowing up the update…", "Gradient clipping is when gradients are clipped.", 3.41],
      ["Write a haiku about overfitting.", "Training loss falls / validation climbs alone / the model has learned noise", "Overfitting is bad / it happens in machine learning / use regularization", 2.07],
      ["What is DPO?", "Direct Preference Optimization fine-tunes a policy on preference pairs with a closed-form objective derived from the RLHF reward…", "DPO is a type of reinforcement learning.", 2.88],
    ],
    truncated: false,
  };
}

// ── store ────────────────────────────────────────────────────────────────────────────────────

type Built = { spec: Spec; startMs: number; reports: Report[]; artifacts: RunArtifact[] };
type Series = { steps: Int32Array; values: Float64Array; ts: Float64Array };

type Store = {
  t0: number;
  runs: Map<string, Built>;
  order: string[];
  series: Map<string, Series>; // `${runId}\u0000${key}`
  overrides: Map<string, { status: RunStatus; ended_at: string; error: string | null }>;
  deleted: Set<string>;
};

function build(): Store {
  const t0 = Date.now();
  const runs = new Map<string, Built>();
  for (const spec of specs()) {
    const startMs = t0 - spec.startedAgo * 1000;
    const created = new Date(startMs + 60_000).toISOString();
    const reports = (spec.reports?.() ?? []).map((rep, i) => ({ ...rep, id: `rep_${spec.id.slice(4)}_${i}`, run_id: spec.id, created_at: created }) as Report);
    const ar = rng(hashStr(spec.id));
    const artifacts: RunArtifact[] = (spec.artifacts ?? []).map((a, i) => {
      const info: ArtifactInfo = {
        id: `art_${spec.id.slice(4)}_${i}`,
        name: a.name.split("/").at(-1)!,
        media_type: a.media_type,
        size: a.size,
        sha256: hex(ar, 64),
        verified: true,
        owner_kind: "run",
        owner_id: spec.id,
        created_at: created,
        created_by: spec.created_by,
      };
      return { artifact: info, name: a.name, kind: a.kind };
    });
    runs.set(spec.id, { spec, startMs, reports, artifacts });
  }
  return { t0, runs, order: [...runs.keys()], series: new Map(), overrides: new Map(), deleted: new Set() };
}

function store(): Store {
  const g = globalThis as unknown as { __matrixRunsStore?: Store };
  g.__matrixRunsStore ??= build();
  return g.__matrixRunsStore;
}

export function resetMockRuns(): void {
  (globalThis as unknown as { __matrixRunsStore?: Store }).__matrixRunsStore = undefined;
}

/** Last step the run reached by now. */
function stepsDone(b: Built, now: number): number {
  const { spec } = b;
  const limit = spec.stopAt ?? spec.steps;
  if (!spec.live) return limit;
  return Math.max(1, Math.min(limit, Math.floor((now - b.startMs) / 1000 / spec.secPerStep)));
}

function endMs(b: Built): number {
  return b.startMs + (b.spec.stopAt ?? b.spec.steps) * b.spec.secPerStep * 1000;
}

function statusOf(b: Built, now: number): { status: RunStatus; ended_at: string | null; error: string | null; heartbeat_at: string | null } {
  const o = store().overrides.get(b.spec.id);
  if (o) return { ...o, heartbeat_at: o.ended_at };
  const end = endMs(b);
  if (b.spec.live && now < end) {
    // Simulated heartbeat: the SDK beats every 30 s.
    return { status: "running", ended_at: null, error: null, heartbeat_at: new Date(now - ((now / 1000) % 30) * 1000).toISOString() };
  }
  const endedIso = new Date(end).toISOString();
  return { status: b.spec.outcome, ended_at: endedIso, error: b.spec.error ?? null, heartbeat_at: endedIso };
}

/** The visible end of a run in ms (now while running, else its end). */
function horizon(b: Built, now: number): number {
  const s = statusOf(b, now);
  return s.ended_at ? Date.parse(s.ended_at) : now;
}

// ── series ───────────────────────────────────────────────────────────────────────────────────

function sysSpecs(gpus: number): KeySpec[] {
  const ks: KeySpec[] = [
    { key: "sys/cpu.percent", gen: (_s, r) => Math.min(100, Math.max(0, 68 + 12 * gauss(r))) },
    { key: "sys/memory.used_mb", gen: (s, r) => 41_000 + Math.min(9000, s * 2) + 300 * gauss(r) },
    { key: "sys/disk.read_mb", gen: (s, r) => Math.max(0, (s % 60 < 3 ? 420 : 18) + 6 * gauss(r)) },
    { key: "sys/disk.write_mb", gen: (s, r) => Math.max(0, (s % 400 === 0 ? 1600 : 2) + 1 * gauss(r)) },
    { key: "sys/net.sent_mb", gen: (_s, r) => Math.max(0, 3 + 1.2 * gauss(r)) },
    { key: "sys/net.recv_mb", gen: (s, r) => Math.max(0, (s % 60 < 3 ? 140 : 12) + 3 * gauss(r)) },
    { key: "sys/proc.rss_mb", gen: (s, r) => 11_800 + Math.min(2500, s * 0.8) + 60 * gauss(r) },
  ];
  for (let g = 0; g < gpus; g++) {
    const off = g * 3;
    const evalDip = (s: number) => s % 250 < 6;
    ks.push(
      { key: `sys/gpu.${g}.utilization`, gen: (s, r) => Math.min(100, Math.max(0, evalDip(s) ? 35 + 10 * r() : 96 - off + 3 * gauss(r))) },
      { key: `sys/gpu.${g}.memory_used_mb`, gen: (s, r) => Math.min(81_559, 71_200 + off * 400 + Math.min(4000, s * 3) + 120 * gauss(r)) },
      { key: `sys/gpu.${g}.temperature_c`, gen: (s, r) => decay(s, 38, 74 + off, 40) + 0.8 * gauss(r) },
      { key: `sys/gpu.${g}.power_w`, gen: (s, r) => Math.max(60, evalDip(s) ? 140 + 20 * r() : 342 - off * 6 + 18 * gauss(r)) },
    );
  }
  return ks;
}

function keySpecs(b: Built): KeySpec[] {
  const { spec } = b;
  return spec.gpus === null ? spec.keys : [...spec.keys, ...sysSpecs(spec.gpus)];
}

/** The full planned series of a key (generated once and cached). */
function fullSeries(b: Built, ks: KeySpec): Series {
  const st = store();
  const id = `${b.spec.id}\u0000${ks.key}`;
  const hit = st.series.get(id);
  if (hit) return hit;
  const r = rng(hashStr(id));
  const sys = ks.key.startsWith("sys/");
  const limit = b.spec.stopAt ?? b.spec.steps;
  let steps: number[];
  let tsOf: (step: number) => number;
  if (sys) {
    const total = Math.floor((limit * b.spec.secPerStep) / b.spec.sysEvery);
    steps = Array.from({ length: total + 1 }, (_, i) => i);
    tsOf = (i) => b.startMs + i * b.spec.sysEvery * 1000 + Math.floor(r() * 200);
  } else {
    const every = ks.every ?? 1;
    steps = [];
    for (let s = every === 1 ? 0 : every; s <= limit; s += every) steps.push(s);
    tsOf = (s) => b.startMs + s * b.spec.secPerStep * 1000 + Math.floor(r() * 50);
  }
  const out: Series = { steps: Int32Array.from(steps), values: new Float64Array(steps.length), ts: new Float64Array(steps.length) };
  steps.forEach((s, i) => {
    out.values[i] = ks.gen(s, r);
    out.ts[i] = tsOf(s);
  });
  st.series.set(id, out);
  return out;
}

/** Points visible now (up to the run's horizon), as M4 input. */
function visiblePoints(b: Built, ks: KeySpec, now: number, fromStep?: number, toStep?: number): M4Point[] {
  const s = fullSeries(b, ks);
  const until = horizon(b, now);
  const sys = ks.key.startsWith("sys/");
  const lastStep = stepsDone(b, now);
  const pts: M4Point[] = [];
  for (let i = 0; i < s.steps.length; i++) {
    const step = s.steps[i];
    if (sys ? s.ts[i] > until : step > lastStep) break;
    if (fromStep !== undefined && step < fromStep) continue;
    if (toStep !== undefined && step > toStep) break;
    pts.push({ step, value: s.values[i], ts: s.ts[i] });
  }
  return pts;
}

const finiteOrNull = (v: number) => (Number.isFinite(v) ? v : null);

function seriesOut(points: M4Point[]): SeriesData {
  return { steps: points.map((p) => p.step), values: points.map((p) => finiteOrNull(p.value)), ts: points.map((p) => new Date(p.ts).toISOString()) };
}

function readSeries(b: Built, names: string[], params: Params, now: number): { series: Record<string, SeriesData>; downsampled: boolean } {
  const maxPoints = Math.max(4, Math.min(5000, Number(params.max_points ?? 1000) || 1000));
  const x = params.x === "time" ? "time" : "step";
  const from = params.from_step !== undefined ? Number(params.from_step) : undefined;
  const to = params.to_step !== undefined ? Number(params.to_step) : undefined;
  const specsByKey = new Map(keySpecs(b).map((k) => [k.key, k]));
  let downsampled = false;
  const series: Record<string, SeriesData> = {};
  for (const name of names) {
    const ks = specsByKey.get(name);
    if (!ks) {
      series[name] = { steps: [], values: [], ts: [] };
      continue;
    }
    const out = m4(visiblePoints(b, ks, now, from, to), maxPoints, x);
    downsampled ||= out.downsampled;
    series[name] = seriesOut(out.points);
  }
  return { series, downsampled };
}

function keyStats(b: Built, now: number): MetricKeyStat[] {
  return keySpecs(b)
    .map((ks) => {
      const pts = visiblePoints(b, ks, now);
      if (!pts.length) return null;
      const finite = pts.map((p) => p.value).filter(Number.isFinite);
      return {
        key: ks.key,
        count: pts.length,
        first_step: pts[0].step,
        last_step: pts[pts.length - 1].step,
        last_value: finiteOrNull(pts[pts.length - 1].value),
        min: finite.length ? Math.min(...finite) : null,
        max: finite.length ? Math.max(...finite) : null,
      };
    })
    .filter((x): x is MetricKeyStat => x !== null)
    .sort((a, b2) => a.key.localeCompare(b2.key));
}

function summaryOf(b: Built, now: number): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const ks of b.spec.keys) {
    const pts = visiblePoints(b, ks, now);
    if (pts.length) out[ks.key] = finiteOrNull(pts[pts.length - 1].value) ?? lastFinite([]);
  }
  return out;
}

function runOut(b: Built, now: number): Run {
  const { spec } = b;
  const s = statusOf(b, now);
  return {
    id: spec.id,
    name: spec.name,
    status: s.status,
    group: spec.group,
    tags: spec.tags,
    params: spec.params,
    git: spec.git,
    env: spec.env,
    summary: summaryOf(b, now),
    dataset_version_id: spec.dataset_version_id ?? null,
    sweep_id: spec.sweep_id ?? null,
    parent_run_id: spec.parent_run_id ?? null,
    created_by: spec.created_by,
    started_at: new Date(b.startMs).toISOString(),
    ended_at: s.ended_at,
    heartbeat_at: s.heartbeat_at,
    error: s.error,
  };
}

function getBuilt(id: string): Built {
  const st = store();
  const b = st.runs.get(id);
  if (!b || st.deleted.has(id)) throw new ApiError(404, "run not found");
  return b;
}

const csv = (v: string | number | undefined, limit: number, what: string) => {
  const items = [...new Set(String(v ?? "").split(",").map((s) => s.trim()).filter(Boolean))];
  if (items.length > limit) throw new ApiError(422, `at most ${limit} ${what}`);
  return items;
};

// ── handlers ─────────────────────────────────────────────────────────────────────────────────

export function runsGet(path: string, params: Params): unknown {
  if (!path.startsWith("/api/v1/runs")) return undefined;
  const st = store();
  const now = Date.now();
  let m: RegExpExecArray | null;

  if (path === "/api/v1/runs") {
    const q = params.q ? String(params.q).toLowerCase() : null;
    const sort = String(params.sort ?? "-started_at");
    let items = st.order
      .filter((id) => !st.deleted.has(id))
      .map((id) => runOut(st.runs.get(id)!, now))
      .filter(
        (r) =>
          (!params.status || r.status === params.status) &&
          (!params.group || r.group === params.group) &&
          (!params.tag || r.tags.includes(String(params.tag))) &&
          (!params.name || r.name === params.name) &&
          (!params.sweep_id || r.sweep_id === params.sweep_id) &&
          (!q || r.name.toLowerCase().includes(q) || (r.group ?? "").toLowerCase().includes(q) || r.tags.some((t) => t.toLowerCase().includes(q))),
      );
    const by = sort.replace(/^-/, "");
    items = items.sort((a, b) => (by === "name" ? a.name.localeCompare(b.name) : a.started_at.localeCompare(b.started_at)) || a.id.localeCompare(b.id));
    if (sort.startsWith("-")) items.reverse();
    const limit = Math.min(200, Math.max(1, Number(params.limit ?? 50) || 50));
    const offset = params.cursor ? Number.parseInt(String(params.cursor).replace(/^o/, ""), 36) : 0;
    if (!Number.isFinite(offset) || offset < 0) throw new ApiError(400, "invalid cursor");
    const page: RunPage = { items: items.slice(offset, offset + limit), next_cursor: offset + limit < items.length ? `o${(offset + limit).toString(36)}` : null };
    return page;
  }

  if (path === "/api/v1/runs/compare") {
    const ids = csv(params.run_ids, 20, "runs");
    const keys = csv(params.keys, 20, "keys");
    if (!ids.length || !keys.length) throw new ApiError(422, "run_ids and keys are required");
    const out: CompareResponse = { runs: {} };
    for (const id of ids) {
      const b = st.runs.get(id);
      if (!b || st.deleted.has(id)) continue;
      out.runs[id] = { name: b.spec.name, series: readSeries(b, keys, params, now).series };
    }
    return out;
  }

  if ((m = /^\/api\/v1\/runs\/([^/]+)$/.exec(path))) return runOut(getBuilt(m[1]), now);
  if ((m = /^\/api\/v1\/runs\/([^/]+)\/metric-keys$/.exec(path))) return { keys: keyStats(getBuilt(m[1]), now) };
  if ((m = /^\/api\/v1\/runs\/([^/]+)\/metrics$/.exec(path))) {
    const b = getBuilt(m[1]);
    let names = csv(params.keys, 100, "keys");
    if (!names.length) names = keySpecs(b).map((k) => k.key).sort();
    const res: SeriesResponse = readSeries(b, names, params, now);
    return res;
  }
  if ((m = /^\/api\/v1\/runs\/([^/]+)\/artifacts$/.exec(path))) return { items: getBuilt(m[1]).artifacts };
  if ((m = /^\/api\/v1\/runs\/([^/]+)\/reports$/.exec(path))) {
    const b = getBuilt(m[1]);
    return { items: params.kind ? b.reports.filter((r) => r.kind === params.kind) : b.reports };
  }
  return undefined;
}

export function runsSend(method: string, path: string, body: unknown): unknown {
  if (!path.startsWith("/api/v1/runs/")) return undefined;
  const st = store();
  const now = Date.now();
  let m: RegExpExecArray | null;
  if (method === "POST" && (m = /^\/api\/v1\/runs\/([^/]+)\/heartbeat$/.exec(path))) {
    getBuilt(m[1]);
    return NO_CONTENT;
  }
  if ((m = /^\/api\/v1\/runs\/([^/]+)$/.exec(path))) {
    const b = getBuilt(m[1]);
    if (method === "DELETE") {
      st.deleted.add(b.spec.id);
      return NO_CONTENT;
    }
    if (method === "PATCH") {
      const p = (body ?? {}) as { status?: unknown; error?: unknown };
      if (p.status !== undefined) {
        if (p.status !== "finished" && p.status !== "failed" && p.status !== "killed") throw new ApiError(422, "status must be finished, failed or killed");
        const cur = statusOf(b, now);
        st.overrides.set(b.spec.id, { status: p.status, ended_at: cur.ended_at ?? new Date(now).toISOString(), error: typeof p.error === "string" ? p.error : cur.error });
      }
      return runOut(b, now);
    }
  }
  return undefined;
}

/** `GET /artifacts/{id}/download` for run artifacts in mock mode: a small placeholder file. */
export function runArtifactDownload(path: string): Response | undefined {
  const m = /^\/api\/v1\/artifacts\/([^/]+)\/download$/.exec(path.replace(/\/+$/, ""));
  if (!m) return undefined;
  for (const b of store().runs.values()) {
    const a = b.artifacts.find((x) => x.artifact.id === m[1]);
    if (a) {
      const name = a.artifact.name.replace(/[^\w.-]/g, "_");
      return new Response(`mock artifact ${a.name} (${a.artifact.size} bytes in the real store)\nsha256 ${a.artifact.sha256}\n`, {
        headers: { "content-type": "text/plain; charset=utf-8", "content-disposition": `attachment; filename="${name}.txt"`, "x-content-type-options": "nosniff" },
      });
    }
  }
  return undefined;
}
