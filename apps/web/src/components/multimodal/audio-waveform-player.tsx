"use client";

import React, { useId, useMemo, useState } from "react";
import { generateWaveformBars } from "@/lib/multimodal-utils";
import type { Media } from "@/lib/types";

export interface AudioWaveformPlayerProps {
  media: Media;
  transcript?: string | null;
  className?: string;
}

export function AudioWaveformPlayer({
  media,
  transcript,
  className = "",
}: AudioWaveformPlayerProps) {
  const [showTranscript, setShowTranscript] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const idPrefix = useId();

  const durationSec = media.duration_ms ? (media.duration_ms / 1000).toFixed(1) : "—";
  const bars = useMemo(() => generateWaveformBars(media.duration_ms, 54), [media.duration_ms]);

  return (
    <div className={`flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-950 p-4 ${className}`}>
      {/* Waveform Visualization SVG */}
      <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-3">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="flex items-center gap-1.5 font-medium text-zinc-300">
            <span aria-hidden className="text-emerald-400">♫</span>
            <span>Audio Spectrogram & Waveform</span>
          </span>
          <div className="flex items-center gap-3 font-mono text-[11px] text-zinc-400">
            <span>Duration: {durationSec}s</span>
            {media.mime && <span className="rounded bg-zinc-800 px-1 text-zinc-300">{media.mime}</span>}
          </div>
        </div>

        <svg
          role="img"
          aria-labelledby={`${idPrefix}-waveform-title ${idPrefix}-waveform-desc`}
          viewBox="0 0 540 60"
          className="h-16 w-full text-emerald-400"
        >
          <title id={`${idPrefix}-waveform-title`}>Audio Amplitude Waveform</title>
          <desc id={`${idPrefix}-waveform-desc`}>
            Normalized visual waveform showing amplitude across {durationSec} seconds with 54 frequency bands.
          </desc>
          <line x1="0" y1="30" x2="540" y2="30" stroke="#3f3f46" strokeWidth="1" strokeDasharray="3 3" />
          {bars.map((bar, i) => {
            const barH = Math.max(4, Math.round(bar * 50));
            const y = (60 - barH) / 2;
            const x = i * 10 + 2;
            return (
              <rect
                key={i}
                x={x}
                y={y}
                width="6"
                height={barH}
                rx="3"
                className="fill-emerald-400/80 hover:fill-emerald-300 transition-colors"
              />
            );
          })}
        </svg>
      </div>

      {/* Audio Controls */}
      {loadError ? (
        <div className="rounded border border-red-900/60 bg-red-950/30 p-3 text-xs text-red-200">
          <p>Audio playback error: unable to load media stream from URL.</p>
          <a
            href={media.url}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-1 inline-block font-mono text-[11px] text-emerald-300 underline"
          >
            Direct download link ↗
          </a>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <audio
            controls
            preload="metadata"
            src={media.url}
            onError={() => setLoadError(true)}
            className="h-10 w-full rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <track kind="captions" />
            <a href={media.url} className="text-xs text-emerald-400 underline">
              Download audio file
            </a>
          </audio>
        </div>
      )}

      {/* Optional Transcript Section */}
      {transcript && (
        <div className="rounded border border-zinc-800/80 bg-zinc-900/40 p-3">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-medium uppercase tracking-wider text-zinc-400">
              Transcription / Annotation
            </h4>
            <button
              type="button"
              onClick={() => setShowTranscript(!showTranscript)}
              className="text-xs text-emerald-400 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-400"
            >
              {showTranscript ? "Hide" : "Show"}
            </button>
          </div>
          {showTranscript && (
            <p className="mt-2 text-xs leading-relaxed text-zinc-200 whitespace-pre-wrap font-sans">
              “{transcript}”
            </p>
          )}
        </div>
      )}
    </div>
  );
}
