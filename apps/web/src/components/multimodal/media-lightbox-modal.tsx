"use client";

import React, { useEffect, useRef } from "react";
import type { Media } from "@/lib/types";
import { ImageBBoxViewer } from "./image-bbox-viewer";
import { AudioWaveformPlayer } from "./audio-waveform-player";
import { PdfVisualPreview } from "./pdf-visual-preview";
import { TextDiffViewer } from "./text-diff-viewer";

export interface MediaLightboxModalProps {
  media: Media | null;
  title?: string;
  isOpen: boolean;
  onClose: () => void;
}

export function MediaLightboxModal({
  media,
  title = "Multimodal Media Preview",
  isOpen,
  onClose,
}: MediaLightboxModalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      // Simple focus trap
      if (e.key === "Tab" && containerRef.current) {
        const focusable = containerRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    closeBtnRef.current?.focus();

    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !media) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="media-lightbox-title"
      ref={containerRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative flex max-h-[92vh] w-full max-w-4xl flex-col rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl overflow-hidden">
        {/* Lightbox Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="rounded bg-emerald-950/60 border border-emerald-800/60 px-2 py-0.5 font-mono text-[11px] text-emerald-300 uppercase">
              {media.kind}
            </span>
            <h2 id="media-lightbox-title" className="text-sm font-semibold text-zinc-100">
              {title}
            </h2>
          </div>

          <div className="flex items-center gap-3">
            {media.url && (
              <a
                href={media.url}
                target="_blank"
                rel="noreferrer noopener"
                className="font-mono text-xs text-zinc-400 hover:text-zinc-200"
              >
                Open original ↗
              </a>
            )}
            <button
              ref={closeBtnRef}
              type="button"
              onClick={onClose}
              aria-label="Close modal"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900 text-sm text-zinc-300 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Lightbox Body */}
        <div className="overflow-y-auto p-5">
          {media.kind === "image" || media.kind === "bbox" ? (
            <ImageBBoxViewer media={media} interactive showControls />
          ) : media.kind === "audio" ? (
            <AudioWaveformPlayer media={media} />
          ) : media.kind === "pdf" ? (
            <PdfVisualPreview media={media} />
          ) : media.kind === "text_diff" ? (
            <TextDiffViewer diff={media.diff} />
          ) : (
            <div className="rounded border border-zinc-800 bg-zinc-900/50 p-6 text-center text-xs text-zinc-300">
              <p>Generic Media Payload: {media.mime || media.kind}</p>
              <a
                href={media.url}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-2 inline-block font-mono text-emerald-400 underline"
              >
                Download artifact
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
