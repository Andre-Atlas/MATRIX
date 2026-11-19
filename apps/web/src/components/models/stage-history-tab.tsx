"use client";

import React from "react";
import { EmptyState } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { getModelStageStyle } from "@/lib/mlops-tokens";
import type { ModelStageHistoryItem } from "@/lib/types";

export interface StageHistoryTabProps {
  history: ModelStageHistoryItem[];
  loading?: boolean;
}

export function StageHistoryTab({ history, loading = false }: StageHistoryTabProps) {
  if (loading) {
    return (
      <div role="status" className="p-6 text-xs text-zinc-400">
        Loading stage history…
      </div>
    );
  }

  if (history.length === 0) {
    return (
      <div className="p-6">
        <EmptyState title="No stage transitions recorded">
          <p className="text-xs text-zinc-400">
            Promoting a version to staging, production, or archived records an immutable audit entry here.
          </p>
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="space-y-3 p-4">
      <div>
        <h3 className="text-sm font-semibold text-zinc-100">Stage Transition Timeline</h3>
        <p className="text-xs text-zinc-400">
          Unique production enforcement: promoting a version to production automatically archives any previous production version in the same transaction.
        </p>
      </div>

      <ol aria-label="Model stage history" className="space-y-2.5">
        {history.map((item) => {
          const fromStyle = getModelStageStyle(item.from_stage);
          const toStyle = getModelStageStyle(item.to_stage);
          return (
            <li
              key={item.id}
              className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono font-semibold text-zinc-100">
                    v{item.version}
                  </span>
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${fromStyle.badge}`}>
                    {fromStyle.label}
                  </span>
                  <span aria-hidden="true" className="text-zinc-400">
                    →
                  </span>
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${toStyle.badge}`}>
                    {toStyle.label}
                  </span>
                </div>
                <span className="font-mono text-[11px] text-zinc-400">
                  {formatTimestamp(item.ts)}
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                <span>
                  Actor: <span className="font-mono text-zinc-200">{item.actor_id}</span> ({item.actor_kind})
                </span>
              </div>
              {item.comment && (
                <p className="mt-1.5 rounded border border-zinc-800/80 bg-zinc-900/60 px-2.5 py-1.5 text-xs text-zinc-200">
                  {item.comment}
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
