"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { formatTimestamp } from "@/lib/format";
import { inputPreview } from "@/lib/eval-matrix";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { Dataset, DatasetBranch, DatasetCommit, DatasetCommitItem, DatasetItem } from "@/lib/types";
import { EmptyState, Mono, td, th } from "@/components/ui";
import { AddItemForm } from "@/components/evals/add-item-form";
import { DeleteItemButton } from "@/components/evals/delete-item-button";
import { secondaryBtn } from "@/components/evals/ui";
import { BranchSelector } from "./branch-selector";
import { CommitHistoryTimeline } from "./commit-history-timeline";
import { MultimodalCell } from "./multimodal-cells";
import { RowInspectorDrawer } from "./row-inspector-drawer";

export interface DatasetStudioClientProps {
  dataset: Dataset;
  initialItems: DatasetItem[];
  initialBranches: DatasetBranch[];
  initialCommits: DatasetCommit[];
}

export function DatasetStudioClient({
  dataset,
  initialItems,
  initialBranches,
  initialCommits,
}: DatasetStudioClientProps) {
  const [branches, setBranches] = useState<DatasetBranch[]>(initialBranches);
  const [activeBranch, setActiveBranch] = useState<string>(
    initialBranches.find((b) => b.is_default)?.name || initialBranches[0]?.name || "main"
  );
  const [commits, setCommits] = useState<DatasetCommit[]>(initialCommits);
  const [showTimeline, setShowTimeline] = useState(false);
  const [items, setItems] = useState<(DatasetItem | DatasetCommitItem)[]>(initialItems);
  const [loadingItems, setLoadingItems] = useState(false);
  const [selectedItem, setSelectedItem] = useState<(DatasetItem | DatasetCommitItem) | null>(null);

  const activeBranchObj = branches.find((b) => b.name === activeBranch) || branches[0];
  const currentHeadCommitId = activeBranchObj?.head_commit_id;

  const isFirstMount = useRef(true);

  // When branch changes, fetch branch commits & commit items
  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return;
    }
    let cancelled = false;

    async function loadBranchData() {
      if (!activeBranch) return;
      try {
        setLoadingItems(true);
        // Fetch commits for this branch
        const commitsRes = await fetch(
          `/api/proxy/api/v1/datasets/${encodeURIComponent(dataset.id)}/commits?branch=${encodeURIComponent(
            activeBranch
          )}`
        );
        if (commitsRes.ok) {
          const cData = await commitsRes.json();
          if (!cancelled && Array.isArray(cData.items)) {
            setCommits(cData.items);
            const head = cData.items[0];
            if (head) {
              // Fetch items for head commit
              const itemsRes = await fetch(
                `/api/proxy/api/v1/datasets/${encodeURIComponent(dataset.id)}/commits/${encodeURIComponent(
                  head.id
                )}/items`
              );
              if (itemsRes.ok) {
                const iData = await itemsRes.json();
                if (!cancelled && Array.isArray(iData.items) && iData.items.length > 0) {
                  setItems(iData.items);
                  return;
                }
              }
            }
          }
        }
      } catch {
        // Fallback to initial items on network error
      } finally {
        if (!cancelled) setLoadingItems(false);
      }
    }

    loadBranchData();
    return () => {
      cancelled = true;
    };
  }, [dataset.id, activeBranch]);

  const handleBranchCreated = (newBranch: DatasetBranch) => {
    setBranches((prev) => [...prev, newBranch]);
  };

  const handleCommitReverted = (newCommit: DatasetCommit) => {
    setCommits((prev) => [newCommit, ...prev]);
    // Update branch head
    setBranches((prev) =>
      prev.map((b) =>
        b.name === activeBranch ? { ...b, head_commit_id: newCommit.id, updated_at: newCommit.created_at } : b
      )
    );
  };

  return (
    <div>
      {/* Breadcrumb with WCAG AA text-zinc-400 */}
      <nav aria-label="Breadcrumb" className="mb-2 text-xs text-zinc-400">
        <Link href="/datasets" className="hover:text-zinc-200">
          Datasets
        </Link>{" "}
        / <Mono className="text-zinc-200">{dataset.name}</Mono>
      </nav>

      {/* Main Header */}
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3 border-b border-zinc-800 pb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate font-mono text-base font-semibold text-zinc-50">{dataset.name}</h1>
            <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-2 py-0.5 font-mono text-[11px] text-emerald-300">
              LakeFS / DVC Zero-Copy
            </span>
          </div>

          {dataset.description && <p className="mt-0.5 text-xs text-zinc-300">{dataset.description}</p>}

          <dl className="mt-1.5 flex flex-wrap gap-4 text-xs">
            <div className="flex gap-1.5">
              <dt className="text-zinc-400">Items</dt>
              <dd className="font-mono tabular-nums text-zinc-200">{items.length}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-zinc-400">Created</dt>
              <dd className="font-mono tabular-nums text-zinc-200">{formatTimestamp(dataset.created_at)}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-zinc-400">ID</dt>
              <dd className="font-mono text-zinc-300">{dataset.id}</dd>
            </div>
          </dl>
        </div>

        <div className="flex items-center gap-2">
          <a
            href={`/api/proxy/datasets/${encodeURIComponent(dataset.id)}/export.jsonl`}
            download={`${dataset.name}.jsonl`}
            className={`${secondaryBtn} leading-7`}
          >
            Export JSONL
          </a>
          <Link
            href={`/experiments/new?dataset=${encodeURIComponent(dataset.id)}`}
            className={`h-7 inline-flex items-center rounded px-3 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
          >
            New experiment
          </Link>
        </div>
      </header>

      {/* LakeFS Branch Selector & Versioning Toolbar */}
      <div className="mb-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
        <BranchSelector
          datasetId={dataset.id}
          branches={branches}
          activeBranch={activeBranch}
          onBranchChange={setActiveBranch}
          onBranchCreated={handleBranchCreated}
          showTimeline={showTimeline}
          onToggleTimeline={() => setShowTimeline(!showTimeline)}
          currentCommitHash={currentHeadCommitId}
        />
      </div>

      {/* Collapsible Commit History Timeline */}
      {showTimeline && (
        <div className="mb-4">
          <CommitHistoryTimeline
            datasetId={dataset.id}
            branch={activeBranch}
            commits={commits}
            currentCommitId={currentHeadCommitId}
            onCommitReverted={handleCommitReverted}
          />
        </div>
      )}

      {/* Add Item Form */}
      <div className="mb-4">
        <AddItemForm datasetId={dataset.id} />
      </div>

      {/* W&B Tables Multimodal Inspector Items Table */}
      {loadingItems ? (
        <div
          role="status"
          className="flex flex-col items-center justify-center rounded-xl border border-zinc-800 bg-zinc-950 p-12 text-zinc-400 text-xs"
        >
          <span className="h-5 w-5 rounded-full border-2 border-emerald-400 border-t-transparent animate-spin motion-reduce:animate-none" />
          <p className="mt-2">Loading versioned dataset items…</p>
        </div>
      ) : items.length === 0 ? (
        <EmptyState title="This dataset branch has no items">
          Add an item above, switch branches, or open a trace span and click &ldquo;Add to dataset&rdquo;.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950 shadow-sm">
          <table className="w-full min-w-[1080px] table-fixed">
            <colgroup>
              <col className="w-12" />
              <col className="w-40" />
              <col className="w-[30%]" />
              <col />
              <col className="w-20" />
              <col className="w-36" />
              <col className="w-32" />
            </colgroup>
            <thead className="border-b border-zinc-800 bg-zinc-900/70">
              <tr>
                <th className={`${th} text-right text-zinc-400`}>#</th>
                <th className={`${th} text-zinc-400`}>Multimodal Media</th>
                <th className={`${th} text-zinc-400`}>Input</th>
                <th className={`${th} text-zinc-400`}>Expected output</th>
                <th className={`${th} text-right text-zinc-400`}>Context</th>
                <th className={`${th} text-zinc-400`}>Created (UTC)</th>
                <th className={`${th} text-right text-zinc-400`}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {items.map((it, i) => (
                <tr
                  key={it.id}
                  className="align-top hover:bg-zinc-900/60 transition-colors cursor-pointer group"
                  onClick={() => setSelectedItem(it)}
                >
                  <td className={`${td} text-right align-top`}>
                    <Mono className="text-zinc-400">{i + 1}</Mono>
                  </td>

                  {/* Rich Multimodal Cell */}
                  <td className="px-3 py-2 align-top" onClick={(e) => e.stopPropagation()}>
                    <MultimodalCell media={it.media} onInspect={() => setSelectedItem(it)} />
                  </td>

                  <td className="px-3 py-2">
                    <pre
                      className="line-clamp-3 whitespace-pre-wrap break-words font-mono text-xs text-emerald-100/90"
                      title={JSON.stringify(it.input, null, 2)}
                    >
                      {inputPreview(it.input, 400)}
                    </pre>
                  </td>

                  <td className="px-3 py-2">
                    {it.expected_output ? (
                      <p className="line-clamp-3 break-words text-xs text-zinc-200" title={it.expected_output}>
                        {it.expected_output}
                      </p>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>

                  <td className={`${td} text-right align-top`}>
                    <Mono className={it.context.length ? "text-zinc-200" : "text-zinc-400"}>
                      {it.context.length}
                    </Mono>
                  </td>

                  <td className={`${td} align-top`}>
                    <Mono className="text-xs text-zinc-300">{formatTimestamp(it.created_at)}</Mono>
                  </td>

                  <td className={`${td} text-right align-top`} onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={() => setSelectedItem(it)}
                        title="Inspect multimodal row details"
                        className="rounded border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-xs text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
                      >
                        Inspect
                      </button>
                      <DeleteItemButton datasetId={dataset.id} itemId={it.id} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Row Inspector Drawer */}
      <RowInspectorDrawer
        isOpen={selectedItem !== null}
        item={selectedItem}
        items={items}
        onClose={() => setSelectedItem(null)}
        onSelectItem={(item) => setSelectedItem(item)}
      />
    </div>
  );
}
