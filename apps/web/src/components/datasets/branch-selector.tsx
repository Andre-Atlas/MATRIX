"use client";

import React, { useState } from "react";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { DatasetBranch, DatasetCommit } from "@/lib/types";
import { NewBranchModal } from "./new-branch-modal";
import { DiffBranchesDrawer } from "./diff-branches-drawer";

export interface BranchSelectorProps {
  datasetId: string;
  branches: DatasetBranch[];
  activeBranch: string;
  onBranchChange: (branchName: string) => void;
  onBranchCreated: (branch: DatasetBranch) => void;
  showTimeline: boolean;
  onToggleTimeline: () => void;
  currentCommitHash?: string | null;
  className?: string;
}

export function BranchSelector({
  datasetId,
  branches,
  activeBranch,
  onBranchChange,
  onBranchCreated,
  showTimeline,
  onToggleTimeline,
  currentCommitHash,
  className = "",
}: BranchSelectorProps) {
  const [showNewModal, setShowNewModal] = useState(false);
  const [showDiffDrawer, setShowDiffDrawer] = useState(false);
  const [copied, setCopied] = useState(false);

  const active = branches.find((b) => b.name === activeBranch) || branches[0];
  const commitSha = currentCommitHash || active?.head_commit_id || "HEAD";
  const displaySha = commitSha.length > 10 ? commitSha.slice(0, 8) : commitSha;

  const handleCopySha = () => {
    navigator.clipboard.writeText(commitSha);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`flex flex-wrap items-center gap-3 ${className}`}>
      {/* Branch selector dropdown */}
      <div className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/70 p-1">
        <span aria-hidden className="pl-2 text-emerald-400 font-mono text-xs">
          ⑂
        </span>
        <label htmlFor="branch-select" className="sr-only">
          Select active branch
        </label>
        <select
          id="branch-select"
          value={activeBranch}
          onChange={(e) => onBranchChange(e.target.value)}
          className="bg-transparent font-mono text-xs font-medium text-zinc-100 pr-3 py-1 focus:outline-none cursor-pointer"
        >
          {branches.map((b) => (
            <option key={b.id} value={b.name} className="bg-zinc-900 text-zinc-100">
              {b.name} {b.is_default ? "★ default" : ""}
            </option>
          ))}
        </select>
      </div>

      {/* Copyable Commit SHA */}
      <div className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/50 px-2.5 py-1 text-xs">
        <span className="text-zinc-400">commit:</span>
        <button
          type="button"
          onClick={handleCopySha}
          title={`Click to copy full commit hash (${commitSha})`}
          aria-label={`Copy commit hash ${commitSha}`}
          className="inline-flex items-center gap-1 font-mono font-medium text-sky-300 hover:text-sky-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
        >
          <span>{displaySha}</span>
          <span className="text-[11px] text-zinc-400">{copied ? "✓" : "📋"}</span>
        </button>
      </div>

      {/* Branch Actions */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setShowNewModal(true)}
          className={`rounded px-2.5 py-1 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
        >
          + New Branch
        </button>

        <button
          type="button"
          onClick={() => setShowDiffDrawer(true)}
          className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          <span>⇄</span>
          <span>Diff Branches</span>
        </button>

        <button
          type="button"
          onClick={onToggleTimeline}
          className={`inline-flex items-center gap-1 rounded border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
            showTimeline
              ? "border-emerald-700 bg-emerald-950/40 text-emerald-200"
              : "border-zinc-700 bg-zinc-800/80 text-zinc-200 hover:bg-zinc-700"
          }`}
        >
          <span>⎇</span>
          <span>{showTimeline ? "Hide History" : "Commit History"}</span>
        </button>
      </div>

      {/* New Branch Modal */}
      <NewBranchModal
        isOpen={showNewModal}
        datasetId={datasetId}
        branches={branches}
        currentBranch={activeBranch}
        onClose={() => setShowNewModal(false)}
        onBranchCreated={(b) => {
          onBranchCreated(b);
          onBranchChange(b.name);
          setShowNewModal(false);
        }}
      />

      {/* Diff Branches Drawer */}
      <DiffBranchesDrawer
        isOpen={showDiffDrawer}
        datasetId={datasetId}
        branches={branches}
        initialBase={activeBranch}
        onClose={() => setShowDiffDrawer(false)}
        onMerged={() => {
          setShowDiffDrawer(false);
        }}
      />
    </div>
  );
}
