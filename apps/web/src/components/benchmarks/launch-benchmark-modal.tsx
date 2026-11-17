"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type {
  BenchmarkCatalogTask,
  BenchmarkEvalMode,
  BenchmarkRun,
  BenchmarkSuite,
  CreateBenchmarkRunRequest,
} from "@/lib/types";

interface LaunchBenchmarkModalProps {
  isOpen: boolean;
  onClose: () => void;
  catalog: BenchmarkCatalogTask[];
  onCreated: (run: BenchmarkRun) => void;
}

const PRESET_MODELS = [
  "meta-llama/Llama-3.1-8B-Instruct",
  "meta-llama/Llama-3.1-70B-Instruct",
  "mistralai/Mistral-7B-Instruct-v0.3",
  "Qwen/Qwen2-VL-7B-Instruct",
  "Qwen/Qwen2.5-Coder-32B-Instruct",
  "google/gemma-2-9b-it",
];

export function LaunchBenchmarkModal({
  isOpen,
  onClose,
  catalog,
  onCreated,
}: LaunchBenchmarkModalProps) {
  const titleId = useId();
  const descId = useId();
  const modalRef = useRef<HTMLDivElement>(null);

  const [suite, setSuite] = useState<BenchmarkSuite>("lm_eval");
  const [model, setModel] = useState<string>(PRESET_MODELS[0]);
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [evalMode, setEvalMode] = useState<BenchmarkEvalMode>("generate_until");
  const [numFewshot, setNumFewshot] = useState<number>(5);
  const [runnerBackend, setRunnerBackend] = useState<string>("vllm");
  const [tensorParallel, setTensorParallel] = useState<number>(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filter catalog tasks by active suite
  const suiteTasks = catalog.filter((t) => t.suite === suite);

  // Initialize selected tasks when suite changes
  useEffect(() => {
    const defaultTasks = suiteTasks.map((t) => t.name);
    setSelectedTasks(defaultTasks);
    if (suite === "vlmevalkit") {
      setEvalMode("multimodal");
      setNumFewshot(0);
      setModel("Qwen/Qwen2-VL-7B-Instruct");
    } else {
      setEvalMode("generate_until");
      setNumFewshot(5);
      setModel("meta-llama/Llama-3.1-8B-Instruct");
    }
  }, [suite]);

  // Focus trap and Escape key listener
  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    modalRef.current?.focus();

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  function toggleTask(taskName: string) {
    setSelectedTasks((prev) =>
      prev.includes(taskName)
        ? prev.filter((t) => t !== taskName)
        : [...prev, taskName],
    );
  }

  function toggleAllTasks() {
    if (selectedTasks.length === suiteTasks.length) {
      setSelectedTasks([]);
    } else {
      setSelectedTasks(suiteTasks.map((t) => t.name));
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!model.trim()) {
      setError("Target model / checkpoint is required.");
      return;
    }
    if (selectedTasks.length === 0) {
      setError("Please select at least one evaluation task.");
      return;
    }

    setSubmitting(true);
    setError(null);

    const payload: CreateBenchmarkRunRequest = {
      name: `${model.split("/").pop() || model} ${suite === "lm_eval" ? "LM-Eval" : "VLMEvalKit"} Run`,
      suite,
      tasks: selectedTasks,
      runner_backend: runnerBackend,
      runner_config: {
        tensor_parallel_size: tensorParallel,
        gpu_memory_utilization: 0.9,
      },
      eval_mode: evalMode,
      num_fewshot: numFewshot,
      target_model: model.trim(),
      sync_exec: true,
    };

    try {
      const res = await fetch("/api/proxy/api/v1/benchmarks/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Request failed with status ${res.status}`);
      }

      const createdRun = (await res.json()) as BenchmarkRun;
      onCreated(createdRun);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to launch benchmark run.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        ref={modalRef}
        tabIndex={-1}
        className="relative max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 p-6 shadow-2xl focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-zinc-100">
              Launch Benchmark Run
            </h2>
            <p id={descId} className="mt-0.5 text-xs text-zinc-400">
              Configure GPU inference runners (LM-Eval / VLMEvalKit)
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <svg
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="mt-4 rounded border border-red-800/70 bg-red-950/40 p-3 text-xs text-red-300"
          >
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {/* Suite Selection */}
          <div>
            <label className="block text-xs font-medium text-zinc-300">
              Benchmark Suite
            </label>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setSuite("lm_eval")}
                className={`rounded border px-3 py-2 text-left text-xs transition ${
                  suite === "lm_eval"
                    ? "border-emerald-600 bg-emerald-950/40 text-emerald-300 ring-1 ring-emerald-500"
                    : "border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                }`}
              >
                <div className="font-semibold">LM-Eval Harness</div>
                <div className="mt-0.5 text-[11px] text-zinc-400">
                  Text reasoning, code & knowledge
                </div>
              </button>
              <button
                type="button"
                onClick={() => setSuite("vlmevalkit")}
                className={`rounded border px-3 py-2 text-left text-xs transition ${
                  suite === "vlmevalkit"
                    ? "border-emerald-600 bg-emerald-950/40 text-emerald-300 ring-1 ring-emerald-500"
                    : "border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                }`}
              >
                <div className="font-semibold">VLMEvalKit</div>
                <div className="mt-0.5 text-[11px] text-zinc-400">
                  Multimodal, vision QA & OCR
                </div>
              </button>
            </div>
          </div>

          {/* Model / Checkpoint */}
          <div>
            <label
              htmlFor="bench-model-input"
              className="block text-xs font-medium text-zinc-300"
            >
              Model / Checkpoint
            </label>
            <input
              id="bench-model-input"
              type="text"
              list="preset-models"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. meta-llama/Llama-3.1-8B-Instruct"
              className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900/80 px-3 py-1.5 font-mono text-xs text-zinc-200 placeholder-zinc-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              required
            />
            <datalist id="preset-models">
              {PRESET_MODELS.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>

          {/* Tasks Multi-Select */}
          <div>
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-zinc-300">
                Evaluation Tasks ({selectedTasks.length} selected)
              </label>
              <button
                type="button"
                onClick={toggleAllTasks}
                className="text-[11px] text-emerald-400 hover:text-emerald-300 focus-visible:outline-none focus-visible:underline"
              >
                {selectedTasks.length === suiteTasks.length
                  ? "Deselect All"
                  : "Select All"}
              </button>
            </div>
            <div className="mt-1.5 grid max-h-36 grid-cols-2 gap-1.5 overflow-y-auto rounded border border-zinc-800 bg-zinc-900/40 p-2">
              {suiteTasks.length > 0 ? (
                suiteTasks.map((task) => {
                  const checked = selectedTasks.includes(task.name);
                  return (
                    <label
                      key={task.name}
                      className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs transition ${
                        checked
                          ? "bg-zinc-800/80 text-zinc-100"
                          : "text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-300"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleTask(task.name)}
                        className="rounded border-zinc-700 bg-zinc-800 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-0"
                      />
                      <span className="font-mono text-[11px] uppercase">
                        {task.name.replace(/_/g, " ")}
                      </span>
                    </label>
                  );
                })
              ) : (
                <div className="col-span-2 py-2 text-center text-xs text-zinc-400">
                  No catalog tasks found for {suite}.
                </div>
              )}
            </div>
          </div>

          {/* Eval Mode & Few-Shot */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label
                htmlFor="bench-eval-mode"
                className="block text-xs font-medium text-zinc-300"
              >
                Eval Mode
              </label>
              <select
                id="bench-eval-mode"
                value={evalMode}
                onChange={(e) => setEvalMode(e.target.value as BenchmarkEvalMode)}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900/80 px-2.5 py-1.5 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              >
                <option value="generate_until">generate_until (Autoregressive)</option>
                <option value="loglikelihood">loglikelihood (Choice logits)</option>
                <option value="multimodal">multimodal (Visual token QA)</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="bench-fewshot"
                className="block text-xs font-medium text-zinc-300"
              >
                Few-shot Examples (N-shot)
              </label>
              <input
                id="bench-fewshot"
                type="number"
                min={0}
                max={32}
                value={numFewshot}
                onChange={(e) => setNumFewshot(Math.max(0, parseInt(e.target.value, 10) || 0))}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900/80 px-2.5 py-1.5 font-mono text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            </div>
          </div>

          {/* Inference Backend & Parallelism */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label
                htmlFor="bench-runner"
                className="block text-xs font-medium text-zinc-300"
              >
                Inference Engine
              </label>
              <select
                id="bench-runner"
                value={runnerBackend}
                onChange={(e) => setRunnerBackend(e.target.value)}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900/80 px-2.5 py-1.5 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              >
                <option value="vllm">vLLM (PagedAttention)</option>
                <option value="sglang">SGLang (RadixAttention)</option>
                <option value="tensorrt-llm">TensorRT-LLM</option>
                <option value="accelerate">Hugging Face Accelerate</option>
                <option value="local_sim">Local Simulator</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="bench-tp"
                className="block text-xs font-medium text-zinc-300"
              >
                Tensor Parallel (TP)
              </label>
              <select
                id="bench-tp"
                value={tensorParallel}
                onChange={(e) => setTensorParallel(parseInt(e.target.value, 10) || 1)}
                className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900/80 px-2.5 py-1.5 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              >
                <option value={1}>TP = 1 (1 GPU)</option>
                <option value={2}>TP = 2 (2 GPUs)</option>
                <option value={4}>TP = 4 (4 GPUs)</option>
                <option value={8}>TP = 8 (8 GPUs)</option>
              </select>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 border-t border-zinc-800 pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded border border-zinc-700 bg-zinc-800/80 px-3 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className={`rounded px-3.5 py-1.5 text-xs font-medium transition ${PRIMARY_BTN_CLASS} ${
                submitting ? "opacity-60 cursor-not-allowed" : ""
              }`}
            >
              {submitting ? "Launching Run..." : "Launch Run"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
