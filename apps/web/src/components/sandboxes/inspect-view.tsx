"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { formatDuration, formatPercent, formatScore, formatTokens } from "@/lib/format";
import { diffLines, splitLines } from "@/lib/line-diff";
import { getSandboxStepStyle, scoreTone } from "@/lib/mlops-tokens";
import type { SandboxRun, SandboxStep, SandboxTaskTranscript } from "@/lib/types";

type InspectViewProps = {
  run: SandboxRun;
  transcripts: SandboxTaskTranscript[];
  loadingTranscripts?: boolean;
  onClose: () => void;
};

type StepTabId = "stdout_stderr" | "files_diff" | "scorer_verdict";

export function InspectView({
  run,
  transcripts,
  loadingTranscripts = false,
  onClose,
}: InspectViewProps) {
  const [selectedTaskIndex, setSelectedTaskIndex] = useState(0);
  const [selectedStepIndex, setSelectedStepIndex] = useState(0);
  const [activeTab, setActiveTab] = useState<StepTabId>("stdout_stderr");
  const [wrapLines, setWrapLines] = useState(true);
  const [copied, setCopied] = useState(false);

  const activeTranscript: SandboxTaskTranscript | null =
    transcripts[selectedTaskIndex] || transcripts[0] || null;

  const steps: SandboxStep[] = useMemo(() => {
    return activeTranscript?.steps || [];
  }, [activeTranscript]);

  const activeStep: SandboxStep | null = steps[selectedStepIndex] || steps[0] || null;

  const timelineContainerRef = useRef<HTMLDivElement>(null);
  const stepButtonRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Reset selected step when transcript changes
  useEffect(() => {
    setSelectedStepIndex(0);
  }, [selectedTaskIndex]);

  // Keep stepButtonRefs array in sync
  useEffect(() => {
    stepButtonRefs.current = stepButtonRefs.current.slice(0, steps.length);
  }, [steps.length]);

  // Focus active step button on Arrow navigation
  const handleTimelineKeyDown = (e: React.KeyboardEvent) => {
    if (steps.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedStepIndex((prev) => {
        const next = Math.min(steps.length - 1, prev + 1);
        stepButtonRefs.current[next]?.focus();
        return next;
      });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedStepIndex((prev) => {
        const next = Math.max(0, prev - 1);
        stepButtonRefs.current[next]?.focus();
        return next;
      });
    }
  };

  // Tab navigation with ArrowLeft / ArrowRight
  const tabIds: StepTabId[] = ["stdout_stderr", "files_diff", "scorer_verdict"];
  const handleTabKeyDown = (e: React.KeyboardEvent, currentTab: StepTabId) => {
    const idx = tabIds.indexOf(currentTab);
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const nextTab = tabIds[(idx + 1) % tabIds.length];
      setActiveTab(nextTab);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prevTab = tabIds[(idx - 1 + tabIds.length) % tabIds.length];
      setActiveTab(prevTab);
    }
  };

  const handleCopyOutput = () => {
    if (!activeStep) return;
    const parts: string[] = [];
    if (activeStep.stdout) parts.push(`[stdout]\n${activeStep.stdout}`);
    if (activeStep.stderr) parts.push(`[stderr]\n${activeStep.stderr}`);
    const textToCopy = parts.join("\n\n");

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(textToCopy).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    } else {
      // Fallback
      const ta = document.createElement("textarea");
      ta.value = textToCopy;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  // Compute diff rows for activeStep
  const diffRows = useMemo(() => {
    if (!activeStep?.diff) return null;
    const diff = activeStep.diff;

    // If hunks are provided
    if (diff.hunks && diff.hunks.length > 0) {
      const parsedHunks: Array<{
        header?: string;
        lines: Array<{ type: "add" | "del" | "context"; text: string }>;
      }> = [];

      for (const hunk of diff.hunks) {
        if (typeof hunk === "string") {
          parsedHunks.push({
            header: "Hunk",
            lines: hunk.split("\n").map((line) => {
              if (line.startsWith("+")) return { type: "add", text: line };
              if (line.startsWith("-")) return { type: "del", text: line };
              return { type: "context", text: line };
            }),
          });
        } else {
          const header =
            hunk.old_start !== undefined
              ? `@@ -${hunk.old_start},${hunk.old_lines ?? 0} +${hunk.new_start ?? 0},${hunk.new_lines ?? 0} @@`
              : "@@ Diff @@";
          const lines = (hunk.lines || []).map((line) => {
            if (line.startsWith("+")) return { type: "add" as const, text: line };
            if (line.startsWith("-")) return { type: "del" as const, text: line };
            return { type: "context" as const, text: line };
          });
          parsedHunks.push({ header, lines });
        }
      }
      return { type: "hunks" as const, hunks: parsedHunks };
    }

    // If before & after strings provided
    if (typeof diff.before === "string" && typeof diff.after === "string") {
      const ops = diffLines(splitLines(diff.before), splitLines(diff.after));
      return {
        type: "ops" as const,
        ops: ops.map((op) => ({
          type: op.type === "add" ? ("add" as const) : op.type === "del" ? ("del" as const) : ("context" as const),
          text: op.type === "add" ? `+ ${op.text}` : op.type === "del" ? `- ${op.text}` : `  ${op.text}`,
        })),
      };
    }

    return null;
  }, [activeStep]);

  const activeStepStyle = activeStep ? getSandboxStepStyle(activeStep.kind) : null;

  return (
    <section
      aria-label="Sandbox Run Inspection Panel"
      className="mt-6 rounded-lg border border-zinc-800 bg-zinc-950 p-4 shadow-xl"
    >
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800/80 pb-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-xs font-semibold text-emerald-400">
            {run.id}
          </span>
          <h2 className="text-sm font-semibold text-zinc-100">{run.name}</h2>
          <span className="inline-flex items-center rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 font-mono text-[11px] font-medium text-zinc-300">
            {run.runner_backend === "docker"
              ? "Docker"
              : run.runner_backend === "k8s"
                ? "K8s"
                : "Local"}
          </span>
          {typeof run.pass_rate === "number" && (
            <span
              className={`font-mono text-xs tabular-nums font-semibold ${scoreTone(
                run.pass_rate,
              )}`}
            >
              {formatPercent(run.pass_rate, 1)} Pass Rate
            </span>
          )}
          <span className="text-xs text-zinc-400 font-mono tabular-nums">
            {formatTokens(run.total_tokens || 0)} tokens
          </span>
        </div>

        <button
          type="button"
          onClick={onClose}
          aria-label="Close Inspection View"
          className="rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          ✕ Close Inspect
        </button>
      </div>

      {/* Task Selector if multiple transcripts exist */}
      {transcripts.length > 1 && (
        <div className="mt-3 flex items-center gap-2 overflow-x-auto border-b border-zinc-800/80 pb-2">
          <span className="text-xs font-medium text-zinc-400">Tasks:</span>
          {transcripts.map((t, idx) => (
            <button
              key={t.id || t.task_key || idx}
              type="button"
              onClick={() => setSelectedTaskIndex(idx)}
              className={`rounded px-2.5 py-1 font-mono text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                selectedTaskIndex === idx
                  ? "border border-emerald-700/80 bg-emerald-950/40 text-emerald-300"
                  : "border border-zinc-800 bg-zinc-900 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
              }`}
            >
              {t.task_key} · {t.status === "passed" ? "✓" : "✕"} (
              {formatScore(t.score)})
            </button>
          ))}
        </div>
      )}

      {loadingTranscripts ? (
        <div
          role="status"
          aria-label="Loading task transcript"
          className="my-10 flex flex-col items-center justify-center space-y-3"
        >
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent motion-reduce:animate-none" />
          <span className="text-xs text-zinc-400">Loading transcript steps...</span>
        </div>
      ) : steps.length === 0 ? (
        <div className="my-8 rounded-md border border-dashed border-zinc-800 p-8 text-center">
          <p className="text-sm font-medium text-zinc-300">
            No transcript steps recorded yet.
          </p>
          <p className="mt-1 text-xs text-zinc-400">
            This run may be queued, currently provisioning, or finished without step traces.
          </p>
        </div>
      ) : (
        /* Split Panel Grid: Left 5 cols (Timeline) - Right 7 cols (Step Detail) */
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
          {/* Left Column (lg:col-span-5): Steps Timeline */}
          <div className="lg:col-span-5">
            <div className="flex items-center justify-between pb-2">
              <div className="flex items-center gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
                  Steps Timeline
                </h3>
                <span className="rounded bg-zinc-800 px-1.5 py-0.2 font-mono text-[11px] text-zinc-400">
                  {steps.length} {steps.length === 1 ? "step" : "steps"}
                </span>
              </div>
              <span className="text-[11px] text-zinc-400">
                Navigate with <kbd className="rounded border border-zinc-700 bg-zinc-900 px-1 font-mono text-[10px] text-zinc-300">↑</kbd> <kbd className="rounded border border-zinc-700 bg-zinc-900 px-1 font-mono text-[10px] text-zinc-300">↓</kbd>
              </span>
            </div>

            <div
              ref={timelineContainerRef}
              role="region"
              aria-label="Steps Timeline navigation"
              tabIndex={0}
              onKeyDown={handleTimelineKeyDown}
              className="max-h-[620px] space-y-2 overflow-y-auto rounded-md border border-zinc-800/80 bg-zinc-900/40 p-2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400/50"
            >
              {steps.map((st, index) => {
                const style = getSandboxStepStyle(st.kind);
                const isSelected = selectedStepIndex === index;
                const exitCodePassed = st.exit_code === 0;
                const hasExitCode = typeof st.exit_code === "number";

                return (
                  <button
                    key={st.step || index}
                    ref={(el) => {
                      stepButtonRefs.current[index] = el;
                    }}
                    type="button"
                    onClick={() => setSelectedStepIndex(index)}
                    aria-selected={isSelected}
                    aria-label={`Step ${st.step}: ${style.label}, ${
                      hasExitCode
                        ? exitCodePassed
                          ? "exit code 0 success"
                          : `exit code ${st.exit_code} failure`
                        : ""
                    }`}
                    className={`w-full rounded-md border p-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      isSelected
                        ? "border-emerald-500/70 bg-zinc-800/90 shadow ring-1 ring-emerald-400/40"
                        : "border-zinc-800 bg-zinc-900/60 hover:border-zinc-700 hover:bg-zinc-800/60"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold text-zinc-300">
                          #{String(st.step).padStart(2, "0")}
                        </span>
                        <span
                          className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold ${style.badge}`}
                        >
                          {style.label}
                        </span>
                      </div>

                      <div className="flex items-center gap-1.5">
                        {hasExitCode && (
                          <span
                            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium ${
                              exitCodePassed
                                ? "border-emerald-800/60 bg-emerald-950/40 text-emerald-300"
                                : "border-red-800/70 bg-red-950/50 text-red-300"
                            }`}
                          >
                            <span aria-hidden="true">
                              {exitCodePassed ? "✓" : "✕"}
                            </span>
                            exit {st.exit_code}
                          </span>
                        )}
                        {typeof st.duration_ms === "number" && (
                          <span className="font-mono text-[11px] text-zinc-400">
                            {formatDuration(st.duration_ms)}
                          </span>
                        )}
                      </div>
                    </div>

                    {st.thought && (
                      <p className="mt-1.5 line-clamp-2 text-xs text-zinc-300">
                        {st.thought}
                      </p>
                    )}

                    {st.tool_args && (
                      <div className="mt-1 truncate font-mono text-[11px] text-zinc-400">
                        {typeof st.tool_args.cmd === "string"
                          ? `$ ${st.tool_args.cmd}`
                          : typeof st.tool_args.code === "string"
                            ? st.tool_args.code.slice(0, 60)
                            : typeof st.tool_args.path === "string"
                              ? `path: ${st.tool_args.path}`
                              : JSON.stringify(st.tool_args).slice(0, 60)}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right Column (lg:col-span-7): Step Detail */}
          <div className="lg:col-span-7">
            {activeStep ? (
              <div className="flex flex-col rounded-md border border-zinc-800 bg-zinc-900/60 p-4">
                {/* Step Header */}
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-3">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-bold text-zinc-100">
                      Step #{String(activeStep.step).padStart(2, "0")}
                    </span>
                    {activeStepStyle && (
                      <span
                        className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold ${activeStepStyle.badge}`}
                      >
                        {activeStepStyle.label}
                      </span>
                    )}
                    {activeStep.tool_name && (
                      <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-zinc-300">
                        {activeStep.tool_name}
                      </code>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    {typeof activeStep.exit_code === "number" && (
                      <span
                        className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-xs font-medium ${
                          activeStep.exit_code === 0
                            ? "border-emerald-800/60 bg-emerald-950/40 text-emerald-300"
                            : "border-red-800/70 bg-red-950/50 text-red-300"
                        }`}
                      >
                        <span aria-hidden="true">
                          {activeStep.exit_code === 0 ? "✓" : "✕"}
                        </span>
                        exit {activeStep.exit_code}
                      </span>
                    )}
                    {typeof activeStep.duration_ms === "number" && (
                      <span className="font-mono text-xs text-zinc-400">
                        {formatDuration(activeStep.duration_ms)}
                      </span>
                    )}
                    {(activeStep.input_tokens || activeStep.output_tokens) && (
                      <span className="font-mono text-xs text-zinc-400">
                        {activeStep.input_tokens || 0} in / {activeStep.output_tokens || 0} out tokens
                      </span>
                    )}
                  </div>
                </div>

                {/* Agent Thought */}
                {activeStep.thought && (
                  <div className="my-3 rounded border border-zinc-800/80 bg-zinc-950 p-2.5">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                      Agent Thought
                    </div>
                    <p className="mt-1 text-xs text-zinc-200">
                      {activeStep.thought}
                    </p>
                  </div>
                )}

                {/* Command / Invocation Arguments */}
                {activeStep.tool_args && (
                  <div className="mb-4">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                      Invocation Command & Arguments
                    </div>
                    <pre className="max-h-36 overflow-x-auto rounded border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200">
                      {typeof activeStep.tool_args.cmd === "string"
                        ? `$ ${activeStep.tool_args.cmd}`
                        : typeof activeStep.tool_args.code === "string"
                          ? activeStep.tool_args.code
                          : JSON.stringify(activeStep.tool_args, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Internal Tabs: stdout/stderr | Modified Files Diff | Scorer Verdict */}
                <div
                  role="tablist"
                  aria-label="Step Detail Sections"
                  className="flex border-b border-zinc-800"
                >
                  <button
                    id="tab-stdout-stderr"
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "stdout_stderr"}
                    aria-controls="panel-stdout-stderr"
                    tabIndex={activeTab === "stdout_stderr" ? 0 : -1}
                    onKeyDown={(e) => handleTabKeyDown(e, "stdout_stderr")}
                    onClick={() => setActiveTab("stdout_stderr")}
                    className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      activeTab === "stdout_stderr"
                        ? "border-emerald-400 text-emerald-300"
                        : "border-transparent text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    <span>stdout / stderr</span>
                    {(activeStep.stdout || activeStep.stderr) && (
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    )}
                  </button>

                  <button
                    id="tab-files-diff"
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "files_diff"}
                    aria-controls="panel-files-diff"
                    tabIndex={activeTab === "files_diff" ? 0 : -1}
                    onKeyDown={(e) => handleTabKeyDown(e, "files_diff")}
                    onClick={() => setActiveTab("files_diff")}
                    className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      activeTab === "files_diff"
                        ? "border-emerald-400 text-emerald-300"
                        : "border-transparent text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    <span>Modified Files Diff</span>
                    {activeStep.diff && (
                      <span className="rounded bg-cyan-950 px-1 py-0.2 font-mono text-[10px] text-cyan-300 border border-cyan-800/60">
                        Δ diff
                      </span>
                    )}
                  </button>

                  <button
                    id="tab-scorer-verdict"
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "scorer_verdict"}
                    aria-controls="panel-scorer-verdict"
                    tabIndex={activeTab === "scorer_verdict" ? 0 : -1}
                    onKeyDown={(e) => handleTabKeyDown(e, "scorer_verdict")}
                    onClick={() => setActiveTab("scorer_verdict")}
                    className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      activeTab === "scorer_verdict"
                        ? "border-emerald-400 text-emerald-300"
                        : "border-transparent text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    <span>Scorer Verdict</span>
                    {activeTranscript?.status && (
                      <span
                        className={`font-mono text-[10px] font-bold ${
                          activeTranscript.status === "passed"
                            ? "text-emerald-400"
                            : "text-red-400"
                        }`}
                      >
                        {activeTranscript.status === "passed" ? "✓" : "✕"}
                      </span>
                    )}
                  </button>
                </div>

                {/* Tab Panel 1: stdout / stderr */}
                {activeTab === "stdout_stderr" && (
                  <div
                    id="panel-stdout-stderr"
                    role="tabpanel"
                    aria-labelledby="tab-stdout-stderr"
                    tabIndex={0}
                    className="pt-3"
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <label className="flex items-center gap-1.5 text-xs text-zinc-400">
                          <input
                            type="checkbox"
                            checked={wrapLines}
                            onChange={(e) => setWrapLines(e.target.checked)}
                            className="h-3.5 w-3.5 rounded border-zinc-700 bg-zinc-900 text-emerald-500 focus:ring-emerald-400"
                          />
                          <span>Wrap lines</span>
                        </label>
                      </div>

                      <button
                        type="button"
                        onClick={handleCopyOutput}
                        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                      >
                        {copied ? "✓ Copied" : "Copy Output"}
                      </button>
                    </div>

                    <div className="space-y-3">
                      {activeStep.stdout ? (
                        <div>
                          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                            stdout
                          </div>
                          <pre
                            className={`max-h-80 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200 ${
                              wrapLines ? "whitespace-pre-wrap break-all" : "whitespace-pre"
                            }`}
                          >
                            {activeStep.stdout}
                          </pre>
                        </div>
                      ) : null}

                      {activeStep.stderr ? (
                        <div>
                          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-red-300">
                            <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
                            [stderr]
                          </div>
                          <pre
                            className={`max-h-80 overflow-auto rounded border border-red-800/50 bg-red-950/20 p-3 font-mono text-xs text-red-200 ${
                              wrapLines ? "whitespace-pre-wrap break-all" : "whitespace-pre"
                            }`}
                          >
                            {activeStep.stderr}
                          </pre>
                        </div>
                      ) : null}

                      {!activeStep.stdout && !activeStep.stderr && (
                        <div className="py-6 text-center text-xs text-zinc-400 italic">
                          No stdout or stderr output produced in this step.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Tab Panel 2: Modified Files Diff */}
                {activeTab === "files_diff" && (
                  <div
                    id="panel-files-diff"
                    role="tabpanel"
                    aria-labelledby="tab-files-diff"
                    tabIndex={0}
                    className="pt-3"
                  >
                    {Boolean(activeStep.tool_args?.path) && (
                      <div className="mb-2 text-xs font-mono text-zinc-300">
                        <span className="text-zinc-400">File: </span>
                        {String(activeStep.tool_args?.path)}
                      </div>
                    )}

                    {diffRows ? (
                      <div className="max-h-80 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-xs">
                        {diffRows.type === "hunks" ? (
                          diffRows.hunks.map((hunk, hIdx) => (
                            <div key={hIdx} className="mb-3 last:mb-0">
                              {hunk.header && (
                                <div className="rounded bg-zinc-900/80 px-2 py-0.5 text-cyan-300 text-[11px]">
                                  {hunk.header}
                                </div>
                              )}
                              <div className="mt-1 divide-y divide-zinc-900">
                                {hunk.lines.map((l, lIdx) => {
                                  const isAdd = l.type === "add";
                                  const isDel = l.type === "del";
                                  return (
                                    <div
                                      key={lIdx}
                                      className={`flex items-start px-2 py-0.5 ${
                                        isAdd
                                          ? "border-l-2 border-emerald-500 bg-emerald-950/30 text-emerald-200"
                                          : isDel
                                            ? "border-l-2 border-red-500 bg-red-950/30 text-red-200"
                                            : "text-zinc-400"
                                      }`}
                                    >
                                      <span
                                        aria-hidden="true"
                                        className="mr-2 inline-block w-3 select-none font-bold"
                                      >
                                        {isAdd ? "+" : isDel ? "-" : " "}
                                      </span>
                                      <span className="whitespace-pre-wrap break-all">
                                        {l.text.replace(/^[+-]/, "")}
                                      </span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          ))
                        ) : (
                          <div className="divide-y divide-zinc-900">
                            {diffRows.ops.map((row, rIdx) => {
                              const isAdd = row.type === "add";
                              const isDel = row.type === "del";
                              return (
                                <div
                                  key={rIdx}
                                  className={`flex items-start px-2 py-0.5 ${
                                    isAdd
                                      ? "border-l-2 border-emerald-500 bg-emerald-950/30 text-emerald-200"
                                      : isDel
                                        ? "border-l-2 border-red-500 bg-red-950/30 text-red-200"
                                        : "text-zinc-400"
                                  }`}
                                >
                                  <span
                                    aria-hidden="true"
                                    className="mr-2 inline-block w-3 select-none font-bold"
                                  >
                                    {isAdd ? "+" : isDel ? "-" : " "}
                                  </span>
                                  <span className="whitespace-pre-wrap break-all">
                                    {row.text.replace(/^[+-]\s?/, "")}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="py-6 text-center text-xs text-zinc-400 italic">
                        No file modifications recorded in this step.
                      </div>
                    )}
                  </div>
                )}

                {/* Tab Panel 3: Scorer Verdict */}
                {activeTab === "scorer_verdict" && (
                  <div
                    id="panel-scorer-verdict"
                    role="tabpanel"
                    aria-labelledby="tab-scorer-verdict"
                    tabIndex={0}
                    className="pt-3 space-y-3"
                  >
                    {activeTranscript ? (
                      <>
                        <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-zinc-800 bg-zinc-950 p-3">
                          <div className="flex items-center gap-2">
                            <span
                              className={`inline-flex items-center gap-1.5 rounded border px-2.5 py-1 text-xs font-semibold ${
                                activeTranscript.status === "passed"
                                  ? "border-emerald-800/60 bg-emerald-950/40 text-emerald-300"
                                  : "border-red-800/70 bg-red-950/50 text-red-300"
                              }`}
                            >
                              <span aria-hidden="true">
                                {activeTranscript.status === "passed" ? "✓" : "✕"}
                              </span>
                              Task {activeTranscript.status.toUpperCase()}
                            </span>
                            <span className="text-xs text-zinc-400">
                              Task Key:{" "}
                              <code className="font-mono text-zinc-200">
                                {activeTranscript.task_key}
                              </code>
                            </span>
                          </div>

                          <div className="font-mono text-sm tabular-nums">
                            Score:{" "}
                            <span
                              className={`font-bold ${scoreTone(
                                activeTranscript.score,
                              )}`}
                            >
                              {formatScore(activeTranscript.score)}
                            </span>
                          </div>
                        </div>

                        {/* Scorer Details / Assertions Breakdown */}
                        {activeTranscript.scorer_details && (
                          <div>
                            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                              Scorer Assertions & Verification Data
                            </div>
                            <div className="grid grid-cols-2 gap-2 rounded border border-zinc-800 bg-zinc-950 p-3 sm:grid-cols-3">
                              {Object.entries(activeTranscript.scorer_details).map(
                                ([key, val]) => (
                                  <div key={key} className="text-xs">
                                    <div className="text-[11px] font-medium text-zinc-400">
                                      {key.replace(/_/g, " ")}
                                    </div>
                                    <div className="font-mono tabular-nums text-zinc-200">
                                      {typeof val === "boolean"
                                        ? val
                                          ? "true"
                                          : "false"
                                        : typeof val === "number"
                                          ? String(val)
                                          : String(val)}
                                    </div>
                                  </div>
                                ),
                              )}
                            </div>
                          </div>
                        )}

                        {/* Verified Artifacts */}
                        {activeTranscript.verified_artifacts &&
                          activeTranscript.verified_artifacts.length > 0 && (
                            <div>
                              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                                Verified Artifacts
                              </div>
                              <ul className="divide-y divide-zinc-800 rounded border border-zinc-800 bg-zinc-950 text-xs">
                                {activeTranscript.verified_artifacts.map(
                                  (art, aIdx) => (
                                    <li
                                      key={aIdx}
                                      className="flex items-center justify-between p-2 font-mono"
                                    >
                                      <span className="text-emerald-300">
                                        {art.path}
                                      </span>
                                      {art.sha256 && (
                                        <span className="text-[11px] text-zinc-400">
                                          sha256: {art.sha256.slice(0, 12)}…
                                        </span>
                                      )}
                                    </li>
                                  ),
                                )}
                              </ul>
                            </div>
                          )}

                        {/* Error info if present */}
                        {activeTranscript.error && (
                          <div className="rounded border border-red-800/70 bg-red-950/40 p-3 text-xs text-red-200">
                            <div className="font-semibold text-red-300 mb-1">
                              Execution Error
                            </div>
                            <pre className="font-mono whitespace-pre-wrap break-all">
                              {activeTranscript.error}
                            </pre>
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="py-6 text-center text-xs text-zinc-400 italic">
                        No scorer verdict available for this run.
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="py-12 text-center text-xs text-zinc-400 italic">
                Select a step on the timeline to inspect details.
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
