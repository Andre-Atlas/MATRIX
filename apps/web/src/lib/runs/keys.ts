// Metric key helpers: prefix groups (`train/`, `eval/`…), system telemetry keys and "best" direction.

export const SYS_PREFIX = "sys/";
export const isSystemKey = (key: string) => key.startsWith(SYS_PREFIX);

/** Group name of a key: the text before the first `/`, or "" for keys without a prefix. */
export function keyPrefix(key: string): string {
  const i = key.indexOf("/");
  return i > 0 ? key.slice(0, i) : "";
}

export type KeyGroup = { prefix: string; keys: string[] };

const GROUP_ORDER = ["train", "eval", "val", "validation", "test"];

/**
 * Groups keys by prefix. Well-known prefixes come first (train, eval, val, test), then the rest
 * alphabetically, and unprefixed keys ("") last. Keys keep natural order inside a group. System
 * keys are left out unless `includeSystem`.
 */
export function groupKeysByPrefix(keys: readonly string[], includeSystem = false): KeyGroup[] {
  const groups = new Map<string, string[]>();
  for (const key of new Set(keys)) {
    if (!includeSystem && isSystemKey(key)) continue;
    const p = keyPrefix(key);
    const list = groups.get(p) ?? [];
    list.push(key);
    groups.set(p, list);
  }
  const rank = (p: string) => {
    if (p === "") return [2, ""] as const;
    const i = GROUP_ORDER.indexOf(p.toLowerCase());
    return i >= 0 ? ([0, String(i).padStart(3, "0")] as const) : ([1, p.toLowerCase()] as const);
  };
  return [...groups.entries()]
    .sort(([a], [b]) => {
      const [ra, sa] = rank(a);
      const [rb, sb] = rank(b);
      return ra - rb || sa.localeCompare(sb);
    })
    .map(([prefix, ks]) => ({ prefix, keys: ks.sort(naturalCompare) }));
}

export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, "en", { numeric: true, sensitivity: "base" });
}

// ── system telemetry ─────────────────────────────────────────────────────────────────────────

export const GPU_METRICS = ["utilization", "memory_used_mb", "temperature_c", "power_w"] as const;
export type GpuMetric = (typeof GPU_METRICS)[number];

export type SystemKeys = {
  cpu: string[];
  memory: string[];
  disk: string[];
  network: string[];
  process: string[];
  gpus: { index: number; keys: Partial<Record<GpuMetric, string>> }[];
  other: string[];
};

/** Sorts `sys/...` keys into the panels of the System tab; GPUs by numeric index. */
export function systemKeys(keys: readonly string[]): SystemKeys {
  const out: SystemKeys = { cpu: [], memory: [], disk: [], network: [], process: [], gpus: [], other: [] };
  const gpus = new Map<number, Partial<Record<GpuMetric, string>>>();
  for (const key of keys) {
    if (!isSystemKey(key)) continue;
    const rest = key.slice(SYS_PREFIX.length);
    const gpu = /^gpu\.(\d+)\.(.+)$/.exec(rest);
    if (gpu) {
      const idx = Number(gpu[1]);
      const metric = gpu[2] as GpuMetric;
      if ((GPU_METRICS as readonly string[]).includes(metric)) {
        const entry = gpus.get(idx) ?? {};
        entry[metric] = key;
        gpus.set(idx, entry);
      } else out.other.push(key);
      continue;
    }
    const head = rest.split(".")[0];
    if (head === "cpu") out.cpu.push(key);
    else if (head === "memory") out.memory.push(key);
    else if (head === "disk") out.disk.push(key);
    else if (head === "net") out.network.push(key);
    else if (head === "proc") out.process.push(key);
    else out.other.push(key);
  }
  out.gpus = [...gpus.entries()].sort(([a], [b]) => a - b).map(([index, k]) => ({ index, keys: k }));
  return out;
}

export function hasTelemetry(keys: readonly string[]): boolean {
  return keys.some(isSystemKey);
}

// ── best value ───────────────────────────────────────────────────────────────────────────────

const LOWER_IS_BETTER = /(^|[/_.\-\s])(loss|error|err|perplexity|ppl|rmse|mse|mae|mape|wer|cer|logloss|mlogloss|time|latency|duration)($|[/_.\-\s])|loss$/i;

/** Whether a smaller value of this metric is better (loss, error, perplexity, rmse, time…). */
export const lowerIsBetter = (key: string) => LOWER_IS_BETTER.test(key);

/** Index of the best value (min or max by key name); null when no value is finite. Ties: first. */
export function bestIndex(key: string, values: readonly (number | null | undefined)[]): number | null {
  const lower = lowerIsBetter(key);
  let best: number | null = null;
  values.forEach((v, i) => {
    if (v === null || v === undefined || !Number.isFinite(v)) return;
    if (best === null || (lower ? v < (values[best] as number) : v > (values[best] as number))) best = i;
  });
  return best;
}
