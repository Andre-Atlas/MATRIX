export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

export function formatCost(usd: number): string {
  if (!Number.isFinite(usd)) return "—";
  if (usd >= 100) return `$${usd.toFixed(2)}`;
  // Real LLM calls often cost a fraction of 0.01 cent; keep two significant digits instead of "$0.0000".
  if (usd > 0 && usd < 0.0001) return `$${usd.toPrecision(2)}`;
  return `$${usd.toFixed(4)}`;
}

/** Compact token counts: 950, 1.2k, 12k, 3.4M. */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs < 1000) return String(Math.round(n));
  if (abs < 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (abs < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** Ratio (0..1) as a percentage. */
export function formatPercent(ratio: number, digits = 1): string {
  if (!Number.isFinite(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** UTC timestamp as "YYYY-MM-DD HH:MM:SS" (stable on server and client). */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 19).replace("T", " ");
}

/** UTC time of day "HH:MM:SS.mmm". */
export function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(11, 23);
}

export function formatScore(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

export function truncate(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, Math.max(0, max - 1))}…`;
}

/**
 * Pretty-prints a payload. JSON is re-indented when the mime type says so or, without a
 * mime type, when the text parses as a JSON object or array. Anything else is returned as is.
 */
export function formatPayload(text: string, mime?: string | null): { text: string; json: boolean } {
  const looksJson = /^\s*[[{]/.test(text);
  if (mime === "application/json" || (!mime && looksJson)) {
    try {
      return { text: JSON.stringify(JSON.parse(text), null, 2), json: true };
    } catch {
      return { text, json: false };
    }
  }
  return { text, json: false };
}

/** Stringifies an attribute value for display. */
export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
