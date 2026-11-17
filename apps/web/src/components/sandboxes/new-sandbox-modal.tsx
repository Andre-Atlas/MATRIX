"use client";

import React, { useEffect, useRef, useState } from "react";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import { proxy } from "@/lib/proxy-client";
import type { CreateSandboxRunRequest, SandboxRun } from "@/lib/types";

type NewSandboxModalProps = {
  open: boolean;
  onClose: () => void;
  onCreated: (newRun: SandboxRun) => void;
};

export function NewSandboxModal({ open, onClose, onCreated }: NewSandboxModalProps) {
  // Section 1: Dataset
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("ds-code-tasks");
  const [runnerBackend, setRunnerBackend] = useState<"docker" | "k8s" | "local_sandbox">("docker");
  const [networkMode, setNetworkMode] = useState<"none" | "restricted" | "bridge">("none");
  const [cpus, setCpus] = useState(2);
  const [memoryMb, setMemoryMb] = useState(2048);
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);

  // Section 2: Solver
  const [solverStrategy, setSolverStrategy] = useState("react");
  const [solverModel, setSolverModel] = useState("claude-3-5-sonnet");
  const [maxSteps, setMaxSteps] = useState(15);
  const [tokenLimit, setTokenLimit] = useState(8000);

  // Section 3: Scorer
  const [scorerType, setScorerType] = useState("unit_test");
  const [scorerTarget, setScorerTarget] = useState("tests/test_refactor.py");
  const [syncExec, setSyncExec] = useState(true);

  // Status
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  const firstInputRef = useRef<HTMLInputElement>(null);
  const previousActiveElement = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      previousActiveElement.current = document.activeElement as HTMLElement | null;
      setTimeout(() => firstInputRef.current?.focus(), 50);
    } else {
      if (previousActiveElement.current && typeof previousActiveElement.current.focus === "function") {
        previousActiveElement.current.focus();
      }
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "Tab") {
        if (!dialogRef.current) return;
        const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Please specify a sandbox run name.");
      firstInputRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);

    const payload: CreateSandboxRunRequest = {
      name: name.trim(),
      runner_backend: runnerBackend,
      dataset_id: datasetId.trim() || null,
      solver_config: {
        strategy: solverStrategy,
        model: solverModel,
        max_steps: Number(maxSteps),
        token_limit: Number(tokenLimit),
      },
      scorer_config: {
        type: scorerType,
        target: scorerTarget.trim(),
      },
      sandbox_policy: {
        network_mode: networkMode,
        timeout_seconds: Number(timeoutSeconds),
        memory_mb: Number(memoryMb),
        cpus: Number(cpus),
      },
      sync_exec: syncExec,
    };

    try {
      const created = await proxy<SandboxRun>("POST", "api/v1/sandboxes/runs", payload);
      onCreated(created);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to launch sandbox run.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-sandbox-run-title"
        className="relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 p-6 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-zinc-800/80 pb-4">
          <div>
            <h2 id="new-sandbox-run-title" className="text-base font-semibold text-zinc-100">
              New Sandbox Run
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              Launch an isolated Inspect AI agent execution across Dataset, Solver, and Scorer.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <span aria-hidden="true" className="text-lg leading-none">
              ×
            </span>
          </button>
        </div>

        {/* Stepper / Modular Pipeline Indicator */}
        <div aria-label="Pipeline Architecture" className="my-4 flex items-center justify-between rounded-md border border-zinc-800/80 bg-zinc-900/60 p-2.5 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-emerald-300">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-950 text-[11px] font-bold text-emerald-400 border border-emerald-800/80">
              1
            </span>
            <span>Dataset & Policy</span>
          </div>
          <span aria-hidden="true" className="text-zinc-600 font-bold">
            →
          </span>
          <div className="flex items-center gap-1.5 font-medium text-sky-300">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-sky-950 text-[11px] font-bold text-sky-400 border border-sky-800/80">
              2
            </span>
            <span>Solver Strategy</span>
          </div>
          <span aria-hidden="true" className="text-zinc-600 font-bold">
            →
          </span>
          <div className="flex items-center gap-1.5 font-medium text-violet-300">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-violet-950 text-[11px] font-bold text-violet-400 border border-violet-800/80">
              3
            </span>
            <span>Scorer & Verification</span>
          </div>
        </div>

        {error && (
          <div
            role="alert"
            className="mb-4 rounded-md border border-red-800/70 bg-red-950/40 p-3 text-xs text-red-200"
          >
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Section 1: Dataset & Environment */}
          <fieldset className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-emerald-400">
              1. Dataset & Container Policy
            </legend>
            <div className="mt-2 space-y-3">
              <div>
                <label htmlFor="sb-run-name" className="block text-xs font-medium text-zinc-300">
                  Run Name <span className="text-emerald-400">*</span>
                </label>
                <input
                  ref={firstInputRef}
                  id="sb-run-name"
                  type="text"
                  required
                  placeholder="e.g. Code Refactor Benchmark #4"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                />
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="sb-dataset-id" className="block text-xs font-medium text-zinc-300">
                    Dataset / Task Suite
                  </label>
                  <select
                    id="sb-dataset-id"
                    value={datasetId}
                    onChange={(e) => setDatasetId(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <option value="ds-code-tasks">ds-code-tasks (Python Refactor & AST)</option>
                    <option value="ds-scraper-tasks">ds-scraper-tasks (DOM & Selector Tests)</option>
                    <option value="ds-sql-tasks">ds-sql-tasks (DB Migration & Schemas)</option>
                    <option value="custom">Custom Task Suite</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="sb-runner-backend" className="block text-xs font-medium text-zinc-300">
                    Runtime Backend
                  </label>
                  <select
                    id="sb-runner-backend"
                    value={runnerBackend}
                    onChange={(e) =>
                      setRunnerBackend(e.target.value as "docker" | "k8s" | "local_sandbox")
                    }
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <option value="docker">Docker (Containerized)</option>
                    <option value="k8s">Kubernetes (Pod Sandbox)</option>
                    <option value="local_sandbox">Local Process Sandbox</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label htmlFor="sb-network-mode" className="block text-xs font-medium text-zinc-300">
                    Network
                  </label>
                  <select
                    id="sb-network-mode"
                    value={networkMode}
                    onChange={(e) =>
                      setNetworkMode(e.target.value as "none" | "restricted" | "bridge")
                    }
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <option value="none">Isolated (None)</option>
                    <option value="restricted">Restricted</option>
                    <option value="bridge">Bridge</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="sb-cpus" className="block text-xs font-medium text-zinc-300">
                    CPUs
                  </label>
                  <input
                    id="sb-cpus"
                    type="number"
                    min={1}
                    max={16}
                    value={cpus}
                    onChange={(e) => setCpus(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-2 py-1.5 font-mono text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  />
                </div>
                <div>
                  <label htmlFor="sb-memory-mb" className="block text-xs font-medium text-zinc-300">
                    Memory (MB)
                  </label>
                  <input
                    id="sb-memory-mb"
                    type="number"
                    min={512}
                    step={256}
                    max={16384}
                    value={memoryMb}
                    onChange={(e) => setMemoryMb(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-2 py-1.5 font-mono text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  />
                </div>
              </div>
            </div>
          </fieldset>

          {/* Section 2: Solver */}
          <fieldset className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-sky-400">
              2. Solver Strategy
            </legend>
            <div className="mt-2 space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="sb-solver-strategy" className="block text-xs font-medium text-zinc-300">
                    Strategy Loop
                  </label>
                  <select
                    id="sb-solver-strategy"
                    value={solverStrategy}
                    onChange={(e) => setSolverStrategy(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <option value="react">ReAct (Reason + Act)</option>
                    <option value="plan_and_solve">Plan-and-Solve</option>
                    <option value="multi_agent">Multi-Agent Handoff</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="sb-solver-model" className="block text-xs font-medium text-zinc-300">
                    LLM / Agent Model
                  </label>
                  <select
                    id="sb-solver-model"
                    value={solverModel}
                    onChange={(e) => setSolverModel(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <option value="claude-3-5-sonnet">claude-3-5-sonnet</option>
                    <option value="gpt-4o">gpt-4o</option>
                    <option value="claude-3-5-haiku">claude-3-5-haiku</option>
                    <option value="qwen-2.5-coder-32b">qwen-2.5-coder-32b</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="sb-max-steps" className="block text-xs font-medium text-zinc-300">
                    Max Steps
                  </label>
                  <input
                    id="sb-max-steps"
                    type="number"
                    min={1}
                    max={50}
                    value={maxSteps}
                    onChange={(e) => setMaxSteps(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  />
                </div>
                <div>
                  <label htmlFor="sb-token-limit" className="block text-xs font-medium text-zinc-300">
                    Token Budget Limit
                  </label>
                  <input
                    id="sb-token-limit"
                    type="number"
                    min={500}
                    step={500}
                    max={64000}
                    value={tokenLimit}
                    onChange={(e) => setTokenLimit(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  />
                </div>
              </div>
            </div>
          </fieldset>

          {/* Section 3: Scorer */}
          <fieldset className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-violet-400">
              3. Scorer & Verification
            </legend>
            <div className="mt-2 space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="sb-scorer-type" className="block text-xs font-medium text-zinc-300">
                    Verifier Type
                  </label>
                  <select
                    id="sb-scorer-type"
                    value={scorerType}
                    onChange={(e) => setScorerType(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <option value="unit_test">Unit Test Exit 0 (pytest)</option>
                    <option value="json_schema">Filesystem & Schema Assertion</option>
                    <option value="llm_rubric">LLM-as-Judge Rubric</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="sb-scorer-target" className="block text-xs font-medium text-zinc-300">
                    Target Assertion Path / Test
                  </label>
                  <input
                    id="sb-scorer-target"
                    type="text"
                    value={scorerTarget}
                    onChange={(e) => setScorerTarget(e.target.value)}
                    className="mt-1 w-full rounded border border-zinc-700/80 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  id="sb-sync-exec"
                  type="checkbox"
                  checked={syncExec}
                  onChange={(e) => setSyncExec(e.target.checked)}
                  className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 text-emerald-500 focus:ring-emerald-400"
                />
                <label htmlFor="sb-sync-exec" className="text-xs text-zinc-300">
                  Execute synchronously (wait for agent transcript and test verdict)
                </label>
              </div>
            </div>
          </fieldset>

          {/* Modal Actions */}
          <div className="flex items-center justify-end gap-3 border-t border-zinc-800/80 pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded border border-zinc-700 bg-zinc-800/80 px-3.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className={`rounded px-4 py-1.5 text-xs font-semibold shadow-sm disabled:opacity-50 ${PRIMARY_BTN_CLASS}`}
            >
              {submitting ? (
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-emerald-300 border-t-transparent motion-reduce:animate-none" />
                  Launching...
                </span>
              ) : (
                "+ Launch Sandbox Run"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
