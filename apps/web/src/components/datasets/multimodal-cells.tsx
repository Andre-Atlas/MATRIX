"use client";

import React from "react";
import { getMediaSummary } from "@/lib/multimodal-utils";
import type { Media } from "@/lib/types";

export interface MultimodalCellProps {
  media?: Media[];
  onInspect?: () => void;
  className?: string;
}

export function MultimodalCell({ media, onInspect, className = "" }: MultimodalCellProps) {
  const summary = getMediaSummary(media);

  if (summary.count === 0) {
    return <span className="text-[11px] text-zinc-400">text</span>;
  }

  const primary = media?.[0];

  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      {summary.hasImage && primary && (primary.kind === "image" || primary.kind === "bbox") && (
        <button
          type="button"
          onClick={onInspect}
          title="Click to inspect image and bounding boxes"
          aria-label="Inspect image with bounding boxes"
          className="group relative flex items-center gap-1 rounded border border-zinc-700 bg-zinc-900 p-0.5 hover:border-emerald-400 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={primary.url}
            alt="Thumbnail"
            className="h-8 w-8 rounded object-cover"
            loading="lazy"
          />
          {summary.boxCount > 0 && (
            <span className="rounded bg-sky-950/80 border border-sky-800/80 px-1 py-0.2 font-mono text-[10px] text-sky-300">
              {summary.boxCount} box{summary.boxCount === 1 ? "" : "es"}
            </span>
          )}
        </button>
      )}

      {summary.hasAudio && (
        <button
          type="button"
          onClick={onInspect}
          title="Click to inspect audio and waveform"
          aria-label="Inspect audio with waveform"
          className="inline-flex items-center gap-1 rounded border border-emerald-800/60 bg-emerald-950/40 px-1.5 py-0.5 font-mono text-[11px] text-emerald-300 hover:bg-emerald-900/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
        >
          <span aria-hidden>♫</span>
          <span>Audio</span>
          {primary?.duration_ms && (
            <span className="text-zinc-400">({(primary.duration_ms / 1000).toFixed(0)}s)</span>
          )}
        </button>
      )}

      {summary.hasPdf && (
        <button
          type="button"
          onClick={onInspect}
          title="Click to inspect PDF visual page and ColPali attention"
          aria-label="Inspect PDF document visual preview"
          className="inline-flex items-center gap-1 rounded border border-sky-800/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[11px] text-sky-300 hover:bg-sky-900/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
        >
          <span aria-hidden>📄</span>
          <span>PDF p.{primary?.page || 1}</span>
        </button>
      )}

      {summary.hasDiff && (
        <button
          type="button"
          onClick={onInspect}
          title="Click to inspect side-by-side text diff"
          aria-label="Inspect side-by-side text diff"
          className="inline-flex items-center gap-1 rounded border border-amber-800/60 bg-amber-950/40 px-1.5 py-0.5 font-mono text-[11px] text-amber-300 hover:bg-amber-900/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
        >
          <span aria-hidden>~</span>
          <span>Diff</span>
        </button>
      )}
    </div>
  );
}
