"use client";

import React, { useEffect, useRef, useState } from "react";
import { PRIMARY_BTN_CLASS } from "@/lib/mlops-tokens";
import type { DatasetCommit, RevertBranchRequest } from "@/lib/types";

export interface RevertConfirmDialogProps {
  isOpen: boolean;
  datasetId: string;
  branch: string;
  targetCommit: DatasetCommit | null;
  onClose: () => void;
  onReverted: (newCommit: DatasetCommit) => void;
}

export function RevertConfirmDialog({
  isOpen,
  datasetId,
  branch,
  targetCommit,
  onClose,
  onReverted,
}: RevertConfirmDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isOpen && targetCommit) {
      setMessage(`Revert to commit ${targetCommit.id.slice(0, 7)}: ${targetCommit.message}`);
      setError(null);
      setTimeout(() => confirmBtnRef.current?.focus(), 50);
    }
  }, [isOpen, targetCommit]);

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

  if (!isOpen || !targetCommit) return null;

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);

    try {
      const payload: RevertBranchRequest = {
        branch,
        target_commit_id: targetCommit.id,
        message: message.trim() || undefined,
      };

      const res = await fetch(`/api/proxy/api/v1/datasets/${encodeURIComponent(datasetId)}/revert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({ detail: "Failed to revert branch" }));
        throw new Error(errData.detail || `Server returned HTTP ${res.status}`);
      }

      const newCommit: DatasetCommit = await res.json();
      onReverted(newCommit);
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
      aria-labelledby="revert-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div className="flex items-center gap-2">
            <span aria-hidden className="text-amber-400 font-mono text-sm">↺</span>
            <h2 id="revert-dialog-title" className="text-sm font-semibold text-zinc-100">
              Confirm Branch Revert
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close confirmation dialog"
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-400 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <div className="rounded border border-amber-800/60 bg-amber-950/30 p-3 text-xs text-amber-200">
            <p className="font-semibold">Explicit impact:</p>
            <p className="mt-1">
              You are reverting branch <span className="font-mono font-bold text-zinc-100">{branch}</span> to the snapshot
              at commit <span className="font-mono font-bold text-zinc-100">{targetCommit.id.slice(0, 8)}</span>.
            </p>
            <p className="mt-1 text-[11px] text-amber-300/90">
              This zero-copy operation will append a new revert commit pointing the working tree to the state of this commit.
            </p>
          </div>

          <div className="rounded border border-zinc-800 bg-zinc-900/60 p-3 text-xs">
            <div className="flex items-center justify-between font-mono text-[11px] text-zinc-400">
              <span>Commit: {targetCommit.id}</span>
              <span>{targetCommit.author || "system"}</span>
            </div>
            <p className="mt-1 text-zinc-200 font-medium">{targetCommit.message}</p>
          </div>

          <div>
            <label htmlFor="revert-msg" className="block text-xs font-medium uppercase tracking-wider text-zinc-400">
              Revert Commit Message
            </label>
            <input
              id="revert-msg"
              type="text"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              disabled={submitting}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 hover:border-zinc-600 focus:border-emerald-400 focus:outline-none"
            />
          </div>

          {error && (
            <div role="alert" className="rounded border border-red-800/70 bg-red-950/40 p-2.5 text-xs text-red-200">
              {error}
            </div>
          )}
        </div>

        <div className="mt-5 flex items-center justify-end gap-2 border-t border-zinc-800 pt-3">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            Cancel
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className={`rounded px-3 py-1.5 text-xs font-medium ${PRIMARY_BTN_CLASS}`}
          >
            {submitting ? "Reverting…" : "Confirm & Revert Snapshot"}
          </button>
        </div>
      </div>
    </div>
  );
}
