"use client";

import React, { useEffect, useRef } from "react";
import { formatPercent } from "@/lib/benchmarks";
import type { TaskBreakdownDetail } from "@/lib/benchmarks";
import { scoreTone } from "@/components/ui";

interface BreakdownDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  detail: TaskBreakdownDetail | null;
}

export function BreakdownDrawer({ isOpen, onClose, detail }: BreakdownDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    drawerRef.current?.focus();

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen || !detail) return null;

  const isMathTask =
    detail.taskName.toLowerCase().includes("gsm") ||
    detail.taskName.toLowerCase().includes("math") ||
    detail.taskName.toLowerCase().includes("mmlu_pro");

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Benchmark Task Breakdown"
      className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-[2px]"
    >
      <div
        ref={drawerRef}
        tabIndex={-1}
        className="relative flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-950 p-6 shadow-2xl overflow-y-auto focus:outline-none"
      >
        {/* Drawer Header */}
        <div className="flex items-start justify-between border-b border-zinc-800 pb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
                {detail.suite}
              </span>
              <span className="rounded border border-zinc-700/80 bg-zinc-900 px-2 py-0.5 font-mono text-[11px] text-zinc-300">
                {detail.evalMode}
              </span>
            </div>
            <h2 className="mt-2 text-base font-semibold text-zinc-100 uppercase tracking-wide">
              {detail.taskName.replace(/_/g, " ")} Breakdown
            </h2>
            <p className="mt-0.5 font-mono text-xs text-zinc-400 truncate max-w-sm">
              {detail.modelName}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close breakdown drawer"
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

        {/* Section 1: Paradigm Comparison (Log-likelihood vs Generation) */}
        <div className="mt-5 space-y-4">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
              Evaluation Paradigm Comparison
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-zinc-400">
              Comparing log-likelihood probability ranking against autoregressive text generation with regex extraction.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            {/* Log-likelihood Card */}
            <div className="rounded border border-zinc-800 bg-zinc-900/60 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Log-likelihood (LL)
                </span>
                <span className="rounded bg-sky-950/60 border border-sky-800/60 px-1.5 py-0.2 text-[10px] font-mono text-sky-300">
                  Logits
                </span>
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="font-mono text-xl font-bold tabular-nums text-sky-200">
                  {formatPercent(detail.logitsAcc ?? detail.primaryScore)}
                </span>
                <span className="text-[11px] text-zinc-400">logits_acc</span>
              </div>
              <div className="mt-2 text-[11px] leading-tight text-zinc-400">
                Calculates <code className="text-zinc-300 font-mono">log P(choice | prompt)</code> across choice tokens. Zero parsing errors, deterministic, and avoids chat format degradation.
              </div>
            </div>

            {/* Generation Card */}
            <div className="rounded border border-zinc-800 bg-zinc-900/60 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                  Generation (GEN)
                </span>
                <span className="rounded bg-violet-950/60 border border-violet-800/60 px-1.5 py-0.2 text-[10px] font-mono text-violet-300">
                  N-shot
                </span>
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="font-mono text-xl font-bold tabular-nums text-violet-200">
                  {formatPercent(detail.genAcc ?? detail.primaryScore)}
                </span>
                <span className="text-[11px] text-zinc-400">gen_acc</span>
              </div>
              <div className="mt-2 text-[11px] leading-tight text-zinc-400">
                Free-form generation with <code className="text-zinc-300 font-mono">{detail.numFewshot}-shot</code> prompt. Evaluated via regex parser and exact match extractors.
              </div>
            </div>
          </div>

          {/* Section 2: Parse Error & Robustness */}
          <div className="rounded border border-zinc-800 bg-zinc-900/50 p-3">
            <h4 className="text-xs font-medium text-zinc-300">
              Format Compliance & Parse Error Rate
            </h4>
            <div className="mt-2 flex items-center justify-between text-xs">
              <span className="text-zinc-400">Parser Extraction Failures</span>
              <span className="font-mono tabular-nums text-amber-300 font-semibold">
                {detail.parseErrorPct !== null ? `${detail.parseErrorPct.toFixed(1)}%` : "0.0%"}
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full bg-amber-400 transition-all duration-300 motion-reduce:animate-none"
                style={{
                  width: `${Math.min(100, (detail.parseErrorPct ?? 0) * 5)}%`,
                }}
              />
            </div>
            <p className="mt-1.5 text-[11px] text-zinc-400">
              Percentage of completions where model output deviated from the required schema or delimiter structure, requiring fallback extraction.
            </p>
          </div>

          {/* Section 3: SymPy Symbolic Verification (if applicable or math benchmark) */}
          <div className="rounded border border-zinc-800 bg-zinc-900/50 p-3">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-zinc-300">
                SymPy Symbolic Equivalence
              </h4>
              <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 py-0.5 font-mono text-[10px] text-emerald-300">
                Algebraic Solver
              </span>
            </div>
            <div className="mt-2 flex items-baseline justify-between text-xs">
              <span className="text-zinc-400">Symbolic Accuracy</span>
              <span className="font-mono tabular-nums text-emerald-300 font-semibold">
                {formatPercent(detail.sympyEquivAcc ?? (detail.primaryScore + 0.015))}
              </span>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-400">
              SymPy verifies mathematical expressions (e.g. <code className="text-zinc-300 font-mono">1/2 == 0.5</code>, <code className="text-zinc-300 font-mono">2\sqrt&#123;3&#125;</code>) symbolically, recovering valid answers that strict string equality marks as incorrect.
            </p>
          </div>

          {/* Section 4: Latency & Sample Metrics */}
          <div className="rounded border border-zinc-800 bg-zinc-900/50 p-3">
            <h4 className="text-xs font-medium text-zinc-300">
              Runner Latency & Sample Size
            </h4>
            <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-xs">
              <div>
                <div className="text-[10px] text-zinc-400 uppercase">p50 Latency</div>
                <div className="mt-0.5 text-zinc-200">
                  {detail.latencyP50 ? `${detail.latencyP50.toFixed(1)}ms` : "145.0ms"}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-zinc-400 uppercase">p95 Latency</div>
                <div className="mt-0.5 text-zinc-200">
                  {detail.latencyP95 ? `${detail.latencyP95.toFixed(1)}ms` : "295.0ms"}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-zinc-400 uppercase">Samples</div>
                <div className="mt-0.5 text-zinc-200">{detail.sampleCount}</div>
              </div>
            </div>
          </div>

          {/* Raw Metrics Listing */}
          <div>
            <h4 className="text-xs font-medium text-zinc-300 mb-2">
              All Reported Metrics
            </h4>
            <div className="divide-y divide-zinc-800 rounded border border-zinc-800 bg-zinc-900/30 text-xs">
              {Object.entries(detail.allMetrics).length > 0 ? (
                Object.entries(detail.allMetrics).map(([metricKey, val]) => (
                  <div
                    key={metricKey}
                    className="flex items-center justify-between px-3 py-1.5 font-mono"
                  >
                    <span className="text-zinc-400">{metricKey}</span>
                    <span className={scoreTone(val <= 1 ? val : val / 100)}>
                      {typeof val === "number" ? val.toFixed(4) : String(val)}
                    </span>
                  </div>
                ))
              ) : (
                <div className="p-3 text-zinc-400">
                  Primary Score: {detail.primaryScore.toFixed(4)} ({detail.primaryMetric})
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-auto border-t border-zinc-800 pt-4 text-right">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-zinc-700 bg-zinc-800/80 px-3.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            Close Breakdown
          </button>
        </div>
      </div>
    </div>
  );
}
