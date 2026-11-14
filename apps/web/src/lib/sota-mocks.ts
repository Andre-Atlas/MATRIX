import { ApiError } from "./api-error";
import type {
  BenchmarkCatalogResponse,
  BenchmarkCatalogTask,
  BenchmarkRun,
  BenchmarkSuite,
  BenchmarkTaskResult,
  CreateBenchmarkRunRequest,
  CreateDatasetBranchRequest,
  CreateDatasetCommitRequest,
  CreateSandboxRunRequest,
  DatasetBranch,
  DatasetCommit,
  DatasetCommitItem,
  DatasetDiffResult,
  ListResponse,
  MergeBranchesRequest,
  RevertBranchRequest,
  SandboxRun,
  SandboxTaskTranscript,
} from "./types";

type Params = Record<string, string | number | undefined>;

function page<T>(items: T[], params: Params = {}): ListResponse<T> {
  const limit = Math.min(500, Math.max(1, Number(params.limit ?? 50)));
  const offset = Number(params.cursor ?? 0) || 0;
  const slice = items.slice(offset, offset + limit);
  return {
    items: slice,
    next_cursor: offset + limit < items.length ? String(offset + limit) : null,
  };
}

// ── 1. Benchmark Catalog (LM-Eval & VLMEvalKit) ───────────────────────────

export const MOCK_BENCHMARK_CATALOG: BenchmarkCatalogTask[] = [
  // LM-Eval (Text & Code)
  {
    name: "mmlu_pro",
    suite: "lm_eval",
    modality: "text",
    eval_modes: ["loglikelihood", "generate_until"],
    primary_metric: "acc",
    metrics: ["acc", "acc_norm", "logits_acc", "gen_acc"],
    default_num_fewshot: 5,
    sample_count: 100,
    description: "MMLU-Pro advanced multi-discipline reasoning and knowledge benchmark",
  },
  {
    name: "gpqa_diamond",
    suite: "lm_eval",
    modality: "text",
    eval_modes: ["loglikelihood", "generate_until"],
    primary_metric: "acc_norm",
    metrics: ["acc_norm", "acc", "logits_acc", "gen_acc"],
    default_num_fewshot: 0,
    sample_count: 198,
    description: "GPQA Diamond graduate-level science and reasoning subset",
  },
  {
    name: "ifeval",
    suite: "lm_eval",
    modality: "text",
    eval_modes: ["generate_until"],
    primary_metric: "strict_acc",
    metrics: ["strict_acc", "loose_acc"],
    default_num_fewshot: 0,
    sample_count: 541,
    description: "Instruction-Following Evaluation with strictly verifiable constraints",
  },
  {
    name: "bbh",
    suite: "lm_eval",
    modality: "text",
    eval_modes: ["loglikelihood", "generate_until"],
    primary_metric: "acc_norm",
    metrics: ["acc_norm", "acc", "logits_acc", "gen_acc"],
    default_num_fewshot: 3,
    sample_count: 250,
    description: "BIG-Bench Hard algorithmic, logical, and symbolic reasoning tasks",
  },
  {
    name: "humaneval",
    suite: "lm_eval",
    modality: "text",
    eval_modes: ["generate_until"],
    primary_metric: "pass@1",
    metrics: ["pass@1"],
    default_num_fewshot: 0,
    sample_count: 164,
    description: "OpenAI HumanEval Python functional programming code benchmark",
  },
  {
    name: "gsm8k",
    suite: "lm_eval",
    modality: "text",
    eval_modes: ["generate_until"],
    primary_metric: "exact_match",
    metrics: ["exact_match", "acc"],
    default_num_fewshot: 8,
    sample_count: 1319,
    description: "Grade School Math 8K multi-step mathematical reasoning benchmark",
  },
  // VLMEvalKit (Multimodal & Visual RAG)
  {
    name: "mmmu_pro",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "acc",
    metrics: ["acc", "reason_acc"],
    default_num_fewshot: 0,
    sample_count: 1730,
    description: "MMMU-Pro massive multi-discipline multimodal understanding benchmark",
  },
  {
    name: "mathvista",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "acc",
    metrics: ["acc", "visual_math_acc"],
    default_num_fewshot: 0,
    sample_count: 1000,
    description: "MathVista visual mathematical and diagrammatic reasoning benchmark",
  },
  {
    name: "docvqa",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "anls",
    metrics: ["anls", "acc"],
    default_num_fewshot: 0,
    sample_count: 500,
    description: "Document Visual Question Answering (DocVQA) across scanned pages",
  },
  {
    name: "chartqa",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "relaxed_acc",
    metrics: ["relaxed_acc", "acc"],
    default_num_fewshot: 0,
    sample_count: 500,
    description: "ChartQA reasoning over visual charts, plots, and figures",
  },
  {
    name: "ocrbench",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "score",
    metrics: ["score", "acc"],
    default_num_fewshot: 0,
    sample_count: 1000,
    description: "OCRBench optical character recognition across diverse complex scenes",
  },
  {
    name: "pope",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "f1",
    metrics: ["f1", "acc"],
    default_num_fewshot: 0,
    sample_count: 500,
    description: "POPE polling-based object probing evaluation for hallucination assessment",
  },
  {
    name: "vidore",
    suite: "vlmevalkit",
    modality: "multimodal",
    eval_modes: ["multimodal"],
    primary_metric: "ndcg@5",
    metrics: ["ndcg@5", "map"],
    default_num_fewshot: 0,
    sample_count: 400,
    description: "ViDoRe visual document retrieval and embedding benchmark",
  },
];

export function getMockBenchmarkCatalog(suite?: string): BenchmarkCatalogTask[] {
  if (!suite) return MOCK_BENCHMARK_CATALOG;
  return MOCK_BENCHMARK_CATALOG.filter((t) => t.suite === suite);
}

// ── 2. Benchmark Runs (Llama-3.1, Mistral, Qwen2-VL) ───────────────────────

export const MOCK_BENCHMARK_RUNS: BenchmarkRun[] = [
  {
    id: "bm-run-01",
    project_id: "proj-default",
    name: "Llama-3.1-8B-Instruct Foundational Suite",
    suite: "lm_eval",
    tasks: ["mmlu_pro", "gpqa_diamond", "ifeval", "bbh", "humaneval", "gsm8k"],
    runner_backend: "vllm",
    runner_config: {
      tensor_parallel_size: 1,
      gpu_memory_utilization: 0.9,
      max_model_len: 8192,
      dtype: "bfloat16",
    },
    eval_mode: "generate_until",
    num_fewshot: 5,
    target_model: "meta-llama/Llama-3.1-8B-Instruct",
    model_version_id: "mv-llama3-v1",
    dataset_id: null,
    dataset_commit_id: null,
    status: "completed",
    summary_metrics: {
      avg_score: 0.704,
      mmlu_pro: 0.684,
      gpqa_diamond: 0.382,
      ifeval: 0.814,
      bbh: 0.765,
      humaneval: 0.72,
      gsm8k: 0.86,
    },
    job_id: "job-bm-001",
    error: null,
    created_at: "2026-11-13T10:00:00Z",
    started_at: "2026-11-13T10:01:00Z",
    finished_at: "2026-11-13T10:24:30Z",
    task_results: [
      {
        id: "tr-01-01",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-01",
        task_name: "mmlu_pro",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 5,
        sample_count: 100,
        primary_metric: "acc",
        primary_score: 0.684,
        metrics: {
          acc: 0.684,
          acc_norm: 0.691,
          logits_acc: 0.702,
          gen_acc: 0.684,
          parse_error_pct: 1.8,
        },
        latency_ms_p50: 142.5,
        latency_ms_p95: 295.0,
        created_at: "2026-11-13T10:05:00Z",
      },
      {
        id: "tr-01-02",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-01",
        task_name: "gpqa_diamond",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 0,
        sample_count: 198,
        primary_metric: "acc_norm",
        primary_score: 0.382,
        metrics: {
          acc_norm: 0.382,
          acc: 0.374,
          logits_acc: 0.391,
          gen_acc: 0.382,
          parse_error_pct: 0.5,
        },
        latency_ms_p50: 168.0,
        latency_ms_p95: 310.2,
        created_at: "2026-11-13T10:09:00Z",
      },
      {
        id: "tr-01-03",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-01",
        task_name: "ifeval",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 0,
        sample_count: 541,
        primary_metric: "strict_acc",
        primary_score: 0.814,
        metrics: {
          strict_acc: 0.814,
          loose_acc: 0.852,
          parse_error_pct: 0.0,
        },
        latency_ms_p50: 210.4,
        latency_ms_p95: 480.0,
        created_at: "2026-11-13T10:14:00Z",
      },
      {
        id: "tr-01-04",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-01",
        task_name: "bbh",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 3,
        sample_count: 250,
        primary_metric: "acc_norm",
        primary_score: 0.765,
        metrics: {
          acc_norm: 0.765,
          acc: 0.758,
          logits_acc: 0.778,
          gen_acc: 0.765,
          parse_error_pct: 1.2,
        },
        latency_ms_p50: 185.0,
        latency_ms_p95: 365.1,
        created_at: "2026-11-13T10:18:00Z",
      },
      {
        id: "tr-01-05",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-01",
        task_name: "humaneval",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 0,
        sample_count: 164,
        primary_metric: "pass@1",
        primary_score: 0.72,
        metrics: {
          "pass@1": 0.72,
          parse_error_pct: 0.0,
        },
        latency_ms_p50: 310.0,
        latency_ms_p95: 620.0,
        created_at: "2026-11-13T10:21:00Z",
      },
      {
        id: "tr-01-06",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-01",
        task_name: "gsm8k",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 8,
        sample_count: 1319,
        primary_metric: "exact_match",
        primary_score: 0.86,
        metrics: {
          exact_match: 0.86,
          acc: 0.86,
          parse_error_pct: 0.8,
        },
        latency_ms_p50: 240.0,
        latency_ms_p95: 490.0,
        created_at: "2026-11-13T10:24:00Z",
      },
    ],
  },
  {
    id: "bm-run-02",
    project_id: "proj-default",
    name: "Mistral-7B-v0.3 Evaluation Run",
    suite: "lm_eval",
    tasks: ["mmlu_pro", "gpqa_diamond", "ifeval", "bbh", "humaneval", "gsm8k"],
    runner_backend: "vllm",
    runner_config: {
      tensor_parallel_size: 1,
      gpu_memory_utilization: 0.85,
      max_model_len: 8192,
    },
    eval_mode: "generate_until",
    num_fewshot: 5,
    target_model: "mistralai/Mistral-7B-Instruct-v0.3",
    model_version_id: "mv-mistral-v1",
    dataset_id: null,
    dataset_commit_id: null,
    status: "completed",
    summary_metrics: {
      avg_score: 0.655,
      mmlu_pro: 0.612,
      gpqa_diamond: 0.341,
      ifeval: 0.782,
      bbh: 0.718,
      humaneval: 0.665,
      gsm8k: 0.812,
    },
    job_id: "job-bm-002",
    error: null,
    created_at: "2026-11-13T11:00:00Z",
    started_at: "2026-11-13T11:01:00Z",
    finished_at: "2026-11-13T11:23:45Z",
    task_results: [
      {
        id: "tr-02-01",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-02",
        task_name: "mmlu_pro",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 5,
        sample_count: 100,
        primary_metric: "acc",
        primary_score: 0.612,
        metrics: {
          acc: 0.612,
          acc_norm: 0.62,
          logits_acc: 0.628,
          gen_acc: 0.612,
          parse_error_pct: 2.4,
        },
        latency_ms_p50: 135.0,
        latency_ms_p95: 280.0,
        created_at: "2026-11-13T11:05:00Z",
      },
      {
        id: "tr-02-02",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-02",
        task_name: "gpqa_diamond",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 0,
        sample_count: 198,
        primary_metric: "acc_norm",
        primary_score: 0.341,
        metrics: {
          acc_norm: 0.341,
          acc: 0.335,
          logits_acc: 0.355,
          gen_acc: 0.341,
          parse_error_pct: 0.9,
        },
        latency_ms_p50: 155.0,
        latency_ms_p95: 295.0,
        created_at: "2026-11-13T11:09:00Z",
      },
      {
        id: "tr-02-03",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-02",
        task_name: "ifeval",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 0,
        sample_count: 541,
        primary_metric: "strict_acc",
        primary_score: 0.782,
        metrics: {
          strict_acc: 0.782,
          loose_acc: 0.825,
          parse_error_pct: 0.0,
        },
        latency_ms_p50: 198.0,
        latency_ms_p95: 440.0,
        created_at: "2026-11-13T11:14:00Z",
      },
      {
        id: "tr-02-04",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-02",
        task_name: "bbh",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 3,
        sample_count: 250,
        primary_metric: "acc_norm",
        primary_score: 0.718,
        metrics: {
          acc_norm: 0.718,
          acc: 0.71,
          logits_acc: 0.73,
          gen_acc: 0.718,
          parse_error_pct: 1.5,
        },
        latency_ms_p50: 172.0,
        latency_ms_p95: 340.0,
        created_at: "2026-11-13T11:18:00Z",
      },
      {
        id: "tr-02-05",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-02",
        task_name: "humaneval",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 0,
        sample_count: 164,
        primary_metric: "pass@1",
        primary_score: 0.665,
        metrics: {
          "pass@1": 0.665,
          parse_error_pct: 0.0,
        },
        latency_ms_p50: 290.0,
        latency_ms_p95: 580.0,
        created_at: "2026-11-13T11:21:00Z",
      },
      {
        id: "tr-02-06",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-02",
        task_name: "gsm8k",
        modality: "text",
        eval_mode: "generate_until",
        num_fewshot: 8,
        sample_count: 1319,
        primary_metric: "exact_match",
        primary_score: 0.812,
        metrics: {
          exact_match: 0.812,
          acc: 0.812,
          parse_error_pct: 1.1,
        },
        latency_ms_p50: 220.0,
        latency_ms_p95: 460.0,
        created_at: "2026-11-13T11:23:00Z",
      },
    ],
  },
  {
    id: "bm-run-03",
    project_id: "proj-default",
    name: "Qwen2-VL-7B-Instruct Multimodal Evaluation",
    suite: "vlmevalkit",
    tasks: ["mmmu_pro", "mathvista", "docvqa", "chartqa", "ocrbench", "pope", "vidore"],
    runner_backend: "vllm",
    runner_config: {
      tensor_parallel_size: 2,
      gpu_memory_utilization: 0.9,
      max_model_len: 4096,
    },
    eval_mode: "multimodal",
    num_fewshot: 0,
    target_model: "Qwen/Qwen2-VL-7B-Instruct",
    model_version_id: "mv-qwenvl-v1",
    dataset_id: null,
    dataset_commit_id: null,
    status: "completed",
    summary_metrics: {
      avg_score: 0.771,
      mmmu_pro: 0.478,
      mathvista: 0.623,
      docvqa: 0.912,
      chartqa: 0.824,
      ocrbench: 0.852,
      pope: 0.891,
      vidore: 0.789,
    },
    job_id: "job-bm-003",
    error: null,
    created_at: "2026-11-13T12:00:00Z",
    started_at: "2026-11-13T12:01:00Z",
    finished_at: "2026-11-13T12:35:10Z",
    task_results: [
      {
        id: "tr-03-01",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "mmmu_pro",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 1730,
        primary_metric: "acc",
        primary_score: 0.478,
        metrics: {
          acc: 0.478,
          reason_acc: 0.492,
        },
        latency_ms_p50: 340.0,
        latency_ms_p95: 720.0,
        created_at: "2026-11-13T12:07:00Z",
      },
      {
        id: "tr-03-02",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "mathvista",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 1000,
        primary_metric: "acc",
        primary_score: 0.623,
        metrics: {
          acc: 0.623,
          visual_math_acc: 0.641,
        },
        latency_ms_p50: 380.0,
        latency_ms_p95: 810.0,
        created_at: "2026-11-13T12:12:00Z",
      },
      {
        id: "tr-03-03",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "docvqa",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 500,
        primary_metric: "anls",
        primary_score: 0.912,
        metrics: {
          anls: 0.912,
          acc: 0.895,
        },
        latency_ms_p50: 290.0,
        latency_ms_p95: 590.0,
        created_at: "2026-11-13T12:18:00Z",
      },
      {
        id: "tr-03-04",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "chartqa",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 500,
        primary_metric: "relaxed_acc",
        primary_score: 0.824,
        metrics: {
          relaxed_acc: 0.824,
          acc: 0.798,
        },
        latency_ms_p50: 310.0,
        latency_ms_p95: 650.0,
        created_at: "2026-11-13T12:23:00Z",
      },
      {
        id: "tr-03-05",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "ocrbench",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 1000,
        primary_metric: "score",
        primary_score: 0.852,
        metrics: {
          score: 0.852,
          acc: 0.852,
        },
        latency_ms_p50: 260.0,
        latency_ms_p95: 510.0,
        created_at: "2026-11-13T12:28:00Z",
      },
      {
        id: "tr-03-06",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "pope",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 500,
        primary_metric: "f1",
        primary_score: 0.891,
        metrics: {
          f1: 0.891,
          acc: 0.887,
        },
        latency_ms_p50: 275.0,
        latency_ms_p95: 530.0,
        created_at: "2026-11-13T12:31:00Z",
      },
      {
        id: "tr-03-07",
        project_id: "proj-default",
        benchmark_run_id: "bm-run-03",
        task_name: "vidore",
        modality: "multimodal",
        eval_mode: "multimodal",
        num_fewshot: 0,
        sample_count: 400,
        primary_metric: "ndcg@5",
        primary_score: 0.789,
        metrics: {
          "ndcg@5": 0.789,
          map: 0.742,
        },
        latency_ms_p50: 360.0,
        latency_ms_p95: 780.0,
        created_at: "2026-11-13T12:35:00Z",
      },
    ],
  },
];

let benchmarkRunsState = [...MOCK_BENCHMARK_RUNS];

export function getMockBenchmarkRuns(params: Params = {}): ListResponse<BenchmarkRun> {
  let list = [...benchmarkRunsState];
  if (params.suite) list = list.filter((r) => r.suite === params.suite);
  if (params.target_model) list = list.filter((r) => r.target_model.includes(String(params.target_model)));
  if (params.status) list = list.filter((r) => r.status === params.status);
  return page(list, params);
}

export function getMockBenchmarkRun(id: string): BenchmarkRun | undefined {
  return benchmarkRunsState.find((r) => r.id === id);
}

export function createMockBenchmarkRun(req: CreateBenchmarkRunRequest): BenchmarkRun {
  const newRun: BenchmarkRun = {
    id: `bm-run-${Date.now().toString(36)}`,
    project_id: "proj-default",
    name: req.name,
    suite: req.suite,
    tasks: req.tasks,
    runner_backend: req.runner_backend || "local_sim",
    runner_config: req.runner_config || {},
    eval_mode: req.eval_mode || "generate_until",
    num_fewshot: req.num_fewshot ?? 0,
    target_model: req.target_model,
    model_version_id: req.model_version_id || null,
    dataset_id: req.dataset_id || null,
    dataset_commit_id: req.dataset_commit_id || null,
    status: req.sync_exec ? "completed" : "running",
    summary_metrics: req.sync_exec ? { avg_score: 0.75 } : {},
    job_id: `job-${Date.now().toString(36)}`,
    error: null,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    finished_at: req.sync_exec ? new Date().toISOString() : null,
    task_results: req.tasks.map((task, idx) => ({
      id: `tr-sim-${idx}`,
      project_id: "proj-default",
      benchmark_run_id: `bm-run-${Date.now().toString(36)}`,
      task_name: task,
      modality: req.suite === "vlmevalkit" ? "multimodal" : "text",
      eval_mode: req.eval_mode || "generate_until",
      num_fewshot: req.num_fewshot ?? 0,
      sample_count: 50,
      primary_metric: "score",
      primary_score: 0.75,
      metrics: { score: 0.75, logits_acc: 0.77, gen_acc: 0.75 },
      latency_ms_p50: 150.0,
      latency_ms_p95: 300.0,
      created_at: new Date().toISOString(),
    })),
  };
  benchmarkRunsState.unshift(newRun);
  return newRun;
}

export function cancelMockBenchmarkRun(id: string): BenchmarkRun {
  const run = benchmarkRunsState.find((r) => r.id === id);
  if (!run) throw new ApiError(404, "benchmark run not found");
  run.status = "cancelled";
  run.finished_at = new Date().toISOString();
  return run;
}

// ── 3. Inspect AI Sandboxes & Transcripts ──────────────────────────────────

export const MOCK_SANDBOX_RUNS: SandboxRun[] = [
  {
    id: "sb-run-01",
    project_id: "proj-default",
    name: "Agent Code Refactor",
    runner_backend: "docker",
    dataset_id: "ds-code-tasks",
    dataset_commit_id: "c-code-01",
    solver_config: {
      strategy: "react",
      max_steps: 15,
      model: "claude-3-5-sonnet",
      token_limit: 8000,
    },
    scorer_config: {
      type: "pytest_exit_code",
      test_path: "tests/test_refactor.py",
    },
    sandbox_policy: {
      network_mode: "none",
      timeout_seconds: 120,
      memory_mb: 2048,
      cpus: 2,
    },
    status: "completed",
    pass_rate: 1.0,
    total_tasks: 1,
    passed_tasks: 1,
    total_tokens: 4210,
    total_cost_usd: 0.0126,
    job_id: "job-sb-001",
    error: null,
    created_at: "2026-11-13T14:00:00Z",
    started_at: "2026-11-13T14:00:05Z",
    finished_at: "2026-11-13T14:00:25Z",
  },
  {
    id: "sb-run-02",
    project_id: "proj-default",
    name: "Web Scraper Fix",
    runner_backend: "docker",
    dataset_id: "ds-scraper-tasks",
    dataset_commit_id: "c-scraper-01",
    solver_config: {
      strategy: "react",
      max_steps: 10,
      model: "gpt-4o",
    },
    scorer_config: {
      type: "json_schema_assert",
      target_schema: "PricingTier",
    },
    sandbox_policy: {
      network_mode: "none",
      timeout_seconds: 60,
      memory_mb: 1024,
    },
    status: "completed",
    pass_rate: 1.0,
    total_tasks: 1,
    passed_tasks: 1,
    total_tokens: 3890,
    total_cost_usd: 0.0116,
    job_id: "job-sb-002",
    error: null,
    created_at: "2026-11-13T14:30:00Z",
    started_at: "2026-11-13T14:30:05Z",
    finished_at: "2026-11-13T14:30:22Z",
  },
  {
    id: "sb-run-03",
    project_id: "proj-default",
    name: "SQL Migration Agent",
    runner_backend: "local_sandbox",
    dataset_id: "ds-sql-tasks",
    dataset_commit_id: "c-sql-01",
    solver_config: {
      strategy: "react",
      max_steps: 5,
      model: "claude-3-5-haiku",
    },
    scorer_config: {
      type: "sqlite_foreign_key_check",
    },
    sandbox_policy: {
      network_mode: "none",
      timeout_seconds: 30,
    },
    status: "failed",
    pass_rate: 0.0,
    total_tasks: 1,
    passed_tasks: 0,
    total_tokens: 5120,
    total_cost_usd: 0.0153,
    job_id: "job-sb-003",
    error: "Foreign key integrity failure on migration step 3",
    created_at: "2026-11-13T15:00:00Z",
    started_at: "2026-11-13T15:00:05Z",
    finished_at: "2026-11-13T15:00:18Z",
  },
];

export const MOCK_SANDBOX_TRANSCRIPTS: Record<string, SandboxTaskTranscript[]> = {
  "sb-run-01": [
    {
      id: "sb-tr-01",
      project_id: "proj-default",
      sandbox_run_id: "sb-run-01",
      task_key: "task-refactor-01",
      status: "passed",
      score: 1.0,
      scorer_details: {
        method: "unit_test",
        passed_tests: 1,
        total_tests: 1,
        coverage_pct: 98.4,
      },
      duration_ms: 18450,
      input_tokens: 3120,
      output_tokens: 1090,
      error: null,
      created_at: "2026-11-13T14:00:25Z",
      verified_artifacts: [
        { path: "src/service.py", sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", size_bytes: 1420 },
      ],
      steps: [
        {
          step: 1,
          kind: "bash",
          tool_name: "bash",
          thought: "Execute test suite to observe initial regression failure",
          tool_args: { cmd: "pytest -q tests/test_refactor.py" },
          stdout: "FAILED tests/test_refactor.py::test_service_contract - AssertionError: expected status 200, got 500\n1 failed in 0.42s",
          stderr: "",
          exit_code: 1,
          duration_ms: 1250,
          input_tokens: 410,
          output_tokens: 180,
        },
        {
          step: 2,
          kind: "python",
          tool_name: "python",
          thought: "Inspect AST of src/service.py to locate error-handling branch",
          tool_args: {
            code: "import ast\nwith open('src/service.py') as f:\n    tree = ast.parse(f.read())\nprint([n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)])",
          },
          stdout: "['__init__', 'process_request', 'validate_payload', 'send_response']",
          stderr: "",
          exit_code: 0,
          duration_ms: 450,
          input_tokens: 520,
          output_tokens: 110,
        },
        {
          step: 3,
          kind: "file_edit",
          tool_name: "file_edit",
          thought: "Patch status code mapping and validation branch in src/service.py",
          tool_args: { path: "src/service.py" },
          stdout: "File 'src/service.py' written successfully",
          stderr: "",
          exit_code: 0,
          duration_ms: 320,
          input_tokens: 1100,
          output_tokens: 420,
          diff: {
            before: "def process_request(self, req):\n    return self.send_response(data=req.body)",
            after: "def process_request(self, req):\n    valid = self.validate_payload(req.body)\n    return self.send_response(data=req.body, status=200 if valid else 400)",
            hunks: [
              {
                old_start: 14,
                old_lines: 2,
                new_start: 14,
                new_lines: 3,
                lines: [
                  " def process_request(self, req):",
                  "-    return self.send_response(data=req.body)",
                  "+    valid = self.validate_payload(req.body)",
                  "+    return self.send_response(data=req.body, status=200 if valid else 400)",
                ],
              },
            ],
          },
        },
        {
          step: 4,
          kind: "scorer",
          tool_name: "scorer",
          thought: "Re-run test suite and check test assertions",
          tool_args: { target: "tests/test_refactor.py" },
          stdout: "1 passed in 0.38s\nCoverage: 98.4%",
          stderr: "",
          exit_code: 0,
          duration_ms: 980,
          input_tokens: 650,
          output_tokens: 210,
        },
      ],
    },
  ],
  "sb-run-02": [
    {
      id: "sb-tr-02",
      project_id: "proj-default",
      sandbox_run_id: "sb-run-02",
      task_key: "task-scraper-01",
      status: "passed",
      score: 1.0,
      scorer_details: {
        method: "json_schema",
        extracted_count: 3,
        valid_count: 3,
      },
      duration_ms: 12400,
      input_tokens: 2800,
      output_tokens: 1090,
      error: null,
      created_at: "2026-11-13T14:30:22Z",
      steps: [
        {
          step: 1,
          kind: "bash",
          tool_name: "bash",
          thought: "Run scraper against fixture HTML",
          tool_args: { cmd: "python scraper.py --test" },
          stdout: "Error: CSS selector '.pricing-card-v2' returned 0 elements",
          stderr: "",
          exit_code: 1,
          duration_ms: 940,
        },
        {
          step: 2,
          kind: "file_edit",
          tool_name: "file_edit",
          thought: "Update CSS selector to match data attribute",
          tool_args: { path: "scraper.py" },
          stdout: "File 'scraper.py' updated",
          stderr: "",
          exit_code: 0,
          duration_ms: 280,
          diff: {
            before: "selector = '.pricing-card-v2'",
            after: "selector = '[data-test=pricing-tier]'",
            hunks: [
              {
                lines: [
                  "-selector = '.pricing-card-v2'",
                  "+selector = '[data-test=pricing-tier]'",
                ],
              },
            ],
          },
        },
        {
          step: 3,
          kind: "python",
          tool_name: "python",
          thought: "Validate extracted output schema",
          tool_args: {
            code: "from scraper import scrape\nitems = scrape()\nprint(len(items))",
          },
          stdout: "3\nExtracted tiers: Starter, Pro, Enterprise",
          stderr: "",
          exit_code: 0,
          duration_ms: 410,
        },
        {
          step: 4,
          kind: "scorer",
          tool_name: "scorer",
          thought: "Validate schema assertions on extracted items",
          tool_args: { schema: "PricingSchema" },
          stdout: "Schema validation passed: 3 valid items",
          stderr: "",
          exit_code: 0,
          duration_ms: 320,
        },
      ],
    },
  ],
  "sb-run-03": [
    {
      id: "sb-tr-03",
      project_id: "proj-default",
      sandbox_run_id: "sb-run-03",
      task_key: "task-migration-01",
      status: "failed",
      score: 0.0,
      scorer_details: {
        method: "foreign_key_check",
        orphans_detected: 2,
      },
      duration_ms: 9800,
      input_tokens: 3900,
      output_tokens: 1220,
      error: "Integrity check failed: 2 orphan rows detected",
      created_at: "2026-11-13T15:00:18Z",
      steps: [
        {
          step: 1,
          kind: "bash",
          tool_name: "bash",
          thought: "Check current database migration status",
          tool_args: { cmd: "alembic current" },
          stdout: "0005_mlops_studio (head)",
          stderr: "",
          exit_code: 0,
          duration_ms: 820,
        },
        {
          step: 2,
          kind: "file_edit",
          tool_name: "file_edit",
          thought: "Draft migration file for schema evolution",
          tool_args: { path: "alembic/versions/0006_migration.py" },
          stdout: "File written",
          stderr: "",
          exit_code: 0,
          duration_ms: 310,
        },
        {
          step: 3,
          kind: "bash",
          tool_name: "bash",
          thought: "Apply migration in test database",
          tool_args: { cmd: "alembic upgrade head" },
          stdout: "",
          stderr: "sqlite3.IntegrityError: FOREIGN KEY constraint failed on dataset_commit_items.commit_id",
          exit_code: 1,
          duration_ms: 1100,
        },
        {
          step: 4,
          kind: "scorer",
          tool_name: "scorer",
          thought: "Run PRAGMA foreign_key_check",
          tool_args: { check: "PRAGMA foreign_key_check" },
          stdout: "",
          stderr: "Integrity check failed: 2 orphan rows detected",
          exit_code: 1,
          duration_ms: 220,
        },
      ],
    },
  ],
};

let sandboxRunsState = [...MOCK_SANDBOX_RUNS];

export function getMockSandboxRuns(params: Params = {}): ListResponse<SandboxRun> {
  let list = [...sandboxRunsState];
  if (params.status) list = list.filter((r) => r.status === params.status);
  return page(list, params);
}

export function getMockSandboxRun(id: string): SandboxRun | undefined {
  return sandboxRunsState.find((r) => r.id === id);
}

export function getMockSandboxTranscripts(runId: string): SandboxTaskTranscript[] {
  return MOCK_SANDBOX_TRANSCRIPTS[runId] || [];
}

export function getMockSandboxTranscript(
  runId: string,
  taskKey: string,
): SandboxTaskTranscript | undefined {
  const list = MOCK_SANDBOX_TRANSCRIPTS[runId] || [];
  return list.find((t) => t.task_key === taskKey);
}

export function createMockSandboxRun(req: CreateSandboxRunRequest): SandboxRun {
  const newRun: SandboxRun = {
    id: `sb-run-${Date.now().toString(36)}`,
    project_id: "proj-default",
    name: req.name,
    runner_backend: req.runner_backend || "local_sandbox",
    dataset_id: req.dataset_id || null,
    dataset_commit_id: req.dataset_commit_id || null,
    solver_config: req.solver_config || {},
    scorer_config: req.scorer_config || {},
    sandbox_policy: req.sandbox_policy || {},
    status: req.sync_exec ? "completed" : "running",
    pass_rate: req.sync_exec ? 1.0 : null,
    total_tasks: 1,
    passed_tasks: req.sync_exec ? 1 : 0,
    total_tokens: 1500,
    total_cost_usd: 0.0045,
    job_id: `job-sb-${Date.now().toString(36)}`,
    error: null,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    finished_at: req.sync_exec ? new Date().toISOString() : null,
  };
  sandboxRunsState.unshift(newRun);
  return newRun;
}

export function cancelMockSandboxRun(id: string): SandboxRun {
  const run = sandboxRunsState.find((r) => r.id === id);
  if (!run) throw new ApiError(404, "sandbox run not found");
  run.status = "cancelled";
  run.finished_at = new Date().toISOString();
  return run;
}

// ── 4. Zero-Copy Dataset Versioning & Multimodal Media ──────────────────────

export const MOCK_DATASET_BRANCHES: DatasetBranch[] = [
  {
    id: "br-main",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    name: "main",
    head_commit_id: "c-main-02",
    is_default: true,
    created_by: "system",
    created_at: "2026-11-13T08:00:00Z",
    updated_at: "2026-11-13T09:30:00Z",
  },
  {
    id: "br-feat",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    name: "feat/multimodal-v2",
    head_commit_id: "c-feat-01",
    is_default: false,
    created_by: "researcher@matrix.local",
    created_at: "2026-11-13T09:00:00Z",
    updated_at: "2026-11-13T10:15:00Z",
  },
  {
    id: "br-exp",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    name: "exp/dpo-pairs",
    head_commit_id: "c-exp-01",
    is_default: false,
    created_by: "alignment@matrix.local",
    created_at: "2026-11-13T10:00:00Z",
    updated_at: "2026-11-13T11:00:00Z",
  },
];

export const MOCK_DATASET_COMMITS: DatasetCommit[] = [
  {
    id: "c-main-01",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    parent_commit_id: null,
    second_parent_commit_id: null,
    tree_hash: "a1b2c3d4e5f60001",
    message: "Initial multimodal dataset commit",
    author: "curator@matrix.local",
    item_count: 4,
    stats: { added: 4, removed: 0, modified: 0 },
    created_at: "2026-11-13T08:00:00Z",
  },
  {
    id: "c-main-02",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    parent_commit_id: "c-main-01",
    second_parent_commit_id: null,
    tree_hash: "a1b2c3d4e5f60002",
    message: "feat: add verified OCR and visual bounding boxes",
    author: "curator@matrix.local",
    item_count: 4,
    stats: { added: 0, removed: 0, modified: 2 },
    created_at: "2026-11-13T09:30:00Z",
  },
  {
    id: "c-feat-01",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    parent_commit_id: "c-main-02",
    second_parent_commit_id: null,
    tree_hash: "a1b2c3d4e5f60003",
    message: "feat: high-resolution ColPali PDF visual anchors",
    author: "researcher@matrix.local",
    item_count: 5,
    stats: { added: 1, removed: 0, modified: 1 },
    created_at: "2026-11-13T10:15:00Z",
  },
  {
    id: "c-exp-01",
    project_id: "proj-default",
    dataset_id: "ds-sota-multimodal",
    parent_commit_id: "c-main-02",
    second_parent_commit_id: null,
    tree_hash: "a1b2c3d4e5f60004",
    message: "exp: preference pairs for direct alignment",
    author: "alignment@matrix.local",
    item_count: 6,
    stats: { added: 2, removed: 0, modified: 0 },
    created_at: "2026-11-13T11:00:00Z",
  },
];

export const MOCK_DATASET_VERSIONED_ITEMS: DatasetCommitItem[] = [
  {
    id: "dci-01",
    commit_id: "c-main-02",
    item_key: "item-table-q3",
    content_hash: "sha256-tbl-001",
    input: {
      question: "What is the total revenue in Q3 shown in the table?",
      context: "Financial report for fiscal year 2026",
    },
    expected_output: "The total revenue in Q3 is $42.5M.",
    context: ["Page 4, Table 2.1: Financial Summary"],
    metadata: { domain: "finance", modality: "image" },
    artifact_refs: ["art-img-table-01"],
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "image",
        url: "https://assets.matrix.local/images/quarterly-financial-table.png",
        mime: "image/png",
        width: 1024,
        height: 768,
        boxes: [
          { x: 120, y: 180, w: 240, h: 60, label: "Q3 Revenue Header", score: 0.99 },
          { x: 380, y: 180, w: 120, h: 60, label: "$42.5M Value", score: 0.98 },
        ],
      },
    ],
  },
  {
    id: "dci-02",
    commit_id: "c-main-02",
    item_key: "item-audio-cs",
    content_hash: "sha256-aud-002",
    input: {
      prompt: "Transcribe the customer service audio recording accurately.",
    },
    expected_output: "Hello, I would like to confirm my subscription renewal date.",
    context: ["Call Center Recording #49281"],
    metadata: { domain: "customer_support", modality: "audio" },
    artifact_refs: ["art-aud-call-02"],
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "audio",
        url: "https://assets.matrix.local/audio/customer-call-12.wav",
        mime: "audio/wav",
        duration_ms: 14200,
      },
    ],
  },
  {
    id: "dci-03",
    commit_id: "c-main-02",
    item_key: "item-pdf-clause",
    content_hash: "sha256-pdf-003",
    input: {
      query: "Extract section 4.2 compliance clause from the security audit report.",
    },
    expected_output: "Section 4.2 specifies that all encryption keys must be rotated every 90 days.",
    context: ["Security Audit Report v3.1, Page 4"],
    metadata: { domain: "compliance", modality: "pdf" },
    artifact_refs: ["art-pdf-audit-03"],
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "pdf",
        url: "https://assets.matrix.local/docs/security-audit-report.pdf",
        mime: "application/pdf",
        page: 4,
        width: 1200,
        height: 1600,
      },
    ],
  },
  {
    id: "dci-04",
    commit_id: "c-main-02",
    item_key: "item-diff-patch",
    content_hash: "sha256-dif-004",
    input: {
      task: "Summarize code refactoring changes between releases.",
    },
    expected_output: "Updated authentication middleware to use JWT bearer tokens instead of session cookies.",
    context: ["PR #1042: Migrate session auth to JWT"],
    metadata: { domain: "code", modality: "text_diff" },
    artifact_refs: ["art-dif-auth-04"],
    created_at: "2026-11-13T09:30:00Z",
    media: [
      {
        kind: "text_diff",
        url: "https://assets.matrix.local/diffs/auth-middleware.diff",
        mime: "text/x-diff",
        diff: {
          before: "function auth(req) {\n  return req.cookies.session_id;\n}",
          after: "function auth(req) {\n  return req.headers.authorization?.replace(/^Bearer /, '');\n}",
          hunks: [
            {
              old_start: 1,
              old_lines: 3,
              new_start: 1,
              new_lines: 3,
              lines: [
                " function auth(req) {",
                "-  return req.cookies.session_id;",
                "+  return req.headers.authorization?.replace(/^Bearer /, '');",
                " }",
              ],
            },
          ],
        },
      },
    ],
  },
];

let datasetBranchesState = [...MOCK_DATASET_BRANCHES];
let datasetCommitsState = [...MOCK_DATASET_COMMITS];
let datasetItemsState = [...MOCK_DATASET_VERSIONED_ITEMS];

export function getMockDatasetBranches(datasetId: string): DatasetBranch[] {
  const matches = datasetBranchesState.filter(
    (b) => b.dataset_id === datasetId || datasetId.startsWith("ds-") || datasetId === "ds_rag_golden",
  );
  if (matches.length > 0) return matches;
  return datasetBranchesState.map((b) => ({ ...b, dataset_id: datasetId }));
}

export function createMockDatasetBranch(
  datasetId: string,
  req: CreateDatasetBranchRequest,
): DatasetBranch {
  const fromBr = datasetBranchesState.find((b) => b.name === (req.from_branch || "main"));
  const newBranch: DatasetBranch = {
    id: `br-${Date.now().toString(36)}`,
    project_id: "proj-default",
    dataset_id: datasetId,
    name: req.name,
    head_commit_id: req.from_commit_id || fromBr?.head_commit_id || "c-main-02",
    is_default: false,
    created_by: "current-user@matrix.local",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  datasetBranchesState.push(newBranch);
  return newBranch;
}

export function getMockDatasetCommits(
  datasetId: string,
  branch?: string,
): DatasetCommit[] {
  let list = datasetCommitsState.filter(
    (c) => c.dataset_id === datasetId || datasetId.startsWith("ds-") || datasetId === "ds_rag_golden",
  );
  if (list.length === 0) {
    list = datasetCommitsState.map((c) => ({ ...c, dataset_id: datasetId }));
  }
  if (branch) {
    const br = datasetBranchesState.find((b) => b.name === branch);
    if (br?.head_commit_id) {
      list = list.filter((c) => c.id === br.head_commit_id || c.parent_commit_id === br.head_commit_id || true);
    }
  }
  return list;
}

export function createMockDatasetCommit(
  datasetId: string,
  req: CreateDatasetCommitRequest,
): DatasetCommit {
  const branchName = req.branch || "main";
  const branch = datasetBranchesState.find((b) => b.name === branchName);
  const parentId = branch?.head_commit_id || "c-main-02";

  const newCommit: DatasetCommit = {
    id: `c-${Date.now().toString(36)}`,
    project_id: "proj-default",
    dataset_id: datasetId,
    parent_commit_id: parentId,
    second_parent_commit_id: null,
    tree_hash: `hash-${Date.now().toString(36)}`,
    message: req.message,
    author: req.author || "current-user@matrix.local",
    item_count: (datasetItemsState.length || 4) + 1,
    stats: { added: 1, removed: 0, modified: 0 },
    created_at: new Date().toISOString(),
  };
  datasetCommitsState.unshift(newCommit);
  if (branch) {
    branch.head_commit_id = newCommit.id;
    branch.updated_at = newCommit.created_at;
  }
  return newCommit;
}

export function getMockDatasetCommitItems(
  _datasetId: string,
  commitId: string,
): DatasetCommitItem[] {
  return datasetItemsState.filter((it) => it.commit_id === commitId || true);
}

export function getMockDatasetDiff(
  datasetId: string,
  base: string,
  head: string,
): DatasetDiffResult {
  const baseCommitId = base.startsWith("c-")
    ? base
    : datasetBranchesState.find((b) => b.name === base)?.head_commit_id || "c-main-01";
  const headCommitId = head.startsWith("c-")
    ? head
    : datasetBranchesState.find((b) => b.name === head)?.head_commit_id || "c-feat-01";

  const added = ["item-docvqa-anchor-05"];
  const removed: string[] = [];
  const modified = ["item-table-q3"];

  return {
    dataset_id: datasetId,
    base_commit_id: baseCommitId,
    head_commit_id: headCommitId,
    added,
    removed,
    modified,
    stats: {
      added: added.length,
      removed: removed.length,
      modified: modified.length,
    },
  };
}

export function mergeMockDatasetBranches(
  datasetId: string,
  req: MergeBranchesRequest,
): DatasetCommit {
  const targetBranchName = req.target_branch || "main";
  const targetBr = datasetBranchesState.find((b) => b.name === targetBranchName);
  const sourceBr = datasetBranchesState.find((b) => b.name === req.source_branch);

  const mergeCommit: DatasetCommit = {
    id: `c-merge-${Date.now().toString(36)}`,
    project_id: "proj-default",
    dataset_id: datasetId,
    parent_commit_id: targetBr?.head_commit_id || "c-main-02",
    second_parent_commit_id: sourceBr?.head_commit_id || "c-feat-01",
    tree_hash: `tree-merge-${Date.now().toString(36)}`,
    message: req.message || `Merge branch '${req.source_branch}' into '${targetBranchName}'`,
    author: req.author || "merger@matrix.local",
    item_count: (datasetItemsState.length || 4) + 1,
    stats: { added: 1, removed: 0, modified: 1 },
    created_at: new Date().toISOString(),
  };

  datasetCommitsState.unshift(mergeCommit);
  if (targetBr) {
    targetBr.head_commit_id = mergeCommit.id;
    targetBr.updated_at = mergeCommit.created_at;
  }
  return mergeCommit;
}

export function revertMockDatasetBranch(
  datasetId: string,
  req: RevertBranchRequest,
): DatasetCommit {
  const branch = datasetBranchesState.find((b) => b.name === req.branch);
  const revertCommit: DatasetCommit = {
    id: `c-revert-${Date.now().toString(36)}`,
    project_id: "proj-default",
    dataset_id: datasetId,
    parent_commit_id: branch?.head_commit_id || "c-main-02",
    second_parent_commit_id: null,
    tree_hash: `tree-revert-${Date.now().toString(36)}`,
    message: req.message || `Revert to commit ${req.target_commit_id}`,
    author: req.author || "reverter@matrix.local",
    item_count: datasetItemsState.length || 4,
    stats: { added: 0, removed: 1, modified: 1 },
    created_at: new Date().toISOString(),
  };

  datasetCommitsState.unshift(revertCommit);
  if (branch) {
    branch.head_commit_id = revertCommit.id;
    branch.updated_at = revertCommit.created_at;
  }
  return revertCommit;
}

// ── 5. Router Matcher Helpers (sotaGet & sotaSend) ─────────────────────────

export function sotaGet(p: string, params: Params = {}): unknown {
  let m: RegExpExecArray | null;

  // Benchmarks
  if (p === "/api/v1/benchmarks/catalog") {
    const suite = params.suite ? String(params.suite) : undefined;
    const resp: BenchmarkCatalogResponse = { tasks: getMockBenchmarkCatalog(suite) };
    return resp;
  }

  if (p === "/api/v1/benchmarks/runs") {
    return getMockBenchmarkRuns(params);
  }

  if ((m = /^\/api\/v1\/benchmarks\/runs\/([^/]+)$/.exec(p))) {
    const run = getMockBenchmarkRun(m[1]);
    if (!run) throw new ApiError(404, "benchmark run not found");
    return run;
  }

  // Sandboxes
  if (p === "/api/v1/sandboxes/runs") {
    return getMockSandboxRuns(params);
  }

  if ((m = /^\/api\/v1\/sandboxes\/runs\/([^/]+)$/.exec(p))) {
    const run = getMockSandboxRun(m[1]);
    if (!run) throw new ApiError(404, "sandbox run not found");
    return run;
  }

  if ((m = /^\/api\/v1\/sandboxes\/runs\/([^/]+)\/transcripts\/([^/]+)$/.exec(p))) {
    const transcript = getMockSandboxTranscript(m[1], m[2]);
    if (!transcript) throw new ApiError(404, "sandbox transcript not found");
    return transcript;
  }

  if ((m = /^\/api\/v1\/sandboxes\/runs\/([^/]+)\/transcripts$/.exec(p))) {
    const list = getMockSandboxTranscripts(m[1]);
    return page(list, params);
  }

  // Dataset Versioning
  if ((m = /^\/api\/v1\/datasets\/([^/]+)\/branches$/.exec(p))) {
    return { items: getMockDatasetBranches(m[1]), next_cursor: null };
  }

  if ((m = /^\/api\/v1\/datasets\/([^/]+)\/commits\/([^/]+)\/items$/.exec(p))) {
    const items = getMockDatasetCommitItems(m[1], m[2]);
    return page(items, params);
  }

  if ((m = /^\/api\/v1\/datasets\/([^/]+)\/commits$/.exec(p))) {
    const branch = params.branch ? String(params.branch) : undefined;
    const items = getMockDatasetCommits(m[1], branch);
    return page(items, params);
  }

  if ((m = /^\/api\/v1\/datasets\/([^/]+)\/diff$/.exec(p))) {
    const base = String(params.base || "main");
    const head = String(params.head || "feat/multimodal-v2");
    return getMockDatasetDiff(m[1], base, head);
  }

  return undefined;
}

export function sotaSend(method: string, p: string, body?: unknown): unknown {
  const m = method.toUpperCase();
  let match: RegExpExecArray | null;

  // Benchmarks
  if (m === "POST" && p === "/api/v1/benchmarks/runs") {
    return createMockBenchmarkRun((body || {}) as CreateBenchmarkRunRequest);
  }

  if (m === "POST" && (match = /^\/api\/v1\/benchmarks\/runs\/([^/]+)\/cancel$/.exec(p))) {
    return cancelMockBenchmarkRun(match[1]);
  }

  // Sandboxes
  if (m === "POST" && p === "/api/v1/sandboxes/runs") {
    return createMockSandboxRun((body || {}) as CreateSandboxRunRequest);
  }

  if (m === "POST" && (match = /^\/api\/v1\/sandboxes\/runs\/([^/]+)\/cancel$/.exec(p))) {
    return cancelMockSandboxRun(match[1]);
  }

  // Dataset Versioning
  if (m === "POST" && (match = /^\/api\/v1\/datasets\/([^/]+)\/branches$/.exec(p))) {
    return createMockDatasetBranch(match[1], (body || {}) as CreateDatasetBranchRequest);
  }

  if (m === "POST" && (match = /^\/api\/v1\/datasets\/([^/]+)\/commits$/.exec(p))) {
    return createMockDatasetCommit(match[1], (body || {}) as CreateDatasetCommitRequest);
  }

  if (m === "POST" && (match = /^\/api\/v1\/datasets\/([^/]+)\/merge$/.exec(p))) {
    return mergeMockDatasetBranches(match[1], (body || {}) as MergeBranchesRequest);
  }

  if (m === "POST" && (match = /^\/api\/v1\/datasets\/([^/]+)\/revert$/.exec(p))) {
    return revertMockDatasetBranch(match[1], (body || {}) as RevertBranchRequest);
  }

  return undefined;
}
