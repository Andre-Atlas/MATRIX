"use client";

import React, { useMemo, useState } from "react";
import { parseDiffHunks } from "@/lib/multimodal-utils";
import type { TextDiffPayload } from "@/lib/types";

export interface TextDiffViewerProps {
  diff?: TextDiffPayload;
  beforeTitle?: string;
  afterTitle?: string;
  className?: string;
}

export function TextDiffViewer({
  diff,
  beforeTitle = "Reference / Before",
  afterTitle = "Candidate / After",
  className = "",
}: TextDiffViewerProps) {
  const [mode, setMode] = useState<"side-by-side" | "unified">("side-by-side");

  const lines = useMemo(() => parseDiffHunks(diff), [diff]);

  const beforeLines = useMemo(() => (diff?.before ?? "").split("\n"), [diff?.before]);
  const afterLines = useMemo(() => (diff?.after ?? "").split("\n"), [diff?.after]);
  const sideBySideMax = Math.max(beforeLines.length, afterLines.length);

  return (
    <div className={`flex flex-col gap-2 rounded-lg border border-zinc-800 bg-zinc-950 p-4 ${className}`}>
      {/* Diff Controls */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-3 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-medium text-zinc-300">Text Diff</span>
          <span className="rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 py-0.5 font-mono text-[11px] text-emerald-300">
            +{lines.filter((l) => l.type === "add").length}
          </span>
          <span className="rounded border border-red-800/70 bg-red-950/50 px-1.5 py-0.5 font-mono text-[11px] text-red-300">
            -{lines.filter((l) => l.type === "del").length}
          </span>
        </div>

        <div className="inline-flex rounded border border-zinc-800 bg-zinc-900 p-0.5">
          <button
            type="button"
            onClick={() => setMode("side-by-side")}
            className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
              mode === "side-by-side"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Side-by-Side
          </button>
          <button
            type="button"
            onClick={() => setMode("unified")}
            className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
              mode === "unified"
                ? "bg-zinc-800 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Unified
          </button>
        </div>
      </div>

      {mode === "side-by-side" ? (
        <div className="grid grid-cols-1 gap-2 overflow-x-auto md:grid-cols-2">
          {/* Left: Before */}
          <div className="rounded border border-zinc-800 bg-zinc-900/40">
            <div className="border-b border-zinc-800 bg-zinc-900/80 px-3 py-1.5 font-mono text-xs font-medium text-red-300">
              - {beforeTitle}
            </div>
            <pre className="max-h-72 overflow-y-auto p-2 font-mono text-xs leading-relaxed">
              {Array.from({ length: sideBySideMax }).map((_, i) => {
                const text = beforeLines[i];
                const changed = text !== afterLines[i];
                if (text === undefined) {
                  return (
                    <div key={i} className="text-zinc-700 select-none">
                      ~
                    </div>
                  );
                }
                return (
                  <div
                    key={i}
                    className={`flex items-start gap-2 px-1 rounded ${
                      changed ? "bg-red-950/40 text-red-200 border-l-2 border-red-500" : "text-zinc-300"
                    }`}
                  >
                    <span className="w-6 shrink-0 select-none text-right font-mono text-zinc-400 tabular-nums">
                      {i + 1}
                    </span>
                    <span className="w-3 select-none text-red-400">{changed ? "-" : " "}</span>
                    <span className="flex-1 break-all whitespace-pre-wrap">{text}</span>
                  </div>
                );
              })}
            </pre>
          </div>

          {/* Right: After */}
          <div className="rounded border border-zinc-800 bg-zinc-900/40">
            <div className="border-b border-zinc-800 bg-zinc-900/80 px-3 py-1.5 font-mono text-xs font-medium text-emerald-300">
              + {afterTitle}
            </div>
            <pre className="max-h-72 overflow-y-auto p-2 font-mono text-xs leading-relaxed">
              {Array.from({ length: sideBySideMax }).map((_, i) => {
                const text = afterLines[i];
                const changed = text !== beforeLines[i];
                if (text === undefined) {
                  return (
                    <div key={i} className="text-zinc-700 select-none">
                      ~
                    </div>
                  );
                }
                return (
                  <div
                    key={i}
                    className={`flex items-start gap-2 px-1 rounded ${
                      changed
                        ? "bg-emerald-950/40 text-emerald-200 border-l-2 border-emerald-500"
                        : "text-zinc-300"
                    }`}
                  >
                    <span className="w-6 shrink-0 select-none text-right font-mono text-zinc-400 tabular-nums">
                      {i + 1}
                    </span>
                    <span className="w-3 select-none text-emerald-400">{changed ? "+" : " "}</span>
                    <span className="flex-1 break-all whitespace-pre-wrap">{text}</span>
                  </div>
                );
              })}
            </pre>
          </div>
        </div>
      ) : (
        /* Unified view */
        <div className="overflow-x-auto rounded border border-zinc-800 bg-zinc-900/40">
          <pre className="max-h-80 overflow-y-auto p-2 font-mono text-xs leading-relaxed">
            {lines.length === 0 ? (
              <span className="text-zinc-400">No diff hunks recorded.</span>
            ) : (
              lines.map((l, i) => {
                if (l.type === "add") {
                  return (
                    <ins
                      key={i}
                      className="flex items-start gap-2 rounded bg-emerald-950/40 px-1 text-emerald-200 no-underline border-l-2 border-emerald-500"
                    >
                      <span className="w-6 shrink-0 select-none text-right text-zinc-400 tabular-nums" />
                      <span className="w-6 shrink-0 select-none text-right text-emerald-400 tabular-nums">
                        {l.newLineNumber ?? "+"}
                      </span>
                      <span className="w-3 select-none text-emerald-400">+</span>
                      <span className="flex-1 break-all whitespace-pre-wrap">{l.text}</span>
                    </ins>
                  );
                }
                if (l.type === "del") {
                  return (
                    <del
                      key={i}
                      className="flex items-start gap-2 rounded bg-red-950/40 px-1 text-red-200 no-underline border-l-2 border-red-500"
                    >
                      <span className="w-6 shrink-0 select-none text-right text-red-400 tabular-nums">
                        {l.oldLineNumber ?? "-"}
                      </span>
                      <span className="w-6 shrink-0 select-none text-right text-zinc-400 tabular-nums" />
                      <span className="w-3 select-none text-red-400">-</span>
                      <span className="flex-1 break-all whitespace-pre-wrap">{l.text}</span>
                    </del>
                  );
                }
                return (
                  <div key={i} className="flex items-start gap-2 px-1 text-zinc-300">
                    <span className="w-6 shrink-0 select-none text-right text-zinc-400 tabular-nums">
                      {l.oldLineNumber ?? ""}
                    </span>
                    <span className="w-6 shrink-0 select-none text-right text-zinc-400 tabular-nums">
                      {l.newLineNumber ?? ""}
                    </span>
                    <span className="w-3 select-none text-zinc-400"> </span>
                    <span className="flex-1 break-all whitespace-pre-wrap">{l.text}</span>
                  </div>
                );
              })
            )}
          </pre>
        </div>
      )}
    </div>
  );
}
