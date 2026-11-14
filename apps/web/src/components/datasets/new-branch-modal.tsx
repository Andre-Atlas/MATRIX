"use client";

import React, { useEffect, useRef, useState } from "react";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { CreateDatasetBranchRequest, DatasetBranch } from "@/lib/types";

export interface NewBranchModalProps {
  isOpen: boolean;
  datasetId: string;
  branches: DatasetBranch[];
  currentBranch: string;
  onClose: () => void;
  onBranchCreated: (branch: DatasetBranch) => void;
}

export function NewBranchModal({
  isOpen,
  datasetId,
  branches,
  currentBranch,
  onClose,
  onBranchCreated,
}: NewBranchModalProps) {
  const [name, setName] = useState("");
  const [fromBranch, setFromBranch] = useState(currentBranch);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setName("");
      setFromBranch(currentBranch);
      setError(null);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen, currentBranch]);

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanName = name.trim();
    if (!cleanName) {
      setError("Branch name is required.");
      return;
    }

    if (!/^[a-zA-Z0-9/_-]+$/.test(cleanName)) {
      setError("Branch name can only contain letters, numbers, slashes, dashes, and underscores.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const payload: CreateDatasetBranchRequest = {
        name: cleanName,
        from_branch: fromBranch,
      };

      const res = await fetch(`/api/proxy/api/v1/datasets/${encodeURIComponent(datasetId)}/branches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({ detail: "Failed to create branch" }));
        throw new Error(errData.detail || `Server returned HTTP ${res.status}`);
      }

      const created: DatasetBranch = await res.json();
      onBranchCreated(created);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-branch-modal-title"
      ref={containerRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div className="flex items-center gap-2">
            <span aria-hidden className="text-emerald-400 font-mono text-sm">⑂</span>
            <h2 id="new-branch-modal-title" className="text-sm font-semibold text-zinc-100">
              Create Zero-Copy Branch
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-400 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label htmlFor="branch-name" className="block text-xs font-medium uppercase tracking-wider text-zinc-400">
              Branch Name
            </label>
            <input
              ref={inputRef}
              id="branch-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. feat/colpali-v2 or fix/pii-scrub"
              disabled={submitting}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 placeholder:text-zinc-600 hover:border-zinc-600 focus:border-emerald-400 focus:outline-none"
            />
            <p className="mt-1 text-[11px] text-zinc-400">
              Zero-copy LakeFS branch referencing the same underlying immutable object blobs.
            </p>
          </div>

          <div>
            <label htmlFor="from-branch" className="block text-xs font-medium uppercase tracking-wider text-zinc-400">
              Source Branch
            </label>
            <select
              id="from-branch"
              value={fromBranch}
              onChange={(e) => setFromBranch(e.target.value)}
              disabled={submitting}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 hover:border-zinc-600 focus:border-emerald-400 focus:outline-none"
            >
              {branches.map((b) => (
                <option key={b.id} value={b.name}>
                  {b.name} ({b.head_commit_id?.slice(0, 7) || "head"}) {b.is_default ? "· default" : ""}
                </option>
              ))}
            </select>
          </div>

          {error && (
            <div role="alert" className="rounded border border-red-800/70 bg-red-950/40 p-2.5 text-xs text-red-200">
              {error}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-zinc-800">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className={`rounded px-3 py-1.5 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
            >
              {submitting ? "Creating Branch…" : "Create Branch"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
