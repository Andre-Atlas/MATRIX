"use client";

import React, { useId, useState } from "react";
import { getBoxColor } from "@/lib/multimodal-utils";
import type { BoundingBox, Media } from "@/lib/types";

export interface ImageBBoxViewerProps {
  media: Media;
  alt?: string;
  className?: string;
  showControls?: boolean;
  interactive?: boolean;
}

export function ImageBBoxViewer({
  media,
  alt = "Multimodal image payload with bounding box annotations",
  className = "",
  showControls = true,
  interactive = true,
}: ImageBBoxViewerProps) {
  const [showBoxes, setShowBoxes] = useState(true);
  const [highlightedIndex, setHighlightedIndex] = useState<number | null>(null);
  const [selectedClass, setSelectedClass] = useState<string>("all");
  const [loadError, setLoadError] = useState(false);
  const idPrefix = useId();

  const boxes: BoundingBox[] = Array.isArray(media.boxes) ? media.boxes : [];
  const width = media.width || 1024;
  const height = media.height || 768;

  // Derive unique classes
  const classes = Array.from(new Set(boxes.map((b) => b.label || "unlabeled"))).sort();

  const filteredBoxes = boxes
    .map((b, originalIndex) => ({ ...b, originalIndex }))
    .filter((b) => selectedClass === "all" || (b.label || "unlabeled") === selectedClass);

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {showControls && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-xs">
          <div className="flex items-center gap-3">
            <label className="flex cursor-pointer items-center gap-2 text-zinc-300">
              <input
                type="checkbox"
                checked={showBoxes}
                onChange={(e) => setShowBoxes(e.target.checked)}
                className="h-4 w-4 rounded border-zinc-700 bg-zinc-800 text-emerald-500 focus:ring-emerald-400"
              />
              <span>Show Bounding Boxes ({boxes.length})</span>
            </label>

            {classes.length > 1 && (
              <div className="flex items-center gap-1.5">
                <span className="text-zinc-400">Class:</span>
                <select
                  aria-label="Filter bounding boxes by class"
                  value={selectedClass}
                  onChange={(e) => setSelectedClass(e.target.value)}
                  className="h-6 rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-200 focus:border-emerald-400 focus:outline-none"
                >
                  <option value="all">All Classes ({boxes.length})</option>
                  {classes.map((cls) => (
                    <option key={cls} value={cls}>
                      {cls} ({boxes.filter((b) => (b.label || "unlabeled") === cls).length})
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 font-mono text-[11px] text-zinc-400">
            <span>
              {width} × {height}
            </span>
            {media.mime && <span className="rounded bg-zinc-800 px-1 text-zinc-300">{media.mime}</span>}
          </div>
        </div>
      )}

      {/* Main Image + SVG container */}
      <div className="relative inline-block w-full overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
        {loadError ? (
          <div className="flex flex-col items-center justify-center p-8 text-center">
            <span className="text-2xl">⚠️</span>
            <p className="mt-2 text-xs font-medium text-red-300">
              Failed to load image payload (URL may be expired or inaccessible).
            </p>
            <p className="mt-1 font-mono text-[11px] text-zinc-400 break-all">{media.url}</p>
            <button
              type="button"
              onClick={() => setLoadError(false)}
              className="mt-3 inline-flex items-center rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Retry loading
            </button>
          </div>
        ) : (
          <div className="relative mx-auto flex items-center justify-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={media.url}
              alt={alt}
              loading="lazy"
              onError={() => setLoadError(true)}
              className="max-h-[32rem] w-full object-contain"
            />

            {showBoxes && boxes.length > 0 && (
              <svg
                role="img"
                aria-labelledby={`${idPrefix}-svg-title ${idPrefix}-svg-desc`}
                viewBox={`0 0 ${width} ${height}`}
                className="absolute inset-0 h-full w-full pointer-events-none"
              >
                <title id={`${idPrefix}-svg-title`}>Bounding Box Annotations</title>
                <desc id={`${idPrefix}-svg-desc`}>
                  Interactive overlay showing {boxes.length} detected object bounding boxes.
                </desc>
                <defs>
                  {filteredBoxes.map((box) => {
                    const color = getBoxColor(box.originalIndex, box.label);
                    return (
                      <filter
                        key={`glow-${box.originalIndex}`}
                        id={`${idPrefix}-glow-${box.originalIndex}`}
                        x="-20%"
                        y="-20%"
                        width="140%"
                        height="140%"
                      >
                        <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor={color} floodOpacity="0.8" />
                      </filter>
                    );
                  })}
                </defs>

                {filteredBoxes.map((box) => {
                  const isHighlighted = highlightedIndex === box.originalIndex;
                  const color = getBoxColor(box.originalIndex, box.label);
                  const labelText = box.label
                    ? `${box.label}${box.score !== undefined ? ` (${Math.round(box.score * 100)}%)` : ""}`
                    : `Box #${box.originalIndex + 1}`;

                  const textW = Math.max(70, labelText.length * 9 + 12);
                  const tagH = 22;
                  const tagY = Math.max(0, box.y - tagH);

                  return (
                    <g key={box.originalIndex}>
                      {/* Bounding box rectangle */}
                      <rect
                        x={box.x}
                        y={box.y}
                        width={box.w}
                        height={box.h}
                        fill={color}
                        fillOpacity={isHighlighted ? 0.35 : 0.15}
                        stroke={color}
                        strokeWidth={isHighlighted ? 4 : 2}
                        strokeDasharray={isHighlighted ? "4 2" : undefined}
                        filter={isHighlighted ? `url(#${idPrefix}-glow-${box.originalIndex})` : undefined}
                        className="transition-all duration-150"
                      />

                      {/* Tag pill */}
                      <g transform={`translate(${box.x}, ${tagY})`}>
                        <rect
                          width={textW}
                          height={tagH}
                          fill="#09090b"
                          fillOpacity="0.85"
                          stroke={color}
                          strokeWidth="1.5"
                          rx="4"
                        />
                        <text
                          x={6}
                          y={15}
                          fill="#f4f4f5"
                          fontSize="12"
                          fontWeight="600"
                          fontFamily="monospace"
                        >
                          {labelText}
                        </text>
                      </g>
                    </g>
                  );
                })}
              </svg>
            )}
          </div>
        )}
      </div>

      {/* Keyboard-focusable object list */}
      {interactive && boxes.length > 0 && (
        <div className="rounded border border-zinc-800 bg-zinc-900/40 p-3">
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-400">
            Detected Objects ({filteredBoxes.length} of {boxes.length})
          </h4>
          <div
            role="list"
            aria-label="Detected bounding boxes list"
            className="grid max-h-48 grid-cols-1 gap-1.5 overflow-y-auto sm:grid-cols-2"
          >
            {filteredBoxes.map((box) => {
              const color = getBoxColor(box.originalIndex, box.label);
              const isHighlighted = highlightedIndex === box.originalIndex;

              return (
                <div
                  key={box.originalIndex}
                  role="button"
                  tabIndex={0}
                  aria-label={`Highlight ${box.label || "box"} at coordinates ${box.x}, ${box.y}`}
                  onFocus={() => setHighlightedIndex(box.originalIndex)}
                  onBlur={() => setHighlightedIndex(null)}
                  onMouseEnter={() => setHighlightedIndex(box.originalIndex)}
                  onMouseLeave={() => setHighlightedIndex(null)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setHighlightedIndex(isHighlighted ? null : box.originalIndex);
                    }
                  }}
                  className={`flex cursor-pointer items-center justify-between gap-2 rounded border px-2.5 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                    isHighlighted
                      ? "border-emerald-500/80 bg-emerald-950/30 text-zinc-100"
                      : "border-zinc-800 bg-zinc-950/70 text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900"
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden
                      className="h-3 w-3 shrink-0 rounded-sm"
                      style={{ backgroundColor: color }}
                    />
                    <span className="truncate font-medium">{box.label || `Object #${box.originalIndex + 1}`}</span>
                  </div>

                  <div className="flex shrink-0 items-center gap-2 font-mono text-[11px]">
                    {box.score !== undefined && (
                      <span className="text-emerald-400 tabular-nums">
                        {Math.round(box.score * 100)}%
                      </span>
                    )}
                    <span className="text-zinc-400 tabular-nums">
                      [{box.x},{box.y},{box.w},{box.h}]
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
