import { describe, expect, it } from "vitest";
import {
  generateWaveformBars,
  getBoxColor,
  getMediaSummary,
  MULTIMODAL_PALETTE,
  parseDiffHunks,
} from "./multimodal-utils";
import type { Media, TextDiffPayload } from "./types";

describe("multimodal-utils", () => {
  describe("getBoxColor", () => {
    it("returns consistent colors for the same label", () => {
      const color1 = getBoxColor(0, "Revenue Header");
      const color2 = getBoxColor(5, "Revenue Header");
      expect(color1).toBe(color2);
      expect(MULTIMODAL_PALETTE).toContain(color1);
    });

    it("cycles through palette when no label is provided", () => {
      const c0 = getBoxColor(0);
      const c1 = getBoxColor(1);
      expect(c0).toBe(MULTIMODAL_PALETTE[0]);
      expect(c1).toBe(MULTIMODAL_PALETTE[1]);
    });
  });

  describe("generateWaveformBars", () => {
    it("generates the requested number of normalized bars", () => {
      const bars = generateWaveformBars(14200, 48);
      expect(bars).toHaveLength(48);
      for (const bar of bars) {
        expect(bar).toBeGreaterThanOrEqual(0.12);
        expect(bar).toBeLessThanOrEqual(1.0);
      }
    });

    it("generates deterministic waveform for the same duration", () => {
      const b1 = generateWaveformBars(5000, 24);
      const b2 = generateWaveformBars(5000, 24);
      expect(b1).toEqual(b2);
    });
  });

  describe("parseDiffHunks", () => {
    it("handles null or undefined diff gracefully", () => {
      expect(parseDiffHunks(undefined)).toEqual([]);
    });

    it("parses structured hunk objects with +/- lines", () => {
      const diff: TextDiffPayload = {
        hunks: [
          {
            old_start: 1,
            new_start: 1,
            lines: [
              " function auth(req) {",
              "-  return req.cookies.session_id;",
              "+  return req.headers.authorization;",
              " }",
            ],
          },
        ],
      };

      const parsed = parseDiffHunks(diff);
      expect(parsed).toHaveLength(4);
      expect(parsed[0]).toEqual({ type: "ctx", oldLineNumber: 1, newLineNumber: 1, text: "function auth(req) {" });
      expect(parsed[1]).toEqual({ type: "del", oldLineNumber: 2, text: "  return req.cookies.session_id;" });
      expect(parsed[2]).toEqual({ type: "add", newLineNumber: 2, text: "  return req.headers.authorization;" });
      expect(parsed[3]).toEqual({ type: "ctx", oldLineNumber: 3, newLineNumber: 3, text: "}" });
    });

    it("falls back to before/after comparison if hunks are not provided", () => {
      const diff: TextDiffPayload = {
        before: "line 1\nold line 2\nline 3",
        after: "line 1\nnew line 2\nline 3",
      };

      const parsed = parseDiffHunks(diff);
      expect(parsed).toHaveLength(4);
      expect(parsed[0].type).toBe("ctx");
      expect(parsed[1].type).toBe("del");
      expect(parsed[2].type).toBe("add");
      expect(parsed[3].type).toBe("ctx");
    });
  });

  describe("getMediaSummary", () => {
    it("summarizes multimodal media kinds and counts", () => {
      const media: Media[] = [
        {
          kind: "image",
          url: "https://example.com/test.png",
          mime: "image/png",
          boxes: [
            { x: 10, y: 10, w: 50, h: 20, label: "Test" },
            { x: 70, y: 10, w: 50, h: 20, label: "Score" },
          ],
        },
        {
          kind: "audio",
          url: "https://example.com/test.wav",
          mime: "audio/wav",
          duration_ms: 12000,
        },
        {
          kind: "pdf",
          url: "https://example.com/test.pdf",
          mime: "application/pdf",
          page: 2,
        },
      ];

      const summary = getMediaSummary(media);
      expect(summary.count).toBe(3);
      expect(summary.hasImage).toBe(true);
      expect(summary.hasAudio).toBe(true);
      expect(summary.hasPdf).toBe(true);
      expect(summary.hasDiff).toBe(false);
      expect(summary.boxCount).toBe(2);
      expect(summary.primaryKind).toBe("image");
    });

    it("handles empty media list", () => {
      const summary = getMediaSummary([]);
      expect(summary.count).toBe(0);
      expect(summary.hasImage).toBe(false);
      expect(summary.boxCount).toBe(0);
    });
  });
});
