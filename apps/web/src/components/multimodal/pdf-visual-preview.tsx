"use client";

import React, { useId, useMemo, useState } from "react";
import type { Media } from "@/lib/types";

export interface PdfVisualPreviewProps {
  media: Media;
  extractedText?: string | null;
  className?: string;
}

export function PdfVisualPreview({
  media,
  extractedText = "SECTION 4.2 COMPLIANCE CLAUSE\n\nAll cryptographic keys and symmetric encryption secrets deployed across production clusters MUST undergo automated rotation every 90 days. Key derivation functions shall adhere to FIPS-140-3 guidelines.\n\nAuditor: Bureau Veritas Security Assessment\nStatus: VERIFIED & COMPLIANT",
  className = "",
}: PdfVisualPreviewProps) {
  const [page, setPage] = useState<number>(media.page || 1);
  const totalPages = Math.max(page, 5);
  const [showPatchOverlay, setShowPatchOverlay] = useState(false);
  const [activeTab, setActiveTab] = useState<"visual" | "text">("visual");
  const [copied, setCopied] = useState(false);
  const idPrefix = useId();

  // Synthetic ColPali patch attention heatmap grid (12x16 patches)
  const patchGrid = useMemo(() => {
    const grid: number[][] = [];
    for (let r = 0; r < 12; r++) {
      const row: number[] = [];
      for (let c = 0; c < 16; c++) {
        // Higher attention around the middle header and text lines
        const distFromCenter = Math.hypot((r - 4) / 4, (c - 8) / 8);
        const score = Math.max(0.05, Math.min(0.95, 1 - distFromCenter * 0.7 + (r === 3 || r === 4 ? 0.35 : 0)));
        row.push(Math.round(score * 100) / 100);
      }
      grid.push(row);
    }
    return grid;
  }, []);

  const handleCopy = () => {
    if (!extractedText) return;
    navigator.clipboard.writeText(extractedText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-950 p-4 ${className}`}>
      {/* Header Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-3 text-xs">
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded border border-zinc-800 bg-zinc-900 p-0.5" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "visual"}
              onClick={() => setActiveTab("visual")}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                activeTab === "visual"
                  ? "bg-zinc-800 text-zinc-100 shadow-sm"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Visual Page
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "text"}
              onClick={() => setActiveTab("text")}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                activeTab === "text"
                  ? "bg-zinc-800 text-zinc-100 shadow-sm"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Extracted Text
            </button>
          </div>

          {activeTab === "visual" && (
            <label className="flex cursor-pointer items-center gap-2 text-zinc-300">
              <input
                type="checkbox"
                checked={showPatchOverlay}
                onChange={(e) => setShowPatchOverlay(e.target.checked)}
                className="h-4 w-4 rounded border-zinc-700 bg-zinc-800 text-sky-500 focus:ring-sky-400"
              />
              <span className="text-xs">ColPali Patch Attention</span>
            </label>
          )}
        </div>

        {/* Mini Paginator */}
        <div className="flex items-center gap-2 font-mono text-xs">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            aria-label="Previous PDF page"
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ←
          </button>
          <span className="text-zinc-300">
            Page <span className="font-semibold text-emerald-400">{page}</span> of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            aria-label="Next PDF page"
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            →
          </button>
        </div>
      </div>

      {/* Content Area */}
      {activeTab === "visual" ? (
        <div className="relative mx-auto flex w-full max-w-xl flex-col items-center justify-center overflow-hidden rounded border border-zinc-800 bg-zinc-900/60 p-4 shadow-inner">
          {/* Simulated High-Res Document Canvas */}
          <div className="relative aspect-[3/4] w-full max-w-md rounded bg-white p-6 text-zinc-900 shadow-md">
            <div className="border-b-2 border-zinc-900 pb-2">
              <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-600">
                AUDIT ASSESSMENT REPORT · FY2026
              </div>
              <h3 className="mt-1 font-serif text-sm font-black text-zinc-950">
                Section 4.2: Cryptographic Secrets & Rotation
              </h3>
            </div>

            <div className="mt-4 space-y-2 text-[11px] leading-relaxed text-zinc-800 font-serif">
              <p>
                All cryptographic keys, certificates, and symmetric secrets deployed across
                production clusters <strong className="bg-amber-100 px-0.5">MUST</strong> undergo
                automated rotation every 90 days.
              </p>
              <p>
                Key derivation functions shall adhere to FIPS-140-3 guidelines and ensure ephemeral
                entropy validation.
              </p>
              <div className="my-3 rounded border border-zinc-300 bg-zinc-50 p-2 font-mono text-[9px] text-zinc-700">
                Policy Rule: SEC-ROT-090<br />
                Enforcement: Automated Daemon v2.4.1<br />
                Audit Trail: Compliant
              </div>
            </div>

            <div className="absolute bottom-4 left-6 right-6 flex items-center justify-between border-t border-zinc-300 pt-2 text-[9px] text-zinc-600 font-mono">
              <span>Doc ID: SEC-2026-AUD</span>
              <span>Page {page} of {totalPages}</span>
            </div>

            {/* ColPali Visual Patch Attention Heatmap Overlay */}
            {showPatchOverlay && (
              <svg
                role="img"
                aria-labelledby={`${idPrefix}-colpali-title ${idPrefix}-colpali-desc`}
                viewBox="0 0 16 12"
                className="absolute inset-0 h-full w-full pointer-events-none rounded opacity-80"
              >
                <title id={`${idPrefix}-colpali-title`}>ColPali Multi-Vector Patch Attention</title>
                <desc id={`${idPrefix}-colpali-desc`}>
                  Visual token late interaction heatmap grid overlaid on document page {page}.
                </desc>
                {patchGrid.map((row, r) =>
                  row.map((score, c) => (
                    <rect
                      key={`${r}-${c}`}
                      x={c}
                      y={r}
                      width="1"
                      height="1"
                      fill="#0284c7"
                      fillOpacity={score * 0.75}
                      stroke="#38bdf8"
                      strokeWidth="0.04"
                      strokeOpacity="0.4"
                    />
                  ))
                )}
              </svg>
            )}
          </div>

          {showPatchOverlay && (
            <div className="mt-3 flex items-center gap-2 rounded bg-sky-950/40 border border-sky-800/60 px-3 py-1.5 text-xs text-sky-200">
              <span className="h-2 w-2 rounded-full bg-sky-400 animate-pulse motion-reduce:animate-none" />
              <span>ColPali Multi-Vector Late Interaction: MaxSim attention focused on Section 4.2 clause</span>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              {copied ? "✓ Copied" : "Copy Extracted Text"}
            </button>
          </div>
          <pre className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200">
            {extractedText}
          </pre>
        </div>
      )}
    </div>
  );
}
