"use client";

import React, { useState } from "react";
import { formatTimestamp } from "@/lib/format";
import { BRANCH_DIFF_STYLE } from "@/lib/mlops-tokens";
import type { DatasetCommit } from "@/lib/types";
import { RevertConfirmDialog } from "./revert-confirm-dialog";

export interface CommitHistoryTimelineProps {
  datasetId: string;
  branch: string;
  commits: DatasetCommit[];
  currentCommitId?: string | null;
  onCommitReverted: (commit: DatasetCommit) => void;
  className?: string;
}

export function CommitHistoryTimeline({
  datasetId,
  branch,
  commits,
  currentCommitId,
  onCommitReverted,
  className = "",
}: CommitHistoryTimelineProps) {
  const [revertingCommit, setRevertingCommit] = useState<DatasetCommit | null>(null);
  const [copiedHash, setCopiedHash] = useState<string | null>(null);

  const handleCopy = (hash: string) => {
    navigator.clipboard.writeText(hash);
    setCopiedHash(hash);
    setTimeout(() => setCopiedHash(null), 1800);
  };

  return (
    <div className={`rounded-xl border border-zinc-800 bg-zinc-950 p-4 shadow-sm ${className}`}>
      <div className="mb-4 flex items-center justify-between border-b border-zinc-800 pb-3">
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-zinc-400 font-mono text-sm">⎇</span>
          <h3 className="text-sm font-semibold text-zinc-100">
            Commit History Timeline · <span className="font-mono text-emerald-300">{branch}</span>
          </h3>
        </div>
        <span className="font-mono text-xs text-zinc-400">
          {commits.length} commit{commits.length === 1 ? "" : "s"}
        </span>
      </div>

      {commits.length === 0 ? (
        <div className="py-8 text-center text-xs text-zinc-400">
          No commit history available for this branch.
        </div>
      ) : (
        <ol className="relative border-l border-zinc-800 ml-3 space-y-4">
          {commits.map((c, idx) => {
            const isHead = idx === 0 || c.id === currentCommitId;
            const stats = c.stats as { added?: number; removed?: number; modified?: number } | undefined;
            const added = stats?.added ?? 0;
            const removed = stats?.removed ?? 0;
            const modified = stats?.modified ?? 0;

            return (
              <li key={c.id} className="ml-4">
                {/* Dot marker on timeline */}
                <span
                  aria-hidden
                  className={`absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-zinc-950 ${
                    isHead ? "bg-emerald-400 ring-2 ring-emerald-500/30" : "bg-zinc-700"
                  }`}
                />

                <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/50 p-3 hover:border-zinc-700 transition-colors">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleCopy(c.id)}
                        title="Click to copy commit hash"
                        aria-label={`Copy hash ${c.id}`}
                        className="inline-flex items-center gap-1 rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-sky-300 hover:bg-zinc-700 hover:text-sky-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
                      >
                        <span>{c.id.slice(0, 8)}</span>
                        <span className="text-[10px] text-zinc-400">
                          {copiedHash === c.id ? "✓" : "📋"}
                        </span>
                      </button>

                      {isHead && (
                        <span className="rounded bg-emerald-950/70 border border-emerald-800/70 px-1.5 py-0.2 text-[10px] font-semibold text-emerald-300 uppercase tracking-wider">
                          HEAD
                        </span>
                      )}

                      <span className="text-xs text-zinc-400">
                        by <span className="text-zinc-200 font-medium">{c.author || "system"}</span>
                      </span>
                    </div>

                    <div className="flex items-center gap-3">
                      <span className="font-mono text-[11px] text-zinc-400">
                        {formatTimestamp(c.created_at)}
                      </span>

                      {!isHead && (
                        <button
                          type="button"
                          onClick={() => setRevertingCommit(c)}
                          className="rounded border border-amber-800/60 bg-amber-950/40 px-2 py-0.5 text-xs font-medium text-amber-300 hover:bg-amber-900/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
                        >
                          Revert
                        </button>
                      )}
                    </div>
                  </div>

                  <p className="mt-2 text-xs font-medium text-zinc-100">{c.message}</p>

                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="font-mono text-zinc-400">Items: {c.item_count}</span>
                    <span className="text-zinc-600">·</span>
                    <div className="flex items-center gap-1.5 font-mono">
                      <span className={`rounded border px-1.5 py-0.2 ${BRANCH_DIFF_STYLE.added.badge}`}>
                        +{added}
                      </span>
                      <span className={`rounded border px-1.5 py-0.2 ${BRANCH_DIFF_STYLE.removed.badge}`}>
                        -{removed}
                      </span>
                      <span className={`rounded border px-1.5 py-0.2 ${BRANCH_DIFF_STYLE.modified.badge}`}>
                        ~{modified}
                      </span>
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {/* Accessible Revert Confirmation Dialog */}
      <RevertConfirmDialog
        isOpen={revertingCommit !== null}
        datasetId={datasetId}
        branch={branch}
        targetCommit={revertingCommit}
        onClose={() => setRevertingCommit(null)}
        onReverted={(newCommit) => {
          onCommitReverted(newCommit);
          setRevertingCommit(null);
        }}
      />
    </div>
  );
}
