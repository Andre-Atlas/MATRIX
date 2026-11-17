"use client";

import React, { useEffect, useRef, useState } from "react";
import { BRANCH_DIFF_STYLE, PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { DatasetBranch, DatasetDiffResult, MergeBranchesRequest } from "@/lib/types";

export interface DiffBranchesDrawerProps {
  isOpen: boolean;
  datasetId: string;
  branches: DatasetBranch[];
  initialBase?: string;
  initialCompare?: string;
  onClose: () => void;
  onMerged?: () => void;
}

export function DiffBranchesDrawer({
  isOpen,
  datasetId,
  branches,
  initialBase = "main",
  initialCompare,
  onClose,
  onMerged,
}: DiffBranchesDrawerProps) {
  const [baseBranch, setBaseBranch] = useState(initialBase);
  const [compareBranch, setCompareBranch] = useState(
    initialCompare || branches.find((b) => b.name !== initialBase)?.name || "feat/multimodal-v2"
  );
  const [loading, setLoading] = useState(false);
  const [diffResult, setDiffResult] = useState<DatasetDiffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<"all" | "added" | "removed" | "modified">("all");
  const [search, setSearch] = useState("");

  // Merge state
  const [showMergePanel, setShowMergePanel] = useState(false);
  const [conflictStrategy, setConflictStrategy] = useState<"fail" | "ours" | "theirs">("fail");
  const [mergeMessage, setMergeMessage] = useState("");
  const [merging, setMerging] = useState(false);
  const [mergeSuccess, setMergeSuccess] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isOpen) {
      setBaseBranch(initialBase);
      if (initialCompare) {
        setCompareBranch(initialCompare);
      } else {
        const other = branches.find((b) => b.name !== initialBase);
        if (other) setCompareBranch(other.name);
      }
      setShowMergePanel(false);
      setMergeSuccess(false);
      setError(null);
    }
  }, [isOpen, initialBase, initialCompare, branches]);

  // Fetch diff whenever base or compare changes
  useEffect(() => {
    if (!isOpen || !baseBranch || !compareBranch) return;

    let cancelled = false;
    async function fetchDiff() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `/api/proxy/api/v1/datasets/${encodeURIComponent(datasetId)}/diff?base=${encodeURIComponent(
            baseBranch
          )}&head=${encodeURIComponent(compareBranch)}`
        );
        if (!res.ok) {
          throw new Error(`Failed to calculate diff (HTTP ${res.status})`);
        }
        const data: DatasetDiffResult = await res.json();
        if (!cancelled) {
          setDiffResult(data);
          setMergeMessage(`Merge branch '${compareBranch}' into '${baseBranch}'`);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchDiff();
    return () => {
      cancelled = true;
    };
  }, [isOpen, datasetId, baseBranch, compareBranch]);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSwap = () => {
    const temp = baseBranch;
    setBaseBranch(compareBranch);
    setCompareBranch(temp);
  };

  const handleMerge = async () => {
    setMerging(true);
    setError(null);

    try {
      const payload: MergeBranchesRequest = {
        source_branch: compareBranch,
        target_branch: baseBranch,
        conflict_strategy: conflictStrategy,
        message: mergeMessage.trim() || undefined,
      };

      const res = await fetch(`/api/proxy/api/v1/datasets/${encodeURIComponent(datasetId)}/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({ detail: "Merge failed" }));
        throw new Error(errData.detail || `Server returned HTTP ${res.status}`);
      }

      setMergeSuccess(true);
      if (onMerged) onMerged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMerging(false);
    }
  };

  const addedItems = diffResult?.added || [];
  const removedItems = diffResult?.removed || [];
  const modifiedItems = diffResult?.modified || [];

  const allChangedItems = [
    ...addedItems.map((k) => ({ key: k, type: "added" as const })),
    ...removedItems.map((k) => ({ key: k, type: "removed" as const })),
    ...modifiedItems.map((k) => ({ key: k, type: "modified" as const })),
  ];

  const filteredItems = allChangedItems.filter((it) => {
    if (filterType !== "all" && it.type !== filterType) return false;
    if (search && !it.key.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="diff-drawer-title"
      ref={containerRef}
      className="fixed inset-0 z-50 flex justify-end bg-black/80 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-full w-full max-w-2xl flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl">
        {/* Drawer Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-4">
          <div className="flex items-center gap-2">
            <span aria-hidden className="text-emerald-400 font-mono text-base">⇄</span>
            <div>
              <h2 id="diff-drawer-title" className="text-sm font-semibold text-zinc-100">
                Diff & Merge Branches
              </h2>
              <p className="text-xs text-zinc-400">
                Compare and merge zero-copy LakeFS dataset versions
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close diff drawer"
            className="inline-flex h-8 w-8 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            ✕
          </button>
        </div>

        {/* Branch Selectors & Swap Bar */}
        <div className="border-b border-zinc-800 bg-zinc-900/50 p-4">
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <label htmlFor="base-branch-sel" className="block text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Base Branch
              </label>
              <select
                id="base-branch-sel"
                value={baseBranch}
                onChange={(e) => setBaseBranch(e.target.value)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.name}>
                    {b.name} {b.is_default ? "(default)" : ""}
                  </option>
                ))}
              </select>
            </div>

            <button
              type="button"
              onClick={handleSwap}
              title="Swap base and compare branches"
              aria-label="Swap base and compare branches"
              className="mt-4 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-zinc-700 bg-zinc-800 text-xs text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              ⇄
            </button>

            <div className="flex-1">
              <label htmlFor="compare-branch-sel" className="block text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                Compare Branch
              </label>
              <select
                id="compare-branch-sel"
                value={compareBranch}
                onChange={(e) => setCompareBranch(e.target.value)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.name}>
                    {b.name} {b.is_default ? "(default)" : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Summary counters with semantic WCAG AA badges */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800/80 pt-3">
            <div className="flex items-center gap-2">
              <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-mono font-medium ${BRANCH_DIFF_STYLE.added.badge}`}>
                <span>+</span>
                <span>{diffResult?.stats.added ?? addedItems.length} Added</span>
              </span>
              <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-mono font-medium ${BRANCH_DIFF_STYLE.removed.badge}`}>
                <span>-</span>
                <span>{diffResult?.stats.removed ?? removedItems.length} Removed</span>
              </span>
              <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-mono font-medium ${BRANCH_DIFF_STYLE.modified.badge}`}>
                <span>~</span>
                <span>{diffResult?.stats.modified ?? modifiedItems.length} Modified</span>
              </span>
            </div>

            <button
              type="button"
              onClick={() => setShowMergePanel(!showMergePanel)}
              className={`rounded px-3 py-1 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
            >
              {showMergePanel ? "Hide Merge" : `Merge into ${baseBranch}`}
            </button>
          </div>
        </div>

        {/* Merge Panel Drawer Section */}
        {showMergePanel && (
          <div className="border-b border-zinc-800 bg-zinc-900/80 p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
              Merge <span className="font-mono text-emerald-300">{compareBranch}</span> into{" "}
              <span className="font-mono text-sky-300">{baseBranch}</span>
            </h3>

            {mergeSuccess ? (
              <div className="mt-3 rounded border border-emerald-800/60 bg-emerald-950/40 p-3 text-xs text-emerald-200">
                ✓ Branches merged successfully! The changes are now in {baseBranch}.
              </div>
            ) : (
              <div className="mt-3 space-y-3">
                <div>
                  <label htmlFor="conflict-strategy" className="block text-xs font-medium text-zinc-400">
                    Conflict Resolution Strategy
                  </label>
                  <select
                    id="conflict-strategy"
                    value={conflictStrategy}
                    onChange={(e) => setConflictStrategy(e.target.value as "fail" | "ours" | "theirs")}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 focus:border-emerald-400 focus:outline-none"
                  >
                    <option value="fail">Fail on conflict (recommended / safe)</option>
                    <option value="ours">Keep base changes (ours)</option>
                    <option value="theirs">Overwrite with compare changes (theirs)</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="merge-msg" className="block text-xs font-medium text-zinc-400">
                    Merge Commit Message
                  </label>
                  <input
                    id="merge-msg"
                    type="text"
                    value={mergeMessage}
                    onChange={(e) => setMergeMessage(e.target.value)}
                    disabled={merging}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 hover:border-zinc-600 focus:border-emerald-400 focus:outline-none"
                  />
                </div>

                {error && (
                  <div role="alert" className="rounded border border-red-800/70 bg-red-950/40 p-2.5 text-xs text-red-200">
                    {error}
                  </div>
                )}

                <div className="flex justify-end gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => setShowMergePanel(false)}
                    className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleMerge}
                    disabled={merging}
                    className={`rounded px-3 py-1 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
                  >
                    {merging ? "Merging…" : "Execute Merge"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Search & Filter Toolbar */}
        <div className="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-2 text-xs">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter changed item keys…"
            className="w-56 rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-400 focus:outline-none"
          />

          <div className="flex items-center gap-1">
            {(["all", "added", "removed", "modified"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setFilterType(t)}
                className={`rounded px-2 py-0.5 text-xs font-medium capitalize transition-colors ${
                  filterType === t
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Changed Items List */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-12 text-zinc-400 text-xs">
              <span className="h-4 w-4 rounded-full border-2 border-emerald-400 border-t-transparent animate-spin motion-reduce:animate-none" />
              <p className="mt-2">Computing zero-copy tree diff…</p>
            </div>
          ) : filteredItems.length === 0 ? (
            <div className="py-12 text-center text-xs text-zinc-400">
              {allChangedItems.length === 0
                ? "Branches are identical. Zero item differences found."
                : "No changed items match the active search or filter."}
            </div>
          ) : (
            <ul className="space-y-2">
              {filteredItems.map((item, idx) => {
                const style = BRANCH_DIFF_STYLE[item.type];
                return (
                  <li
                    key={`${item.key}-${idx}`}
                    className={`flex items-center justify-between rounded-lg border border-zinc-800/80 p-3 text-xs ${style.rowClass}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`inline-flex h-5 w-5 items-center justify-center rounded font-mono font-bold ${style.badge}`}>
                        {style.prefix}
                      </span>
                      <span className="font-mono text-zinc-100 font-medium">{item.key}</span>
                    </div>

                    <span className={`rounded border px-2 py-0.5 font-mono text-[11px] uppercase ${style.badge}`}>
                      {style.label}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
