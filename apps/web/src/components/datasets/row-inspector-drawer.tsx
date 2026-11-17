"use client";

import React, { useEffect, useRef, useState } from "react";
import { formatTimestamp } from "@/lib/format";
import type { DatasetCommitItem, DatasetItem, Media } from "@/lib/types";
import { ImageBBoxViewer } from "../multimodal/image-bbox-viewer";
import { AudioWaveformPlayer } from "../multimodal/audio-waveform-player";
import { PdfVisualPreview } from "../multimodal/pdf-visual-preview";
import { TextDiffViewer } from "../multimodal/text-diff-viewer";

export interface RowInspectorDrawerProps {
  isOpen: boolean;
  item: DatasetItem | DatasetCommitItem | null;
  items: (DatasetItem | DatasetCommitItem)[];
  onClose: () => void;
  onSelectItem: (item: DatasetItem | DatasetCommitItem) => void;
}

export function RowInspectorDrawer({
  isOpen,
  item,
  items,
  onClose,
  onSelectItem,
}: RowInspectorDrawerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeTab, setActiveTab] = useState<"multimodal" | "raw">("multimodal");

  const currentIndex = item ? items.findIndex((it) => it.id === item.id) : -1;
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < items.length - 1;

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowLeft" && (e.metaKey || e.altKey) && hasPrev) {
        e.preventDefault();
        onSelectItem(items[currentIndex - 1]);
      } else if (e.key === "ArrowRight" && (e.metaKey || e.altKey) && hasNext) {
        e.preventDefault();
        onSelectItem(items[currentIndex + 1]);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose, currentIndex, hasPrev, hasNext, items, onSelectItem]);

  if (!isOpen || !item) return null;

  const mediaList: Media[] = Array.isArray(item.media) ? item.media : [];
  const imageMedia = mediaList.find((m) => m.kind === "image" || m.kind === "bbox");
  const audioMedia = mediaList.find((m) => m.kind === "audio");
  const pdfMedia = mediaList.find((m) => m.kind === "pdf");
  const diffMedia = mediaList.find((m) => m.kind === "text_diff");

  const itemKey = "item_key" in item ? item.item_key : item.id;
  const contentHash = "content_hash" in item ? item.content_hash : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="row-inspector-title"
      ref={containerRef}
      className="fixed inset-0 z-50 flex justify-end bg-black/80 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-full w-full max-w-3xl flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl">
        {/* Drawer Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-4">
          <div className="flex items-center gap-3">
            <span aria-hidden className="text-emerald-400 font-mono text-base">🔍</span>
            <div>
              <div className="flex items-center gap-2">
                <h2 id="row-inspector-title" className="text-sm font-semibold text-zinc-100 font-mono">
                  {itemKey}
                </h2>
                {contentHash && (
                  <span className="rounded bg-zinc-900 border border-zinc-800 px-1.5 py-0.2 font-mono text-[10px] text-zinc-400">
                    {contentHash.slice(0, 12)}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-400 mt-0.5">
                Created: {formatTimestamp(item.created_at)} · Row #{currentIndex + 1} of {items.length}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Paginator */}
            <div className="flex items-center gap-1 mr-2">
              <button
                type="button"
                disabled={!hasPrev}
                onClick={() => onSelectItem(items[currentIndex - 1])}
                title="Previous row (Alt+←)"
                aria-label="Previous row"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                ←
              </button>
              <button
                type="button"
                disabled={!hasNext}
                onClick={() => onSelectItem(items[currentIndex + 1])}
                title="Next row (Alt+→)"
                aria-label="Next row"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                →
              </button>
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close inspector drawer"
              className="inline-flex h-8 w-8 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-xs text-zinc-300 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              ✕
            </button>
          </div>
        </div>

        {/* View Mode Tabs */}
        <div className="flex border-b border-zinc-800 bg-zinc-900/60 px-5 py-2">
          <div className="inline-flex rounded border border-zinc-800 bg-zinc-900 p-0.5" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "multimodal"}
              onClick={() => setActiveTab("multimodal")}
              className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
                activeTab === "multimodal"
                  ? "bg-zinc-800 text-zinc-100 shadow-sm"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Multimodal Views ({mediaList.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "raw"}
              onClick={() => setActiveTab("raw")}
              className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
                activeTab === "raw"
                  ? "bg-zinc-800 text-zinc-100 shadow-sm"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Raw Input & Ground Truth
            </button>
          </div>
        </div>

        {/* Drawer Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {activeTab === "multimodal" ? (
            mediaList.length === 0 ? (
              <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center text-xs text-zinc-400">
                <p>This dataset item has no attached multimodal binary media.</p>
                <p className="mt-1">Check the &quot;Raw Input & Ground Truth&quot; tab for text input fields.</p>
              </div>
            ) : (
              <div className="space-y-6">
                {/* 1. Image + Interactive SVG Bounding Boxes */}
                {imageMedia && (
                  <section aria-labelledby="section-image-bboxes">
                    <h3 id="section-image-bboxes" className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-300">
                      Visual Image & Bounding Boxes
                    </h3>
                    <ImageBBoxViewer media={imageMedia} interactive showControls />
                  </section>
                )}

                {/* 2. Audio Player & SVG Waveform */}
                {audioMedia && (
                  <section aria-labelledby="section-audio-waveform">
                    <h3 id="section-audio-waveform" className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-300">
                      Audio Stream & Waveform Spectrogram
                    </h3>
                    <AudioWaveformPlayer
                      media={audioMedia}
                      transcript={item.expected_output || JSON.stringify(item.input)}
                    />
                  </section>
                )}

                {/* 3. Visual PDF / Document Preview & ColPali Attention */}
                {pdfMedia && (
                  <section aria-labelledby="section-pdf-preview">
                    <h3 id="section-pdf-preview" className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-300">
                      Document Page Preview & ColPali RAG Attention
                    </h3>
                    <PdfVisualPreview media={pdfMedia} extractedText={item.expected_output} />
                  </section>
                )}

                {/* 4. Side-by-Side Text Diff */}
                {diffMedia && (
                  <section aria-labelledby="section-text-diff">
                    <h3 id="section-text-diff" className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-300">
                      Structured Text Diff
                    </h3>
                    <TextDiffViewer diff={diffMedia.diff} />
                  </section>
                )}
              </div>
            )
          ) : (
            <div className="space-y-4">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                  Input Payload
                </h4>
                <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 font-mono text-xs text-emerald-100/90">
                  {JSON.stringify(item.input, null, 2)}
                </pre>
              </div>

              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                  Expected Output (Ground Truth)
                </h4>
                {item.expected_output ? (
                  <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 font-mono text-xs text-zinc-200">
                    {item.expected_output}
                  </pre>
                ) : (
                  <p className="text-xs text-zinc-400 italic">None specified</p>
                )}
              </div>

              {item.context && item.context.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                    Context References ({item.context.length})
                  </h4>
                  <ul className="space-y-1.5">
                    {item.context.map((ctx, idx) => (
                      <li
                        key={idx}
                        className="rounded border border-zinc-800 bg-zinc-900/40 p-2 font-mono text-xs text-zinc-300"
                      >
                        {ctx}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {item.metadata && Object.keys(item.metadata).length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                    Metadata
                  </h4>
                  <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-900/40 p-2 font-mono text-[11px] text-zinc-400">
                    {JSON.stringify(item.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
