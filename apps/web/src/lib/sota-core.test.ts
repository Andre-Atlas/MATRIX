import { describe, expect, it } from "vitest";
import {
  BRANCH_DIFF_STYLE,
  SANDBOX_STEP_STYLE,
  getBranchDiffStyle,
  getSandboxStepStyle,
} from "./mlops-tokens";
import { mockGet, mockSend } from "./mock-api";
import {
  MOCK_BENCHMARK_CATALOG,
  MOCK_BENCHMARK_RUNS,
  MOCK_DATASET_BRANCHES,
  MOCK_DATASET_COMMITS,
  MOCK_DATASET_VERSIONED_ITEMS,
  MOCK_SANDBOX_RUNS,
  getMockBenchmarkCatalog,
  getMockBenchmarkRun,
  getMockDatasetDiff,
  getMockSandboxRun,
  getMockSandboxTranscript,
} from "./sota-mocks";
import type {
  BenchmarkCatalogResponse,
  BenchmarkRun,
  CreateBenchmarkRunRequest,
  CreateDatasetBranchRequest,
  CreateDatasetCommitRequest,
  CreateSandboxRunRequest,
  DatasetBranch,
  DatasetCommit,
  DatasetDiffResult,
  ListResponse,
  MergeBranchesRequest,
  RevertBranchRequest,
  SandboxRun,
  SandboxTaskTranscript,
} from "./types";

describe("SOTA MLOps Tokens", () => {
  it("defines step styles for all sandbox step kinds", () => {
    const kinds = ["bash", "python", "file_edit", "tool_call", "scorer"] as const;
    for (const kind of kinds) {
      const style = SANDBOX_STEP_STYLE[kind];
      expect(style).toBeDefined();
      expect(style.label).toBeTruthy();
      expect(style.badge).toContain("border-");
      expect(style.badge).toContain("text-");
    }
  });

  it("returns fallback style for unknown sandbox step kind", () => {
    const fallback = getSandboxStepStyle("unknown_tool");
    expect(fallback.label).toBe("unknown_tool");
    expect(fallback.badge).toContain("border-zinc-700");

    const bashStyle = getSandboxStepStyle("bash");
    expect(bashStyle.label).toBe("Bash");
    expect(bashStyle.badge).toContain("amber");
  });

  it("defines branch diff styles with prefix and accessible colors", () => {
    expect(BRANCH_DIFF_STYLE.added.prefix).toBe("+");
    expect(BRANCH_DIFF_STYLE.added.badge).toContain("emerald");
    expect(BRANCH_DIFF_STYLE.removed.prefix).toBe("-");
    expect(BRANCH_DIFF_STYLE.removed.badge).toContain("red");
    expect(BRANCH_DIFF_STYLE.modified.prefix).toBe("~");
    expect(BRANCH_DIFF_STYLE.modified.badge).toContain("amber");
  });

  it("returns fallback diff style for unknown change type", () => {
    const fallback = getBranchDiffStyle("renamed");
    expect(fallback.label).toBe("renamed");
    expect(fallback.prefix).toBe("");

    const added = getBranchDiffStyle("added");
    expect(added.prefix).toBe("+");
  });
});

describe("SOTA Mocks Data Helpers", () => {
  it("provides comprehensive benchmark catalog covering LM-Eval and VLMEvalKit", () => {
    expect(MOCK_BENCHMARK_CATALOG.length).toBeGreaterThanOrEqual(13);
    const lmTasks = getMockBenchmarkCatalog("lm_eval");
    const vlmTasks = getMockBenchmarkCatalog("vlmevalkit");

    expect(lmTasks.map((t) => t.name)).toEqual(
      expect.arrayContaining(["mmlu_pro", "gpqa_diamond", "ifeval", "bbh", "humaneval", "gsm8k"]),
    );
    expect(vlmTasks.map((t) => t.name)).toEqual(
      expect.arrayContaining([
        "mmmu_pro",
        "mathvista",
        "docvqa",
        "chartqa",
        "ocrbench",
        "pope",
        "vidore",
      ]),
    );
  });

  it("provides realistic benchmark runs with logits vs generation breakdown", () => {
    expect(MOCK_BENCHMARK_RUNS.length).toBeGreaterThanOrEqual(3);
    const llamaRun = MOCK_BENCHMARK_RUNS.find((r) => r.target_model.includes("Llama-3.1-8B-Instruct"));
    expect(llamaRun).toBeDefined();
    expect(llamaRun?.task_results).toBeDefined();
    expect(llamaRun?.task_results?.length).toBeGreaterThanOrEqual(5);

    const mmluTask = llamaRun?.task_results?.find((t) => t.task_name === "mmlu_pro");
    expect(mmluTask).toBeDefined();
    expect(mmluTask?.metrics).toHaveProperty("acc");
    expect(mmluTask?.metrics).toHaveProperty("logits_acc");
    expect(mmluTask?.metrics).toHaveProperty("gen_acc");

    const qwenVlmRun = MOCK_BENCHMARK_RUNS.find((r) => r.target_model.includes("Qwen2-VL-7B-Instruct"));
    expect(qwenVlmRun).toBeDefined();
    expect(qwenVlmRun?.suite).toBe("vlmevalkit");
  });

  it("provides sandbox runs with detailed transcripts and evidence steps", () => {
    expect(MOCK_SANDBOX_RUNS.length).toBeGreaterThanOrEqual(3);
    const refactorRun = getMockSandboxRun("sb-run-01");
    expect(refactorRun).toBeDefined();
    expect(refactorRun?.name).toContain("Agent Code Refactor");

    const transcript = getMockSandboxTranscript("sb-run-01", "task-refactor-01");
    expect(transcript).toBeDefined();
    expect(transcript?.steps).toBeDefined();
    expect(transcript?.steps?.length).toBeGreaterThanOrEqual(3);

    const stepKinds = transcript?.steps?.map((s) => s.kind || s.tool_name);
    expect(stepKinds).toContain("bash");
    expect(stepKinds).toContain("file_edit");
    expect(stepKinds).toContain("scorer");
  });

  it("provides dataset versioning with multimodal media items and branch diffs", () => {
    expect(MOCK_DATASET_BRANCHES.length).toBeGreaterThanOrEqual(3);
    expect(MOCK_DATASET_BRANCHES.map((b) => b.name)).toEqual(
      expect.arrayContaining(["main", "feat/multimodal-v2", "exp/dpo-pairs"]),
    );
    expect(MOCK_DATASET_COMMITS.length).toBeGreaterThanOrEqual(3);

    // Multimodal media items
    const hasImageBbox = MOCK_DATASET_VERSIONED_ITEMS.some((it) =>
      it.media.some((m) => m.kind === "image" && m.boxes && m.boxes.length > 0),
    );
    const hasAudio = MOCK_DATASET_VERSIONED_ITEMS.some((it) =>
      it.media.some((m) => m.kind === "audio" && (m.duration_ms ?? 0) > 0),
    );
    const hasPdf = MOCK_DATASET_VERSIONED_ITEMS.some((it) =>
      it.media.some((m) => m.kind === "pdf" && (m.page ?? 0) >= 1),
    );
    const hasTextDiff = MOCK_DATASET_VERSIONED_ITEMS.some((it) =>
      it.media.some((m) => m.kind === "text_diff" && m.diff?.before),
    );

    expect(hasImageBbox).toBe(true);
    expect(hasAudio).toBe(true);
    expect(hasPdf).toBe(true);
    expect(hasTextDiff).toBe(true);

    const diff = getMockDatasetDiff("ds-sota-multimodal", "c-main-01", "c-feat-01");
    expect(diff.stats.added).toBeGreaterThanOrEqual(0);
    expect(diff.stats.modified).toBeGreaterThanOrEqual(1);
  });
});

describe("SOTA Mock API Handlers", () => {
  it("handles GET /api/v1/benchmarks/catalog and runs", async () => {
    const catalog = await mockGet<BenchmarkCatalogResponse>("/api/v1/benchmarks/catalog");
    expect(catalog.tasks.length).toBeGreaterThanOrEqual(13);

    const filteredCatalog = await mockGet<BenchmarkCatalogResponse>(
      "/api/v1/benchmarks/catalog",
      { suite: "lm_eval" },
    );
    expect(filteredCatalog.tasks.every((t) => t.suite === "lm_eval")).toBe(true);

    const runs = await mockGet<ListResponse<BenchmarkRun>>("/api/v1/benchmarks/runs");
    expect(runs.items.length).toBeGreaterThanOrEqual(3);

    const singleRun = await mockGet<BenchmarkRun>(`/api/v1/benchmarks/runs/${runs.items[0].id}`);
    expect(singleRun.id).toBe(runs.items[0].id);
    expect(singleRun.task_results).toBeDefined();
  });

  it("handles POST /api/v1/benchmarks/runs and cancel", async () => {
    const createReq: CreateBenchmarkRunRequest = {
      name: "Test Benchmark Run",
      suite: "lm_eval",
      tasks: ["mmlu_pro", "gsm8k"],
      target_model: "meta-llama/Llama-3.1-8B-Instruct",
      eval_mode: "generate_until",
      num_fewshot: 5,
    };
    const created = await mockSend<BenchmarkRun>("POST", "/api/v1/benchmarks/runs", createReq);
    expect(created.id).toBeTruthy();
    expect(created.name).toBe("Test Benchmark Run");

    const cancelled = await mockSend<BenchmarkRun>(
      "POST",
      `/api/v1/benchmarks/runs/${created.id}/cancel`,
    );
    expect(cancelled.status).toBe("cancelled");
  });

  it("handles GET /api/v1/sandboxes/runs and transcripts", async () => {
    const runs = await mockGet<ListResponse<SandboxRun>>("/api/v1/sandboxes/runs");
    expect(runs.items.length).toBeGreaterThanOrEqual(3);

    const runId = runs.items[0].id;
    const runDetail = await mockGet<SandboxRun>(`/api/v1/sandboxes/runs/${runId}`);
    expect(runDetail.id).toBe(runId);

    const transcripts = await mockGet<ListResponse<SandboxTaskTranscript>>(
      `/api/v1/sandboxes/runs/${runId}/transcripts`,
    );
    expect(transcripts.items.length).toBeGreaterThanOrEqual(1);

    const taskKey = transcripts.items[0].task_key;
    const transcript = await mockGet<SandboxTaskTranscript>(
      `/api/v1/sandboxes/runs/${runId}/transcripts/${taskKey}`,
    );
    expect(transcript.task_key).toBe(taskKey);
    expect(transcript.steps).toBeDefined();
  });

  it("handles POST /api/v1/sandboxes/runs and cancel", async () => {
    const createReq: CreateSandboxRunRequest = {
      name: "New Agent Test Run",
      runner_backend: "docker",
      solver_config: { strategy: "react", max_steps: 10 },
      scorer_config: { type: "test_assertions" },
    };
    const created = await mockSend<SandboxRun>("POST", "/api/v1/sandboxes/runs", createReq);
    expect(created.id).toBeTruthy();
    expect(created.name).toBe("New Agent Test Run");

    const cancelled = await mockSend<SandboxRun>(
      "POST",
      `/api/v1/sandboxes/runs/${created.id}/cancel`,
    );
    expect(cancelled.status).toBe("cancelled");
  });

  it("handles GET and POST for dataset versioning endpoints", async () => {
    const dsId = "ds-sota-multimodal";

    // Branches
    const branches = await mockGet<ListResponse<DatasetBranch>>(`/api/v1/datasets/${dsId}/branches`);
    expect(branches.items.some((b) => b.name === "main")).toBe(true);

    const newBranch = await mockSend<DatasetBranch>("POST", `/api/v1/datasets/${dsId}/branches`, {
      name: "experiment/clip-vit",
      from_branch: "main",
    } satisfies CreateDatasetBranchRequest);
    expect(newBranch.name).toBe("experiment/clip-vit");

    // Commits
    const commits = await mockGet<ListResponse<DatasetCommit>>(`/api/v1/datasets/${dsId}/commits`);
    expect(commits.items.length).toBeGreaterThanOrEqual(3);

    const newCommit = await mockSend<DatasetCommit>("POST", `/api/v1/datasets/${dsId}/commits`, {
      branch: "main",
      message: "feat: add 50 visual document items",
    } satisfies CreateDatasetCommitRequest);
    expect(newCommit.message).toBe("feat: add 50 visual document items");

    // Commit items
    const commitId = commits.items[0].id;
    const commitItems = await mockGet<ListResponse<unknown>>(
      `/api/v1/datasets/${dsId}/commits/${commitId}/items`,
    );
    expect(commitItems.items.length).toBeGreaterThanOrEqual(1);

    // Diff
    const diff = await mockGet<DatasetDiffResult>(`/api/v1/datasets/${dsId}/diff`, {
      base: "main",
      head: "feat/multimodal-v2",
    });
    expect(diff.dataset_id).toBe(dsId);
    expect(diff.stats).toBeDefined();

    // Merge
    const mergeRes = await mockSend<DatasetCommit>("POST", `/api/v1/datasets/${dsId}/merge`, {
      source_branch: "feat/multimodal-v2",
      target_branch: "main",
      conflict_strategy: "ours",
      message: "Merge feat/multimodal-v2 into main",
    } satisfies MergeBranchesRequest);
    expect(mergeRes.id).toBeTruthy();

    // Revert
    const revertRes = await mockSend<DatasetCommit>("POST", `/api/v1/datasets/${dsId}/revert`, {
      branch: "main",
      target_commit_id: commitId,
      message: "Revert main to previous commit",
    } satisfies RevertBranchRequest);
    expect(revertRes.id).toBeTruthy();
  });
});
