// Formatting for the runs screens: durations, telemetry units, artifact sizes and metric values.

/** Seconds as a compact duration: `850 ms`, `42s`, `3m 05s`, `2h 14m`, `3d 4h`. */
export function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Run duration: end − start, or now − start while it runs. */
export function runDuration(startedAt: string, endedAt: string | null, now: number = Date.now()): string {
  const a = Date.parse(startedAt);
  const b = endedAt ? Date.parse(endedAt) : now;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return "—";
  return formatSeconds(Math.max(0, (b - a) / 1000));
}

/** Watts: `245 W`, `1.20 kW`. */
export function formatPower(watts: number | null | undefined): string {
  if (watts === null || watts === undefined || !Number.isFinite(watts)) return "—";
  if (Math.abs(watts) >= 1000) return `${(watts / 1000).toFixed(2)} kW`;
  return `${watts >= 100 ? Math.round(watts) : watts.toFixed(1)} W`;
}

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];

/** Bytes in binary units: `512 B`, `1.5 KiB`, `12.0 MiB`, `3.25 GiB`. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  let v = bytes;
  let u = 0;
  while (v >= 1024 && u < UNITS.length - 1) {
    v /= 1024;
    u += 1;
  }
  if (u === 0) return `${Math.round(v)} B`;
  return `${v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${UNITS[u]}`;
}

/** Telemetry megabytes (the SDK reports `_mb` keys) in the same units as `formatBytes`. */
export const formatMegabytes = (mb: number | null | undefined) =>
  mb === null || mb === undefined || !Number.isFinite(mb) ? "—" : formatBytes(mb * 1024 * 1024);

/**
 * Metric values: 4 significant digits, scientific notation for very small or large magnitudes
 * (learning rates like `3.00e-5`), `NaN`/`∞` spelled out.
 */
export function formatMetric(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (Number.isNaN(v)) return "NaN";
  if (!Number.isFinite(v)) return v > 0 ? "∞" : "−∞";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a < 1e-3 || a >= 1e6) return v.toExponential(2);
  if (Number.isInteger(v)) return String(v);
  return String(Number(v.toPrecision(4)));
}

/** Short git commit (7 characters). */
export const shortCommit = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : "—");

/** Value formatter for a telemetry key by its unit suffix. */
export function formatSystemValue(key: string, v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (/_mb$/.test(key)) return formatMegabytes(v);
  if (/power_w$/.test(key)) return formatPower(v);
  if (/temperature_c$/.test(key)) return `${Math.round(v)} °C`;
  if (/(percent|utilization)$/.test(key)) return `${v.toFixed(0)}%`;
  return formatMetric(v);
}
