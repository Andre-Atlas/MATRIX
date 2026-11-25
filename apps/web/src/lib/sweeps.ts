import type {
  CanonicalSweepConfig,
  CanonicalSweepParam,
  CanonicalSweepStatus,
} from "./types";

export const MAX_GRID_COMBINATIONS = 100_000;
export const MAX_SWEEP_RUNS = 10_000;

export function getSweepStatusStyle(status: CanonicalSweepStatus | string): {
  label: string;
  badge: string;
  dot: string;
} {
  switch (status) {
    case "running":
      return {
        label: "Running",
        badge: "border-emerald-700/80 bg-emerald-950/60 text-emerald-200",
        dot: "bg-emerald-400",
      };
    case "paused":
      return {
        label: "Paused",
        badge: "border-amber-700/80 bg-amber-950/60 text-amber-200",
        dot: "bg-amber-400",
      };
    case "finished":
      return {
        label: "Finished",
        badge: "border-sky-700/80 bg-sky-950/60 text-sky-200",
        dot: "bg-sky-400",
      };
    case "stopped":
      return {
        label: "Stopped",
        badge: "border-zinc-600 bg-zinc-900 text-zinc-200",
        dot: "bg-zinc-400",
      };
    default:
      return {
        label: status,
        badge: "border-zinc-700 bg-zinc-900 text-zinc-300",
        dot: "bg-zinc-400",
      };
  }
}

export type ImportanceEntry = {
  param: string;
  score: number;
};

/** Normalizes backend `importance` (`[{lr: 0.6}, {bs: 0.4}]` or `Record<string, number>`) into sorted entries. */
export function normalizeImportance(
  importance: Record<string, number>[] | Record<string, number> | null | undefined,
): ImportanceEntry[] {
  if (!importance) return [];
  const entries: ImportanceEntry[] = [];
  if (Array.isArray(importance)) {
    for (const item of importance) {
      if (!item || typeof item !== "object") continue;
      for (const [k, v] of Object.entries(item)) {
        if (typeof v === "number" && Number.isFinite(v)) {
          entries.push({ param: k, score: v });
        }
      }
    }
  } else if (typeof importance === "object") {
    for (const [k, v] of Object.entries(importance)) {
      if (typeof v === "number" && Number.isFinite(v)) {
        entries.push({ param: k, score: v });
      }
    }
  }
  return entries.sort((a, b) => b.score - a.score);
}

/** Computes total grid combinations (or null if non-grid / invalid). */
export function estimateGridSize(
  parameters: Record<string, CanonicalSweepParam>,
): { size: number | null; errors: string[] } {
  const errors: string[] = [];
  const keys = Object.keys(parameters);
  if (keys.length === 0) {
    return { size: 0, errors: ["At least one parameter is required."] };
  }
  let total = 1;
  for (const [name, p] of Object.entries(parameters)) {
    if (p.type === "choice") {
      if (!Array.isArray(p.values) || p.values.length === 0) {
        errors.push(`Parameter "${name}": choice values must be non-empty.`);
        continue;
      }
      total *= p.values.length;
    } else if (p.type === "constant") {
      total *= 1;
    } else if (p.type === "int") {
      const step = p.step ?? 1;
      if (step <= 0 || p.max < p.min) {
        errors.push(`Parameter "${name}": invalid int range [${p.min}, ${p.max}] step ${step}.`);
        continue;
      }
      const count = Math.floor((p.max - p.min) / step) + 1;
      total *= Math.max(1, count);
    } else {
      errors.push(`Parameter "${name}": grid search does not support float ranges.`);
    }
  }
  if (total > MAX_GRID_COMBINATIONS) {
    errors.push(
      `Grid size (${total.toLocaleString()}) exceeds maximum of ${MAX_GRID_COMBINATIONS.toLocaleString()} combinations.`,
    );
  }
  return { size: errors.length === 0 ? total : null, errors };
}

/** Client-side pre-validation matching G2 contract rules before POST /api/v1/sweeps. */
export function validateSweepDraft(
  name: string,
  config: CanonicalSweepConfig,
): { valid: boolean; errors: string[]; gridSize: number | null } {
  const errors: string[] = [];
  if (!name.trim()) errors.push("Sweep name is required.");
  if (!config.metric?.key?.trim()) errors.push("Target metric key is required.");
  const paramEntries = Object.entries(config.parameters ?? {});
  if (paramEntries.length === 0) {
    errors.push("Define at least one hyperparameter.");
  }

  for (const [k, p] of paramEntries) {
    if ((p.type === "int" || p.type === "float") && p.min > p.max) {
      errors.push(`Parameter "${k}": min (${p.min}) must be <= max (${p.max}).`);
    }
    if ((p.type === "int" || p.type === "float") && p.log && p.min <= 0) {
      errors.push(`Parameter "${k}": log scale requires min > 0.`);
    }
    if (p.type === "choice" && (!Array.isArray(p.values) || p.values.length === 0)) {
      errors.push(`Parameter "${k}": choice requires at least one value.`);
    }
  }

  if (config.max_runs !== undefined && (config.max_runs < 1 || config.max_runs > MAX_SWEEP_RUNS)) {
    errors.push(`max_runs must be between 1 and ${MAX_SWEEP_RUNS.toLocaleString()}.`);
  }

  let gridSize: number | null = null;
  if (config.method === "grid") {
    const grid = estimateGridSize(config.parameters ?? {});
    gridSize = grid.size;
    errors.push(...grid.errors);
  }

  return { valid: errors.length === 0, errors, gridSize };
}
