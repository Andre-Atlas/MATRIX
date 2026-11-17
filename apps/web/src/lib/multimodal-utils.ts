import { VARIANT_COLORS } from "./mlops-tokens";
import type { BoundingBox, Media, TextDiffPayload } from "./types";

export const MULTIMODAL_PALETTE = [
  "#38bdf8", // sky-400
  "#34d399", // emerald-400
  "#fb923c", // orange-400
  "#a78bfa", // violet-400
  "#f472b6", // pink-400
  "#22d3ee", // cyan-400
  "#facc15", // yellow-400
  "#818cf8", // indigo-400
] as const;

export function getBoxColor(index: number, label?: string): string {
  if (label) {
    let hash = 0;
    for (let i = 0; i < label.length; i++) {
      hash = (hash << 5) - hash + label.charCodeAt(i);
      hash |= 0;
    }
    const idx = Math.abs(hash) % MULTIMODAL_PALETTE.length;
    return MULTIMODAL_PALETTE[idx];
  }
  return MULTIMODAL_PALETTE[index % MULTIMODAL_PALETTE.length];
}

export function generateWaveformBars(durationMs?: number, count = 48): number[] {
  const seed = (durationMs || 10000) % 1000;
  const bars: number[] = [];
  for (let i = 0; i < count; i++) {
    // Deterministic pseudo-random audio envelope simulation
    const angle = (i / count) * Math.PI;
    const baseEnvelope = Math.sin(angle);
    const harmonic1 = Math.sin(angle * 3.5 + seed * 0.05) * 0.35;
    const harmonic2 = Math.cos(angle * 7.2 + i) * 0.25;
    const raw = Math.abs(baseEnvelope * 0.6 + harmonic1 + harmonic2);
    // Clamp between 0.12 and 1.0
    const clamped = Math.max(0.12, Math.min(1.0, raw));
    bars.push(Math.round(clamped * 100) / 100);
  }
  return bars;
}

export interface ParsedDiffLine {
  type: "add" | "del" | "ctx";
  oldLineNumber?: number;
  newLineNumber?: number;
  text: string;
}

export function parseDiffHunks(diff?: TextDiffPayload): ParsedDiffLine[] {
  if (!diff) return [];
  const lines: ParsedDiffLine[] = [];

  if (Array.isArray(diff.hunks) && diff.hunks.length > 0) {
    for (const hunk of diff.hunks) {
      if (typeof hunk === "string") {
        const rawLines = hunk.split("\n");
        for (const raw of rawLines) {
          if (raw.startsWith("+")) {
            lines.push({ type: "add", text: raw.slice(1) });
          } else if (raw.startsWith("-")) {
            lines.push({ type: "del", text: raw.slice(1) });
          } else {
            lines.push({ type: "ctx", text: raw.startsWith(" ") ? raw.slice(1) : raw });
          }
        }
      } else if (typeof hunk === "object" && Array.isArray(hunk.lines)) {
        let curOld = hunk.old_start ?? 1;
        let curNew = hunk.new_start ?? 1;
        for (const raw of hunk.lines) {
          if (raw.startsWith("+")) {
            lines.push({ type: "add", newLineNumber: curNew++, text: raw.slice(1) });
          } else if (raw.startsWith("-")) {
            lines.push({ type: "del", oldLineNumber: curOld++, text: raw.slice(1) });
          } else {
            lines.push({
              type: "ctx",
              oldLineNumber: curOld++,
              newLineNumber: curNew++,
              text: raw.startsWith(" ") ? raw.slice(1) : raw,
            });
          }
        }
      }
    }
  } else if (diff.before !== undefined || diff.after !== undefined) {
    const beforeLines = (diff.before ?? "").split("\n");
    const afterLines = (diff.after ?? "").split("\n");
    const max = Math.max(beforeLines.length, afterLines.length);

    for (let i = 0; i < max; i++) {
      const b = beforeLines[i];
      const a = afterLines[i];
      if (b === a && b !== undefined) {
        lines.push({ type: "ctx", oldLineNumber: i + 1, newLineNumber: i + 1, text: b });
      } else {
        if (b !== undefined) {
          lines.push({ type: "del", oldLineNumber: i + 1, text: b });
        }
        if (a !== undefined) {
          lines.push({ type: "add", newLineNumber: i + 1, text: a });
        }
      }
    }
  }

  return lines;
}

export interface MediaSummary {
  hasImage: boolean;
  hasAudio: boolean;
  hasPdf: boolean;
  hasDiff: boolean;
  boxCount: number;
  primaryKind: string | null;
  count: number;
}

export function getMediaSummary(media?: Media[]): MediaSummary {
  if (!media || !Array.isArray(media) || media.length === 0) {
    return {
      hasImage: false,
      hasAudio: false,
      hasPdf: false,
      hasDiff: false,
      boxCount: 0,
      primaryKind: null,
      count: 0,
    };
  }

  let hasImage = false;
  let hasAudio = false;
  let hasPdf = false;
  let hasDiff = false;
  let boxCount = 0;

  for (const m of media) {
    if (m.kind === "image" || m.kind === "bbox") {
      hasImage = true;
      if (Array.isArray(m.boxes)) {
        boxCount += m.boxes.length;
      }
    } else if (m.kind === "audio") {
      hasAudio = true;
    } else if (m.kind === "pdf") {
      hasPdf = true;
    } else if (m.kind === "text_diff") {
      hasDiff = true;
    }
  }

  return {
    hasImage,
    hasAudio,
    hasPdf,
    hasDiff,
    boxCount,
    primaryKind: media[0]?.kind ?? null,
    count: media.length,
  };
}
